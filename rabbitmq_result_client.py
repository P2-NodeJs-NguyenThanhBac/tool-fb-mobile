import json
import os
import asyncio
from typing import Any, Dict, Optional

import aio_pika
import pymongo_management

RABBIT_URL = os.getenv("RABBIT_URL", "amqp://admin:123456@43.239.223.214:5672")
RABBIT_RESULT_QUEUE = os.getenv("RABBIT_RESULT_QUEUE", "q.command.result")
RABBIT_RESULT_PUBLISH_RETRIES = max(1, int(os.getenv("RABBIT_RESULT_PUBLISH_RETRIES", "3")))
RABBIT_RESULT_RETRY_INTERVAL_SECONDS = max(5, int(os.getenv("RABBIT_RESULT_RETRY_INTERVAL_SECONDS", "15")))


async def _publish_rabbitmq_result_payload(payload: Dict[str, Any]) -> tuple[bool, Optional[str]]:
    last_error = None
    for attempt in range(1, RABBIT_RESULT_PUBLISH_RETRIES + 1):
        try:
            conn = await aio_pika.connect_robust(RABBIT_URL)
            async with conn:
                ch = await conn.channel(publisher_confirms=True)

                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                msg = aio_pika.Message(
                    body=body,
                    content_type="application/json",
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                    message_id=str(payload.get("command_id") or ""),
                )

                await ch.default_exchange.publish(msg, routing_key=RABBIT_RESULT_QUEUE)
            return True, None
        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"
            if attempt < RABBIT_RESULT_PUBLISH_RETRIES:
                await asyncio.sleep(min(2 ** (attempt - 1), 3))

    return False, last_error


async def publish_rabbitmq_result(payload: Dict[str, Any]) -> tuple[bool, Optional[str]]:
    await pymongo_management.save_rabbitmq_result_outbox(payload)
    ok, error = await _publish_rabbitmq_result_payload(payload)
    await pymongo_management.mark_rabbitmq_result_publish_attempt(
        payload.get("command_id"),
        ok=ok,
        error=error,
    )
    return ok, error


async def flush_pending_rabbitmq_results(limit: int = 100) -> int:
    docs = await pymongo_management.get_pending_rabbitmq_outbox(limit=limit)
    flushed = 0
    for doc in docs:
        payload = doc.get("payload") or {}
        command_id = payload.get("command_id") or doc.get("command_id")
        if not command_id:
            continue
        ok, error = await _publish_rabbitmq_result_payload(payload)
        await pymongo_management.mark_rabbitmq_result_publish_attempt(
            command_id,
            ok=ok,
            error=error,
        )
        if ok:
            flushed += 1
    return flushed


async def start_rabbitmq_result_retry_loop():
    while True:
        try:
            await flush_pending_rabbitmq_results(limit=100)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        await asyncio.sleep(RABBIT_RESULT_RETRY_INTERVAL_SECONDS)
