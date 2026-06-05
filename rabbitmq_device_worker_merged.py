import asyncio
import json
import logging
import os
from typing import Any, Dict, Optional

import aio_pika

from util import log_message, DEVICE_LIST_NAME
import pymongo_management
from rabbitmq_result_client import publish_rabbitmq_result
# from fb_task_patched import get_urgent_objects

SEEN_COMMANDS: dict[str, float] = {}
SEEN_TTL_SECONDS = 600

RABBIT_URL = os.getenv("RABBIT_URL", "amqp://admin:123456@43.239.223.214:5672")
RABBIT_EXCHANGE = os.getenv("RABBIT_EXCHANGE", "ex.machine")
RABBIT_EXCHANGE_TYPE = os.getenv("RABBIT_EXCHANGE_TYPE", "direct")
RABBIT_RESULT_QUEUE = os.getenv("RABBIT_RESULT_QUEUE", "q.command.result")
PREFETCH = int(os.getenv("RABBIT_PREFETCH", "1"))
PUBLISH_QUEUED_RESULT = int(os.getenv("RABBIT_PUBLISH_QUEUED_RESULT", "0"))
RABBIT_RECOVERY_INTERVAL_SECONDS = max(5, int(os.getenv("RABBIT_RECOVERY_INTERVAL_SECONDS", "30")))
RABBIT_RUNNING_STALE_MINUTES = max(1, int(os.getenv("RABBIT_RUNNING_STALE_MINUTES", "30")))


import time

def _cleanup_seen_commands():
    now = time.time()
    expired = [k for k, ts in SEEN_COMMANDS.items() if now - ts > SEEN_TTL_SECONDS]
    for k in expired:
        SEEN_COMMANDS.pop(k, None)

def _is_duplicate_command(command_id: str) -> bool:
    if not command_id:
        return False
    _cleanup_seen_commands()
    if command_id in SEEN_COMMANDS:
        return True
    SEEN_COMMANDS[command_id] = time.time()
    return False

def _loads(b: bytes) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(b.decode("utf-8"))
    except Exception:
        return None


def _maybe_parse_json(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    raw = value.strip()
    if not raw:
        return value
    if (raw.startswith("{") and raw.endswith("}")) or (raw.startswith("[") and raw.endswith("]")):
        try:
            return json.loads(raw)
        except Exception:
            return value
    return value


def _normalize_job(payload: Dict[str, Any]) -> Dict[str, Any]:
    job = dict(payload)

    command_id = str(
        payload.get("command_id")
        or payload.get("id")
        or payload.get("_id")
        or ""
    ).strip()

    raw_action = str(payload.get("type") or payload.get("action") or "").strip().lower()

    params = _maybe_parse_json(payload.get("params") or {})
    if not isinstance(params, dict):
        params = {}

    files = payload.get("files")
    if files is None:
        files = params.get("files")
    files = _maybe_parse_json(files)

    action = raw_action
    if action in ("post_to_wall", "post_wall"):
        action = "post_wall"
    elif action in ("post_to_group", "post_group"):
        action = "post_group"
    elif action in ("join_group", "join_fb_group"):
        action = "join_group"
    elif action in ("crawl_post_comments", "crawl_comments", "fetch_post_comments"):
        action = "crawl_post_comments"
    elif action in (
        "crawl_mobile_fb_1_1_farming",
        "crawl_fb_1_1_farming",
        "crawl_messenger_unread_farming",
        "crawl_messenger_notifications_farming",
        "fb_mobile_message_farming",
    ):
        action = "crawl_mobile_fb_1_1_farming"

    job["command_id"] = command_id
    job["id"] = command_id
    job["action"] = action
    job["type"] = action
    job["params"] = params
    job["files"] = files
    job["content"] = payload.get("content") or params.get("content") or ""
    job["group_link"] = payload.get("group_link") or params.get("group_link")
    job["post_link"] = payload.get("post_link") or params.get("post_link")
    job["user_id"] = payload.get("user_id") or params.get("user_id")
    job["user_name"] = payload.get("user_name") or params.get("user_name")
    auto_join_default = True if action == "post_group" else False
    job["auto_join_if_needed"] = bool(
        payload.get("auto_join_if_needed", params.get("auto_join_if_needed", auto_join_default))
    )
    job["join_timeout_sec"] = int(
        payload.get("join_timeout_sec", params.get("join_timeout_sec", 15))
    )
    job["source"] = "rabbitmq"
    job["kind"] = payload.get("kind") or "action"
    return job


def _rabbit_result_type(action: str) -> str:
    return {
        "post_wall": "post_to_wall",
        "post_group": "post_to_group",
    }.get(action, action)


async def _publish_result(ch: aio_pika.Channel, payload: Dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    msg = aio_pika.Message(
        body=body,
        content_type="application/json",
        delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
        message_id=str(payload.get("command_id") or ""),
    )
    await ch.default_exchange.publish(msg, routing_key=RABBIT_RESULT_QUEUE)


async def enqueue_recoverable_rabbitmq_jobs(
    machine_code: str,
    limit: int = 50,
    include_fresh_queued: bool = False,
) -> int:
    docs = await pymongo_management.claim_recoverable_rabbitmq_jobs(
        machine_code=machine_code,
        limit=limit,
        running_stale_minutes=RABBIT_RUNNING_STALE_MINUTES,
        include_fresh_queued=include_fresh_queued,
    )
    if not docs:
        return 0

    from fb_task_patched import get_urgent_objects

    event, queue = get_urgent_objects(machine_code)
    pushed = 0
    for doc in docs:
        job = doc.get("latest_job") or doc.get("job")
        if not isinstance(job, dict):
            continue
        await queue.put(job)
        pushed += 1

    if pushed:
        event.set()
    return pushed


async def start_rabbitmq_recovery_loop(machine_code: str):
    first_pass = True
    while True:
        try:
            pushed = await enqueue_recoverable_rabbitmq_jobs(
                machine_code,
                include_fresh_queued=first_pass,
            )
            first_pass = False
            if pushed:
                dev_label = DEVICE_LIST_NAME.get(machine_code, machine_code)
                log_message(
                    f"[RabbitMQ] [{dev_label}] recovered {pushed} persisted command(s)",
                    logging.INFO,
                )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            dev_label = DEVICE_LIST_NAME.get(machine_code, machine_code)
            log_message(
                f"[RabbitMQ] [{dev_label}] recovery loop error: {type(e).__name__}: {e}",
                logging.WARNING,
            )
        await asyncio.sleep(RABBIT_RECOVERY_INTERVAL_SECONDS)


async def start_device_rabbit_consumer(machine_code: str):
    """
    Consumer cho từng thiết bị.

    Mục tiêu của bản merge:
      - RabbitMQ chỉ nhận và bơm job vào queue RAM của device.
      - KHÔNG tự gọi handle_urgent_if_any() ở đây để tránh đè luồng main.py.
      - Việc execute job sẽ diễn ra tự nhiên bên trong fb_task_patched / run_on_device_original.

    Queue bind:
      - exchange: ex.machine (direct)
      - routing_key: machine.<machine_code>
      - queue: q.machine.<machine_code>
    """
    queue_name = f"q.machine.{machine_code}"
    routing_key = f"machine.{machine_code}"
    dev_label = DEVICE_LIST_NAME.get(machine_code, machine_code)

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

                log_message(
                    f"[RabbitMQ] [{dev_label}] consuming {queue_name} <- {RABBIT_EXCHANGE}:{routing_key}",
                    logging.INFO,
                )

                async with q.iterator() as it:
                    async for msg in it:
                        try:
                            payload = _loads(msg.body)
                            if not payload:
                                log_message(
                                    f"[RabbitMQ] [{dev_label}] bad json -> drop body={msg.body[:200]!r}",
                                    logging.WARNING,
                                )
                                await msg.ack()
                                continue

                            job = _normalize_job(payload)
                            if str(job.get("kind") or "action").lower() != "action":
                                log_message(
                                    f"[RabbitMQ] [{dev_label}] non-action message received in device queue -> drop payload={payload}",
                                    logging.WARNING,
                                )
                                await msg.ack()
                                continue
                            if not job.get("action"):
                                log_message(
                                    f"[RabbitMQ] [{dev_label}] thiếu action/type -> drop payload={payload}",
                                    logging.WARNING,
                                )
                                if job.get("command_id"):
                                    pub_ok, pub_err = await publish_rabbitmq_result(
                                        {
                                            "command_id": job.get("command_id"),
                                            "device_id": machine_code,
                                            "type": "",
                                            "ok": False,
                                            "status": "FAILED",
                                            "reason": "missing action/type",
                                        }
                                    )
                                    if not pub_ok:
                                        log_message(
                                            f"[RabbitMQ] [{dev_label}] publish missing-action result failed: {pub_err}",
                                            logging.WARNING,
                                        )
                                await msg.ack()
                                continue
                            
                            from fb_task_patched import get_urgent_objects
                            event, queue = get_urgent_objects(machine_code)
                            cmd_id = job.get("command_id") or ""
                            _command_doc, rabbitmq_can_run, claim_reason = await pymongo_management.claim_command_for_rabbitmq(
                                cmd_id,
                                machine_code=machine_code,
                            )
                            if not rabbitmq_can_run:
                                log_message(
                                    f"[RabbitMQ] [{dev_label}] command_id={cmd_id} đã được nguồn khác claim "
                                    f"(status={claim_reason}) -> bỏ qua RabbitMQ để tránh chạy trùng",
                                    logging.WARNING,
                                )
                                await msg.ack()
                                continue

                            inbox_doc, inserted = await pymongo_management.register_rabbitmq_inbox_job(
                                machine_code,
                                job,
                            )
                            inbox_status = str((inbox_doc or {}).get("status") or "").upper()
                            if inbox_status == "FINISHED":
                                log_message(
                                    f"[RabbitMQ] [{dev_label}] command_id={cmd_id} already finished -> skip rerun",
                                    logging.WARNING,
                                )
                                await msg.ack()
                                continue
                            if not inserted and inbox_status in {"QUEUED", "RUNNING"}:
                                log_message(
                                    f"[RabbitMQ] [{dev_label}] duplicate command_id={cmd_id} status={inbox_status} -> skip duplicate enqueue",
                                    logging.WARNING,
                                )
                                await msg.ack()
                                continue

                            await queue.put(job)
                            await pymongo_management.mark_rabbitmq_inbox_queued(cmd_id)
                            event.set()

                            log_message(
                                f"[RabbitMQ] [{dev_label}] queued command_id={job.get('command_id')} action={job.get('action')}",
                                logging.INFO,
                            )

                            if PUBLISH_QUEUED_RESULT:
                                try:
                                    await _publish_result(
                                        ch,
                                        {
                                            "command_id": job.get("command_id"),
                                            "device_id": machine_code,
                                            "type": _rabbit_result_type(job.get("action") or ""),
                                            "ok": True,
                                            "status": "QUEUED",
                                            "reason": "accepted_by_device_queue",
                                        },
                                    )
                                except Exception as pub_err:
                                    log_message(
                                        f"[RabbitMQ] [{dev_label}] publish QUEUED result failed: {pub_err}",
                                        logging.WARNING,
                                    )

                            await msg.ack()

                        except asyncio.CancelledError:
                            raise
                        except Exception as msg_err:
                            log_message(
                                f"[RabbitMQ] [{dev_label}] xử lý message lỗi: {type(msg_err).__name__}: {msg_err}",
                                logging.WARNING,
                            )
                            try:
                                await msg.nack(requeue=True)
                            except Exception:
                                pass

        except asyncio.CancelledError:
            raise
        except Exception as e:
            log_message(
                f"[RabbitMQ] [{dev_label}] consumer error: {type(e).__name__}: {e} (retry 3s)",
                logging.WARNING,
            )
            await asyncio.sleep(3)

