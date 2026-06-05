import asyncio
import json
import logging
import os
from typing import Any, Dict, Optional, Tuple

import aio_pika

from util import log_message, DEVICE_LIST_NAME
from fb_task_patched import get_urgent_objects, handle_urgent_if_any

RABBIT_URL = os.getenv("RABBIT_URL", "amqp://admin:123456@43.239.223.214:5672")
RABBIT_EXCHANGE = os.getenv("RABBIT_EXCHANGE", "ex.machine")
RABBIT_EXCHANGE_TYPE = os.getenv("RABBIT_EXCHANGE_TYPE", "direct")
RABBIT_RESULT_QUEUE = os.getenv("RABBIT_RESULT_QUEUE", "q.command.result")

# Prefetch=1 để tránh 1 máy ăn nhiều job cùng lúc (UI automation rất dễ đụng nhau)
PREFETCH = int(os.getenv("RABBIT_PREFETCH", "1"))


def _loads(b: bytes) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(b.decode("utf-8"))
    except Exception:
        return None


async def _publish_result(ch: aio_pika.Channel, payload: Dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    msg = aio_pika.Message(
        body=body,
        content_type="application/json",
        delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
        message_id=str(payload.get("command_id") or ""),
    )
    # sendToQueue -> default exchange
    await ch.default_exchange.publish(msg, routing_key=RABBIT_RESULT_QUEUE)


async def start_device_rabbit_consumer(driver, machine_code: str):
    """
    machine_code phải đúng với NodeJS publishToMachine(machineCode).
    Consumer:
      - queue: q.machine.<machine_code>
      - exchange: ex.machine (direct)
      - routingKey: machine.<machine_code>
    Result:
      - queue: q.command.result
    """
    queue_name = f"q.machine.{machine_code}"
    routing_key = f"machine.{machine_code}"

    while True:
        try:
            conn = await aio_pika.connect_robust(RABBIT_URL)
            async with conn:
                ch = await conn.channel()
                await ch.set_qos(prefetch_count=PREFETCH)

                ex_type = getattr(
                    aio_pika.ExchangeType,
                    (RABBIT_EXCHANGE_TYPE or "direct").lower(),
                    aio_pika.ExchangeType.DIRECT,
                )
                ex = await ch.declare_exchange(RABBIT_EXCHANGE, ex_type, durable=True)

                q = await ch.declare_queue(queue_name, durable=True)
                await q.bind(ex, routing_key=routing_key)

                dev_label = DEVICE_LIST_NAME.get(driver.serial, driver.serial)
                log_message(f"[RabbitMQ] [{dev_label}] consuming {queue_name} <- {RABBIT_EXCHANGE}:{routing_key}", logging.INFO)

                async with q.iterator() as it:
                    async for msg in it:
                        payload = _loads(msg.body)
                        log_message(f"[RabbitMQ] payload: {payload}", logging.INFO)
                        log_message(
                            f"[RabbitMQ DEBUG] params type={type(payload.get('params'))}",
                            logging.INFO
                        )
                        if not payload:
                            log_message(f"[RabbitMQ] bad json -> drop, body={msg.body[:200]!r}", logging.WARNING)
                            await msg.ack()
                            continue

                        command_id = str(payload.get("command_id") or "").strip()
                        action = str(payload.get("type") or payload.get("action") or "").strip()

                        # 1) đẩy vào urgent queue RAM để reuse handle_urgent_if_any
                        event, queue = get_urgent_objects(driver.serial)
                        job = dict(payload)
                        job["id"] = command_id  # unify for existing code paths
                        job["action"] = action  # unify
                        job["source"] = "rabbitmq"
                        await queue.put(job)
                        event.set()

                        # 2) chạy job ngay (synchronous) để chỉ ACK khi done
                        ok, reason = False, ""
                        try:
                            # handle_urgent_if_any sẽ chạy hết queue của device (có lock)
                            await handle_urgent_if_any(driver, crm_id=payload.get("crm_id"), account=payload.get("user_id") or "default")
                            ok = True
                        except Exception as e:
                            ok = False
                            reason = f"{type(e).__name__}: {e}"

                        # 3) publish result cho NodeJS
                        try:
                            await _publish_result(
                                ch,
                                {
                                    "command_id": command_id,
                                    "device_id": machine_code,
                                    "type": action,
                                    "ok": ok,
                                    "status": "SUCCESS" if ok else "FAILED",
                                    "reason": reason,
                                },
                            )
                        except Exception as e:
                            # Nếu không publish được result, KHÔNG ack để RabbitMQ redeliver (an toàn)
                            log_message(f"[RabbitMQ] publish result failed -> nack requeue. err={e}", logging.WARNING)
                            await msg.nack(requeue=True)
                            continue

                        # 4) ACK job message sau khi publish result ok
                        await msg.ack()

        except asyncio.CancelledError:
            raise
        except Exception as e:
            log_message(f"[RabbitMQ] consumer error: {type(e).__name__}: {e} (retry 3s)", logging.WARNING)
            await asyncio.sleep(3)