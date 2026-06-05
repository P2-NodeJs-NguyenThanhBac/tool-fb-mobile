from unittest import result

import fb_task_patched as fb_runtime
from fb_task_patched import *
# from sending_message_and_adding_friend import DeviceHandler, NUM_PHONE
from Zalo_Phase import DeviceHandler
from PIL import Image,ImageChops, ImageStat
import uiautomator2 as u2
import asyncio
import logging
import time
import json
import os
import hashlib
import re
import subprocess
from typing import Optional, Callable
from datetime import datetime, time as dt_time, timedelta, timezone
from module.android_automation_common import (
    collapse_statusbar_driver_async,
    disable_auto_rotation_driver,
    notification_shade_has_focus as common_notification_shade_has_focus,
    resolve_launch_activity_driver,
    shell_response_text,
)

import sys
sys.stdout.reconfigure(encoding='utf-8')
import threading

from module import *
from util import *
import main_lib
import pymongo_management
# from rabbitmq_device_worker_merged import start_device_rabbit_consumer
from rabbitmq_device_worker_merged import (
    start_device_rabbit_consumer,
    start_rabbitmq_recovery_loop,
    RABBIT_URL,
)
from rabbitmq_result_client import start_rabbitmq_result_retry_loop
from util.device_runtime_state import get_current_account, is_online as is_runtime_online
from urgent_queue import get_next_task_if_due

# ======================= CẤU HÌNH =======================
DEVICE_LIST = []   # Danh sách thiết bị hiện tại
active_tasks = {}  # Lưu task của từng thiết bị
screen_standing_event = {} # Lưu sự kiện màn hình đứng của từng thiết bị
restart_event = {} # Lưu sự kiện restart của từng thiết bị
HOME_PACKAGES = {
    "com.android.launcher",
    "com.google.android.apps.nexuslauncher",
    "com.sec.android.app.launcher",
    "com.miui.home",
    "com.oppo.launcher",
    "com.bbk.launcher2",
    "com.vivo.launcher",
    "com.gogo.launcher",
    "com.huawei.android.launcher",
    "com.teslacoilsw.launcher",
    "com.perfect.asmr.tidy.free.aspgg.game.launcher"
}
ZALO_PKG = "com.zing.zalo"
FACEBOOK_PKG = "com.facebook.katana"
TERMUX_PKG = "com.termux"
MESSENGER_PKG = "com.facebook.orca"
PLAY_STORE_PKG = "com.android.vending"
PERMISSION_PACKAGES = {
    "com.google.android.permissioncontroller",
    "com.android.permissioncontroller",
    "com.android.packageinstaller"
}
TARGET_PACKAGES = {ZALO_PKG, FACEBOOK_PKG, MESSENGER_PKG} | PERMISSION_PACKAGES
FACEBOOK_APP_LINK_DOMAINS = (
    "facebook.com",
    "www.facebook.com",
    "m.facebook.com",
    "web.facebook.com",
    "mbasic.facebook.com",
    "mobile.facebook.com",
    "touch.facebook.com",
    "free.facebook.com",
)
TERMUX_GRACE_SECONDS = 180.0
OTHER_APP_GRACE_SECONDS = 3


# Cache phục vụ chế độ "one-time" nếu cần giữ tương thích ở chỗ khác
_VPN_CHECKED = set()
_STATUS_FILE_CHECK = set()
_FACEBOOK_LINK_HANDLING_CHECKED = set()

HEARTBEAT_SECONDS = 15.0
DEVICE_OFFLINE_RETRY = 3          # driver fail 3 lần liên tiếp mới offline
ADB_REMOVE_RETRY = 3              # adb scan mất 3 vòng liên tiếp mới remove
_ADB_MISS_COUNT = {}
ROTATION_GUARD_INTERVAL = 20.0
STATUSBAR_GUARD_INTERVAL = 2.0
STATUSBAR_GUARD_ERROR_LOG_INTERVAL = 30.0
PHONE_SYSTEM_DIALOG_PACKAGE_NAMES = (
    "com.android.phone",
    "com.android.stk",
    "com.android.stk2",
)
PHONE_SYSTEM_DIALOG_PACKAGES = tuple(
    f'package="{package_name}"' for package_name in PHONE_SYSTEM_DIALOG_PACKAGE_NAMES
)
PHONE_SYSTEM_DIALOG_REQUIRED_MARKERS = (
    'resource-id="android:id/message"',
    'resource-id="android:id/parentpanel"',
)
PHONE_SYSTEM_DIALOG_DISMISS_MARKERS = (
    'resource-id="android:id/button2"',
    'resource-id="android:id/button1"',
    'resource-id="com.android.phone:id/input_field"',
    "viettel",
    "sim",
    "nhap so",
    "cuoc goi",
)

import aio_pika

CONTROL_QUEUE = "q.tool.control"
VN_TZ = timezone(timedelta(hours=7))
PASSIVE_SCHEDULE_CHECK_SECONDS = 30.0
PASSIVE_WAIT_ONLY_WINDOWS = (
    (dt_time(10, 30), dt_time(14, 0)),
    (dt_time(18, 30), dt_time(23, 0)),
)
APP_START_ACTIVITIES = {
    "com.cloudflare.onedotonedotonedotone": "com.cloudflare.app.presentation.main.SplashActivity",
    TERMUX_PKG: ".app.TermuxActivity",
    FACEBOOK_PKG: ".LoginActivity",
    ZALO_PKG: ".ui.SplashActivity",
}


def _shell_response_text(result) -> str:
    if hasattr(result, "output"):
        return (result.output or "").strip()
    if hasattr(result, "text"):
        return (result.text or "").strip()
    if hasattr(result, "stdout"):
        return (result.stdout or "").strip()
    return str(result or "").strip()


def _parse_app_links_allowed(raw: str) -> Optional[bool]:
    for line in (raw or "").splitlines():
        if "Verification link handling allowed:" not in line:
            continue
        value = line.rsplit(":", 1)[-1].strip().lower()
        if value == "true":
            return True
        if value == "false":
            return False
    return None


def _parse_disabled_app_link_domains(raw: str) -> set[str]:
    disabled_domains = set()
    in_disabled_block = False
    for line in (raw or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped == "Disabled:":
            in_disabled_block = True
            continue
        if stripped.endswith(":"):
            in_disabled_block = False
            continue
        if in_disabled_block:
            disabled_domains.add(stripped)
    return disabled_domains


async def ensure_facebook_link_handling_allowed(driver, device_id: str, use_cache: bool = True) -> bool:
    if use_cache and device_id in _FACEBOOK_LINK_HANDLING_CHECKED:
        return True

    device_label = DEVICE_LIST_NAME.get(device_id, device_id)
    set_commands = (
        f"pm set-app-links-allowed --user 0 --package {FACEBOOK_PKG} true",
        f"cmd package set-app-links-allowed --user 0 --package {FACEBOOK_PKG} true",
    )

    last_error = ""
    for cmd in set_commands:
        try:
            result = await asyncio.to_thread(driver.shell, cmd)
            output = _shell_response_text(result)
            lowered = output.lower()
            if "unknown command" in lowered or "error:" in lowered:
                last_error = output
                continue
            break
        except Exception as e:
            last_error = str(e)
    else:
        log_message(
            f"[{device_label}] Khong bat duoc quyen tu mo link Facebook bang app: {last_error}",
            logging.WARNING,
        )
        return False

    domains = " ".join(FACEBOOK_APP_LINK_DOMAINS)
    selection_commands = (
        f"pm set-app-links-user-selection --user 0 --package {FACEBOOK_PKG} true {domains}",
        f"cmd package set-app-links-user-selection --user 0 --package {FACEBOOK_PKG} true {domains}",
    )
    selection_error = ""
    selection_ok = False
    for cmd in selection_commands:
        try:
            result = await asyncio.to_thread(driver.shell, cmd)
            output = _shell_response_text(result)
            lowered = output.lower()
            if "unknown command" in lowered or "error:" in lowered:
                selection_error = output
                continue
            selection_ok = True
            break
        except Exception as e:
            selection_error = str(e)

    if not selection_ok:
        log_message(
            f"[{device_label}] Khong bat duoc Selection state cho domain Facebook: {selection_error}",
            logging.WARNING,
        )

    try:
        check_result = await asyncio.to_thread(
            driver.shell,
            f"pm get-app-links --user 0 {FACEBOOK_PKG}",
        )
        check_output = _shell_response_text(check_result)
        allowed = _parse_app_links_allowed(check_output)
        disabled_domains = _parse_disabled_app_link_domains(check_output)
    except Exception as e:
        allowed = None
        disabled_domains = set()
        check_output = str(e)

    blocked_domains = disabled_domains.intersection(FACEBOOK_APP_LINK_DOMAINS)
    if allowed is True and not blocked_domains:
        _FACEBOOK_LINK_HANDLING_CHECKED.add(device_id)
        log_message(
            f"[{device_label}] Da bat quyen tu mo link Facebook bang app",
            logging.INFO,
        )
        return True

    if blocked_domains:
        log_message(
            f"[{device_label}] Link Facebook van bi Disabled o domain: {', '.join(sorted(blocked_domains))}",
            logging.WARNING,
        )
        return False

    if allowed is False:
        log_message(
            f"[{device_label}] Da gui lenh bat link Facebook nhung trang thai van false",
            logging.WARNING,
        )
        return False

    _FACEBOOK_LINK_HANDLING_CHECKED.add(device_id)
    log_message(
        f"[{device_label}] Da gui lenh bat link Facebook, khong doc duoc trang thai xac nhan: {check_output}",
        logging.WARNING,
    )
    return True


def _resolve_launch_activity(driver, package_name: str) -> Optional[str]:
    try:
        raw = _shell_response_text(
            driver.shell(["cmd", "package", "resolve-activity", "--brief", package_name])
        )
    except Exception:
        return None
    for line in reversed(raw.splitlines()):
        line = line.strip()
        if "/" not in line or line.startswith("priority="):
            continue
        resolved_package, activity = line.split("/", 1)
        if resolved_package == package_name and activity:
            return activity
    return None


def _notification_shade_has_focus(window_dump: str) -> bool:
    if not window_dump:
        return False
    for line in window_dump.splitlines():
        if (
            ("mCurrentFocus" in line or "mFocusedWindow" in line)
            and "NotificationShade" in line
        ):
            return True
    return False

def _phone_system_window_has_focus(window_dump: str) -> bool:
    if not window_dump:
        return False
    for line in window_dump.splitlines():
        if (
            ("mCurrentFocus" in line or "mFocusedWindow" in line)
            and any(package_name in line for package_name in PHONE_SYSTEM_DIALOG_PACKAGE_NAMES)
        ):
            return True
    return False

async def collapse_statusbar_if_notification_shade_open(driver, device_id: str, window_dump: Optional[str] = None) -> bool:
    if window_dump is None:
        raw = await asyncio.to_thread(driver.shell, "dumpsys window")
        window_dump = _shell_response_text(raw)
    if not _notification_shade_has_focus(window_dump):
        return False
    await asyncio.to_thread(driver.shell, "cmd statusbar collapse")
    log_message(
        f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] Phát hiện thanh thông báo/cài đặt nhanh đang che màn hình -> đã đóng lại",
        logging.WARNING,
    )
    return True

def _looks_like_phone_system_dialog(xml_dump: str) -> bool:
    if not xml_dump:
        return False
    normalized = xml_dump.lower()
    if not any(package_marker in normalized for package_marker in PHONE_SYSTEM_DIALOG_PACKAGES):
        return False
    if not all(marker in normalized for marker in PHONE_SYSTEM_DIALOG_REQUIRED_MARKERS):
        return False
    return any(marker in normalized for marker in PHONE_SYSTEM_DIALOG_DISMISS_MARKERS)

async def dismiss_phone_system_dialog_if_present(driver, device_id: str) -> bool:
    try:
        xml_dump = await asyncio.to_thread(driver.dump_hierarchy)
    except Exception:
        return False
    if not _looks_like_phone_system_dialog(xml_dump):
        return False
    try:
        cancel_button = driver(resourceId="android:id/button2")
        if cancel_button.exists:
            await asyncio.to_thread(cancel_button.click)
        else:
            await asyncio.to_thread(driver.press, "back")
    except Exception:
        await asyncio.to_thread(driver.press, "back")
    log_message(
        f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] Da dong popup he thong Phone/SIM",
        logging.WARNING,
    )
    return True

async def statusbar_guard_loop(device_id: str, get_driver, interval: float = STATUSBAR_GUARD_INTERVAL):
    last_error_log_at = 0.0

    while True:
        try:
            await asyncio.sleep(interval)

            driver = get_driver() if callable(get_driver) else None
            if driver is None:
                continue
            raw = await asyncio.to_thread(driver.shell, "dumpsys window")
            window_dump = _shell_response_text(raw)
            await collapse_statusbar_if_notification_shade_open(driver, device_id, window_dump=window_dump)
            if _phone_system_window_has_focus(window_dump):
                await dismiss_phone_system_dialog_if_present(driver, device_id)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            now = time.monotonic()
            if now - last_error_log_at >= STATUSBAR_GUARD_ERROR_LOG_INTERVAL:
                last_error_log_at = now
                log_message(
                    f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] Statusbar guard lỗi: {e}",
                    logging.WARNING,
                )

def _install_app_start_without_monkey_launch():
    try:
        original_app_start = u2.Device.app_start
    except Exception:
        return

    if getattr(original_app_start, "_without_monkey_launch", False):
        return

    def app_start_without_monkey(self, package_name: str, activity=None, wait: bool = False, stop: bool = False, use_monkey: bool = False):
        resolved_activity = activity
        if not use_monkey and not resolved_activity:
            resolved_activity = APP_START_ACTIVITIES.get(package_name)
            if not resolved_activity:
                resolved_activity = _resolve_launch_activity(self, package_name)
        return original_app_start(
            self,
            package_name,
            activity=resolved_activity,
            wait=wait,
            stop=stop,
            use_monkey=use_monkey,
        )

    app_start_without_monkey._without_monkey_launch = True
    u2.Device.app_start = app_start_without_monkey


def _parse_rotation_int(text: str) -> Optional[int]:
    raw = str(text or "").strip()
    if not raw:
        return None

    lowered = raw.lower()
    if lowered in {"0", "1", "2", "3"}:
        return int(lowered)

    patterns = [
        r"SurfaceOrientation:\s*(\d+)",
        r"surfaceOrientation[=:]\s*(\d+)",
        r"mCurrentRotation[=:]\s*(?:ROTATION_)?(\d+)",
        r"\bmRotation[=:]\s*(?:ROTATION_)?(\d+)",
        r"\brotation[=:]\s*(?:ROTATION_)?(\d+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, raw, re.IGNORECASE)
        if match:
            try:
                return int(match.group(1))
            except Exception:
                return None

    if "portrait" in lowered:
        return 0
    if "landscape" in lowered:
        return 1
    return None

def _time_in_window(current: dt_time, start: dt_time, end: dt_time) -> bool:
    if start <= end:
        return start <= current < end
    return current >= start or current < end
def should_passive_wait_only_now(now: Optional[datetime] = None) -> bool:
    current = (now or datetime.now(VN_TZ)).time()
    return any(
        _time_in_window(current, start, end)
        for start, end in PASSIVE_WAIT_ONLY_WINDOWS
    )
async def passive_wait_schedule_loop():
    last_applied = None
    while True:
        try:
            scheduled_value = should_passive_wait_only_now()
            current_value = fb_runtime.get_passive_wait_only()
            if current_value != scheduled_value or last_applied != scheduled_value:
                await apply_passive_wait_only(scheduled_value)
                mode_text = "tat nuoi, chi cho lenh RabbitMQ" if scheduled_value else "bat nuoi"
                log_message(
                    f"[SCHEDULE] PASSIVE_WAIT_ONLY={scheduled_value} -> {mode_text}",
                    logging.INFO,
                )
                last_applied = scheduled_value
            await asyncio.sleep(PASSIVE_SCHEDULE_CHECK_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log_message(
                f"[SCHEDULE] passive_wait_schedule_loop error: {type(e).__name__}: {e}",
                logging.WARNING,
            )
            await asyncio.sleep(PASSIVE_SCHEDULE_CHECK_SECONDS)

async def start_control_rabbit_consumer():
    while True:
        try:
            conn = await aio_pika.connect_robust(RABBIT_URL)
            async with conn:
                ch = await conn.channel()
                await ch.set_qos(prefetch_count=10)

                q = await ch.declare_queue(CONTROL_QUEUE, durable=True)

                log_message(f"[CONTROL] consuming {CONTROL_QUEUE}", logging.INFO)

                async with q.iterator() as it:
                    async for msg in it:
                        try:
                            payload = json.loads(msg.body.decode("utf-8"))
                            result = await handle_control_message(payload)
                            log_message(f"[CONTROL] Done: {result}", logging.INFO)
                            await msg.ack()
                        except Exception as e:
                            log_message(f"[CONTROL] Error: {e}", logging.ERROR)
                            try:
                                await msg.nack(requeue=False)
                            except Exception:
                                pass
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log_message(f"[CONTROL] consumer error: {type(e).__name__}: {e} (retry 3s)", logging.WARNING)
            await asyncio.sleep(3)
            
async def apply_passive_wait_only(value: bool, device_id: str = None):
    if device_id:
        await fb_runtime.set_passive_wait_only_now(value, device_id=device_id)
        log_message(f"[CONTROL] passive_wait_only={value} device={device_id}", logging.INFO)
        return

    # global
    fb_runtime.set_passive_wait_only(value)

    if value:
        # force các máy đang chạy dừng action hiện tại nhanh hơn
        for did in list(active_tasks.keys()):
            try:
                await fb_runtime.set_passive_wait_only_now(True, device_id=did)
            except Exception as e:
                log_message(f"[CONTROL] force passive failed for {did}: {e}", logging.WARNING)

    log_message(f"[CONTROL] passive_wait_only={value} device=ALL", logging.INFO)

async def handle_control_message(msg: dict):
    action = (msg.get("action") or "").strip().lower()
    device_id = msg.get("device_id")

    if action == "set_passive_wait_only":
        value = bool(msg.get("value", False))
        await apply_passive_wait_only(value, device_id=device_id)
        return {"ok": True, "action": action, "value": value, "device_id": device_id}

    return {"ok": False, "error": f"Unknown control action: {action}"}

async def close_facebook_online_session_if_left_app(device_id: str, driver) -> None:
    if driver is None:
        return
    try:
        info = await asyncio.to_thread(driver.app_current)
        package_name = (info or {}).get("package") or ""
    except Exception:
        return

    if package_name in (FACEBOOK_PKG, MESSENGER_PKG):
        return

    try:
        await pymongo_management.update_statusFB(statusFB="Offline", device_id=device_id)
    except Exception as e:
        log_message(
            f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] Close FB online session failed: {type(e).__name__} | {e}",
            logging.WARNING,
        )

async def close_facebook_online_session(device_id: str, reason: str) -> None:
    try:
        await pymongo_management.update_statusFB(statusFB="Offline", device_id=device_id)
    except Exception as e:
        log_message(
            f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] Close FB online session failed ({reason}): {type(e).__name__} | {e}",
            logging.WARNING,
        )

async def refresh_facebook_online_session_from_runtime(device_id: str) -> bool:
    """
    Runtime state is the fastest source for the active Facebook account.
    Keep Mongo/CRM in sync with it so CRM does not show Offline while the
    device runner still knows which account is online.
    """
    try:
        current_account = (get_current_account(device_id) or "").strip()
        if not current_account or current_account == "default":
            return False

        if not is_runtime_online(device_id, max_age_sec=max(HEARTBEAT_SECONDS * 4, 90)):
            return False

        await pymongo_management.switch_online_account_for_device(
            device_id=device_id,
            current_account=current_account,
        )
        return True
    except Exception as e:
        log_message(
            f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] Refresh FB online session from runtime failed: {type(e).__name__} | {e}",
            logging.WARNING,
        )
        return False

async def device_status_heartbeat(device_id: str, get_driver, rabbit_task_getter, interval: float = HEARTBEAT_SECONDS):
    """
    Heartbeat định kỳ cho từng máy:
      - refresh devices.status=True liên tục
      - lưu thêm last_seen / heartbeat_at
      - lưu cờ adb_connected / rabbit_connected
    Lưu ý:
      - KHÔNG set offline ở đây nếu adb check lỗi 1 lần
      - việc set offline để device_supervisor xử lý sau 3 lần liên tiếp
    """
    dev_label = DEVICE_LIST_NAME.get(device_id, device_id)

    while True:
        try:
            driver = get_driver() if callable(get_driver) else None

            adb_ok = False
            if driver is not None:
                try:
                    adb_ok = await check_driver(driver)
                except Exception:
                    adb_ok = False

            rabbit_task = rabbit_task_getter() if callable(rabbit_task_getter) else None
            rabbit_ok = rabbit_task is not None and not rabbit_task.done()

            if adb_ok:
                await pymongo_management.touch_device_heartbeat(
                    device_id,
                    is_rabbit_connected=rabbit_ok,
                    is_adb_connected=True,
                    note="device_supervisor_heartbeat",
                )
                refreshed = await refresh_facebook_online_session_from_runtime(device_id)
                if not refreshed:
                    await close_facebook_online_session_if_left_app(device_id, driver)
            else:
                # Chỉ ghi chú nhẹ, không ép status=False tại đây
                # để tránh false offline do lỗi 1 nhịp
                pass

        except asyncio.CancelledError:
            raise
        except Exception as hb_err:
            log_message(f"[{dev_label}] heartbeat lỗi: {hb_err}", logging.WARNING)

        await asyncio.sleep(interval)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
_install_app_start_without_monkey_launch()

USER_DEVICE_MAP_REFRESH_SECONDS = 30.0
FACEBOOK_DEVICES_FILE = "Facebook.devices.json"
LEGACY_USER_DEVICE_MAP_FILE = "User_id and device_id.json"

def build_user_device_map_from_devices(devices):
    # with open(path, "r", encoding="utf-8") as f:
    #     devices = json.load(f)

    # if not isinstance(devices, list):
    #     raise ValueError(f"{path} phai la JSON array")

    user_device_map = {}
    duplicate_accounts = {}

    for device in devices or []:
        if not isinstance(device, dict):
            continue

        device_id = str(device.get("device_id") or "").strip()
        if not device_id:
            continue

        accounts = device.get("accounts") or []
        if not isinstance(accounts, list):
            continue

        for account_info in accounts:
            if not isinstance(account_info, dict):
                continue

            account = str(account_info.get("account") or "").strip()
            if not account:
                continue

            previous_device_id = user_device_map.get(account)
            if previous_device_id and previous_device_id != device_id:
                duplicate_accounts[account] = (previous_device_id, device_id)

            user_device_map[account] = device_id

    if duplicate_accounts:
        logging.warning(
            "[CONFIG] Trung account trong Mongo devices, dang dung mapping xuat hien sau cung: %s",
            # path,
            duplicate_accounts,
        )

    return user_device_map

def load_user_device_map_from_mongo_devices():
    devices = get_all_devices_from_mongo()
    return build_user_device_map_from_devices(devices)

def build_device_users_map(user_device_map):
    device_users = {}
    for user_key, device_id in user_device_map.items():
        if not device_id:
            continue
        device_users.setdefault(device_id, []).append(user_key)
    return device_users

try:
    USER_DEVICE_MAP = load_user_device_map_from_mongo_devices()
    logging.info(
        "[CONFIG] Da tao USER_DEVICE_MAP tu Mongo devices (%s account)",
        # FACEBOOK_DEVICES_FILE,
        len(USER_DEVICE_MAP),
    )
except Exception as exc:
    # logging.warning(
    #     "[CONFIG] Khong the doc %s (%s). Fallback sang %s",
    #     FACEBOOK_DEVICES_FILE,
    #     exc,
    #     LEGACY_USER_DEVICE_MAP_FILE,
    # )
    # with open(LEGACY_USER_DEVICE_MAP_FILE, "r", encoding="utf-8") as f:
    #     USER_DEVICE_MAP = json.load(f)
    logging.warning("[CONFIG] Khong the doc Mongo devices (%s). USER_DEVICE_MAP rong.", exc)
    USER_DEVICE_MAP = {}

# Tạo map ngược: device_id -> list[user_id]
DEVICE_USERS = build_device_users_map(USER_DEVICE_MAP)

# try:
#     _USER_DEVICE_MAP_MTIME = os.path.getmtime(FACEBOOK_DEVICES_FILE)
# except OSError:
#     _USER_DEVICE_MAP_MTIME = None

_USER_DEVICE_MAP_LAST_REFRESH = time.monotonic()

def refresh_user_device_maps(force: bool = False):
    global USER_DEVICE_MAP, DEVICE_USERS,  _USER_DEVICE_MAP_LAST_REFRESH

    now = time.monotonic()
    if not force and now - _USER_DEVICE_MAP_LAST_REFRESH < USER_DEVICE_MAP_REFRESH_SECONDS:
        return False

    # try:
    #     current_mtime = os.path.getmtime(FACEBOOK_DEVICES_FILE)
    # except OSError:
    #     current_mtime = None

    # if current_mtime == _USER_DEVICE_MAP_MTIME:
    #     return False

    try:
        user_device_map = load_user_device_map_from_mongo_devices()
    except Exception as exc:
        logging.warning(
            "[CONFIG] Khong the refresh USER_DEVICE_MAP tu Mongo devices (%s). Giu mapping hien tai.",
            # FACEBOOK_DEVICES_FILE,
            exc,
        )
        return False

    USER_DEVICE_MAP = user_device_map
    DEVICE_USERS = build_device_users_map(user_device_map)
    # _USER_DEVICE_MAP_MTIME = current_mtime
    _USER_DEVICE_MAP_LAST_REFRESH = now
    logging.info(
        "[CONFIG] Refreshed USER_DEVICE_MAP tu Mongo devices (%s account)",
        # FACEBOOK_DEVICES_FILE,
        len(USER_DEVICE_MAP),
    )
    return True
# ======================= HÀM HỖ TRỢ =======================
def _is_home_pkg(pkg: str) -> bool:
    return pkg in HOME_PACKAGES

def _is_target_pkg(pkg: str) -> bool:
    return pkg in TARGET_PACKAGES

async def ensure_1111_vpn_on(driver, device_id: str, use_cache: bool = False):
    """
    Bật app 1.1.1.1 và gạt switch nếu chưa bật.
    - use_cache=False: luôn kiểm tra lại, phù hợp khi bắt đầu mỗi vòng.
    - use_cache=True: chỉ kiểm tra 1 lần cho mỗi device_id trong process hiện tại.
    Không fail nếu không có app.
    """
    if use_cache and device_id in _VPN_CHECKED:
        return

    try:
        check_mode = "một lần" if use_cache else "đầu mỗi vòng"
        log_message(
            f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] Kiểm tra bật 1.1.1.1 ({check_mode})",
            logging.INFO,
        )

        await asyncio.to_thread(driver.app_start, "com.cloudflare.onedotonedotonedotone")
        await asyncio.sleep(3.0)

        sw = driver(resourceId="com.cloudflare.onedotonedotonedotone:id/launchSwitch")
        if sw.exists:
            try:
                checked = bool(sw.info.get("checked"))
            except Exception:
                checked = False

            if not checked:
                sw.click()
                await asyncio.sleep(1.5)

        if use_cache:
            _VPN_CHECKED.add(device_id)

        ok_mode = "One-time" if use_cache else "Per-round"
        log_message(
            f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] {ok_mode} check 1.1.1.1 OK",
            logging.INFO,
        )

    except Exception as e:
        log_message(
            f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] VPN check lỗi: {e}",
            logging.WARNING,
        )


async def ensure_1111_vpn_on_once(driver, device_id: str):
    """
    Wrapper tương thích cho các flow cũ cần check đúng 1 lần / process.
    """
    await ensure_1111_vpn_on(driver, device_id, use_cache=True)

async def global_urgent_scheduler():
    """
    Scheduler global:
      - Mỗi 60s kiểm tra urgent_queue.json
      - Nếu đã đủ 30' kể từ job trước (do urgent_queue.py kiểm soát),
        sẽ lấy 1 job ra
      - Quyết định job đó thuộc device nào, rồi đẩy vào queue + set event
        để fb_task.urgent_worker() xử lý như cũ.
    """
    while True:
        try:
            if not fb_runtime.ENABLE_MONGO_COMMAND_SCAN:
                await asyncio.sleep(60)
                continue
            refresh_user_device_maps()
            job = get_next_task_if_due()
            if job:
                # 1) Tìm device target
                device_ids = job.get("device_ids")
                target_device_id = None

                # Ensure device_ids là list
                if isinstance(device_ids, str):
                    device_ids = [device_ids]
                if isinstance(device_ids, list):
                    # Ưu tiên device đang online trong DEVICE_LIST
                    for did in DEVICE_LIST:
                        if did in device_ids:
                            target_device_id = did
                            break

                # Nếu chưa có device từ device_ids -> map theo user_id (CRM)
                if not target_device_id:
                    user_key = str(job.get("user_id") or "").strip()
                    if user_key:
                        target_device_id = USER_DEVICE_MAP.get(user_key)

                if not target_device_id:
                    log_message(f"[SCHED] ❌ Không xác định được device cho job {job.get('id')}", logging.WARNING)
                else:
                    # 2) Bơm vào queue urgent của device đó
                    event, queue = get_urgent_objects(target_device_id)
                    await queue.put(job)
                    event.set()
                    log_message(
                        f"[SCHED] ➡️ Dispatch job id={job.get('id')} action={job.get('action')} "
                        f"tới device {DEVICE_LIST_NAME.get(target_device_id, target_device_id)}",
                        logging.INFO,
                    )
        except Exception as e:
            log_message(f"[SCHED] Lỗi trong global_urgent_scheduler: {e}", logging.ERROR)

        # Check mỗi 60s, việc delay 30' đã được urgent_queue.py xử lý
        await asyncio.sleep(60)

async def disable_auto_rotation(driver, device_id: str):
    """
    Tắt auto-rotate theo kiểu chịu được nhiều ROM:
    - accelerometer_rotation = 0
    - user_rotation = 0
    - user-rotation lock 0 nếu máy cần khóa cứng để không xoay giao diện
    """
    import asyncio
    import logging
    def device_label():
        return DEVICE_LIST_NAME.get(device_id, f"Máy {device_id}")
    def parse_shell_response(result):
        if hasattr(result, "output"):
            return (result.output or "").strip()
        elif hasattr(result, "text"):
            return (result.text or "").strip()
        elif hasattr(result, "stdout"):
            return (result.stdout or "").strip()
        return str(result).strip()
    def adb_shell_sync(cmd: str) -> str:
        adb_path = WINDOW_ADB_PATH if os.name == "nt" else LINUX_ADB_PATH
        args = [adb_path, "-s", device_id, "shell", *cmd.split()]
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
        )
        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()
        if result.returncode != 0:
            detail = stderr or stdout or f"returncode={result.returncode}"
            raise RuntimeError(detail)
        return stdout
    async def sh(cmd: str, *, log_output: bool = True):
        try:
            out = await asyncio.to_thread(adb_shell_sync, cmd)
            source = "adb"
        except Exception as adb_err:
            try:
                res = await asyncio.to_thread(driver.shell, cmd)
                out = parse_shell_response(res)
                source = "driver.shell"
                log_message(
                    f"[{device_label()}] adb direct lỗi, fallback driver.shell cho '{cmd}': {adb_err}",
                    logging.WARNING
                )
            except Exception as e:
                log_message(
                    f"[{device_label()}] shell lỗi: {cmd} -> adb={adb_err} | driver={e}",
                    logging.WARNING
                )
                return ""
        try:
            if log_output:
                preview = out
                if len(preview) > 180:
                    preview = preview[:180] + "...<truncated>"
                log_message(
                    f"[{device_label()}] shell[{source}]: {cmd} -> {preview}",
                    logging.INFO
                )
            return out
        except Exception as e:
            log_message(
                f"[{device_label()}] lỗi log shell output: {cmd} -> {e}",
                logging.WARNING
            )
            return out
    async def get_wm_rotation_state() -> str:
        wm_state = await sh("cmd window user-rotation")
        if not wm_state:
            wm_state = await sh("wm user-rotation")
        return (wm_state or "").strip()
    async def get_actual_display_rotation() -> tuple[Optional[int], str]:
        commands = [
            "dumpsys input",
            "dumpsys display",
            "dumpsys window displays",
            "dumpsys window",
        ]
        for cmd in commands:
            raw = await sh(cmd, log_output=False)
            rotation = _parse_rotation_int(raw)
            if rotation is not None:
                log_message(
                    f"[{device_label()}] actual display rotation via '{cmd}' -> {rotation}",
                    logging.INFO,
                )
                return rotation, cmd
        log_message(
            f"[{device_label()}] Không đọc được actual display rotation từ dumpsys",
            logging.WARNING,
        )
        return None, ""
    async def get_visual_orientation() -> tuple[Optional[str], str]:
        try:
            image = await asyncio.to_thread(driver.screenshot)
            width, height = image.size
            orientation = "portrait" if height >= width else "landscape"
            log_message(
                f"[{device_label()}] visual orientation via screenshot -> {orientation} ({width}x{height})",
                logging.INFO,
            )
            return orientation, "screenshot"
        except Exception as e:
            log_message(
                f"[{device_label()}] Không đọc được visual orientation từ screenshot: {e}",
                logging.WARNING,
            )
            return None, ""
    async def apply_rotation_settings(*, hard_lock: bool, ignore_orientation_request: bool = False):
        await sh("settings put system accelerometer_rotation 0")
        await sh("settings --user 0 put system accelerometer_rotation 0")
        await sh("settings put system user_rotation 0")
        await sh("settings --user 0 put system user_rotation 0")
        await sh("settings put secure show_rotation_suggestions 0")
        await sh("settings --user 0 put secure show_rotation_suggestions 0")
        if hard_lock:
            await sh("cmd window user-rotation lock 0")
            await sh("wm user-rotation lock 0")
            ignore_orientation_request = True
        if ignore_orientation_request:
            await sh("cmd window set-ignore-orientation-request true")
            await sh("wm set-ignore-orientation-request true")
        await asyncio.sleep(0.2)
    try:
        log_message(
            f"[{device_label()}] Kiểm tra trạng thái tự động xoay màn hình",
            logging.INFO
        )
        accel = await sh("settings get system accelerometer_rotation")
        wm_state = await get_wm_rotation_state()
        wm_state_low = wm_state.lower()
        actual_rotation, actual_rotation_source = await get_actual_display_rotation()
        visual_orientation, visual_source = await get_visual_orientation()
        if accel == "0" and "lock 0" in wm_state_low and actual_rotation == 0 and visual_orientation != "landscape":
            await apply_rotation_settings(hard_lock=True, ignore_orientation_request=True)
            log_message(
                f"[{device_label()}] Auto-rotate đã tắt sẵn, màn hình đang dọc -> refresh hard-lock portrait guard",
                logging.INFO
            )
            return
        if actual_rotation not in (None, 0) or visual_orientation == "landscape":
            log_message(
                f"[{device_label()}] Phát hiện màn hình chưa ở dọc (rotation={actual_rotation} từ {actual_rotation_source}, visual={visual_orientation or 'unknown'} từ {visual_source or 'n/a'}) -> sẽ đưa về portrait trước khi khóa",
                logging.WARNING,
            )
        if accel not in ("", "1"):
            log_message(
                f"[{device_label()}] Giá trị accelerometer_rotation bất thường: {accel!r} -> vẫn thử tắt",
                logging.WARNING
            )
        # Mục tiêu hiện tại là giữ dọc cứng trong suốt runtime, nên luôn áp dụng hard-lock portrait.
        await apply_rotation_settings(hard_lock=True, ignore_orientation_request=True)
        await asyncio.sleep(0.5)
        accel_after = await sh("settings get system accelerometer_rotation")
        wm_after = await get_wm_rotation_state()
        actual_after, actual_after_source = await get_actual_display_rotation()
        visual_after, visual_after_source = await get_visual_orientation()
        if actual_after not in (None, 0) or visual_after == "landscape":
            log_message(
                f"[{device_label()}] Sau hard-lock portrait vẫn chưa về dọc (actual={actual_after} từ {actual_after_source}, visual={visual_after or 'unknown'} từ {visual_after_source or 'n/a'}) -> ép redraw rồi kiểm tra lại",
                logging.WARNING,
            )
            await apply_rotation_settings(hard_lock=True, ignore_orientation_request=True)
            await asyncio.sleep(0.5)
            try:
                await asyncio.to_thread(driver.press, "home")
            except Exception:
                pass
            await asyncio.sleep(0.5)
            accel_after = await sh("settings get system accelerometer_rotation")
            wm_after = await get_wm_rotation_state()
            actual_after, actual_after_source = await get_actual_display_rotation()
            visual_after, visual_after_source = await get_visual_orientation()
        log_message(
            f"[{device_label()}] Kết quả auto-rotate: accelerometer_rotation={accel_after}, wm_state={wm_after!r}, actual_rotation={actual_after}, visual_orientation={visual_after}",
            logging.INFO if accel_after == "0" and "lock 0" in wm_after.lower() and actual_after in (None, 0) and visual_after != "landscape" else logging.WARNING
        )
    except Exception as e:
        log_message(
            f"[{device_label()}] Lỗi khi kiểm tra/tắt auto-rotate: {e}",
            logging.WARNING
        )
# async def disable_auto_rotation(driver, device_id: str):
#     """
#     Thực sự tắt chế độ tự động xoay màn hình của hệ thống Android
#     """
    
#     def parse_shell_response(result):
#         """Helper function để xử lý ShellResponse object từ UIAutomator2"""
#         if hasattr(result, 'output'):
#             return result.output.strip()
#         elif hasattr(result, 'text'):
#             return result.text.strip()
#         elif hasattr(result, 'stdout'):
#             return result.stdout.strip()
#         else:
#             return str(result).strip()
    
#     try:
#         log_message(f"[{DEVICE_LIST_NAME[device_id]}] Tắt chế độ tự động xoay màn hình hệ thống", logging.INFO)
        
#         # Kiểm tra trạng thái hiện tại trước
#         try:
#             current_result = driver.shell("settings get system accelerometer_rotation")
#             current_value = parse_shell_response(current_result)
#             log_message(f"[{DEVICE_LIST_NAME[device_id]}] Trạng thái auto-rotation hiện tại: {current_value}", logging.INFO)
#         except Exception as e:
#             log_message(f"[{DEVICE_LIST_NAME[device_id]}] Không thể kiểm tra trạng thái hiện tại: {e}", logging.INFO)
#             current_value = "unknown"
        
#         # Tắt auto-rotation qua shell command
#         try:
#             await asyncio.to_thread(driver.shell, "settings put system accelerometer_rotation 0")
#             log_message(f"[{DEVICE_LIST_NAME[device_id]}] Đã gửi lệnh tắt auto-rotation qua settings", logging.INFO)
#             await asyncio.sleep(1)  # Chờ settings apply
            
#         except Exception as e:
#             log_message(f"[{DEVICE_LIST_NAME[device_id]}] Lỗi tắt auto-rotation qua settings: {e}", logging.INFO)
        
#         # Kiểm tra trạng thái sau khi tắt
#         try:
#             result = await asyncio.to_thread(driver.shell, "settings get system accelerometer_rotation")
#             final_value = parse_shell_response(result)
            
#             if final_value == "0":
#                 status = "TẮT ✅"
#                 log_message(f"[{DEVICE_LIST_NAME[device_id]}] Auto-rotation đã được TẮT thành công!", logging.INFO)
#             elif final_value == "1":
#                 status = "BẬT ❌"
#                 log_message(f"[{DEVICE_LIST_NAME[device_id]}] Auto-rotation vẫn còn BẬT - có thể cần retry", logging.WARNING)
#             else:
#                 status = f"KHÔNG XÁC ĐỊNH ({final_value})"
#                 log_message(f"[{DEVICE_LIST_NAME[device_id]}] Trạng thái auto-rotation không xác định: {final_value}", logging.WARNING)
            
#             log_message(f"[{DEVICE_LIST_NAME[device_id]}] Trạng thái auto-rotation cuối: {status}", logging.INFO)
            
#         except Exception as e:
#             log_message(f"[{DEVICE_LIST_NAME[device_id]}] Không thể kiểm tra trạng thái cuối: {e}", logging.INFO)
        
#         log_message(f"[{DEVICE_LIST_NAME[device_id]}] Hoàn thành disable auto-rotation", logging.INFO)
        
#     except Exception as e:
#         log_message(f"[{DEVICE_LIST_NAME[device_id]}] Lỗi khi tắt auto-rotation: {e}", logging.WARNING)
# async def mute_device_volume(driver, device_id: str):
#     """
#     Sử dụng ADB Keyevent để tắt âm lượng.
#     """
#     try:
#         log_message(f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] 🔇 Đang tắt âm lượng...", logging.INFO)

#         # Keycode 164 = KEYCODE_VOLUME_MUTE
#         await asyncio.to_thread(driver.shell, "input keyevent 164")

#         log_message(f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] ✅ Đã tắt âm lượng hoàn toàn.", logging.INFO)

#     except Exception as e:
#         log_message(f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] ⚠️ Lỗi khi tắt âm lượng: {e}", logging.WARNING)

async def mute_device_volume(driver, device_id: str):
    """
    Set tat ca audio stream ve 0, rieng ringtone/cuoc goi den giu muc 15.
    """
    try:
        device_label = DEVICE_LIST_NAME.get(device_id, device_id)
        log_message(f"[{device_label}] 🔇 Đang set âm lượng: stream 2 = 15, các stream khác = 0...", logging.INFO)

        # Android audio streams:
        # 0 voice_call, 1 system, 2 ring, 3 music/media, 4 alarm,
        # 5 notification, 6 bluetooth_sco, 7 system_enforced,
        # 8 dtmf, 9 tts, 10 accessibility.
        volume_commands = [
            "cmd media_session volume --stream 0 --set 0",
            "cmd media_session volume --stream 1 --set 0",
            "cmd media_session volume --stream 3 --set 0",
            "cmd media_session volume --stream 4 --set 0",
            "cmd media_session volume --stream 5 --set 0",
            "cmd media_session volume --stream 6 --set 0",
            "cmd media_session volume --stream 7 --set 0",
            "cmd media_session volume --stream 8 --set 0",
            "cmd media_session volume --stream 9 --set 0",
            "cmd media_session volume --stream 10 --set 0",
            "cmd media_session volume --stream 2 --set 15",
            "settings put global zen_mode 0",
        ]

        for cmd in volume_commands:
            try:
                await asyncio.to_thread(driver.shell, cmd)
            except Exception as cmd_err:
                log_message(f"[{device_label}] ⚠️ Lệnh âm lượng lỗi: {cmd} -> {cmd_err}", logging.WARNING)

        log_message(f"[{device_label}] ✅ Đã set âm lượng: chỉ giữ ringtone/cuộc gọi đến ở mức 15.", logging.INFO)

    except Exception as e:
        log_message(f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] ⚠️ Lỗi khi tắt âm lượng: {e}", logging.WARNING)
        
# class InactivityWatchdog:
#     def __init__(
#         self,
#         driver,
#         device_id: str,
#         idle_seconds: int = 60,
#         on_resume: Optional[Callable[[str], "asyncio.Future"]] = None,
#         on_restart: Optional[Callable[[], "asyncio.Future"]] = None,
#         phase_provider: Optional[Callable[[], str]] = None,
#         check_frozen: bool = True, # Kiểm tra trạng thái màn hình bị đứng
#         frozen_threshold: int = 1, # Số lần kiểm tra liên tiếp màn hình đứng mới coi là bị đứng
#         ui_check_interval: int = 40 # Thời gian kiểm tra UI (giây)
#     ):
#         self.driver = driver
#         self.device_id = device_id
#         self.idle_seconds = int(idle_seconds)
#         self.on_resume = on_resume
#         self.on_restart = on_restart
#         self.phase_provider = phase_provider or (lambda: "facebook")
#         self._task: Optional[asyncio.Task] = None
#         self._stop = asyncio.Event()

#         self._last_seen_target = time.time()
#         self._home_since: Optional[float] = None
#         self.check_frozen = check_frozen          # Có kiểm tra màn hình đứng không
#         self.frozen_threshold = frozen_threshold # số lần đứng liên tiếp để coi là bị đứng
#         self._last_ui_hash = None # Lưu hash của UI gần nhất
#         self._same_ui_count = 0 # Đếm số lần UI không đổi liên tiếp
#         self.ui_check_interval = ui_check_interval # Khoảng thời gian kiểm tra UI
#         self._last_ui_check = 0 # Thời gian lần cuối kiểm tra UI

#     def log(self, msg: str, level=logging.INFO):
#         try:
#             log_message(f"[{self.device_id}][Watchdog] {msg}", level)
#         except Exception:
#             print(f"[{self.device_id}][Watchdog] {msg}")

#     async def start(self):
#         if self._task is None:
#             self._stop.clear()
#             self._task = asyncio.create_task(self._run())

#     async def stop(self):
#         if self._task:
#             self._stop.set()
#             try:
#                 await self._task
#             except Exception:
#                 pass
#             self._task = None

#     async def _run(self):
#         while not self._stop.is_set():
#             # Đọc app hiện tại
#             try:
#                 info = await asyncio.to_thread(self.driver.app_current)
#                 pkg = (info or {}).get("package", "") or ""
#             except Exception:
#                 pkg = ""

#             now = time.time()

#             # Cập nhật lần cuối thấy target
#             if _is_target_pkg(pkg):
#                 self._last_seen_target = now
#                 self._home_since = None  # reset
#                 # 🔍 Kiểm tra "màn hình bị đứng"
#                 if self.check_frozen and now - self._last_ui_check >= self.ui_check_interval:
#                     self._last_ui_check = now  # ✅ reset mốc thời gian check UI
#                     try:
#                         xml = await asyncio.to_thread(self.driver.dump_hierarchy)
#                         ui_hash = hashlib.sha256(xml.encode('utf-8')).hexdigest()

#                         if self._last_ui_hash == ui_hash:
#                             self._same_ui_count += 1
#                         else:
#                             self._same_ui_count = 0
#                             self._last_ui_hash = ui_hash

#                         if self._same_ui_count >= self.frozen_threshold:
#                             self.log(f"[{DEVICE_LIST_NAME[self.device_id]}] ⚠️ UI không thay đổi {self.frozen_threshold} lần → đặt trạng thái đứng màn hình ", logging.WARNING)
#                             screen_standing_event[self.device_id] = True
#                             break
#                     except Exception as e:
#                         self.log(f"Lỗi kiểm tra đứng màn hình: {e}", logging.DEBUG)
#             # elif _is_home_pkg(pkg):
#             else:
#                 # if self._home_since is None:
#                 #     self._home_since = now
#                 phase = self.phase_provider()
#                 self.on_resume(phase)
#             # else:
#             #     # ở app khác, không reset _home_since
#             #     pass

#             # # Nếu ở HOME quá 12s -> resume app theo phase
#             # if self._home_since and now - self._home_since >= 1:
#             #     self._home_since = None  # tránh spam
#             #     if self.on_resume:
#             #         phase = self.phase_provider()
#             #         self.log(f"[{DEVICE_LIST_NAME[self.device_id]}]HOME >=1s → mở lại app theo phase='{phase}'")
#             #         try:
#             #             self.on_resume(phase)
#             #             asyncio.create_task(disable_auto_rotation(self.driver, self.device_id))
#             #         except Exception as e:
#             #             self.log(f"[{DEVICE_LIST_NAME[self.device_id]}]Lỗi on_resume: {e}", logging.WARNING)


#             await asyncio.sleep(1.0)

class InactivityWatchdog(threading.Thread):
    def __init__(
        self,
        driver,
        device_id: str,
        idle_seconds: int = 60,
        on_resume: Optional[Callable[[str], None]] = None,
        phase_provider: Optional[Callable[[], str]] = None,
        check_frozen: bool = True,
        frozen_threshold: int = 1,
        ui_check_interval: int = 80
    ):
        super().__init__()
        self.daemon = True

        self.driver = driver
        self.device_id = device_id
        self.idle_seconds = int(idle_seconds)
        self.on_resume = on_resume
        self.phase_provider = phase_provider or (lambda: "facebook")

        self._stop_event = threading.Event()

        # Theo dõi thời gian khi app đi ra ngoài Facebook/Zalo
        self._home_since: Optional[float] = None
        self._last_away_pkg: Optional[str] = None

        # Frozen check
        self.check_frozen = check_frozen
        self.frozen_threshold = frozen_threshold
        self._last_img = None
        self._same_ui_count = 0
        self.ui_check_interval = ui_check_interval
        self._last_ui_check = 0

    def stop(self):
        self._stop_event.set()

    def _device_name(self):
        return DEVICE_LIST_NAME.get(self.device_id, self.device_id)

    def _reset_away_timer(self):
        self._home_since = None
        self._last_away_pkg = None

    def _reset_frozen_state(self):
        self._same_ui_count = 0
        self._last_img = None

    def _resume_current_phase(self, reason: str = ""):
        if not self.on_resume:
            return

        try:
            phase = self.phase_provider()
            log_message(
                f"[{self._device_name()}] Watchdog resume app theo phase='{phase}'"
                + (f" | lý do: {reason}" if reason else ""),
                logging.WARNING
            )
            self.on_resume(phase)
            try:
                res = self.driver.shell("settings get system accelerometer_rotation")
                cur = ""
                if hasattr(res, "output"):
                    cur = (res.output or "").strip()
                elif hasattr(res, "text"):
                    cur = (res.text or "").strip()
                elif hasattr(res, "stdout"):
                    cur = (res.stdout or "").strip()
                else:
                    cur = str(res).strip()
                adb_path = WINDOW_ADB_PATH if os.name == "nt" else LINUX_ADB_PATH

                def adb_shell_text(cmd: str) -> str:
                    result = subprocess.run(
                        [adb_path, "-s", self.device_id, "shell", *cmd.split()],
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="ignore",
                    )
                    return ((result.stdout or "").strip() or (result.stderr or "").strip())

                wm_cur = adb_shell_text("cmd window user-rotation").lower()
                actual_rotation = None
                try:
                    actual_rotation = _parse_rotation_int(adb_shell_text("dumpsys input"))
                except Exception:
                    actual_rotation = None
                visual_orientation = None
                try:
                    shot = self.driver.screenshot()
                    width, height = shot.size
                    visual_orientation = "portrait" if height >= width else "landscape"
                except Exception:
                    visual_orientation = None
                if cur != "0" or "lock 0" not in wm_cur or actual_rotation not in (None, 0) or visual_orientation == "landscape":
                    adb_shell_text("settings put system accelerometer_rotation 0")
                    adb_shell_text("settings put system user_rotation 0")
                    adb_shell_text("cmd window user-rotation lock 0")
                    adb_shell_text("cmd window set-ignore-orientation-request true")
            except Exception:
                pass
        except Exception as e:
            log_message(
                f"[{self._device_name()}] Lỗi resume watchdog: {e}",
                logging.WARNING
            )

    def _get_screen_image(self):
        """
        Chụp màn hình, crop bớt phần trên, resize nhỏ, grayscale để so sánh nhanh.
        """
        try:
            img = self.driver.screenshot()
            w, h = img.size

            # Cắt 10% phía trên để giảm nhiễu status bar / đồng hồ / icon mạng
            top_crop = int(h * 0.1)
            img = img.crop((0, top_crop, w, h))

            # Resize nhỏ để so sánh nhanh
            img = img.resize((100, 100), Image.Resampling.BOX)

            # Chuyển grayscale
            return img.convert("L")
        except Exception:
            return None

    def _is_image_frozen(self, img1, img2, tolerance_percent=8.0):
        """
        True nếu 2 ảnh gần như giống nhau.
        tolerance_percent = % khác biệt cho phép.
        """
        if img1 is None or img2 is None:
            return False

        try:
            diff = ImageChops.difference(img1, img2)
            hist = diff.histogram()

            # Bỏ qua nhiễu rất nhỏ
            diff_pixels = sum(hist[10:])
            total_pixels = 100 * 100
            diff_percent = (diff_pixels / total_pixels) * 100

            return diff_percent < tolerance_percent
        except Exception:
            return False

    def _handle_target_packages(self, now: float, pkg: str):
        """
        Khi đang ở Facebook/Zalo:
        - reset timer "đi ra ngoài"
        - check frozen theo chu kỳ
        """
        self._reset_away_timer()

        if not self.check_frozen:
            return

        if now - self._last_ui_check < self.ui_check_interval:
            return

        self._last_ui_check = now
        current_img = self._get_screen_image()

        if self._last_img is not None and current_img is not None:
            is_frozen = self._is_image_frozen(
                self._last_img,
                current_img,
                tolerance_percent=8.0
            )

            if is_frozen:
                self._same_ui_count += 1
            else:
                self._same_ui_count = 0

        # luôn cập nhật ảnh mới nhất
        self._last_img = current_img

        if self._same_ui_count >= self.frozen_threshold:
            log_message(
                f"[{self._device_name()}] ⚠️ Màn hình đứng yên (Facebook/Zalo) -> Set cờ reset",
                logging.WARNING
            )
            screen_standing_event[self.device_id] = True
            self._same_ui_count = 0
            self._last_img = None

    def _handle_termux(self, now: float):
        """
        Cho Termux thời gian grace dài hơn để chạy lệnh.
        """
        # Sang app khác rồi thì không nên tiếp tục so frozen của Facebook/Zalo
        self._reset_frozen_state()

        if self._last_away_pkg != TERMUX_PKG:
            self._home_since = now
            self._last_away_pkg = TERMUX_PKG
            log_message(
                f"[{self._device_name()}] Watchdog phát hiện đang ở Termux -> bắt đầu grace {TERMUX_GRACE_SECONDS}s",
                logging.INFO
            )
            return

        if self._home_since is not None and (now - self._home_since >= TERMUX_GRACE_SECONDS):
            self._reset_away_timer()
            self._resume_current_phase(reason=f"ở Termux quá {TERMUX_GRACE_SECONDS}s")

    def _is_allowed_temporary_external_package(self, now: float, pkg: str) -> bool:
        try:
            until = float(getattr(self.driver, "_fb_messenger_install_until", 0.0) or 0.0)
            allowed = getattr(self.driver, "_fb_messenger_install_allowed_packages", set()) or set()
        except Exception:
            return False
        return now <= until and pkg in allowed

    def _handle_other_packages(self, now: float, pkg: str):
        """
        Các app khác ngoài Facebook/Zalo/Termux:
        resume nhanh hơn.
        """
        self._reset_frozen_state()

        if self._is_allowed_temporary_external_package(now, pkg):
            self._reset_away_timer()
            return

        if self._last_away_pkg != pkg:
            self._home_since = now
            self._last_away_pkg = pkg
            return

        if self._home_since is not None and (now - self._home_since >= OTHER_APP_GRACE_SECONDS):
            self._reset_away_timer()
            self._resume_current_phase(reason=f"ra app khác '{pkg}' quá {OTHER_APP_GRACE_SECONDS}s")

    def run(self):
        log_message(
            f"[{self._device_name()}] Watchdog Thread bắt đầu chạy song song",
            logging.INFO
        )

        while not self._stop_event.is_set():
            try:
                try:
                    info = self.driver.app_current()
                    pkg = (info or {}).get("package", "") or ""
                except Exception:
                    pkg = ""

                now = time.time()

                # 1) Đang ở app chính
                if pkg in TARGET_PACKAGES:
                    self._handle_target_packages(now, pkg)

                # 2) Đang ở Termux
                elif pkg == TERMUX_PKG:
                    self._handle_termux(now)

                # 3) Đang ở app khác
                else:
                    self._handle_other_packages(now, pkg)

            except Exception as e:
                log_message(
                    f"[{self._device_name()}] Watchdog error: {e}",
                    logging.WARNING
                )

            time.sleep(0.5)

# ======================= LUỒNG THIẾT BỊ =======================
class RestartThisDevice(Exception):
    pass

async def device_once(device_id: str):
    """
    Chạy một vòng đầy đủ cho MỘT thiết bị:
      - Kết nối
      - Tắt auto-rotation để tránh xoay màn hình
      - Bật VPN 1.1.1.1 nếu cần (1 lần)
      - Watchdog chạy nền
      - Pha 'zalo' (một vòng) -> Pha 'facebook'
    """
    # # # ===== PHA Cào zalo =====
    # while not check_zalo_ran_today(device_id):
    #     log_message(f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] đang cào zalo", logging.INFO)
    #     await asyncio.sleep(60)
    restart_event[device_id] = False
    screen_standing_event[device_id] = False
    # Kết nối thiết bị
    driver = await asyncio.to_thread(u2.connect_usb, device_id)
    handler = DeviceHandler(driver, device_id)
    await handler.connect()
    await ensure_facebook_link_handling_allowed(driver, device_id, use_cache=True)

    # Tắt auto-rotate sớm ngay khi ADB đã sẵn sàng, không phụ thuộc Termux/VPN.
    await disable_auto_rotation(driver, device_id)

    async def _rotation_guard_loop():
        while True:
            try:
                await asyncio.sleep(ROTATION_GUARD_INTERVAL)
                await disable_auto_rotation(driver, device_id)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log_message(
                    f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] Rotation guard lỗi: {e}",
                    logging.WARNING,
                )
                await asyncio.sleep(5.0)

    # Mỗi vòng mới đều kiểm tra/bật lại 1.1.1.1 trước khi chạy task
    await ensure_1111_vpn_on(driver, device_id)
    await disable_auto_rotation(driver, device_id)

    # Kiểm tra cài đặt Termux và termux-api
    result = await main_lib.check_termux_api_installed(driver)
    if not result:
        return
    await disable_auto_rotation(driver, device_id)

    #Tắt âm lượng 
    await mute_device_volume(driver, device_id)
    # Trạng thái pha hiện tại để watchdog biết cần resume app nào khi về HOME
    current_phase = {"value": "zalo"}

    # Cờ yêu cầu restart từ watchdog
    restart_event_flag = asyncio.Event()

    def _on_resume(phase: str):
        # Đưa việc mở app sang luồng riêng để không chặn Watchdog
        target = ZALO_PKG if phase == "zalo" else FACEBOOK_PKG
        # Dùng create_task + to_thread để lệnh này chạy ngầm hoàn toàn
        driver.app_start( target)

    async def _on_restart():
        # Dọn app để về trạng thái sạch rồi bật cờ restart
        try:
            await asyncio.to_thread(driver.app_stop, ZALO_PKG) # dừng ứng dụng, trạng thái sẽ là không hoạt động -> android cho về home page
        except Exception:
            pass
        try:
            await asyncio.to_thread(driver.app_stop, FACEBOOK_PKG)
        except Exception:
            pass
        restart_event_flag.set()

    # Watchdog
    watchdog = InactivityWatchdog(
        driver=driver,
        device_id=device_id,
        idle_seconds=60,
        on_resume=_on_resume,
        phase_provider=lambda: current_phase["value"],
    )
    # await watchdog.start()
    watchdog.start()
    rotation_guard_task = asyncio.create_task(
        _rotation_guard_loop(),
        name=f"rotation_guard_{device_id}",
    )
    await pymongo_management.update_device_status(device_id, True)  # Cập nhật thiết bị thành online
    try:
        # ===== PHA FACEBOOK =====
        await ensure_1111_vpn_on(driver, device_id)
        await disable_auto_rotation(driver, device_id)
        current_phase["value"] = "facebook"
        
        # Ưu tiên gửi tin nhắn => cào tin nhắn trước khi vào luồng chính
        current_acc = get_current_account(device_id)
        print(f"[FACEBOOK] current_acc={current_acc}")
        if current_acc and current_acc != "default":
            if fb_runtime.is_messenger_1_1_priority_active(driver, current_acc):
                await fb_runtime.run_messenger_1_1_priority_session(
                    driver,
                    current_acc,
                    restart_event=restart_event,
                    reason="active session before Facebook phase",
                )
                return
            if await fb_runtime.has_messenger_queue_pending(current_acc):
                await fb_runtime.send_messenger_queue_from_facebook_home(
                    driver,
                    current_acc,
                    restart_event=restart_event,
                    reason="queue before Facebook phase",
                )
                return
            await fb_runtime.check_and_crawl_messenger_priority(driver, current_acc)
            if fb_runtime.is_messenger_1_1_priority_active(driver, current_acc):
                return

        # Chạy flow Facebook như thường lệ
        await run_on_device_original(driver, screen_standing_event, restart_event)
        if current_acc and current_acc != "default" and fb_runtime.is_messenger_1_1_priority_active(driver, current_acc):
            await fb_runtime.run_messenger_1_1_priority_session(
                driver,
                current_acc,
                restart_event=restart_event,
                reason="active session after Facebook phase",
            )
            return
        logging.info(
            "[%s] Sau pha Facebook: restart_flag=%s",
            DEVICE_LIST_NAME.get(device_id, device_id),
            restart_event_flag.is_set()
        )

        if restart_event_flag.is_set():
            logging.warning(
                "[%s] Watchdog yêu cầu restart sau Facebook -> bỏ qua Zalo",
                DEVICE_LIST_NAME.get(device_id, device_id),
            )
            raise RestartThisDevice("RESTART_THIS_DEVICE (sau pha Facebook)")

        # ===== PHA ZALO =====
        # await asyncio.to_thread(driver.app_stop, FACEBOOK_PKG)
        # current_phase["value"] = "zalo"
        # logging.info("[%s] BẮT ĐẦU pha Zalo", DEVICE_LIST_NAME.get(device_id, device_id))

        # await asyncio.to_thread(driver.app_start, ZALO_PKG)
        # await asyncio.sleep(2.0)

        # logging.info("[%s] GỌI handler.run(Zalo)", DEVICE_LIST_NAME.get(device_id, device_id))
        # await asyncio.to_thread(handler.run, screen_standing_event)  # khi code zalo la dong bo 
        # # await handler.run(screen_standing_event)                   # khi code zalo la bat dong bo
        # logging.info("[%s] KẾT THÚC pha Zalo", DEVICE_LIST_NAME.get(device_id, device_id))

        # if restart_event_flag.is_set():
        #     raise RestartThisDevice("RESTART_THIS_DEVICE (sau pha Zalo)")
        


    finally:
        rotation_guard_task.cancel()
        try:
            await rotation_guard_task
        except Exception:
            pass
        watchdog.stop()
        try:
            await asyncio.to_thread(watchdog.join, 2.0)
        except Exception:
            pass

async def check_driver(driver):
    try:
        _ = driver.info
        return True
    except Exception:
        return False
    
async def device_supervisor(device_id: str):
    """
    Giám sát riêng từng thiết bị:
      - Giữ nguyên flow chạy của main.py.
      - Nhận urgent task qua RabbitMQ, nhưng KHÔNG execute trong consumer.
      - Consumer chỉ bơm job vào queue RAM của đúng device; fb_task_patched sẽ xử lý.
      - Có heartbeat định kỳ để DB/UI luôn thấy máy còn kết nối.
      - Chỉ offline sau 3 lần lỗi liên tiếp.
    """
    driver = None
    rabbit_task = None
    rabbit_recovery_task = None
    heartbeat_task = None
    statusbar_guard_task = None

    while True:
        try:
            driver = await asyncio.to_thread(u2.connect_usb, device_id)
            await ensure_facebook_link_handling_allowed(driver, device_id, use_cache=True)
            break
        except Exception:
            await asyncio.sleep(2.0)

    crm_device_id = driver.serial

    # Lấy danh sách user_id CRM cho thiết bị này
    mapped_accounts = DEVICE_USERS.get(crm_device_id, [])
    if not mapped_accounts:
        log_message(
            f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] ❌ Không tìm thấy user_id nào tương ứng trong JSON — dùng default rỗng",
            logging.ERROR,
        )

    # Urgent queue dùng chung với fb_task_patched
    event, queue = get_urgent_objects(crm_device_id)
    _ = (event, queue)

    rabbit_task = asyncio.create_task(
        start_device_rabbit_consumer(machine_code=crm_device_id),
        name=f"rabbitmq_{crm_device_id}",
    )
    rabbit_recovery_task = asyncio.create_task(
        start_rabbitmq_recovery_loop(machine_code=crm_device_id),
        name=f"rabbitmq_recovery_{crm_device_id}",
    )
    logging.info("[MAIN] scheduled RabbitMQ consumer for %s", crm_device_id)
    await asyncio.sleep(0)

    heartbeat_task = asyncio.create_task(
        device_status_heartbeat(
            device_id=device_id,
            get_driver=lambda: driver,
            rabbit_task_getter=lambda: rabbit_task,
            interval=HEARTBEAT_SECONDS,
        ),
        name=f"heartbeat_{device_id}",
    )

    statusbar_guard_task = asyncio.create_task(
        statusbar_guard_loop(
            device_id=device_id,
            get_driver=lambda: driver,
            interval=STATUSBAR_GUARD_INTERVAL,
        ),
        name=f"statusbar_guard_{device_id}",
    )

    task = None
    temp_alive = True
    driver_fail_count = 0
    device_status_path = DEVICE_STATUS_PATH(device_id)

    await main_lib.reset_active()

    try:
        while True:
            try:
                # Nếu consumer RabbitMQ bị chết ngoài ý muốn thì dựng lại
                if rabbit_task is None or rabbit_task.done():
                    exc = None
                    try:
                        exc = rabbit_task.exception() if rabbit_task else None
                    except Exception:
                        exc = None
                    log_message(
                        f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] ♻️ RabbitMQ consumer đã dừng{' do lỗi: ' + str(exc) if exc else ''} -> khởi động lại",
                        logging.WARNING,
                    )
                    rabbit_task = asyncio.create_task(
                        start_device_rabbit_consumer(machine_code=crm_device_id),
                        name=f"rabbitmq_{crm_device_id}",
                    )
                if rabbit_recovery_task is None or rabbit_recovery_task.done():
                    exc = None
                    try:
                        exc = rabbit_recovery_task.exception() if rabbit_recovery_task else None
                    except Exception:
                        exc = None
                    log_message(
                        f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] ♻️ RabbitMQ recovery loop đã dừng{' do lỗi: ' + str(exc) if exc else ''} -> khởi động lại",
                        logging.WARNING,
                    )
                    rabbit_recovery_task = asyncio.create_task(
                        start_rabbitmq_recovery_loop(machine_code=crm_device_id),
                        name=f"rabbitmq_recovery_{crm_device_id}",
                    )
                while True:
                    still_alive = await check_driver(driver)

                    if still_alive:
                        if driver_fail_count > 0:
                            log_message(
                                f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] ✅ Driver kết nối lại sau {driver_fail_count} lần lỗi liên tiếp.",
                                logging.INFO,
                            )
                        driver_fail_count = 0

                        if not temp_alive:
                            log_message(
                                f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] ✅ Kết nối thiết bị đã được khôi phục.",
                                logging.INFO
                            )
                            await pymongo_management.update_device_status(
                                device_id,
                                True,
                                extra_fields={
                                    "adb_connected": True,
                                    "rabbit_connected": rabbit_task is not None and not rabbit_task.done(),
                                    "heartbeat_note": "driver_restored",
                                }
                            )
                        temp_alive = True

                    else:
                        driver_fail_count += 1
                        log_message(
                            f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] ⚠️ Driver check lỗi {driver_fail_count}/{DEVICE_OFFLINE_RETRY}",
                            logging.WARNING,
                        )

                        if driver_fail_count < DEVICE_OFFLINE_RETRY:
                            await asyncio.sleep(2.0)
                            continue

                        # Đủ 3 lần lỗi liên tiếp mới tính là mất kết nối
                        if temp_alive:
                            log_message(
                                f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] ❌ Mất kết nối thiết bị sau {DEVICE_OFFLINE_RETRY} lần lỗi liên tiếp.",
                                logging.WARNING
                            )
                            await pymongo_management.update_device_status(
                                device_id,
                                False,
                                extra_fields={
                                    "adb_connected": False,
                                    "rabbit_connected": rabbit_task is not None and not rabbit_task.done(),
                                    "heartbeat_note": "driver_lost_after_retries",
                                }
                            )
                            await close_facebook_online_session(device_id, "driver_lost_after_retries")
                            try:
                                marked = await pymongo_management.mark_group_post_link_retry_device_disconnected(
                                    device_id,
                                    min_age_minutes=15,
                                    max_age_minutes=20,
                                )
                                if marked:
                                    log_message(
                                        f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] Đã cập nhật {marked} command lấy link lỗi do bị rút thiết bị",
                                        logging.WARNING,
                                    )
                            except Exception as mark_err:
                                log_message(
                                    f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] Lỗi cập nhật command lấy link khi rút thiết bị: {mark_err}",
                                    logging.WARNING,
                                )
                                
                        temp_alive = False

                        if task is not None and not task.done():
                            task.cancel()
                            log_message(
                                f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] ❌ Hủy task đang chạy do mất kết nối thiết bị.",
                                logging.WARNING
                            )
                            try:
                                await task
                            except Exception:
                                pass

                        await asyncio.sleep(5.0)

                        try:
                            driver = await asyncio.to_thread(u2.connect_usb, device_id)
                        except Exception as reconnect_err:
                            log_message(
                                f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] ⚠️ Reconnect lỗi: {reconnect_err}",
                                logging.WARNING
                            )
                        continue

                    is_paused = False
                    try:
                        with open(device_status_path, 'r', encoding='utf-8') as f:
                            device_status = json.load(f)
                        if device_status.get('active', False):
                            is_paused = True
                        _STATUS_FILE_CHECK.discard(device_id)
                    except FileNotFoundError:
                        if device_id not in _STATUS_FILE_CHECK:
                            log_message(
                                f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] ✅ Không tìm thấy file status, tiếp tục chạy.",
                                logging.WARNING
                            )
                            _STATUS_FILE_CHECK.add(device_id)
                    except Exception as e:
                        log_message(
                            f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] ❌ Lỗi: {e}, tiếp tục chạy.",
                            logging.WARNING
                        )

                    if is_paused:
                        if task is not None and not task.done():
                            task.cancel()
                            log_message(
                                f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] ⏸️ Phát hiện tạm dừng từ file status, hủy task đang chạy.",
                                logging.WARNING
                            )
                            try:
                                await task
                            except Exception:
                                pass

                        await asyncio.sleep(2)
                        continue
                    else:
                        break

                if task is None or task.done():
                    task = asyncio.create_task(device_once(device_id))

                await asyncio.sleep(2.0)

            except RestartThisDevice:
                log_message(
                    f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] ↻ Watchdog yêu cầu RESTART — khởi động lại quy trình cho máy này.",
                    logging.WARNING
                )
                restart_event[device_id] = False
                await asyncio.sleep(2.0)
                continue

            except asyncio.CancelledError:
                raise

            except Exception as e:
                if driver is not None and await check_driver(driver):
                    log_message(
                        f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] Lỗi không mong muốn: {e}. Sẽ thử chạy lại sau.",
                        logging.ERROR
                    )
                await asyncio.sleep(5.0)
                continue

    finally:
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except Exception:
                pass

        if rabbit_task is not None and not rabbit_task.done():
            rabbit_task.cancel()
            try:
                await rabbit_task
            except Exception:
                pass

        if rabbit_recovery_task is not None and not rabbit_recovery_task.done():
            rabbit_recovery_task.cancel()
            try:
                await rabbit_recovery_task
            except Exception:
                pass

        if heartbeat_task is not None and not heartbeat_task.done():
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except Exception:
                pass

        if statusbar_guard_task is not None and not statusbar_guard_task.done():
            statusbar_guard_task.cancel()
            try:
                await statusbar_guard_task
            except Exception:
                pass
            
async def run_all_devices():
    """
    Theo dõi thay đổi thiết bị ADB — thêm/bớt task tương ứng.
    Chỉ remove/offline khi thiết bị mất 3 vòng scan liên tiếp.
    """
    global DEVICE_LIST, active_tasks, _ADB_MISS_COUNT

    while True:
        new_list = scan_connected_devices()

        # reset miss count cho thiết bị đang còn nhìn thấy
        for device_id in new_list:
            _ADB_MISS_COUNT[device_id] = 0

        # added thật sự
        added = [d for d in new_list if d not in DEVICE_LIST]

        # thiết bị cũ nhưng đang không thấy ở vòng scan này
        maybe_removed = [d for d in DEVICE_LIST if d not in new_list]
        removed = []

        for device_id in maybe_removed:
            _ADB_MISS_COUNT[device_id] = _ADB_MISS_COUNT.get(device_id, 0) + 1
            log_message(
                f"⚠️ Thiết bị [{device_id}] không thấy ở adb scan {_ADB_MISS_COUNT[device_id]}/{ADB_REMOVE_RETRY}",
                logging.WARNING
            )
            if _ADB_MISS_COUNT[device_id] >= ADB_REMOVE_RETRY:
                removed.append(device_id)

        # Xử lý thiết bị mới
        for device_id in added:
            log_message(f"📱 Thiết bị [{device_id}] đã được kết nối và sẵn sàng")
            DEVICE_LIST.append(device_id)
            _ADB_MISS_COUNT[device_id] = 0
            DEVICE_LIST_NAME.setdefault(device_id, f"Máy {device_id}")

            task = asyncio.create_task(device_supervisor(device_id))
            active_tasks[device_id] = task

            try:
                device_name = await asyncio.wait_for(
                    pymongo_management.get_device_name_by_id(device_id),
                    timeout=5.0
                )
                DEVICE_LIST_NAME[device_id] = f"Máy {device_name}"
                log_message(f"📱 Thiết bị [{device_id}] có tên là: Máy {device_name}")
            except asyncio.TimeoutError:
                log_message(
                    f"⚠️ Quá thời gian lấy tên thiết bị [{device_id}] -> Dùng ID mặc định",
                    logging.WARNING
                )
                DEVICE_LIST_NAME[device_id] = f"Máy {device_id}"

        # Xử lý thiết bị bị ngắt kết nối thật sự
        for device_id in removed:
            log_message(f"❌ Thiết bị [{device_id}] đã bị ngắt kết nối sau {ADB_REMOVE_RETRY} vòng scan liên tiếp")
            if device_id in DEVICE_LIST:
                DEVICE_LIST.remove(device_id)

            DEVICE_LIST_NAME.pop(device_id, None)
            _ADB_MISS_COUNT.pop(device_id, None)

            if device_id in active_tasks:
                active_tasks[device_id].cancel()
                del active_tasks[device_id]

            await pymongo_management.update_device_status(
                device_id,
                False,
                extra_fields={
                    "adb_connected": False,
                    "heartbeat_note": "removed_after_adb_scan_retries",
                }
            )
            await close_facebook_online_session(device_id, "removed_after_adb_scan_retries")

        log_message(f"📋 Danh sách thiết bị hiện tại: {DEVICE_LIST_NAME}")

        await asyncio.sleep(10)

async def main():
    # Chạy song song:
    #   - run_all_devices(): quản lý device + supervisor
    #   - global_urgent_scheduler(): bơm job từ file vào từng device
    await asyncio.gather(
        run_all_devices(),
        global_urgent_scheduler(),
        passive_wait_schedule_loop(),
        start_control_rabbit_consumer(),
        start_rabbitmq_result_retry_loop(),
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("[!] Dừng bằng bàn phím (KeyboardInterrupt)")
        asyncio.run(pymongo_management.update_device_status(None, False))
    except Exception as e:
        log_message(f"Lỗi chạy chính: {e}", logging.ERROR)
        asyncio.run(pymongo_management.update_device_status(None, False))
