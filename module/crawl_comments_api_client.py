import asyncio
import logging
import os
import requests
from util import log_message

DEFAULT_CRAWL_API = os.getenv(
    "COMMENT_CRAWL_API",
    "http://123.24.206.25:8012/crawl-comments"
)

async def call_crawl_comments_api(group_link: str, link: str, timeout: int = 180) -> dict:
    url = DEFAULT_CRAWL_API.rstrip("/")

    payload = {
        "group_link": group_link,
        "link": link,
    }

    def _do_request():
        return requests.post(url, json=payload, timeout=timeout)

    try:
        resp = await asyncio.to_thread(_do_request)
        status_code = resp.status_code

        try:
            data = resp.json()
        except Exception:
            data = {
                "raw_text": resp.text
            }

        # Chuẩn hoá kết quả
        ok = bool(
            status_code == 200 and (
                data.get("ok") is True
                or data.get("success") is True
                or "comments" in data
                or "data" in data
            )
        )

        reason = ""
        if not ok:
            reason = (
                data.get("message")
                or data.get("reason")
                or f"crawl api http {status_code}"
            )

        return {
            "ok": ok,
            "status_code": status_code,
            "reason": reason,
            "payload_sent": payload,
            "data": data,
        }

    except Exception as e:
        log_message(
            f"[crawl_comments_api_client] call api failed: {type(e).__name__}: {e}",
            logging.ERROR,
        )
        return {
            "ok": False,
            "status_code": 0,
            "reason": f"{type(e).__name__}: {e}",
            "payload_sent": payload,
            "data": None,
        }