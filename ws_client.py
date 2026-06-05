# ws_client.py
# --------------------------------------------
# Lightweight WebSocket client for CRM control
# - Connects to CRM WS
# - Registers device
# - Receives "command" -> put into URGENT_QUEUE and set URGENT_EVENT
# - Exposes notify_ack(job_id, status, reason=None) for sending ACKs back to CRM
# - Resilient reconnect with exponential backoff
# --------------------------------------------

from __future__ import annotations
import asyncio
import json
import logging
import aiohttp, os
import random
import time
import contextlib
from typing import Any, Dict, Optional
import pymongo_management
from pymongo_management import get_commands
from urgent_queue import enqueue_urgent_task

import websockets
from websockets.exceptions import ConnectionClosed

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
CMD_EVENTS = set(os.getenv("CRM_CMD_EVENTS", "command,cmd,crm_command").split(","))
ACTION_EVENTS = {"post_to_wall", "post_to_group", "join_group", "switch_account"}

# Public API for other modules (e.g., fb_task.py) to push ACKs
_ACK_QUEUE: "asyncio.Queue[Dict[str, Any]]" = asyncio.Queue()

# thêm hàm helper:
def _normalize_job(raw):
    if not isinstance(raw, dict):
        return {}

    # Nếu payload thực nằm trong raw["data"], trộn nó vào:
    payload = raw.get("data")
    if isinstance(payload, dict):
        merged = {**payload, **{k: v for k, v in raw.items() if k != "data"}}
        raw = merged

    # Lấy action
    action = raw.get("action") or raw.get("type")
    params = raw.get("params") or {}

    # Nếu params rỗng nhưng raw có content/files/... thì coi raw như params
    if not params:
        params = {}
        for k in ("content", "files", "group_link", "auto_join_if_needed", "join_timeout_sec"):
            if k in raw:
                params[k] = raw[k]

    # ưu tiên _id nếu có
    jid = raw.get("id") or raw.get("_id") or f"crm-{action}-{int(time.time()*1000)}"

    job = {
        "id": str(jid),
        "action": action,
        "params": params,
        "device_ids": raw.get("device_ids") or raw.get("device_id"),
        "user_id": raw.get("user_id"),
        "created_at": float(raw.get("created_at") or time.time()),
        "ttl_sec": int(raw.get("ttl_sec") or 300),
    }

    # Bổ sung các field phẳng cho tool dùng
    job["content"] = raw.get("content") or params.get("content")
    job["files"] = raw.get("files") or params.get("files")
    job["group_link"] = raw.get("group_link") or params.get("group_link")
    job["auto_join_if_needed"] = raw.get("auto_join_if_needed") or params.get("auto_join_if_needed")
    job["join_timeout_sec"] = raw.get("join_timeout_sec") or params.get("join_timeout_sec") or 15
    job["user_name"] = raw.get("user_name") or params.get("user_name")


    # Nếu server cũ lưu riêng device_id -> chuyển thành mảng device_ids
    if not job["device_ids"] and raw.get("device_id"):
        job["device_ids"] = [raw["device_id"]]

    return job



def notify_ack(job_id: str, status: str, reason: Optional[str] = None) -> None:
    """
    Non-blocking enqueue of an ACK to be sent to CRM via WS as soon as connected.
    status: received | started | finished | failed | expired
    """
    payload = {
        "type": "ack",
        "job_id": job_id,
        "status": status,
        "reason": reason or "",
        "ts": time.time(),
    }
    try:
        _ACK_QUEUE.put_nowait(payload)
    except Exception as e:
        logger.exception("ACK enqueue failed: %s", e)


class WSClient:
    """
    WebSocket client that:
      - connects to CRM
      - registers this device
      - forwards received 'command' messages to URGENT_QUEUE and sets URGENT_EVENT
      - flushes ACKs from _ACK_QUEUE to CRM

    Integration points you must pass in:
      urgent_queue: asyncio.Queue to receive jobs (your existing URGENT_QUEUE)
      urgent_event: asyncio.Event to wake up urgent worker (your existing URGENT_EVENT)
      driver_serial: ADB/device serial (string)
      crm_ws_url: WS endpoint from CRM, e.g. wss://socket.hungha365.com:4000/ws
      jwt_token: JWT or shared secret string for auth (server-side must verify)

    Optional:
      capabilities: list of supported actions
      device_filter: callable(job_dict) -> bool  to drop commands not meant for this device
    """
    async def _pull_commands(self):
        base = os.getenv("CRM_HTTP_BASE", "https://socket.hungha365.com:4000").rstrip("/")
        if not base:
            logger.warning("[WS] CRM_HTTP_BASE not set, skip pulling")
            return
        urls = [f"{base}/api/common_api/next", f"{base}/api/next"]
        params = {"device_id": self.device_id, "user_id": self.user_id}
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as sess:
            for url in urls:
                try:
                    async with sess.get(url, params=params) as resp:
                        if resp.status != 200:
                            continue
                        data = await resp.json(content_type=None)
                        items = data if isinstance(data, list) else data.get("results") or data.get("data") or []
                        for raw in items:
                            job = _normalize_job(raw)
                            if not self._job_targets_me(job):
                                continue
                            await self._dispatch_job(job)
                            logger.info(
                                "[WS] dispatched job id=%s action=%s (pulled)",
                                job.get("id"),
                                job.get("action"),
                            )
                        return
                except Exception as e:
                    logger.debug("[WS] pull error %s: %s", url, e)
            logger.info("[WS] no jobs pulled")
            
    def __init__(
        self,
        urgent_queue: "asyncio.Queue[Dict[str, Any]]",
        urgent_event: asyncio.Event,
        driver_serial: str,
        user_id: Optional[str] = None,
        user_ids: Optional[list[str]] = None,  # 🔥 thêm tham số mới
        crm_ws_url: Optional[str] = None,
        jwt_token: Optional[str] = None,
        capabilities: Optional[list[str]] = None,
        device_filter: Optional[callable] = None,
    ) -> None:
        self.urgent_queue = urgent_queue
        self.urgent_event = urgent_event
        self.device_id = driver_serial

        raw_env_uid = os.getenv("CRM_USER_ID", "")

        # 🔧 TRÁNH BUG: nếu lỡ truyền list vào user_id thì gộp vào user_ids
        if isinstance(user_id, list):
            user_ids = (user_ids or []) + user_id
            user_id = None

        # single uid (nếu có)
        effective_single_uid: str = ""
        if isinstance(user_id, str) and user_id.strip():
            effective_single_uid = user_id.strip()
        elif isinstance(raw_env_uid, str) and raw_env_uid.strip():
            effective_single_uid = raw_env_uid.strip()

        # Danh sách user mà WSClient sẽ xử lý
        if user_ids:
            # ép hết sang string
            self.user_ids = [str(u).strip() for u in user_ids if str(u).strip()]
        else:
            self.user_ids = [effective_single_uid] if effective_single_uid else []

        # Giữ lại 1 user mặc định cho log / fallback
        self.user_id = self.user_ids[0] if self.user_ids else None

        logger.info(
            "[WS] effective user_ids=%r (default=%r, env=%r)",
            self.user_ids,
            self.user_id,
            raw_env_uid,
        )

        self.crm_ws_url = (crm_ws_url or os.getenv("CRM_WS") 
                           or "wss://socket.hungha365.com:4000/ws")
        self.jwt_token = jwt_token or os.getenv("CRM_JWT", "")
        self.capabilities = capabilities or ["post_wall", "post_group", "chat", "switch_account"]
        self.device_filter = device_filter

        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._connected_event = asyncio.Event()
        self._stop = asyncio.Event()
        self._forward_acks_task: Optional[asyncio.Task] = None

        # Deduplicate job ids (idempotency)
        self._seen_jobs: set[str] = set()


    # ---------- public control ----------
    async def stop(self) -> None:
        self._stop.set()
        if self._forward_acks_task:
            self._forward_acks_task.cancel()
            with contextlib.suppress(Exception):
                await self._forward_acks_task
        if self._ws and not self._ws.closed:
            await self._ws.close()
        self._connected_event.clear()

    # ---------- core loop ----------
    async def run(self) -> None:
        """
        Main lifecycle:
          - try connect with backoff
          - on connect: register, start ack forwarder
          - receive messages forever; reconnect on drop
        """
        logger.info("[WS] run() started")
        backoff = 1.0
        while not self._stop.is_set():
            try:
                logger.info("[WS] connecting to %s", self.crm_ws_url)
                async with websockets.connect(
                    self.crm_ws_url,
                    ping_interval=20,
                    ping_timeout=20,
                    max_queue=None,
                ) as ws:
                    self._ws = ws
                    self._connected_event.set()
                    backoff = 1.0  # reset backoff on successful connect

                    # start ACK forwarder
                    self._forward_acks_task = asyncio.create_task(self._forward_acks_loop(), name="ws_forward_acks")

                    # register this device
                    await self._send({
                        "type": "register",
                        "device_id": self.device_id,
                        "user_id": self.user_id,
                        "capabilities": self.capabilities,
                        "auth": self.jwt_token,
                    })
                    logger.info("[WS] registered device_id=%s", self.device_id)

                    # receive loop
                    await self._recv_loop()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("[WS] connection error: %s", e)

            # teardown on disconnect
            self._connected_event.clear()
            self._ws = None

            # backoff before retry
            sleep_sec = min(30.0, backoff + random.uniform(0, 0.5))
            logger.info("[WS] reconnecting in %.1fs...", sleep_sec)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=sleep_sec)
            except asyncio.TimeoutError:
                pass
            backoff = min(30.0, backoff * 2.0)

    # ---------- internals ----------
    async def _send(self, obj: Dict[str, Any]) -> None:
        if not self._ws:
            raise RuntimeError("WS not connected")
        await self._ws.send(json.dumps(obj, ensure_ascii=False))
    async def _dispatch_job(self, job: Dict[str, Any]) -> None:
        """
        Route job:
          - post_wall / post_group: vào file urgent_queue (delay)
          - join_group + các action khác: vào urgent_queue của device (chạy ngay)
        """
        action = (job.get("action") or "").strip().lower()

        # chuẩn hoá giống fb_task
        if action in ("post_to_wall", "post_wall"):
            action = "post_wall"
        elif action in ("post_to_group", "post_group"):
            action = "post_group"
        elif action in ("join_group", "join_fb_group"):
            action = "join_group"
        job["action"] = action

        # Các action cần delay: post_wall, post_group
        if action in ("post_wall", "post_group"):
            enqueue_urgent_task(job)
            logging.getLogger(__name__).info(
                "[WS] enqueued delayed job to file id=%s action=%s",
                job.get("id"), action
            )
        else:
            # join_group và các action khác -> urgent ngay lập tức
            await self.urgent_queue.put(job)
            self.urgent_event.set()
            logging.getLogger(__name__).info(
                "[WS] queued immediate job id=%s action=%s",
                job.get("id"), action
            )
        # await self.urgent_queue.put(job)
        # self.urgent_event.set()
        # logging.getLogger(__name__).info(
        #         "[WS] queued immediate job id=%s action=%s",
        #         job.get("id"), action
        # )
    async def _recv_loop(self) -> None:
        """
        Handle inbound WS messages:
          - type == "command": dispatch to urgent queue
          - ignore unknown types
        """
        assert self._ws is not None
        ws = self._ws
        while True:
            try:
                msg = await ws.recv()
            except ConnectionClosed as e:
                logger.warning("[WS] closed: %s", e)
                break

            try:
                obj = json.loads(msg)
            except Exception:
                logger.debug("[WS] non-JSON message ignored")
                continue

            mtype = (obj.get("type") or "").strip()
            if mtype == 'new_command_notification':
                note = obj.get("data") or {}
                note_user_id = str(note.get("user_id") or "").strip()
                
                # 🔥 Xác định user_id nào mình sẽ xử lý
                chosen_uid: Optional[str] = None
                if note_user_id:
                    # Nếu WSClient được cấu hình nhiều user_ids -> xử lý nếu trong danh sách
                    if self.user_ids and note_user_id in map(str, self.user_ids):
                        chosen_uid = note_user_id
                    else:
                        logger.info(
                "[WS] skip notification not for me: user_id=%s mine=%s obj=%s",
                note_user_id, self.user_ids, obj
            )
                        continue
                else:
                    # Không có user_id trong notification -> fallback dùng user mặc định (nếu có)
                    chosen_uid = str(self.user_id) if self.user_id else None

                if not chosen_uid:
                    logger.info("[WS] skip notification (no suitable user_id) obj=%s", obj)
                    continue
                logger.info(
        "[WS] signal: new_command_notification -> executing legacy commands (MongoDB style) for user_id=%s",
        chosen_uid,
    )
                try:
                    cmds = None
                    # retry nhanh: 0.3s, 0.6s, 1.0s, 1.6s (tổng ~3.5s)
                    delays = [0.3, 0.6, 1.0, 1.6, 2.0, 2.6]

                    for d in [0.0] + delays:
                        if d:
                            await asyncio.sleep(d)
                        cmds = await get_commands(chosen_uid)
                        if cmds:
                            break
                    if not cmds:
                        logger.info("[WS] no legacy commands found for user_id=%s (after retries)", chosen_uid)
                        continue
                    for raw in cmds:
                        base = raw.get("data") or raw
                        job = _normalize_job(base)
                        # điền mặc định còn thiếu
                        if not job.get("user_id"):
                            job["user_id"] = chosen_uid
                        if not job.get("action"):
                            logger.warning("[WS] legacy job missing action/type: %s", base)
                            continue
                        
                        # route filter
                        if not self._job_targets_me(job):
                            continue
                        
                        # TTL
                        if time.time() - float(job["created_at"]) > int(job["ttl_sec"]):
                            notify_ack(job_id=str(job["id"]), status="expired", reason="ttl exceeded before pickup")
                            continue
                        
                        # idempotency
                        jid = str(job["id"])
                        if jid in self._seen_jobs:
                            continue
                        self._seen_jobs.add(jid)
                        # ACK + enqueue
                        notify_ack(job_id=jid, status="received")
                        await self._dispatch_job(job)
                        logger.info(
                            "[WS] dispatched job id=%s action=%s (legacy/mongo)",
                            jid,
                            job["action"],
                        )
                except Exception as e:
                    logger.error("[WS] legacy execution error: %s", e)
                    continue
            elif mtype in CMD_EVENTS:
                job = obj.get("data") or {}
            # nếu CRM đẩy thẳng action (post_to_wall / ...) -> tự bọc thành job
            elif mtype in ACTION_EVENTS and ("params" in obj):
                job = {
        "id": obj.get("id") or f"crm-{mtype}-{int(time.time()*1000)}",
        "action": mtype,
        "params": obj.get("params") or {},
        # route theo device trước, không có thì theo user
        "device_ids": obj.get("device_ids"),
        "user_id": obj.get("user_id"),
        "user_name": obj.get("user_name"),
        "created_at": obj.get("created_at"),
        "ttl_sec": obj.get("ttl_sec", 300),
        }
            else:
                logger.info("[WS] ignore msg type=%s obj=%s", mtype, obj)
                continue

            # Optional: filter commands to this device_id (defensive)
            if self.device_filter and not self.device_filter(job):
                continue
            if not self._job_targets_me(job):
                continue

            # TTL check
            created_at = float(job.get("created_at", time.time()))
            ttl = int(job.get("ttl_sec", 300))
            if time.time() - created_at > ttl:
                # expired before execution; auto-ack and skip
                notify_ack(job_id=str(job.get("id")), status="expired", reason="ttl exceeded before pickup")
                continue

            # idempotency
            jid = str(job.get("id"))
            if jid in self._seen_jobs:
                # already queued/processed
                continue
            self._seen_jobs.add(jid)

            # ACK received immediately (optional but recommended)
            notify_ack(job_id=jid, status="received")

            # Dispatch to urgent queue
            try:
                await self._dispatch_job(job)
                logger.info(
                    "[WS] dispatched job id=%s action=%s",
                    jid,
                    job.get("action"),
                )
            except Exception as e:
                logger.exception("[WS] dispatch error: %s", e)
                notify_ack(job_id=jid, status="failed", reason=f"enqueue error: {e}")

    async def _forward_acks_loop(self) -> None:
        """
        Drain _ACK_QUEUE and send over WS as soon as connected.
        Survives reconnects: waits on _connected_event if disconnected.
        """
        while True:
            payload = await _ACK_QUEUE.get()  # wait for new ack
            # Wait until connected
            await self._connected_event.wait()
            try:
                await self._send(payload)
            except Exception as e:
                # If send failed (e.g., race with disconnect), requeue once
                logger.warning("[WS] send ack failed, will retry once: %s", e)
                try:
                    _ACK_QUEUE.put_nowait(payload)
                except Exception:
                    logger.exception("[WS] requeue ack failed, dropping!")

    def _job_targets_me(self, job: Dict[str, Any]) -> bool:
        device_ids = job.get("device_ids")
        user_id = job.get("user_id")

        matched = False

        # 1. Ưu tiên route theo device_id
        if device_ids:
            try:
                matched = self.device_id in device_ids
            except Exception:
                matched = False

        # 2. Nếu chưa match theo device, route theo user_id (trong danh sách user_ids)
        if not matched and user_id and self.user_ids:
            try:
                matched = str(user_id) in [str(u) for u in self.user_ids]
            except Exception:
                matched = False

        # 3. Nếu cả device_ids và user_id đều không có -> coi như broadcast
        if not matched and not device_ids and not user_id:
            matched = True

        if not matched:
            logger.info(
                "[WS] job not for me: dev=%s my_users=%s job=%s",
                self.device_id, self.user_ids, job,
            )
        return matched


# ---------- helper to wire into your existing app ----------
# Example usage in your main boot file:
#
# from ws_client import WSClient, notify_ack
#
# async def main():
#     driver = ...  # your adb driver with .serial
#     # URGENT_QUEUE and URGENT_EVENT should already exist in your codebase
#     wscli = WSClient(
#         urgent_queue=URGENT_QUEUE,
#         urgent_event=URGENT_EVENT,
#         driver_serial=driver.serial,
#         crm_ws_url=os.getenv("CRM_WS", "wss://socket.hungha365.com:4000/ws"),
#         jwt_token=os.getenv("CRM_JWT", ""),
#         capabilities=["post_wall","post_group","chat"],
#     )
#     ws_task = asyncio.create_task(wscli.run(), name="crm_ws")
#     fb_task = asyncio.create_task(run_on_device_original(driver))
#     await asyncio.gather(ws_task, fb_task)
#
# In your urgent handler (handle_urgent_if_any), call:
#   notify_ack(job_id, "started")
#   ... do work ...
#   notify_ack(job_id, "finished")  or  notify_ack(job_id, "failed", reason="...")
#
# Make sure all UI actions (swap/join/post/chat) are inside DEVICE_ACTION_LOCK,
# and you return to Home before releasing the lock.
