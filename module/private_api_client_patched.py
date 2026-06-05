import os
import logging
import asyncio
import requests
from util import log_message

# Default giữ như file cũ (port 5000). Có thể override bằng env PRIVATE_API_BASE.
BASE = "http://192.168.1.35:8000"

def _base() -> str:
    return (os.environ.get("PRIVATE_API_BASE", BASE) or BASE).rstrip("/")

async def _get(url: str, *, params: dict | None = None, timeout: int = 10):
    def _do():
        return requests.get(url, params=params or {}, timeout=timeout)
    return await asyncio.to_thread(_do)

async def _post(url: str, *, json_body: dict | None = None, timeout: int = 10):
    def _do():
        return requests.post(url, json=json_body or {}, timeout=timeout)
    return await asyncio.to_thread(_do)

async def fetch_next_job(device_id: str | None):
    """
    Device tool gọi để lấy job pending phù hợp với device_id.
    Server: GET /api/next?device_id=...
    """
    url = f"{_base()}/api/next"
    params = {"device_id": device_id} if device_id else {}
    try:
        r = await _get(url, params=params, timeout=10)
        if r.status_code == 204:
            return None
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log_message(f"[private_api_client] fetch_next_job error: {type(e).__name__}: {e}", logging.WARNING)
        return None

async def mark_done(job_id: str, ok: bool, reason: str | None = None, result: dict | None = None, link: str | None = None):
    """
    Báo kết quả job về server: POST /api/done
    Mở rộng: có thể gửi result/link (server sẽ lưu nếu hỗ trợ).
    """
    url = f"{_base()}/api/done"
    try:
        payload = {
            "id": job_id,
            "status": "done" if ok else "failed",
            "reason": reason,
            "result": result,
            "link": link,
        }
        log_message(f"[private_api_client] POST {url} id={job_id} ok={ok}", logging.DEBUG)
        await _post(url, json_body=payload, timeout=10)
    except Exception as e:
        log_message(f"[private_api_client] mark_done error: {type(e).__name__}: {e}", logging.WARNING)

async def enqueue_command(cmd: dict) -> dict | None:
    """
    (Tuỳ chọn) Cho tool/CLI enqueue command giống CRM lên server: POST /api/command
    cmd ví dụ:
    {
      "type": "post_to_group",
      "crm_id": "22938184",
      "user_id": "0984485936",
      "params": {...}
    }
    """
    url = f"{_base()}/api/command"
    try:
        log_message(f"[private_api_client] POST {url} enqueue_command", logging.DEBUG)
        r = await _post(url, json_body=cmd, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log_message(f"[private_api_client] enqueue_command error: {type(e).__name__}: {e}", logging.WARNING)
        return None
