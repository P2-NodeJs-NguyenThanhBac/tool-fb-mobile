# device_runtime_state.py
from __future__ import annotations

import json
import time
import os
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Optional

_STATE_DIR = Path(__file__).resolve().parent / "runtime_state"
_STATE_DIR.mkdir(parents=True, exist_ok=True)

_LOCK = Lock()


def _path(device_id: str) -> Path:
    safe = str(device_id).replace("/", "_").replace("\\", "_").strip()
    return _STATE_DIR / f"device_{safe}.json"


def load_state(device_id: str) -> Dict[str, Any]:
    p = _path(device_id)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(device_id: str, state: Dict[str, Any]) -> None:
    p = _path(device_id)
    tmp = p.with_suffix(".tmp")
    # atomic write
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def set_online_account(
    device_id: str,
    *,
    account: str,
    username: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Ghi nhận device đang online với account nào.
    account: thường là số điện thoại / user_id mà tool dùng để switch.
    username: tên hiển thị FB (nếu bạn có).
    """
    with _LOCK:
        st = load_state(device_id)
        st["device_id"] = device_id
        st["status"] = "Online"
        st["current_account"] = str(account).strip()
        if username:
            st["current_username"] = str(username).strip()
        st["updated_at"] = int(time.time())
        save_state(device_id, st)
        return st


def set_offline(device_id: str) -> Dict[str, Any]:
    with _LOCK:
        st = load_state(device_id)
        st["device_id"] = device_id
        st["status"] = "Offline"
        st["updated_at"] = int(time.time())
        save_state(device_id, st)
        return st


def get_current_account(device_id: str) -> Optional[str]:
    st = load_state(device_id)
    acc = st.get("current_account")
    return str(acc).strip() if acc else None


def set_messenger_priority_hold(
    device_id: str,
    *,
    account: str,
    hold_seconds: Optional[int] = None,
) -> Dict[str, Any]:
    account = str(account or "").strip()
    if not device_id or not account:
        return {}
    if hold_seconds is None:
        try:
            hold_seconds = int(os.getenv("FB_1_1_HOLD_AFTER_SEND_SECONDS", "900"))
        except Exception:
            hold_seconds = 900
    now = int(time.time())
    expires_at = 0 if int(hold_seconds or 0) <= 0 else now + int(hold_seconds or 0)
    with _LOCK:
        st = load_state(device_id)
        st["device_id"] = device_id
        st["messenger_1_1_priority_hold"] = {
            "active": True,
            "account": account,
            "last_activity": now,
            "expires_at": expires_at,
        }
        st["updated_at"] = now
        save_state(device_id, st)
        return st


def is_messenger_priority_hold_active(device_id: str, account: Optional[str] = None) -> bool:
    if not device_id:
        return False
    st = load_state(device_id)
    hold = st.get("messenger_1_1_priority_hold") or {}
    if not isinstance(hold, dict) or not hold.get("active"):
        return False
    hold_account = str(hold.get("account") or "").strip()
    if account and hold_account and hold_account != str(account or "").strip():
        return False
    expires_at = int(hold.get("expires_at") or 0)
    if expires_at and int(time.time()) > expires_at:
        clear_messenger_priority_hold(device_id)
        return False
    return True


def clear_messenger_priority_hold(device_id: str) -> Dict[str, Any]:
    if not device_id:
        return {}
    with _LOCK:
        st = load_state(device_id)
        hold = st.get("messenger_1_1_priority_hold") or {}
        if isinstance(hold, dict):
            hold["active"] = False
            hold["cleared_at"] = int(time.time())
            st["messenger_1_1_priority_hold"] = hold
        st["updated_at"] = int(time.time())
        save_state(device_id, st)
        return st


def is_online(device_id: str, max_age_sec: int = 300) -> bool:
    """
    Online "gần đây" nếu updated_at trong max_age_sec.
    (phòng trường hợp tool chết mà file còn Online)
    """
    st = load_state(device_id)
    if st.get("status") != "Online":
        return False
    ts = st.get("updated_at") or 0
    try:
        ts = int(ts)
    except Exception:
        return False
    return (int(time.time()) - ts) <= max_age_sec

def _atomic_write_json(path: str, data: Dict[str, Any]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

def update_current_account_only(
    state_path: str,
    *,
    new_account: str,
    new_username: Optional[str] = None
) -> Dict[str, Any]:
    """
    Update current_account/current_username/updated_at.
    Giữ nguyên device_id và status như trong file.
    """
    new_account = (new_account or "").strip()
    if not new_account:
        raise ValueError("new_account is empty")

    try:
        with open(state_path, "r", encoding="utf-8") as f:
            st = json.load(f)
    except FileNotFoundError:
        # Nếu file chưa có, tạo mới tối thiểu theo schema bạn đang dùng
        st = {
            "device_id": "",
            "status": "Online",
            "current_account": "",
            "current_username": "",
            "updated_at": 0,
        }

    # ✅ giữ nguyên st["device_id"] và st["status"] (không đụng vào)
    st["current_account"] = new_account
    if new_username is not None:
        st["current_username"] = str(new_username).strip()
    st["updated_at"] = int(time.time())

    _atomic_write_json(state_path, st)
    return st
