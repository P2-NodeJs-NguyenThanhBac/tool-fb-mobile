import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from util import DEVICE_LIST_NAME, log_message
from .messenger_notifications import crawl_messenger_notifications


DEFAULT_DATA_DIR = Path("data") / "mobile_message_farming"


def _device_label(driver) -> str:
    serial = getattr(driver, "serial", "") or "unknown"
    return DEVICE_LIST_NAME.get(serial, serial)


def _safe_str(value: Any) -> str:
    return ("" if value is None else str(value)).strip()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _data_dir() -> Path:
    configured = _safe_str(os.getenv("FB_MOBILE_FARMING_DATA_DIR"))
    base = Path(configured) if configured else DEFAULT_DATA_DIR
    base.mkdir(parents=True, exist_ok=True)
    return base


def _safe_filename(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in _safe_str(value))
    return safe or "unknown"


def _append_jsonl(path: Path, item: Dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")))
        f.write("\n")


def _write_latest(path: Path, item: Dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def persist_mobile_message_farming_event(event: Dict[str, Any]) -> Dict[str, str]:
    device_id = _safe_filename(event.get("device_id") or "unknown")
    base = _data_dir()
    event_path = base / f"{device_id}.jsonl"
    latest_path = base / f"{device_id}.latest.json"

    _append_jsonl(event_path, event)
    _write_latest(latest_path, event)

    return {
        "event_path": str(event_path),
        "latest_path": str(latest_path),
    }


async def crawl_mobile_fb_1_1_farming(
    driver,
    command_id: Optional[str] = None,
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Sidecar crawl for FB account farming only.

    This function must not send CRM 1-1 messages, update CRM conversation/message
    status, or publish to the FB message send/result queues.
    """
    params = params or {}
    account = _safe_str(
        params.get("account")
        or params.get("accountKey")
        or params.get("facebookAccountId")
        or params.get("user_id")
    )

    crawl_result = await crawl_messenger_notifications(
        driver,
        command_id=command_id,
        params=params,
    )

    event = {
        "schema_version": 1,
        "purpose": "fb_mobile_message_farming",
        "sidecar_only": True,
        "affects_crm_message_flow": False,
        "command_id": command_id or "",
        "device_id": getattr(driver, "serial", "") or "",
        "account": account,
        "created_at": _utc_now_iso(),
        "result": crawl_result,
    }

    try:
        paths = await asyncio.to_thread(persist_mobile_message_farming_event, event)
        event["storage"] = paths
    except Exception as e:
        crawl_result["ok"] = False
        crawl_result["reason"] = f"persist mobile farming event failed: {type(e).__name__}: {e}"
        log_message(
            f"[{_device_label(driver)}] mobile message farming persist failed: {type(e).__name__}: {e}",
            logging.ERROR,
        )
        event["storage"] = {}

    log_message(
        f"[{_device_label(driver)}] Mobile FB farming crawl done | ok={bool(crawl_result.get('ok'))} | sidecar_only=True",
        logging.INFO,
    )

    return event
