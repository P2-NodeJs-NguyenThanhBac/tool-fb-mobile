import asyncio
import base64
import contextlib
import html
import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

try:
    from module.android_automation_common import (
        is_package_installed_adb,
        normalize_text,
        normalize_ui_text,
        parse_bounds as common_parse_bounds,
        resolve_launch_activity_adb,
        safe_str as common_safe_str,
    )
except Exception:
    from android_automation_common import (
        is_package_installed_adb,
        normalize_text,
        normalize_ui_text,
        parse_bounds as common_parse_bounds,
        resolve_launch_activity_adb,
        safe_str as common_safe_str,
    )

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
_ORIGINAL_SYS_PATH = list(sys.path)
sys.path = [
    item
    for item in sys.path
    if item and Path(item).resolve() != PROJECT_ROOT
]
try:
    import socketio
finally:
    sys.path = _ORIGINAL_SYS_PATH

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_DB_FILE = Path(os.getenv("FB_1_1_MEMORY_CACHE_NAME", "__memory_chat_history__.json"))
DEFAULT_CRM_SENDER_URL = os.getenv("FB_1_1_CRM_SENDER_URL", "http://123.24.206.25:8024")
DEFAULT_CRM_RECEIVER_URL = os.getenv("FB_1_1_CRM_RECEIVER_URL", "http://123.24.206.25:8023")
MESSENGER_PKG = "com.facebook.orca"
FACEBOOK_PKG = "com.facebook.katana"
PLAY_STORE_PKG = "com.android.vending"
MESSENGER_MARKET_URI = "market://details?id=com.facebook.orca"
MESSENGER_WEB_URI = "https://play.google.com/store/apps/details?id=com.facebook.orca"
DEFAULT_INSTALL_WAIT_SECONDS = int(os.getenv("FB_1_1_INSTALL_WAIT_SECONDS", "180"))
DEFAULT_UI_CRAWL_ENABLED = os.getenv("FB_1_1_UI_CRAWL_ENABLED", "1").lower() not in {"0", "false", "no"}
DEFAULT_ACTIVE_ACCOUNT_CACHE_SECONDS = int(os.getenv("FB_1_1_ACTIVE_ACCOUNT_CACHE_SECONDS", "21600"))
DEFAULT_FORCE_PROFILE_IDENTIFY_ON_FIRST_RUN = os.getenv("FB_1_1_FORCE_PROFILE_IDENTIFY_ON_FIRST_RUN", "1").lower() not in {"0", "false", "no"}
DEFAULT_USE_ACTIVE_ACCOUNT_FILE_CACHE = os.getenv("FB_1_1_USE_ACTIVE_ACCOUNT_FILE_CACHE", "0").lower() in {"1", "true", "yes", "on"}
DEFAULT_LOCAL_MESSAGE_LIMIT = int(os.getenv("FB_1_1_LOCAL_MESSAGE_LIMIT", "20"))
_SESSION_ACTIVE_ACCOUNT_CACHE: dict[str, Dict[str, str]] = {}


def _safe_str(value: Any) -> str:
    return common_safe_str(value)

class MobileOneToOneMessenger:
    """
    Mobile Messenger bot for CRM 1-1 chat commands.

    This module uses the platform-tools/adb.exe bundled in tool-fb-mobile and
    listens for send_message_command from fb_1_1_api_sender.py.
    """

    def __init__(
        self,
        db_file: str | os.PathLike[str] = DEFAULT_DB_FILE,
        user_id_chat: str = "default",
        facebook_username: str = "default",
        facebook_password: str = "default",
        crm_sender_url: str = DEFAULT_CRM_SENDER_URL,
        crm_receiver_url: str = DEFAULT_CRM_RECEIVER_URL,
    ):
        self.db_file = Path(db_file)
        self.use_file_chat_cache = os.getenv("FB_1_1_USE_DB_TIN_NHAN_CACHE", "0").lower() in {"1", "true", "yes", "on"}
        self.user_id_chat = user_id_chat
        self.facebook_username = facebook_username
        self.facebook_password = facebook_password
        self.crm_sender_url = crm_sender_url.rstrip("/")
        self.crm_receiver_url = crm_receiver_url.rstrip("/")
        self.chat_history_db: Dict[str, Any] = {}
        self.logged_chat_ids = set()
        self.logged_message_keys = set()
        self.active_device: Optional[str] = None
        self.connected_devices: list[str] = []
        self.active_device_name = ""
        self.device_accounts: list[str] = []
        self.device_account_names: Dict[str, str] = {}
        self.device_account_device_ids: Dict[str, str] = {}
        self.device_account_device_names: Dict[str, str] = {}
        self.device_account_passwords: Dict[str, str] = {}
        self.adb_path = self._resolve_adb_path()
        self.device_lock = threading.Lock()
        self.ui_driver = None
        self.messenger_checked = False
        self.current_facebook_account_snapshot: Dict[str, str] = {}
        self.active_account_verified = False
        self.message_requests_crawled_once = False
        self._unread_scan_in_progress = False
        self.messenger_pin_attempt_index = 0
        self.ui_crawl_enabled = DEFAULT_UI_CRAWL_ENABLED
        self.socket_registered = False
        self._socket_heartbeat_started = False
        self._socket_transport_warning_printed = False
        self._priority_current_thread_scan_started = False
        self.sio = socketio.Client(logger=False, engineio_logger=False)
        self.setup_socket_events()
        self.connect_to_device()
        self.sync_account_from_device()

    def _normalize_ui_text(self, value: str) -> str:
        return normalize_ui_text(value)

    def _normalize_exact_ui_text(self, value: str) -> str:
        return normalize_text(value, remove_accents=False)

    def _contains_vietnamese_diacritic(self, value: str) -> bool:
        text = _safe_str(value)
        if any(ch in text for ch in "đĐ"):
            return True
        return any(unicodedata.category(ch) == "Mn" for ch in unicodedata.normalize("NFD", text))

    def _strict_accent_name_search_enabled(self, participant_name: str) -> bool:
        if not self._contains_vietnamese_diacritic(participant_name):
            return False
        return os.getenv("FB_1_1_ALLOW_UNACCENTED_NAME_FALLBACK", "0").lower() not in {"1", "true", "yes", "on"}

    def _accent_sensitive_name_match(self, participant_name: str, label: str) -> bool:
        target = self._normalize_exact_ui_text(participant_name)
        candidate = self._normalize_exact_ui_text(label)
        if not target or not candidate:
            return False
        if target == candidate or target in candidate:
            return True
        tokens = [token for token in target.split() if len(token) >= 2]
        return bool(tokens and all(token in candidate for token in tokens[: min(3, len(tokens))]))

    def _parse_bounds(self, bounds: str):
        return common_parse_bounds(bounds)

    def _tap_bounds_center(self, bounds: str, reason: str = "") -> bool:
        parsed = self._parse_bounds(bounds)
        if not parsed:
            return False

        x1, y1, x2, y2 = parsed
        x = (x1 + x2) // 2
        y = (y1 + y2) // 2

        print(f"[MOBILE] ADB tap at ({x}, {y}) for {reason}, bounds={bounds}")
        self.run_adb(["shell", "input", "tap", str(x), str(y)])
        time.sleep(2)
        return True

    def _tap_bounds_relative(self, bounds: str, x_ratio: float, y_ratio: float, reason: str = "") -> bool:
        parsed = self._parse_bounds(bounds)
        if not parsed:
            return False

        x1, y1, x2, y2 = parsed
        x = int(x1 + max(0.0, min(1.0, x_ratio)) * (x2 - x1))
        y = int(y1 + max(0.0, min(1.0, y_ratio)) * (y2 - y1))

        print(f"[MOBILE] ADB tap at ({x}, {y}) for {reason}, bounds={bounds}")
        self.run_adb(["shell", "input", "tap", str(x), str(y)])
        time.sleep(2)
        return True
    
    def _resolve_adb_path(self) -> str:
        candidates = [
            os.getenv("ADB_PATH"),
            shutil.which("adb"),
            str(PROJECT_ROOT / "platform-tools" / "adb.exe"),
        ]
        for candidate in candidates:
            if candidate and Path(candidate).exists():
                return str(candidate)
        return "adb"

    def run_adb(self, command: str | list[str] | tuple[str, ...], device_id: Optional[str] = None) -> str:
        prefix = [self.adb_path]
        target_device = device_id or self.active_device
        if target_device:
            prefix += ["-s", target_device]

        if isinstance(command, (list, tuple)):
            full_cmd = prefix + list(command)
        else:
            full_cmd = prefix + shlex.split(command)

        try:
            result = subprocess.run(full_cmd, capture_output=True, text=True, timeout=15)
            if result.returncode != 0:
                stderr = _safe_str(result.stderr)
                if stderr:
                    print(f"[ADB] Command failed ({result.returncode}): {stderr}")
            return _safe_str(result.stdout)
        except FileNotFoundError:
            print(f"[ADB] adb not found at '{self.adb_path}'. Set ADB_PATH or keep platform-tools/adb.exe in this folder.")
            return ""
        except Exception as exc:
            print(f"[ADB] Execution error: {exc}")
            return ""

    def _run_adb_raw(
        self,
        command: list[str] | tuple[str, ...],
        *,
        timeout: float = 15.0,
        binary_stdout: bool = False,
        log_errors: bool = False,
    ) -> str | bytes:
        prefix = [self.adb_path]
        if self.active_device:
            prefix += ["-s", self.active_device]
        full_cmd = prefix + list(command)
        try:
            result = subprocess.run(
                full_cmd,
                capture_output=True,
                text=not binary_stdout,
                timeout=max(1.0, float(timeout)),
            )
            if log_errors and result.returncode != 0:
                stderr = result.stderr if not binary_stdout else result.stderr.decode("utf-8", "replace")
                if _safe_str(stderr):
                    print(f"[ADB] Command failed ({result.returncode}): {_safe_str(stderr)}")
            return result.stdout or (b"" if binary_stdout else "")
        except Exception as exc:
            if log_errors:
                print(f"[ADB] Raw execution error: {exc}")
            return b"" if binary_stdout else ""

    def _uiautomator_recently_timed_out(self) -> bool:
        return time.time() < float(getattr(self, "_uiautomator_dump_timed_out_until", 0.0) or 0.0)

    def _uiautomator_call_recently_timed_out(self) -> bool:
        return time.time() < float(getattr(self, "_uiautomator_call_timed_out_until", 0.0) or 0.0)

    def _dump_hierarchy_via_uiautomator2(self, driver, *, reason: str = "", timeout: Optional[float] = None) -> str:
        driver = driver or self.connect_ui_driver()
        if driver is None or self._uiautomator_recently_timed_out():
            return ""

        timeout = float(timeout or os.getenv("FB_1_1_DUMP_HIERARCHY_TIMEOUT_SECONDS", "4"))
        done = threading.Event()
        result: dict[str, Any] = {}

        def worker() -> None:
            try:
                result["xml"] = _safe_str(driver.dump_hierarchy())
            except Exception as exc:
                result["exc"] = exc
            finally:
                done.set()

        thread = threading.Thread(target=worker, name="messenger-dump-hierarchy", daemon=True)
        thread.start()
        if not done.wait(max(0.5, timeout)):
            self._uiautomator_dump_timed_out_until = time.time() + 45
            print(f"[WINDOW_DUMP] driver.dump_hierarchy timed out after {timeout:.1f}s; using ADB dump fallback. reason={reason}")
            return ""

        if result.get("exc") is not None:
            print(f"[WINDOW_DUMP] driver.dump_hierarchy failed ({reason}): {result['exc']}")
            return ""
        return _safe_str(result.get("xml"))

    def _dump_hierarchy_via_adb(self, *, reason: str = "") -> str:
        remote_paths = [
            "/data/local/tmp/window_dump.xml",
            "/sdcard/window_dump.xml",
            "/storage/emulated/0/window_dump.xml",
        ]
        local_path = self._window_dump_local_path()
        local_path.parent.mkdir(parents=True, exist_ok=True)

        for remote_path in remote_paths:
            self._run_adb_raw(["shell", "rm", "-f", remote_path], timeout=5)
            for compressed in (False, True):
                dump_cmd = ["shell", "uiautomator", "dump"]
                if compressed:
                    dump_cmd.append("--compressed")
                dump_cmd.append(remote_path)
                output = _safe_str(self._run_adb_raw(dump_cmd, timeout=18))
                if output:
                    mode = "compressed" if compressed else "normal"
                    print(f"[WINDOW_DUMP] {reason}: adb uiautomator {mode} -> {remote_path}: {output}")

                raw = self._run_adb_raw(["exec-out", "cat", remote_path], timeout=8, binary_stdout=True)
                xml = raw.decode("utf-8", "replace").strip() if isinstance(raw, bytes) else _safe_str(raw)
                if not xml or "<node" not in xml:
                    continue
                try:
                    ET.fromstring(xml)
                except Exception:
                    continue
                with contextlib.suppress(Exception):
                    local_path.write_text(xml, encoding="utf-8")
                return xml

        print(f"[WINDOW_DUMP] ADB dump fallback failed. reason={reason}")
        return ""

    def _dump_hierarchy_xml(self, driver=None, *, reason: str = "", timeout: Optional[float] = None) -> str:
        xml = self._dump_hierarchy_via_uiautomator2(driver, reason=reason, timeout=timeout)
        if xml:
            return xml
        return self._dump_hierarchy_via_adb(reason=reason)

    def _call_uiautomator_with_timeout(self, func, *, reason: str = "", timeout: Optional[float] = None):
        timeout = float(timeout or os.getenv("FB_1_1_UIAUTOMATOR_CALL_TIMEOUT_SECONDS", "8"))
        done = threading.Event()
        result: dict[str, Any] = {}

        def worker() -> None:
            try:
                result["value"] = func()
            except Exception as exc:
                result["exc"] = exc
            finally:
                done.set()

        thread = threading.Thread(target=worker, name="messenger-uia-call", daemon=True)
        thread.start()
        if not done.wait(max(0.5, timeout)):
            self._uiautomator_call_timed_out_until = time.time() + 45
            print(f"[UI] uiautomator2 call timed out after {timeout:.1f}s; reason={reason}")
            return False, None
        if result.get("exc") is not None:
            print(f"[UI] uiautomator2 call failed ({reason}): {result['exc']}")
            return False, None
        return True, result.get("value")

    def _adb_keyboard_ime_id(self) -> str:
        return os.getenv("FB_1_1_ADB_KEYBOARD_IME", "com.android.adbkeyboard/.AdbIME")

    def _current_input_method(self) -> str:
        return _safe_str(self.run_adb(["shell", "settings", "get", "secure", "default_input_method"]))

    def _input_text_via_current_ime(self, text: str, *, reason: str = "", verify=None) -> bool:
        value = _safe_str(text)
        if not value:
            self._last_send_failure_reason = f"Current IME input skipped empty text for {reason or 'focused field'}"
            return False
        if os.getenv("FB_1_1_DISABLE_CURRENT_IME_INPUT", "0").lower() in {"1", "true", "yes", "on"}:
            self._last_send_failure_reason = "Current IME input is disabled by FB_1_1_DISABLE_CURRENT_IME_INPUT"
            return False

        current_ime = self._current_input_method()
        if not current_ime:
            print(f"[IME] Current input method is unknown; cannot input text for {reason or 'focused field'}.")
            self._last_send_failure_reason = f"Current input method is unknown while inputting {reason or 'focused field'}"
            return False

        encoded = base64.b64encode(value.encode("utf-8")).decode("ascii")
        attempts = (
            ("ADB_KEYBOARD_INPUT_TEXT", "text", encoded),
            ("ADB_INPUT_B64", "msg", encoded),
            ("ADB_INPUT_B64", "text", encoded),
            ("ADB_INPUT_TEXT", "msg", value),
            ("ADB_INPUT_TEXT", "text", value),
        )
        last_output = ""
        for action, extra_name, extra_value in attempts:
            output = self.run_adb(["shell", "am", "broadcast", "-a", action, "--es", extra_name, extra_value])
            last_output = output
            if "Broadcast completed" in output or "result=" in output:
                print(
                    f"[IME] Sent text input broadcast {action}/{extra_name} "
                    f"for {reason or 'focused field'}: {current_ime}"
                )
                time.sleep(float(os.getenv("FB_1_1_IME_INPUT_SETTLE_SECONDS", "0.25")))
                if verify is None or bool(verify()):
                    return True
                print(f"[IME] Broadcast {action}/{extra_name} did not update {reason or 'focused field'}; trying next input variant.")

        print(f"[IME] Current IME broadcast failed for {reason or 'focused field'}: current={current_ime} output={last_output}")
        self._last_send_failure_reason = (
            f"Current IME broadcast failed for {reason or 'focused field'} "
            f"(current={current_ime})"
        )
        return False

    def is_package_installed(self, package_name: str) -> bool:
        return is_package_installed_adb(self.run_adb, package_name)

    def resolve_launch_activity(self, package_name: str) -> str:
        return resolve_launch_activity_adb(self.run_adb, package_name)

    def connect_ui_driver(self):
        if self.ui_driver is not None:
            return self.ui_driver
        if not self.active_device and not self.connect_to_device():
            return None

        try:
            import uiautomator2 as u2

            self.ui_driver = u2.connect_usb(self.active_device)
            return self.ui_driver
        except Exception as exc:
            print(f"[UI] Cannot connect uiautomator2 to {self.active_device}: {exc}")
            return None

    def open_play_store_for_messenger(self) -> None:
        print("[MOBILE] Messenger is not installed. Opening Play Store page...")
        self.run_adb(
            [
                "shell",
                "am",
                "start",
                "-a",
                "android.intent.action.VIEW",
                "-d",
                MESSENGER_MARKET_URI,
                PLAY_STORE_PKG,
            ]
        )
        time.sleep(4)
        if not self.is_current_package(PLAY_STORE_PKG):
            self.run_adb(
                [
                    "shell",
                    "am",
                    "start",
                    "-a",
                    "android.intent.action.VIEW",
                    "-d",
                    MESSENGER_WEB_URI,
                ]
            )

    def _dump_visible_ui_labels(self, driver, limit: int = 20) -> list[str]:
        labels: list[str] = []
        try:
            root = ET.fromstring(driver.dump_hierarchy())
        except Exception:
            root = None

        if root is not None:
            seen = set()
            for node in root.iter("node"):
                label = self._node_label(node)
                if not label or label in seen:
                    continue
                seen.add(label)
                labels.append(label)
                if len(labels) >= limit:
                    break

        if labels:
            return labels

        # Fallback to adb-generated window_dump.xml when uiautomator2 returns
        # an empty or stale hierarchy.
        return self._window_dump_labels(reason="dump visible labels")[:limit]

    def _click_ui_text(self, driver, text_options: list[str]) -> bool:
        normalized_options = [self._normalize_ui_text(text) for text in text_options]

        # Cách 1: thử click bằng uiautomator2 trước
        for text in text_options:
            try:
                element = driver(text=text)
                if element.exists:
                    print(f"[MOBILE] UIAutomator click by text: {text}")
                    element.click()
                    time.sleep(2)
                    return True
            except Exception as exc:
                print(f"[MOBILE] Click by text failed for {text}: {exc}")

            try:
                element = driver(description=text)
                if element.exists:
                    print(f"[MOBILE] UIAutomator click by description: {text}")
                    element.click()
                    time.sleep(2)
                    return True
            except Exception as exc:
                print(f"[MOBILE] Click by description failed for {text}: {exc}")

        # Cách 2: fallback chắc hơn - dump XML, normalize tiếng Việt, lấy bounds rồi ADB tap
        try:
            xml = driver.dump_hierarchy()
            root = ET.fromstring(xml)
        except Exception as exc:
            print(f"[MOBILE] Cannot dump UI hierarchy for fallback tap: {exc}")
            return self._click_window_dump_text(text_options, allow_contains=True, reason="_click_ui_text fallback")

        candidates = []

        for node in root.iter("node"):
            text = _safe_str(node.attrib.get("text"))
            desc = _safe_str(node.attrib.get("content-desc"))
            label = desc or text

            if not label:
                continue

            normalized_label = self._normalize_ui_text(label)

            matched = any(
                option == normalized_label or option in normalized_label
                for option in normalized_options
            )

            if not matched:
                continue

            bounds = _safe_str(node.attrib.get("bounds"))
            if not bounds:
                continue

            candidates.append({
                "label": label,
                "normalized_label": normalized_label,
                "bounds": bounds,
                "clickable": node.attrib.get("clickable"),
                "class": node.attrib.get("class"),
            })

        if not candidates:
            return self._click_window_dump_text(text_options, allow_contains=True, reason="_click_ui_text no hierarchy candidate")

        # Ưu tiên đúng nút Cài đặt / Install / Update trước
        priority_keywords = [
            self._normalize_ui_text("Cài đặt"),
            self._normalize_ui_text("Install"),
            self._normalize_ui_text("Cập nhật"),
            self._normalize_ui_text("Update"),
        ]

        candidates.sort(
            key=lambda item: (
                0 if any(keyword == item["normalized_label"] for keyword in priority_keywords) else 1,
                0 if item["clickable"] == "true" else 1,
            )
        )

        target = candidates[0]
        print(
            "[MOBILE] Fallback matched UI node: "
            f"label={target['label']} | bounds={target['bounds']} | "
            f"clickable={target['clickable']} | class={target['class']}"
        )

        return self._tap_bounds_center(target["bounds"], reason=target["label"])

    def _find_best_ui_text_node(
        self,
        driver,
        text_options: list[str],
        *,
        allow_contains: bool = True,
        reason: str = "",
    ) -> Optional[Dict[str, Any]]:
        """Find the most likely visible UI node for the given labels.

        This is stricter than _click_ui_text because some Facebook screens contain
        both the generic label "Tin nhắn" and the real CTA "MỞ MESSENGER".
        For those screens we need to rank the CTA above generic labels.
        """
        normalized_options = [self._normalize_ui_text(text) for text in text_options if _safe_str(text)]
        if not normalized_options:
            return None

        try:
            xml = driver.dump_hierarchy()
            root = ET.fromstring(xml)
        except Exception as exc:
            print(f"[MOBILE] Cannot dump UI hierarchy for ranked click ({reason}): {exc}")
            print(f"[WINDOW_DUMP] Falling back to adb uiautomator dump for ranked click: {reason}")
            return self._find_best_window_dump_text_node(
                text_options,
                allow_contains=allow_contains,
                reason=reason,
            )

        priority_terms = [
            self._normalize_ui_text("MỞ MESSENGER"),
            self._normalize_ui_text("Mở Messenger"),
            self._normalize_ui_text("Open Messenger"),
            self._normalize_ui_text("OPEN MESSENGER"),
            self._normalize_ui_text("Mở ứng dụng Messenger"),
            self._normalize_ui_text("Open in Messenger"),
        ]
        weak_terms = [
            self._normalize_ui_text("Tin nhắn"),
            self._normalize_ui_text("Messages"),
            self._normalize_ui_text("Chats"),
        ]

        candidates: list[Dict[str, Any]] = []
        for node in root.iter("node"):
            label = self._node_label(node)
            if not label:
                continue

            normalized_label = self._normalize_ui_text(label)
            exact_match = any(option == normalized_label for option in normalized_options)
            contains_match = allow_contains and any(option in normalized_label for option in normalized_options)
            if not exact_match and not contains_match:
                continue

            bounds = _safe_str(node.attrib.get("bounds"))
            if not bounds:
                continue

            parsed_bounds = self._parse_bounds(bounds)
            area = 0
            y1 = 0
            if parsed_bounds:
                x1, y1, x2, y2 = parsed_bounds
                area = (x2 - x1) * (y2 - y1)

            class_name = _safe_str(node.attrib.get("class"))
            clickable = node.attrib.get("clickable") == "true"
            enabled = node.attrib.get("enabled", "true") != "false"

            score = 0
            if exact_match:
                score += 100
            if contains_match:
                score += 30
            if clickable:
                score += 35
            if enabled:
                score += 10
            if "button" in class_name.lower():
                score += 25
            if any(term and term in normalized_label for term in priority_terms):
                score += 250
            if any(term and term == normalized_label for term in weak_terms):
                score -= 80
            score += min(area // 10000, 20)
            score += min(y1 // 300, 8)

            candidates.append({
                "label": label,
                "normalized_label": normalized_label,
                "bounds": bounds,
                "clickable": node.attrib.get("clickable"),
                "enabled": node.attrib.get("enabled"),
                "class": class_name,
                "score": score,
            })

        if not candidates:
            print(f"[WINDOW_DUMP] UI hierarchy did not find target for {reason}. Trying fresh window_dump.xml.")
            return self._find_best_window_dump_text_node(
                text_options,
                allow_contains=allow_contains,
                reason=reason,
            )

        candidates.sort(key=lambda item: item["score"], reverse=True)
        return candidates[0]

    def _click_best_ui_text(
        self,
        driver,
        text_options: list[str],
        *,
        allow_contains: bool = True,
        reason: str = "",
    ) -> bool:
        target = self._find_best_ui_text_node(
            driver,
            text_options,
            allow_contains=allow_contains,
            reason=reason,
        )
        if not target:
            return False

        print(
            "[MOBILE] Ranked UI tap: "
            f"label={target['label']} | bounds={target['bounds']} | "
            f"clickable={target['clickable']} | class={target['class']} | "
            f"score={target['score']} | reason={reason}"
        )
        return self._tap_bounds_center(target["bounds"], reason=reason or target["label"])

    def click_messenger_entrypoint_if_visible(self, driver=None) -> bool:
        driver = driver or self.connect_ui_driver()
        if driver is None:
            return False

        entrypoint_texts = [
            "MỞ MESSENGER",
            "Mở Messenger",
            "Mở ứng dụng Messenger",
            "Open Messenger",
            "OPEN MESSENGER",
            "Open in Messenger",
            "Go to Messenger",
            "Use Messenger",
            "Dùng Messenger",
            "Nhắn tin",
            "Message",
            "Messages",
            "Tiếp tục",
            "Tiếp tục với",
            "Tiếp tục dưới tên",
            "Continue",
            "Continue with",
                ["Tiếp tục", "Continue", "Tiếp tục dưới tên", "Continue as"],
        ]

        if self._click_best_ui_text(
            driver,
            entrypoint_texts,
            allow_contains=True,
            reason="Messenger entrypoint/continue",
        ):
            time.sleep(4)
            return True
        return False

    def click_required_messenger_prompt_if_visible(self, driver=None) -> bool:
        driver = driver or self.connect_ui_driver()
        if driver is None:
            return False

        try:
            root = ET.fromstring(driver.dump_hierarchy())
        except Exception:
            return False

        visible_blob = self._normalize_ui_text(" | ".join(self._node_label(node) for node in root.iter("node")))
        if not visible_blob:
            return False

        positive_prompt_terms = [
            "Lưu thông tin đăng nhập",
            "Save login info",
            "gửi thông báo",
            "send notifications",
            "notifications",
            "allow notification",
        ]
        skip_prompt_terms = [
            "đồng bộ danh bạ",
            "tải danh bạ lên",
            "sync contacts",
            "upload contacts",
            "find contacts",
            "end-to-end encryption",
            "mã hóa đầu cuối",
            "e2ee",
            "secure storage",
        ]

        if any(self._normalize_ui_text(term) in visible_blob for term in skip_prompt_terms):
            if self._click_best_ui_text(
                driver,
                [
                    "Bỏ qua",
                    "Skip",
                    "Không phải bây giờ",
                    "Not now",
                    "Lúc khác",
                    "Để sau",
                    "Cancel",
                    "Dismiss",
                ],
                allow_contains=True,
                reason="Messenger onboarding skip",
            ):
                time.sleep(3)
                return True

        if not any(self._normalize_ui_text(term) in visible_blob for term in positive_prompt_terms):
            return False

        preferred_actions = [
            "Cho phép",
            "Allow",
            "Allow notifications",
            "Cho phép Messenger gửi thông báo",
            "Lưu",
            "Save",
            "Save login info",
            "Lưu thông tin đăng nhập",
            "Lưu thông tin",
            "Tiếp tục",
            "Continue",
        ]

        blocked_actions = [
            self._normalize_ui_text("Không cho phép"),
            self._normalize_ui_text("Don't allow"),
            self._normalize_ui_text("Deny"),
            self._normalize_ui_text("Lúc khác"),
            self._normalize_ui_text("Not now"),
            self._normalize_ui_text("Skip"),
            self._normalize_ui_text("Bỏ qua"),
        ]

        normalized_actions = [self._normalize_ui_text(text) for text in preferred_actions]
        candidates = []
        for node in root.iter("node"):
            label = self._node_label(node)
            if not label:
                continue
            normalized_label = self._normalize_ui_text(label)
            if any(blocked and blocked in normalized_label for blocked in blocked_actions):
                continue
            if not any(action and (action == normalized_label or action in normalized_label) for action in normalized_actions):
                continue

            bounds = _safe_str(node.attrib.get("bounds"))
            if not bounds:
                continue
            candidates.append({
                "label": label,
                "bounds": bounds,
                "clickable": node.attrib.get("clickable"),
                "score": (
                    50
                    + (20 if node.attrib.get("clickable") == "true" else 0)
                    + (30 if normalized_label in {self._normalize_ui_text("Cho phép"), self._normalize_ui_text("Allow"), self._normalize_ui_text("Lưu"), self._normalize_ui_text("Save")} else 0)
                ),
            })

        if not candidates:
            return False

        candidates.sort(key=lambda item: item["score"], reverse=True)
        target = candidates[0]
        print(f"[LOGIN] Clicking required Messenger prompt: {target['label']} | bounds={target['bounds']}")
        return self._tap_bounds_center(target["bounds"], reason=f"Messenger prompt: {target['label']}")

    def _adb_type_pin_digits(self, pin_value: str, *, device=None, reason: str = "") -> None:
        """Type numeric PIN reliably on Messenger's custom PIN keypad.

        `adb shell input text 000000` is unreliable on this screen because the
        focused object can be a React Native/ViewGroup wrapper instead of a real
        EditText. Numeric keyevents are handled more consistently by Android.
        """
        digits = "".join(ch for ch in _safe_str(pin_value) if ch.isdigit())
        runner = device.run_adb if device is not None and hasattr(device, "run_adb") else self.run_adb

        # Clear any partial stale input first. KEYCODE_DEL = 67.
        for _ in range(8):
            runner(["shell", "input", "keyevent", "67"])
            time.sleep(0.03)

        keycode_by_digit = {
            "0": "7", "1": "8", "2": "9", "3": "10", "4": "11",
            "5": "12", "6": "13", "7": "14", "8": "15", "9": "16",
        }
        print(f"[LOGIN] Typing PIN by keyevents for {reason}: {'*' * len(digits)}")
        for digit in digits:
            runner(["shell", "input", "keyevent", keycode_by_digit[digit]])
            time.sleep(0.12)

    def _focus_dump_first_pin_field(self, device, *, reason: str = "") -> bool:
        """Focus the actual Messenger PIN field from fresh window_dump nodes.

        Do not use click_text() here because click_best() may promote the match
        to a clickable parent ViewGroup; that parent can receive the tap but not
        keyboard focus. We tap the smallest/topmost matching PIN node directly.
        """
        try:
            nodes = device.read_nodes(reason=f"{reason} read PIN field nodes")
        except Exception as exc:
            print(f"[LOGIN] Cannot read dump nodes for PIN focus: {exc}")
            nodes = []

        candidates = []
        for node in nodes:
            raw = " | ".join([
                getattr(node, "text", ""),
                getattr(node, "desc", ""),
                getattr(node, "resource_id", ""),
                getattr(node, "class_name", ""),
            ])
            norm = self._normalize_ui_text(raw)
            if not norm:
                continue
            if not (
                "truong ma khoa" in norm
                or "chinh sua 1/6" in norm
                or "1/6 chu so" in norm
                or "android.widget.edittext" in norm
            ):
                continue
            bounds = getattr(node, "bounds", None)
            if not bounds:
                continue
            x1, y1, x2, y2 = bounds
            area = max(0, x2 - x1) * max(0, y2 - y1)
            candidates.append((area, y1, node))

        if candidates:
            candidates.sort(key=lambda item: (item[0], item[1]))
            target = candidates[0][2]
            print(
                "[LOGIN] Focusing real PIN node: "
                f"label={getattr(target, 'label', '')!r} bounds={getattr(target, 'bounds_text', getattr(target, 'bounds', ''))}"
            )
            return device.tap_bounds(getattr(target, "bounds"), reason=f"{reason} focus real PIN field")

        # Last-resort deterministic tap in the PIN field region. force_tap_ratio
        # ignores the global coordinate fallback flag by design.
        if hasattr(device, "force_tap_ratio"):
            print("[LOGIN] PIN field node not found. Using safe center-field fallback tap.")
            return device.force_tap_ratio(0.50, 0.285, reason=f"{reason} focus PIN field fallback")

        return False

    def _submit_messenger_pin_value(self, driver, pin_value: str, reason: str) -> bool:
        pin_value = _safe_str(pin_value)
        if not pin_value:
            return False

        adapter = getattr(self, "dump_ui_adapter", None)

        # Dump-first path: called from mobile_dump_automation_rebuilt with driver=None.
        # Use fresh dump nodes + numeric keyevents. This fixes the case where the
        # code tapped a parent ViewGroup then `input text 000000` did not populate
        # Messenger's PIN boxes.
        if driver is None and adapter is not None:
            device = adapter.device
            stage_before = ""
            try:
                stage_before = self._detect_messenger_pin_stage(
                    self._normalize_ui_text(device.text_blob(reason=f"{reason} detect stage before submit"))
                )
            except Exception:
                stage_before = ""

            print(
                f"[LOGIN] Dump-first PIN submit: entering {len(pin_value)} digits "
                f"for {reason} | stage={stage_before or 'unknown'}"
            )

            # First attempt: focus the real PIN field, then send digit keyevents.
            self._focus_dump_first_pin_field(device, reason=reason)
            time.sleep(0.35)
            self._adb_type_pin_digits(pin_value, device=device, reason=reason)
            time.sleep(0.8)
            device.run_adb(["shell", "input", "keyevent", "66"])
            time.sleep(1.2)

            # Some Messenger builds auto-advance after 6 digits. Others expose a button.
            device.click_text(
                ["Tiếp", "Tiếp tục", "Xong", "OK", "Continue", "Next", "Done", "Confirm", "Submit"],
                allow_contains=True,
                reason=reason,
            )
            time.sleep(1.2)

            # Verification and fallback are only active while still on the same
            # Messenger PIN stage. This keeps the patch isolated from unrelated
            # screens/tasks.
            if stage_before and self._still_on_pin_stage(
                device,
                stage_before,
                reason=f"{reason} verify after keyevents",
            ):
                print(f"[LOGIN] PIN stage still visible after keyevents. Retrying safe fallback for {reason}.")
                self._focus_dump_first_pin_field(device, reason=f"{reason} retry focus")
                time.sleep(0.25)
                device.run_adb(["shell", "input", "text", pin_value])
                time.sleep(0.8)
                device.run_adb(["shell", "input", "keyevent", "66"])
                time.sleep(1.0)

            if stage_before and self._still_on_pin_stage(
                device,
                stage_before,
                reason=f"{reason} verify after input-text fallback",
            ):
                self._focus_dump_first_pin_field(device, reason=f"{reason} coordinate retry focus")
                time.sleep(0.25)
                self._tap_zero_key_by_coordinate(device, reason=reason)
                time.sleep(0.8)
                device.run_adb(["shell", "input", "keyevent", "66"])
                time.sleep(1.0)

            return True

        try:
            fields = driver(className="android.widget.EditText")
            try:
                field_count = len(fields)
            except Exception:
                field_count = fields.count if isinstance(fields.count, int) else fields.count()

            if field_count == 0:
                blob = self._normalize_ui_text(self._read_visible_ui_text_blob(driver))
                if "truong ma khoa" in blob or "chinh sua 1/6" in blob:
                    field_count = 6

            if field_count >= 1:
                try:
                    fields[0].click()
                    time.sleep(0.5)
                except Exception:
                    pass
                self._adb_type_pin_digits(pin_value, reason=reason)
                time.sleep(0.5)
                self.run_adb(["shell", "input", "keyevent", "66"])
        except Exception:
            self._adb_type_pin_digits(pin_value, reason=reason)
            self.run_adb(["shell", "input", "keyevent", "66"])

        time.sleep(2)
        try:
            if self._click_best_ui_text(
                driver,
                ["Tiếp", "Tiếp tục", "Xong", "OK", "Continue", "Next", "Done", "Confirm", "Submit"],
                allow_contains=True,
                reason=reason,
            ):
                return True
        except Exception:
            pass

        self.run_adb(["shell", "input", "keyevent", "66"])
        time.sleep(3)
        return True

    def _detect_messenger_pin_stage(self, normalized_blob: str) -> str:
        """Return the exact Messenger PIN stage for blocking PIN setup screens only.

        The create and confirm screens share the same PIN field layout and the
        confirm screen can still contain descriptive text like "Hãy tạo mã PIN".
        Therefore confirmation must be detected before create, and this helper
        must only be used after the caller has verified PIN-related text exists.
        """
        blob = _safe_str(normalized_blob)
        confirm_terms = [
            "xac nhan ma pin",
            "confirm pin",
            "confirm your pin",
            "nhap lai ma pin",
            "re-enter pin",
            "re enter pin",
        ]
        create_terms = [
            "tao ma pin",
            "tao pin",
            "create a pin",
            "create pin",
            "set up pin",
            "thiet lap ma pin",
        ]
        enter_terms = [
            "nhap ma pin",
            "enter pin",
            "enter your pin",
            "ma pin khong dung",
            "incorrect pin",
            "wrong pin",
        ]

        if any(term in blob for term in confirm_terms):
            return "confirm"
        if any(term in blob for term in create_terms):
            return "create"
        if any(term in blob for term in enter_terms):
            return "enter"
        if "truong ma khoa" in blob or "chinh sua 1/6" in blob or "edittext" in blob:
            return "pin_field"
        return ""

    def _still_on_pin_stage(self, device, expected_stage: str, *, reason: str = "") -> bool:
        """Check if Messenger is still on the same PIN stage after an input attempt."""
        try:
            blob = self._normalize_ui_text(device.text_blob(reason=reason or "verify PIN stage"))
        except Exception:
            return False
        stage = self._detect_messenger_pin_stage(blob)
        if expected_stage in {"create", "confirm"}:
            return stage == expected_stage
        return stage in {"create", "confirm", "enter", "pin_field"}

    def _tap_zero_key_by_coordinate(self, device, *, reason: str = "") -> None:
        """Last-resort numeric keypad input for PIN screens only.

        This is intentionally private and only called after the current screen is
        still verified as a Messenger PIN stage, so it will not affect chat,
        Facebook feed, Play Store, or other tasks.
        """
        if not hasattr(device, "force_tap_ratio"):
            return
        print(f"[LOGIN] Fallback PIN input by tapping numeric 0 key for {reason}")
        for _ in range(6):
            device.force_tap_ratio(0.50, 0.905, reason=f"{reason} tap 0 key fallback")
            time.sleep(0.12)

    def handle_messenger_pin_prompt_if_visible(self, driver=None) -> bool:
        # Nếu patch dump_automation đang hoạt động, nó sẽ sử dụng adapter.device để thực hiện dump.
        if driver is not None:
            visible_blob = self._read_visible_ui_text_blob(driver)
        else:
            # Lấy blob từ adapter nếu driver là None (gọi từ bản vá rebuilt)
            adapter = getattr(self, "dump_ui_adapter", None)
            visible_blob = adapter.device.text_blob(reason="PIN check") if adapter else ""
            
        normalized_blob = self._normalize_ui_text(visible_blob)
        if not normalized_blob:
            return False

        messenger_home_terms = [
            "doan chat", "chats", "tab 1/4", "tab menu",
            "hoi meta ai hoac tim kiem", "tin nhan moi",
        ]
        if "tab menu" in normalized_blob and any(term in normalized_blob for term in messenger_home_terms):
            print("[LOGIN] Messenger home/chat list visible; skip PIN handler.")
            return False

        create_pin_terms = [
            "tạo mã pin",
            "tạo pin",
            "create a pin",
            "create pin",
            "set up pin",
            "thiết lập mã pin",
            "tạo mã pin",
        ]
        confirm_pin_terms = [
            "xác nhận mã pin",
            "confirm pin",
            "confirm your pin",
            "nhập lại mã pin",
        ]
        enter_pin_terms = [
            "nhập mã pin",
            "enter pin",
            "enter your pin",
            "mã pin không đúng",
            "incorrect pin",
            "wrong pin",
        ]
        pin_terms = create_pin_terms + confirm_pin_terms + enter_pin_terms

        if not any(self._normalize_ui_text(term) in normalized_blob for term in pin_terms):
            return False

        # Nếu đã có ô nhập liệu/EditText thì đang ở bước nhập PIN, không được bấm lại nút "Tạo mã PIN".
        has_input_fields = False
        if driver is not None:
            try:
                if driver(className="android.widget.EditText").exists:
                    has_input_fields = True
            except Exception:
                pass

        if not has_input_fields:
            # Fallback dump-first: Messenger thường expose ô PIN bằng content-desc "Trường mã khóa, chỉnh sửa 1/6 chữ số".
            if (
                "truong ma khoa" in normalized_blob
                or "chinh sua 1/6" in normalized_blob
                or "edittext" in normalized_blob
            ):
                has_input_fields = True

        # Bước trung gian: Nếu thấy nút "Tạo mã PIN" hoặc "Thiết lập mã PIN" to giữa màn hình,
        # phải bấm vào đó trước để mở màn hình nhập mã PIN thực tế.
        if not has_input_fields and self._click_best_ui_text(
            driver,
            ["Tạo mã PIN", "Thiết lập mã PIN", "Tiếp tục", "Create PIN", "Set up PIN", "Continue"],
            allow_contains=True,
            reason="Messenger start PIN creation landing button (pre-entry check)",
        ):
            time.sleep(4)
            return True

        # Ưu tiên kiểm tra màn hình XÁC NHẬN trước vì nó thường chứa cả từ khóa của màn TẠO trong mô tả.
        # Không dùng cùng một điều kiện lẫn lộn cho create/confirm; tách stage để màn xác nhận
        # luôn nhập lại đúng PIN 000000.
        pin_stage = self._detect_messenger_pin_stage(normalized_blob)

        if pin_stage == "confirm":
            print("[LOGIN] Messenger confirm PIN screen detected. Re-entering PIN 000000.")
            return self._submit_messenger_pin_value(driver, "000000", "Messenger confirm PIN")

        if pin_stage == "create":
            print("[LOGIN] Messenger create PIN screen detected. Creating PIN 000000.")
            self.messenger_pin_attempt_index = 0
            return self._submit_messenger_pin_value(driver, "000000", "Messenger create PIN")

        pin_attempts = ["00000", "888888", "123456"]
        if self.messenger_pin_attempt_index >= len(pin_attempts):
            print("[LOGIN] Messenger PIN input already tried all configured values. Skipping PIN screen.")
            if self._click_best_ui_text(
                driver,
                ["Bỏ qua", "Skip", "Lúc khác", "Để sau", "Not now", "Cancel", "Dismiss"],
                allow_contains=True,
                reason="Messenger PIN skip after attempts",
            ):
                time.sleep(3)
                return True
            return False

        pin_value = pin_attempts[self.messenger_pin_attempt_index]
        self.messenger_pin_attempt_index += 1
        print(f"[LOGIN] Messenger enter PIN screen detected. Trying PIN attempt {self.messenger_pin_attempt_index}: {pin_value}")
        return self._submit_messenger_pin_value(driver, pin_value, "Messenger enter PIN")

    def handle_messenger_recovery_or_pin_prompt_if_visible(self, driver=None) -> bool:
        driver = driver or self.connect_ui_driver()
        if driver is None:
            return False

        visible_blob = self._read_visible_ui_text_blob(driver)
        normalized_blob = self._normalize_ui_text(visible_blob)
        if not normalized_blob:
            return False

        restore_terms = [
            "khôi phục đoạn chat",
            "khôi phục lịch sử chat",
            "khôi phục ngay",
            "restore chats",
            "restore now",
            "restore chat history",
        ]

        if any(self._normalize_ui_text(term) in normalized_blob for term in restore_terms):
            print("[LOGIN] Messenger restore prompt detected. Skipping restore, not clicking 'Khôi phục ngay'.")

            # Trên màn chính Messenger, dòng "Khôi phục lịch sử chat. Khôi phục ngay"
            # chỉ là banner trên đầu danh sách chat, không phải popup chặn thao tác.
            # Không được nhấn Back ở trạng thái này vì Back sẽ quay về Facebook.
            if self.is_messenger_home_screen(driver):
                print("[LOGIN] Restore banner is on Messenger home screen. Ignoring it and continuing.")
                return False

            if self._click_best_ui_text(
                driver,
                ["Lúc khác", "Để sau", "Bỏ qua", "Không phải bây giờ", "Not now", "Skip", "Cancel", "Dismiss"],
                allow_contains=True,
                reason="Messenger restore skip",
            ):
                time.sleep(3)
                return True

            if self._click_best_ui_text(
                driver,
                [
                    "Nhấp để bỏ qua trang dưới cùng này",
                    "Bỏ qua trang dưới cùng này",
                    "Dismiss bottom sheet",
                    "Close bottom sheet",
                    "Đóng",
                    "Close",
                ],
                allow_contains=True,
                reason="Messenger restore bottom sheet close",
            ):
                time.sleep(2)
                return True

            # Không fallback bằng phím Back cho restore prompt nữa.
            # Nếu không có nút bỏ qua, giữ nguyên Messenger và để luồng sau kiểm tra home/menu.
            print("[LOGIN] No restore skip button found. Keeping Messenger foreground; not pressing Back.")
            return False

        if self.handle_messenger_pin_prompt_if_visible(driver):
            return True

        return False

    def click_open_messenger_button_if_visible(self) -> bool:
        driver = self.connect_ui_driver()
        if driver is None:
            return False

        if self.click_messenger_entrypoint_if_visible(driver):
            print("[FACEBOOK] Open Messenger button clicked.")
            return True

        return False

    def click_install_if_visible(self) -> bool:
        driver = self.connect_ui_driver()
        if driver is None:
            print("[MOBILE] Cannot click install because uiautomator2 is not connected.")
            return False

        if self.get_current_package_name(driver) != PLAY_STORE_PKG and not self.is_current_package(PLAY_STORE_PKG):
            print(f"[MOBILE] Skip install click: Play Store is not foreground (current={self.get_current_package_name(driver)}).")
            return False

        primary_install_texts = [
            "Install",
            "Cài đặt",
            "Cài đặt",
            "Update",
            "Cập nhật",
            "Cập nhật",
        ]

        secondary_texts = [
            "Continue",
            "Tiếp tục",
            "Try again",
            "Thử lại",
        ]

        def _find_clickable_node_by_dump(text_options: list[str], *, reason: str = "") -> Optional[Dict[str, Any]]:
            """Find a Play Store action button by parsing driver.dump_hierarchy() only.

            Không dùng adb uiautomator dump, không pull window_dump.xml về local.
            Chỉ nhận node exact text/content-desc là Cài đặt/Install/Cập nhật/Update
            để tránh click nhầm các nhãn cài đặt khác rồi văng về Facebook.
            """
            normalized_options = [self._normalize_ui_text(item) for item in text_options if _safe_str(item)]
            if not normalized_options:
                return None

            try:
                xml_dump = driver.dump_hierarchy()
                root = ET.fromstring(xml_dump)
            except Exception as exc:
                print(f"[MOBILE] Cannot read Play Store hierarchy for {reason}: {exc}")
                return None

            candidates: list[Dict[str, Any]] = []
            labels: list[str] = []
            screen_w, screen_h = self._get_screen_size()

            for node in root.iter():
                if not isinstance(node.tag, str) or not node.attrib:
                    continue

                text_value = _safe_str(node.attrib.get("text"))
                desc_value = _safe_str(node.attrib.get("content-desc"))
                label = desc_value or text_value
                if label:
                    labels.append(label)

                normalized_label = self._normalize_ui_text(label)
                if not normalized_label:
                    continue

                # Với nút cài đặt phải match chính xác. Không dùng contains
                # vì Play Store có thể có các text phụ chứa "Cài đặt".
                exact_match = any(option == normalized_label for option in normalized_options)
                if not exact_match:
                    continue

                bounds = _safe_str(node.attrib.get("bounds"))
                parsed_bounds = self._parse_bounds(bounds)
                if not parsed_bounds:
                    continue

                x1, y1, x2, y2 = parsed_bounds
                class_name = _safe_str(node.attrib.get("class"))
                clickable = node.attrib.get("clickable") == "true"
                enabled = node.attrib.get("enabled", "true") != "false"
                visible = node.attrib.get("visible-to-user", "true") != "false"
                area = max(0, (x2 - x1) * (y2 - y1))
                center_x = (x1 + x2) // 2
                center_y = (y1 + y2) // 2

                if not enabled or not visible:
                    continue

                score = 0
                score += 200
                if clickable:
                    score += 60
                if "button" in class_name.lower():
                    score += 50
                # Nút chính của Play Store thường ở nửa dưới/phải vùng thông tin app.
                if center_y >= int(screen_h * 0.18):
                    score += 20
                if center_x >= int(screen_w * 0.35):
                    score += 10
                score += min(area // 10000, 25)

                candidates.append({
                    "label": label,
                    "bounds": bounds,
                    "class": class_name,
                    "clickable": node.attrib.get("clickable"),
                    "enabled": node.attrib.get("enabled"),
                    "visible": node.attrib.get("visible-to-user"),
                    "score": score,
                    "center": (center_x, center_y),
                })

            if not candidates:
                visible_preview = " | ".join(dict.fromkeys(labels[:50]))
                print(f"[MOBILE] No exact Play Store action node found for {reason}. Visible labels: {visible_preview}")
                return None

            candidates.sort(key=lambda item: item["score"], reverse=True)
            target = candidates[0]
            print(
                "[MOBILE] Play Store action candidate from dump_hierarchy: "
                f"label={target['label']} | bounds={target['bounds']} | "
                f"class={target['class']} | clickable={target['clickable']} | "
                f"enabled={target['enabled']} | visible={target['visible']} | score={target['score']}"
            )
            return target

        before_labels = self._dump_visible_ui_labels(driver, limit=30)
        print("[MOBILE] Before install click labels: " + " | ".join(before_labels))

        clicked_text = None
        target = _find_clickable_node_by_dump(primary_install_texts, reason="primary install/update")
        if target:
            center = target.get("center")
            if center:
                x, y = center
                print(f"[MOBILE] ADB tap Play Store install center ({x}, {y}) label={target['label']} bounds={target['bounds']}")
                self.run_adb(["shell", "input", "tap", str(x), str(y)])
                time.sleep(1.5)
                clicked_text = target["label"]

        if not clicked_text:
            target = _find_clickable_node_by_dump(secondary_texts, reason="secondary install flow")
            if target:
                center = target.get("center")
                if center:
                    x, y = center
                    print(f"[MOBILE] ADB tap Play Store secondary center ({x}, {y}) label={target['label']} bounds={target['bounds']}")
                    self.run_adb(["shell", "input", "tap", str(x), str(y)])
                    time.sleep(1.5)
                    clicked_text = target["label"]

        if not clicked_text:
            labels = self._dump_visible_ui_labels(driver, limit=20)
            if labels:
                print("[MOBILE] Store install button not found. Visible labels: " + " | ".join(labels))
            else:
                print("[MOBILE] Store install button not found and UI hierarchy is empty.")
            return False

        print(f"[MOBILE] Store action button clicked: {clicked_text}")
        print("[MOBILE] Store action button click submitted, verifying install state...")

        # Không kết luận thành công ngay sau tap. Poll lại Play Store/package.
        # Nếu bị nhảy sang Facebook, coi là click sai/không thành công và quay lại Play Store.
        deadline = time.time() + 20
        while time.time() < deadline:
            if self.is_package_installed(MESSENGER_PKG):
                print("[MOBILE] Messenger package installed after click.")
                return True

            current_package = self.get_current_package_name(driver)
            if current_package == FACEBOOK_PKG or self.is_current_package(FACEBOOK_PKG):
                print("[MOBILE] Unexpectedly returned to Facebook after install tap. Reopening Play Store and retrying later.")
                self.open_play_store_for_messenger()
                time.sleep(3)
                return False

            labels = self._dump_visible_ui_labels(driver, limit=35)
            blob = self._normalize_ui_text(" | ".join(labels))

            installing_keywords = [
                "pending",
                "installing",
                "downloading",
                "waiting",
                "cancel",
                "dang cho",
                "dang cai",
                "dang tai",
                "huy",
            ]
            installed_keywords = [
                "open",
                "mo",
                "uninstall",
                "go cai dat",
            ]

            if any(keyword in blob for keyword in installing_keywords):
                print("[MOBILE] Play Store install/download state detected: " + " | ".join(labels))
                return True

            # Chỉ coi "Mở/Open" là thành công nếu pm path đã xác nhận package.
            if any(keyword in blob for keyword in installed_keywords):
                if self.is_package_installed(MESSENGER_PKG):
                    print("[MOBILE] Play Store shows Open and Messenger package exists.")
                    return True
                print("[MOBILE] Play Store shows Open-like text, but package is not confirmed yet. Keep waiting.")

            time.sleep(2)

        after_labels = self._dump_visible_ui_labels(driver, limit=30)
        print("[MOBILE] Click was sent, but install state was not confirmed.")
        print("[MOBILE] After click labels: " + " | ".join(after_labels))
        return False

    def ensure_messenger_installed(self, wait_seconds: int = DEFAULT_INSTALL_WAIT_SECONDS) -> bool:
        if not self.active_device and not self.connect_to_device():
            return False
        if self.is_package_installed(MESSENGER_PKG):
            return True

        self.open_play_store_for_messenger()

        deadline = time.time() + max(5, wait_seconds)
        next_click_at = 0.0
        while time.time() < deadline:
            if self.is_package_installed(MESSENGER_PKG):
                print("[MOBILE] Messenger installation detected.")
                return True

            current_package = self.get_current_package_name()
            if current_package != PLAY_STORE_PKG and not self.is_current_package(PLAY_STORE_PKG):
                print(f"[MOBILE] Play Store is not foreground during install wait (current={current_package}). Reopening Play Store.")
                self.open_play_store_for_messenger()
                time.sleep(4)

            if time.time() >= next_click_at:
                clicked = self.click_install_if_visible()
                if not clicked:
                    print("[MOBILE] Install click failed or no valid install state detected.")
                next_click_at = time.time() + 10
            time.sleep(3)

        print("[MOBILE] Messenger is still not installed. Complete Play Store install on the device and rerun.")
        return False

    def is_current_package(self, package_name: str) -> bool:
        current = self.get_current_package_name()
        if current:
            return current == package_name

        # Last-resort fallback: only trust the package exposed by the current
        # accessibility hierarchy. A raw substring search in dumpsys is unsafe
        # because background/back-stack activities can keep com.facebook.orca
        # visible while Facebook is actually foreground.
        try:
            root = self.refresh_window_dump_root(reason=f"foreground package fallback: {package_name}")
            for node in root.iter("node"):
                pkg = _safe_str(node.attrib.get("package"))
                if pkg:
                    return pkg == package_name
        except Exception:
            pass
        return False
    
    def _get_screen_size(self) -> tuple[int, int]:
        output = self.run_adb(["shell", "wm", "size"])
        match = re.search(r"Physical size:\s*(\d+)x(\d+)", output)
        if match:
            return int(match.group(1)), int(match.group(2))

        match = re.search(r"Override size:\s*(\d+)x(\d+)", output)
        if match:
            return int(match.group(1)), int(match.group(2))

        return 1080, 2400

    def _tap_screen_ratio(self, x_ratio: float, y_ratio: float, reason: str = "") -> bool:
        width, height = self._get_screen_size()
        x = int(width * x_ratio)
        y = int(height * y_ratio)

        print(f"[MOBILE] ADB fallback tap at ({x}, {y}) for {reason}")
        self.run_adb(["shell", "input", "tap", str(x), str(y)])
        time.sleep(2)
        return True

    def _window_dump_local_path(self) -> Path:
        """Local path used for the latest adb window dump."""
        configured = os.getenv("FB_1_1_WINDOW_DUMP_FILE")
        if configured:
            configured_path = Path(configured)
            return configured_path if configured_path.is_absolute() else PROJECT_ROOT / configured_path
        return PROJECT_ROOT / "window_dump.xml"

    def refresh_window_dump_root(self, reason: str = "") -> Optional[ET.Element]:
        """Return a fresh UI XML root without adb pull/local window_dump.

        Theo luồng join_group: lấy XML trực tiếp bằng driver.dump_hierarchy()
        rồi parse bằng ElementTree. Hàm này KHÔNG tạo file /sdcard/window_dump.xml,
        KHÔNG adb pull và KHÔNG ghi window_dump.xml ra local nữa.
        """
        driver = self.connect_ui_driver()
        if driver is None:
            print(f"[WINDOW_DUMP] Cannot dump hierarchy because uiautomator2 is not connected: {reason}")
            return None

        try:
            xml_dump = self._dump_hierarchy_xml(driver, reason=reason or "refresh window dump")
            if not _safe_str(xml_dump):
                print(f"[WINDOW_DUMP] UI dump returned empty XML: {reason}")
                return None

            root = ET.fromstring(xml_dump)
            print(f"[WINDOW_DUMP] Parsed XML from hierarchy provider | reason={reason}")
            return root
        except Exception as exc:
            print(f"[WINDOW_DUMP] hierarchy parse failed ({reason}): {exc}")
            return None

    def _iter_window_dump_nodes(self, root: ET.Element):
        """Iterate UI nodes safely for both <node> and non-standard roots."""
        for node in root.iter():
            if not isinstance(node.tag, str):
                continue
            # Android uiautomator normally uses <node>, but root.iter() is safer
            # and matches the style used in join_group.py.
            if node.attrib:
                yield node

    def _window_dump_labels(self, root: Optional[ET.Element] = None, *, reason: str = "") -> list[str]:
        root = root or self.refresh_window_dump_root(reason=reason or "read labels")
        if root is None:
            return []

        labels: list[str] = []
        seen = set()
        for node in self._iter_window_dump_nodes(root):
            label = self._node_label(node)
            if not label or label in seen:
                continue
            seen.add(label)
            labels.append(label)
        return labels

    def _read_window_dump_text_blob(self, *, reason: str = "") -> str:
        labels = self._window_dump_labels(reason=reason or "read text blob")
        return " | ".join(labels)

    def _find_best_window_dump_text_node(
        self,
        text_options: list[str],
        *,
        allow_contains: bool = True,
        reason: str = "",
        blocked_texts: Optional[list[str]] = None,
        prefer_bottom: bool = False,
        prefer_right: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """Find the best visible node from a fresh adb window_dump.xml.

        This is the stable fallback path when uiautomator2 returns stale/empty
        hierarchy. It follows the join_group approach: parse XML -> inspect
        text/content-desc/class/bounds -> compute center -> tap by ADB.
        """
        normalized_options = [self._normalize_ui_text(text) for text in text_options if _safe_str(text)]
        normalized_blocked = [self._normalize_ui_text(text) for text in (blocked_texts or []) if _safe_str(text)]
        if not normalized_options:
            return None

        root = self.refresh_window_dump_root(reason=reason or "find UI text")
        if root is None:
            return None

        priority_terms = [
            self._normalize_ui_text("MỞ MESSENGER"),
            self._normalize_ui_text("Mở Messenger"),
            self._normalize_ui_text("Open Messenger"),
            self._normalize_ui_text("OPEN MESSENGER"),
            self._normalize_ui_text("Mở ứng dụng Messenger"),
            self._normalize_ui_text("Open in Messenger"),
            self._normalize_ui_text("Chấp nhận"),
            self._normalize_ui_text("Accept"),
        ]
        weak_terms = [
            self._normalize_ui_text("Tin nhắn"),
            self._normalize_ui_text("Messages"),
            self._normalize_ui_text("Chats"),
        ]

        screen_w, screen_h = self._get_screen_size()
        candidates: list[Dict[str, Any]] = []

        for node in self._iter_window_dump_nodes(root):
            label = self._node_label(node)
            if not label:
                continue

            normalized_label = self._normalize_ui_text(label)
            if any(blocked and blocked in normalized_label for blocked in normalized_blocked):
                continue

            exact_match = any(option == normalized_label for option in normalized_options)
            contains_match = allow_contains and any(option in normalized_label for option in normalized_options)
            if not exact_match and not contains_match:
                continue

            bounds = _safe_str(node.attrib.get("bounds"))
            parsed_bounds = self._parse_bounds(bounds)
            if not parsed_bounds:
                continue

            x1, y1, x2, y2 = parsed_bounds
            area = max(0, (x2 - x1) * (y2 - y1))
            center_x = (x1 + x2) // 2
            center_y = (y1 + y2) // 2
            class_name = _safe_str(node.attrib.get("class"))
            clickable = node.attrib.get("clickable") == "true"
            enabled = node.attrib.get("enabled", "true") != "false"
            visible = node.attrib.get("visible-to-user", "true") != "false"

            score = 0
            if exact_match:
                score += 140
            if contains_match:
                score += 45
            if clickable:
                score += 40
            if enabled:
                score += 20
            if visible:
                score += 15
            if "button" in class_name.lower():
                score += 35
            if any(term and term in normalized_label for term in priority_terms):
                score += 180
            if any(term and term == normalized_label for term in weak_terms):
                score -= 70
            if prefer_bottom and center_y >= int(screen_h * 0.60):
                score += 35
            if prefer_right and center_x >= int(screen_w * 0.50):
                score += 25
            score += min(area // 10000, 25)
            score += min(y1 // 300, 8)

            candidates.append({
                "label": label,
                "normalized_label": normalized_label,
                "bounds": bounds,
                "clickable": node.attrib.get("clickable"),
                "enabled": node.attrib.get("enabled"),
                "visible": node.attrib.get("visible-to-user"),
                "class": class_name,
                "score": score,
                "center": (center_x, center_y),
                "source": "window_dump",
            })

        if not candidates:
            labels = self._window_dump_labels(root, reason=reason or "find UI text")
            print(
                f"[WINDOW_DUMP] No matched node for {reason}. Visible labels: "
                + " | ".join(labels[:80])
            )
            return None

        candidates.sort(key=lambda item: item["score"], reverse=True)
        target = candidates[0]
        print(
            "[WINDOW_DUMP] Best node: "
            f"label={target['label']} | bounds={target['bounds']} | "
            f"class={target['class']} | clickable={target['clickable']} | "
            f"enabled={target['enabled']} | score={target['score']} | reason={reason}"
        )
        return target

    def _click_window_dump_text(
        self,
        text_options: list[str],
        *,
        allow_contains: bool = True,
        reason: str = "",
        blocked_texts: Optional[list[str]] = None,
        prefer_bottom: bool = False,
        prefer_right: bool = False,
        settle_seconds: float = 1.5,
    ) -> bool:
        target = self._find_best_window_dump_text_node(
            text_options,
            allow_contains=allow_contains,
            reason=reason,
            blocked_texts=blocked_texts,
            prefer_bottom=prefer_bottom,
            prefer_right=prefer_right,
        )
        if not target:
            return False

        center = target.get("center")
        if center:
            x, y = center
            print(f"[WINDOW_DUMP] ADB tap center ({x}, {y}) for {reason or target['label']}")
            self.run_adb(["shell", "input", "tap", str(x), str(y)])
            time.sleep(settle_seconds)
            return True

        return self._tap_bounds_center(target["bounds"], reason=reason or target["label"])

    def wait_for_current_package(self, package_name: str, timeout: int = 20) -> bool:
        driver = self.connect_ui_driver()
        deadline = time.time() + timeout

        while time.time() < deadline:
            if driver is not None and not self._uiautomator_call_recently_timed_out():
                ok, current = self._call_uiautomator_with_timeout(
                    lambda: driver.app_current() or {},
                    reason=f"wait for current package {package_name}",
                    timeout=3,
                )
                if ok and isinstance(current, dict) and current.get("package") == package_name:
                    return True

            if self.is_current_package(package_name):
                return True

            time.sleep(1)

        return False

    def get_current_package_name(self, driver=None) -> str:
        driver = driver or self.connect_ui_driver()
        if driver is not None and not self._uiautomator_call_recently_timed_out():
            ok, current = self._call_uiautomator_with_timeout(
                lambda: driver.app_current() or {},
                reason="get current package",
                timeout=3,
            )
            if ok and isinstance(current, dict):
                return _safe_str(current.get("package"))

        try:
            output = self.run_adb(["shell", "dumpsys", "window", "windows"])
            match = re.search(r"m(?:CurrentFocus|FocusedApp)=.*?\s([A-Za-z0-9_.]+)/", output)
            if match:
                return _safe_str(match.group(1))
        except Exception:
            pass

        try:
            output = self.run_adb(["shell", "dumpsys", "activity", "activities"])
            match = re.search(r"(?:mResumedActivity|topResumedActivity|ResumedActivity):.*?\s([A-Za-z0-9_.]+)/", output)
            if match:
                return _safe_str(match.group(1))
        except Exception:
            pass

        return ""

    def bring_messenger_to_front(self, driver=None, reason: str = "") -> bool:
        driver = driver or self.connect_ui_driver()
        current_package = self.get_current_package_name(driver)
        if current_package == MESSENGER_PKG or self.is_current_package(MESSENGER_PKG):
            return True

        print(f"[MOBILE] Messenger is no longer foreground after {reason}. Reopening Messenger.")
        if driver is not None:
            try:
                driver.app_start(MESSENGER_PKG)
            except Exception as exc:
                print(f"[MOBILE] driver.app_start Messenger failed: {exc}")

        if not self.wait_for_current_package(MESSENGER_PKG, timeout=8):
            activity = self.resolve_launch_activity(MESSENGER_PKG)
            if activity:
                self.run_adb(["shell", "am", "start", "-n", activity])
            else:
                self.run_adb([
                    "shell",
                    "monkey",
                    "-p",
                    MESSENGER_PKG,
                    "-c",
                    "android.intent.category.LAUNCHER",
                    "1",
                ])

        time.sleep(3)
        return self.wait_for_current_package(MESSENGER_PKG, timeout=8)

    def _safe_messenger_back(self, driver=None, reason: str = "") -> bool:
        """Press Back only while Messenger is foreground, then recover if Android leaves Messenger.

        This prevents the old loop from backing out Messenger -> Facebook -> Android launcher.
        """
        driver = driver or self.connect_ui_driver()
        before_package = self.get_current_package_name(driver)
        if before_package != MESSENGER_PKG and not self.is_current_package(MESSENGER_PKG):
            print(f"[MOBILE] Skip Back for {reason}: Messenger is not foreground (current={before_package}).")
            return False

        print(f"[MOBILE] Safe Messenger Back for {reason}.")
        try:
            if driver is not None:
                driver.press("back")
            else:
                self.run_adb(["shell", "input", "keyevent", "4"])
        except Exception:
            self.run_adb(["shell", "input", "keyevent", "4"])
        time.sleep(1.5)

        after_package = self.get_current_package_name(driver)
        if after_package != MESSENGER_PKG and not self.is_current_package(MESSENGER_PKG):
            print(
                f"[MOBILE] Back left Messenger during {reason} (current={after_package}). "
                "Reopening Messenger and stopping further Back presses."
            )
            self.bring_messenger_to_front(driver, reason=f"safe back recovery: {reason}")
            return False
        return True

    def is_message_requests_list_screen(self, driver=None) -> bool:
        driver = driver or self.connect_ui_driver()
        if driver is None:
            return False
        if self.get_current_package_name(driver) != MESSENGER_PKG and not self.is_current_package(MESSENGER_PKG):
            return False

        # Không dùng blob toàn màn hình để nhận diện list Tin nhắn đang chờ.
        # Ở màn Đoạn chat, một row bình thường cũng có preview/text là
        # "Tin nhắn đang chờ", khiến code cũ tưởng vẫn đang ở request list.
        try:
            root = ET.fromstring(self._dump_hierarchy_xml(driver, reason="message requests screen check"))
            if self._conversation_header_sender_from_root(root) and self._root_has_messenger_composer(root):
                return False
        except Exception:
            pass

        request_terms = [
            "tin nhan dang cho",
            "message requests",
            "yeu cau nhan tin",
            "loi moi nhan tin",
        ]
        screen_w, screen_h = self._get_screen_size()
        has_top_request_title = False
        for node in self._dump_nodes(driver):
            norm = node.get("norm", "")
            x1, y1, x2, y2 = node.get("parsed_bounds", (0, 0, 0, 0))
            if y2 > int(screen_h * 0.28):
                continue
            if any(term and term in norm for term in request_terms):
                has_top_request_title = True
                break

        return has_top_request_title and not self.is_message_request_detail_screen(driver)

    def _has_selected_messenger_chats_tab(self, driver=None) -> bool:
        driver = driver or self.connect_ui_driver()
        if driver is None:
            return False
        if self.get_current_package_name(driver) != MESSENGER_PKG and not self.is_current_package(MESSENGER_PKG):
            return False

        screen_w, screen_h = self._get_screen_size()
        for node in self._dump_nodes(driver):
            norm = node.get("norm", "")
            if not norm:
                continue
            if "lua chon khac" in norm or "more options" in norm:
                continue

            is_chats_tab = (
                "doan chat" in norm
                or norm == "chats"
                or norm.startswith("chats,")
                or re.search(r"\btab\s+1/\d+\b", norm)
            )
            if not is_chats_tab:
                continue

            x1, y1, x2, y2 = node.get("parsed_bounds", (0, 0, 0, 0))
            center_x = (x1 + x2) / max(1, screen_w)
            center_y = (y1 + y2) / max(1, screen_h)
            if center_x <= 0.35 and center_y >= 0.82:
                return True
        return False

    def is_messenger_chats_home_screen(self, driver=None) -> bool:
        """Detect the normal Messenger Chats tab, not Message Requests/detail screens."""
        driver = driver or self.connect_ui_driver()
        if driver is None:
            return False
        if self.get_current_package_name(driver) != MESSENGER_PKG and not self.is_current_package(MESSENGER_PKG):
            return False

        blob = self._normalize_ui_text(self._read_visible_ui_text_blob(driver))
        if not blob:
            return False

        if self.is_message_requests_list_screen(driver) or self.is_message_request_detail_screen(driver):
            return False
        if self._find_messenger_composer_node(driver) is not None:
            return False

        negative_terms = [
            "chap nhan", "accept", "chan", "block", "xoa", "delete",
            "log in", "dang nhap", "password", "mat khau",
        ]
        if any(term in blob for term in negative_terms):
            return False

        if self._has_selected_messenger_chats_tab(driver):
            return True

        positive_terms = ["doan chat", "chats", "tim kiem", "search", "menu", "messenger"]
        return any(term in blob for term in positive_terms)

    def _messenger_blocking_prompt_visible(self, driver=None) -> bool:
        driver = driver or self.connect_ui_driver()
        if driver is None:
            return False

        blob = self._normalize_ui_text(self._read_visible_ui_text_blob(driver))
        if not blob:
            return False

        restore_terms = [
            "khôi phục đoạn chat",
            "khôi phục lịch sử chat",
            "khôi phục ngay",
            "restore chats",
            "restore now",
            "restore chat history",
        ]

        pin_terms = [
            "mã pin",
            "create a pin",
            "tạo mã pin",
            "nhập mã pin",
            "enter pin",
            "confirm pin",
            "xác nhận mã pin",
            "thiết lập mã pin",
        ]

        if any(self._normalize_ui_text(term) in blob for term in pin_terms):
            return True

        if any(self._normalize_ui_text(term) in blob for term in restore_terms):
            # Nếu đã thấy các thành phần màn chính Messenger thì restore chỉ là banner,
            # không phải prompt chặn. Không coi nó là blocking để tránh nhấn Back.
            home_terms = [
                "hỏi meta ai",
                "tìm kiếm",
                "search",
                "đoạn chat",
                "chats",
                "menu",
                "thông báo",
            ]
            if any(self._normalize_ui_text(term) in blob for term in home_terms):
                return False
            return True

        return False

    def wait_until_messenger_ready_for_navigation(self, driver=None, timeout: int = 12) -> bool:
        driver = driver or self.connect_ui_driver()
        deadline = time.time() + max(1, timeout)

        while time.time() < deadline:
            if not self.bring_messenger_to_front(driver, reason="prompt handling"):
                return False

            # PIN/setup screens must be handled before home detection.
            # Otherwise status-bar labels such as "Thông báo của Tin nhắn" can make
            # the broad "tin nhắn" home keyword look true while Messenger is still
            # blocked on "Xác nhận mã PIN".
            if self._messenger_blocking_prompt_visible(driver):
                handled = self.handle_messenger_recovery_or_pin_prompt_if_visible(driver)
                if handled:
                    time.sleep(2)
                    continue

                if self.is_messenger_home_screen(driver):
                    return True

                return False

            if self.is_messenger_home_screen(driver):
                return True

            time.sleep(1)

        return self.is_messenger_home_screen(driver) or (
            self.is_current_package(MESSENGER_PKG)
            and not self._messenger_blocking_prompt_visible(driver)
        )

    def collapse_statusbar_if_notification_shade_open(self) -> bool:
        """Close Android notification shade when it is covering Facebook/Messenger.

        Some ROMs expose notification labels in dump_hierarchy. If the focused
        window is NotificationShade, clicks may hit the shade instead of the app.
        """
        try:
            window_dump = self.run_adb(["shell", "dumpsys", "window"])
            shade_has_focus = any(
                ("mCurrentFocus" in line or "mFocusedWindow" in line) and "NotificationShade" in line
                for line in window_dump.splitlines()
            )
            if not shade_has_focus:
                return False
            self.run_adb(["shell", "cmd", "statusbar", "collapse"])
            time.sleep(1)
            print("[MOBILE] Notification shade was open -> collapsed before clicking Messenger.")
            return True
        except Exception as exc:
            print(f"[MOBILE] Cannot collapse notification shade: {exc}")
            return False

    def _facebook_home_node_candidate(self, driver=None) -> Optional[Dict[str, Any]]:
        driver = driver or self.connect_ui_driver()
        if driver is None:
            return None

        try:
            root = ET.fromstring(driver.dump_hierarchy())
        except Exception as exc:
            print(f"[FACEBOOK] Cannot dump UI to find Home tab: {exc}")
            return None

        home_terms = [
            self._normalize_ui_text("Trang chủ"),
            self._normalize_ui_text("Trang chính"),
            self._normalize_ui_text("Home"),
            self._normalize_ui_text("News Feed"),
            self._normalize_ui_text("Bảng tin"),
        ]
        blocked_terms = [
            self._normalize_ui_text("Trang chủ của"),
            self._normalize_ui_text("Home page of"),
        ]

        candidates: list[Dict[str, Any]] = []
        screen_w, screen_h = self._get_screen_size()
        for node in root.iter("node"):
            label = self._node_label(node)
            if not label:
                continue
            normalized_label = self._normalize_ui_text(label)
            if any(term and term in normalized_label for term in blocked_terms):
                continue

            exact_match = any(term == normalized_label for term in home_terms)
            contains_match = any(term and term in normalized_label for term in home_terms)
            if not exact_match and not contains_match:
                continue

            bounds = _safe_str(node.attrib.get("bounds"))
            parsed_bounds = self._parse_bounds(bounds)
            if not parsed_bounds:
                continue

            x1, y1, x2, y2 = parsed_bounds
            class_name = _safe_str(node.attrib.get("class"))
            clickable = node.attrib.get("clickable") == "true"
            center_y = (y1 + y2) // 2
            center_x = (x1 + x2) // 2

            score = 0
            if exact_match:
                score += 100
            if contains_match:
                score += 30
            if clickable:
                score += 40
            if "button" in class_name.lower():
                score += 25
            if "tab" in normalized_label:
                score += 40
            # Facebook navigation tabs usually sit near the top or bottom edge.
            if center_y <= int(screen_h * 0.22) or center_y >= int(screen_h * 0.80):
                score += 35
            # Home is normally one of the first tabs.
            if center_x <= int(screen_w * 0.35):
                score += 15

            candidates.append({
                "label": label,
                "bounds": bounds,
                "clickable": node.attrib.get("clickable"),
                "class": class_name,
                "score": score,
            })

        if not candidates:
            return None
        candidates.sort(key=lambda item: item["score"], reverse=True)
        return candidates[0]

    def is_facebook_messenger_entry_visible(self, driver=None) -> bool:
        driver = driver or self.connect_ui_driver()
        if driver is None or not self.is_current_package(FACEBOOK_PKG):
            return False

        if self._find_best_ui_text_node(
            driver,
            [
                "MỞ MESSENGER",
                "Mở Messenger",
                "Open Messenger",
                "Messenger",
                "Messages",
                "Nhắn tin",
            ],
            allow_contains=False,
            reason="Facebook Messenger entry visibility",
        ):
            return True

        blob = self._normalize_ui_text(self._read_visible_ui_text_blob(driver))
        return any(
            self._normalize_ui_text(term) in blob
            for term in ["messenger", "mở messenger", "open messenger", "nhắn tin"]
        )

    def wait_for_facebook_messenger_entry_visible(self, driver=None, *, timeout: float = 4.0) -> bool:
        driver = driver or self.connect_ui_driver()
        deadline = time.time() + max(0.5, float(timeout))
        while time.time() < deadline:
            if self.is_facebook_messenger_entry_visible(driver):
                print("[FACEBOOK] Messenger entry is visible on Home; skip feed scroll.")
                return True
            time.sleep(0.5)
        return False

    def is_facebook_home_context_visible(self, driver=None) -> bool:
        driver = driver or self.connect_ui_driver()
        if driver is None or not self.is_current_package(FACEBOOK_PKG):
            return False

        blob = self._normalize_ui_text(self._read_visible_ui_text_blob(driver))
        if not blob:
            return False

        negative_terms = [
            "reels",
            "watch",
            "thông báo",
            "notifications",
            "bạn bè",
            "friends",
            "nhóm",
            "groups",
        ]
        positive_terms = [
            "trang chủ",
            "trang chính",
            "home",
            "bảng tin",
            "news feed",
            "facebook",
            "bạn đang nghĩ gì",
            "what's on your mind",
            "messenger",
        ]

        if any(self._normalize_ui_text(term) in blob for term in positive_terms):
            # Nếu đồng thời thấy Messenger/Home thì ưu tiên coi là màn có thể thao tác,
            # tránh chạm vào phím điều hướng hệ thống khi Facebook đã ở đúng màn.
            return True

        return not any(self._normalize_ui_text(term) in blob for term in negative_terms)

    def click_facebook_home_tab_if_visible(self, driver=None) -> bool:
        driver = driver or self.connect_ui_driver()
        if driver is None:
            return False
        if not self.is_current_package(FACEBOOK_PKG):
            return False

        if self.is_facebook_home_context_visible(driver) and self.is_facebook_messenger_entry_visible(driver):
            print("[FACEBOOK] Facebook already appears to be on Home with Messenger visible. Skip Home tap.")
            return True

        target = self._facebook_home_node_candidate(driver)
        if target:
            print(
                "[FACEBOOK] Clicking Home tab before Messenger lookup: "
                f"label={target['label']} | bounds={target['bounds']} | "
                f"clickable={target['clickable']} | class={target['class']}"
            )
            self._tap_bounds_center(target["bounds"], reason="Facebook Home tab")
            time.sleep(3)
            return True

        # Không dùng fallback tap bottom-left/top-left nữa. Trên nhiều máy Samsung,
        # vùng bottom-left rất gần phím điều hướng hệ thống nên có thể bị văng ra
        # màn hình chính; vùng top-left/top cũng dễ kéo thanh thông báo xuống.
        print("[FACEBOOK] Home tab not found by UI text. Skip unsafe coordinate fallback taps.")
        return False

    def scroll_facebook_home_to_top(self, driver=None, repeats: int = 1) -> bool:
        driver = driver or self.connect_ui_driver()
        if driver is None:
            return False
        if not self.is_current_package(FACEBOOK_PKG):
            return False

        if self.is_facebook_messenger_entry_visible(driver):
            print("[FACEBOOK] Messenger entry already visible. Skip feed scroll-to-top.")
            return True

        width, height = self._get_screen_size()
        # One controlled pull is enough to reveal Facebook's top header. A
        # second pull can move past the header after Messenger has appeared.
        repeat_count = 1
        for index in range(repeat_count):
            if not self.is_current_package(FACEBOOK_PKG):
                print("[FACEBOOK] Stop scroll-to-top because Facebook is no longer foreground.")
                return False

            if self.is_facebook_messenger_entry_visible(driver):
                print("[FACEBOOK] Messenger entry became visible during scroll-to-top.")
                return True

            print(f"[FACEBOOK] Safely scrolling home feed to top ({index + 1}/{repeat_count}).")
            # Chỉ swipe trong vùng nội dung giữa/dưới màn hình. Không bắt đầu từ
            # sát mép trên để tránh kéo Android notification/control shade xuống.
            self.run_adb([
                "shell",
                "input",
                "swipe",
                str(width // 2),
                str(int(height * 0.62)),
                str(width // 2),
                str(int(height * 0.78)),
                "260",
            ])
            time.sleep(0.8)
            self.collapse_statusbar_if_notification_shade_open()

            if self.is_facebook_messenger_entry_visible(driver):
                print("[FACEBOOK] Messenger entry visible after one scroll-to-top.")
                return True

        return self.is_current_package(FACEBOOK_PKG)

    def ensure_facebook_home_for_messenger_icon(self, driver=None, scroll_to_top: bool = True) -> bool:
        """Move Facebook to Home before looking for the Messenger icon.

        The routine is intentionally conservative: if Facebook is already on a
        usable Home screen, do not tap random fallback coordinates and do not
        perform aggressive downward swipes. Those two actions caused some
        devices to jump to the Android launcher or pull the notification shade.
        """
        driver = driver or self.connect_ui_driver()
        if driver is None:
            return False
        if not self.is_current_package(FACEBOOK_PKG):
            return False

        self.collapse_statusbar_if_notification_shade_open()
        before_labels = self._dump_visible_ui_labels(driver, limit=30)
        if before_labels:
            print("[FACEBOOK] Labels before Home normalization: " + " | ".join(before_labels))

        if self.is_facebook_messenger_entry_visible(driver):
            print("[FACEBOOK] Messenger entry is already visible. Skip Home normalization.")
            return True

        home_clicked = self.click_facebook_home_tab_if_visible(driver)
        if not self.is_current_package(FACEBOOK_PKG):
            print("[FACEBOOK] Facebook left foreground while normalizing Home. Stop.")
            return False

        if home_clicked and self.wait_for_facebook_messenger_entry_visible(driver, timeout=4.0):
            return True

        if scroll_to_top and not self.is_facebook_messenger_entry_visible(driver):
            self.scroll_facebook_home_to_top(driver, repeats=1)

        self.collapse_statusbar_if_notification_shade_open()
        after_labels = self._dump_visible_ui_labels(driver, limit=40)
        if after_labels:
            print("[FACEBOOK] Labels after Home normalization: " + " | ".join(after_labels))
        return self.is_current_package(FACEBOOK_PKG)

    def open_facebook(self) -> bool:
        if not self.is_package_installed(FACEBOOK_PKG):
            print("[FACEBOOK] Facebook app is not installed on this device.")
            return False

        driver = self.connect_ui_driver()

        if driver is not None:
            try:
                print("[FACEBOOK] Opening Facebook app...")
                driver.app_start(FACEBOOK_PKG)
                time.sleep(5)

                if self.get_current_package_name(driver) == FACEBOOK_PKG:
                    return True
            except Exception as exc:
                print(f"[FACEBOOK] driver.app_start Facebook failed: {exc}")

        activity = self.resolve_launch_activity(FACEBOOK_PKG)
        if activity:
            self.run_adb(["shell", "am", "start", "-n", activity])
        else:
            self.run_adb([
                "shell",
                "monkey",
                "-p",
                FACEBOOK_PKG,
                "-c",
                "android.intent.category.LAUNCHER",
                "1",
            ])

        time.sleep(5)
        return self.is_current_package(FACEBOOK_PKG)

    def click_facebook_messenger_icon(self) -> bool:
        driver = self.connect_ui_driver()
        if driver is None:
            print("[FACEBOOK] Cannot click Messenger icon because uiautomator2 is not connected.")
            return False

        self.collapse_statusbar_if_notification_shade_open()
        print(f"[MOBILE] [{self.active_device}] Searching for Messenger icon on current screen...")

        labels = self._dump_visible_ui_labels(driver, limit=40)
        print("[FACEBOOK] Visible labels before Messenger icon click: " + " | ".join(labels))

        messenger_icon_texts = [
            "Messenger",
            "Messages",
            "Chats",
            "Nhắn tin",
            "Trò chuyện",
        ]

        # Thử click icon Messenger ngay trên màn hiện tại trước. Nếu đã ở Trang chủ
        # thì không cần normalize/click Home/scroll nữa.
        if self._click_best_ui_text(
            driver,
            messenger_icon_texts,
            allow_contains=False,
            reason="Facebook Messenger icon before Home normalization",
        ):
            print("[FACEBOOK] Messenger icon clicked by ranked UI node before Home normalization.")
            time.sleep(5)
            return True

        # Nếu không thấy icon ngay, có thể do đang ở Tab khác (Reels/Thông báo...)
        self.ensure_facebook_home_for_messenger_icon(driver, scroll_to_top=True)

        if self._click_best_ui_text(
            driver,
            messenger_icon_texts,
            allow_contains=False,
            reason="Facebook Messenger icon after Home normalization",
        ):
            print("[FACEBOOK] Messenger icon clicked by ranked UI node after Home normalization.")
            time.sleep(5)
            return True

        # Sau khi về Home, kiểm tra lại xem có bị kẹt ở trang trung gian không
        if self.click_open_messenger_button_if_visible():
            return True

        if not self.is_current_package(FACEBOOK_PKG):
            print("[FACEBOOK] Facebook is no longer foreground before Messenger icon click.")
            return False

        labels = self._dump_visible_ui_labels(driver, limit=50)
        print("[FACEBOOK] Visible labels after Home normalization before Messenger icon click: " + " | ".join(labels))

        if self.click_open_messenger_button_if_visible():
            return True

        if self._click_best_ui_text(
            driver,
            messenger_icon_texts,
            allow_contains=False,
            reason="Facebook Messenger icon",
        ):
            print("[FACEBOOK] Messenger icon clicked by ranked UI node.")
            time.sleep(5)
            return True

        print("[FACEBOOK] Messenger icon not found by text even after normalization.")
        return False

    def complete_messenger_auto_login_from_facebook(
        self,
        timeout: int = 35,
        account_snapshot: Optional[Dict[str, str]] = None,
    ) -> bool:
        driver = self.connect_ui_driver()
        if driver is None:
            return False

        deadline = time.time() + timeout
        account_snapshot = account_snapshot or self.current_facebook_account_snapshot or self.get_runtime_current_account_snapshot()

        while time.time() < deadline:
            current_package = self.get_current_package_name(driver)

            if current_package == MESSENGER_PKG:
                if self.click_required_messenger_prompt_if_visible(driver) or self.handle_messenger_recovery_or_pin_prompt_if_visible(driver):
                    time.sleep(3)
                    continue

                if self.select_matching_messenger_account(account_snapshot, timeout=8):
                    if self.is_messenger_home_screen(driver):
                        print("[LOGIN] Messenger account selected and home screen detected.")
                        return True
                    time.sleep(2)
                    continue

                if self.is_messenger_home_screen(driver):
                    print("[LOGIN] Messenger opened from Facebook account.")
                    return True

                if self._visible_text_exists(driver, ["Log in", "Đăng nhập", "Password", "Mật khẩu"]):
                    return self.login_to_messenger()

                if self.click_continue_for_saved_account(account_snapshot) or self.click_messenger_entrypoint_if_visible(driver):
                    print("[LOGIN] Continue button clicked in Messenger.")
                    time.sleep(6)
                    continue

                if not self._visible_text_exists(driver, ["Log in", "Đăng nhập", "Password", "Mật khẩu"]):
                    print("[LOGIN] Messenger is open, but home screen is not confirmed yet. Keep waiting.")
                    time.sleep(2)
                    continue

            # Có trường hợp Facebook chưa mở Messenger mà đang đứng ở màn "MỞ MESSENGER".
            if current_package == FACEBOOK_PKG and self.click_open_messenger_button_if_visible():
                continue

            if self.handle_messenger_recovery_or_pin_prompt_if_visible(driver):
                time.sleep(3)
                continue

            # Có trường hợp Facebook mở màn hình xác nhận trước khi nhảy sang Messenger.
            if self.click_continue_for_saved_account(account_snapshot) or self.click_messenger_entrypoint_if_visible(driver):
                print("[LOGIN] Continue button clicked while waiting for Messenger.")
                time.sleep(6)
                continue

            time.sleep(1)

        print("[LOGIN] Timeout while waiting for Messenger auto-login from Facebook.")
        return self.wait_until_messenger_ready_for_navigation(driver, timeout=5)

    def open_messenger(self) -> bool:
        # AUTHORITATIVE ENTRYPOINT:
        # Đây là nơi duy nhất chịu trách nhiệm mở Messenger từ Facebook.
        if not self.ensure_messenger_installed():
            return False

        driver = self.connect_ui_driver()
        current_package = self.get_current_package_name(driver)

        # Quan trọng: nếu tool đã đang ở Messenger thì không được mở Facebook nữa.
        # Trước đây hàm này luôn gọi open_facebook(), nên các vòng quét tin nhắn
        # có thể làm app bị nhảy từ Messenger về Facebook/Trang chủ.
        if current_package == MESSENGER_PKG:
            print("[MOBILE] Messenger is already foreground. Skip opening Facebook/Home.")
            self._handle_pin_screen_and_back()
            if not self.wait_until_messenger_ready_for_navigation(driver, timeout=8):
                print("[MOBILE] Messenger is foreground but not ready for navigation.")
                return False
            self.messenger_checked = True
            return True

        print("[MOBILE] Messenger is installed. Opening Facebook first to enter Messenger with the active Facebook account.")

        if not self.open_facebook():
            return False

        driver = self.connect_ui_driver()
        if driver is not None:
            # Chỉ chuẩn hóa Trang chủ nếu Messenger chưa hiển thị. Tránh lặp lại
            # Home tap + scroll hai lần khiến máy thoát về màn hình điện thoại
            # hoặc kéo thanh công cụ xuống.
            if not self.is_facebook_messenger_entry_visible(driver):
                self.ensure_facebook_home_for_messenger_icon(driver, scroll_to_top=True)

        # Lưu lại account đang hiển thị trên Facebook trước khi bấm icon Messenger.
        account_snapshot = self.snapshot_facebook_account_before_messenger()

        if not self.click_facebook_messenger_icon():
            print("[MOBILE] Messenger was not opened after clicking Messenger MỞ MESSENGER in Facebook.")
            return False

        if not self.wait_for_current_package(MESSENGER_PKG, timeout=25):
            print("[MOBILE] Messenger was not opened after clicking Messenger icon in Facebook.")
            return False

        if not self.complete_messenger_auto_login_from_facebook(timeout=35, account_snapshot=account_snapshot):
            print("[MOBILE] Messenger opened but auto-login from Facebook may not be completed.")
            return False

        if not self.wait_until_messenger_ready_for_navigation(timeout=12):
            print("[MOBILE] Messenger opened but is not ready for navigation after prompt handling.")
            return False

        print("[MOBILE] Messenger is ready through Facebook account.")
        self.messenger_checked = True
        if self.has_pending_crm_send_queue():
            print("[SEND] CRM send queue is pending; skip initial Message Requests crawl after Messenger open.")
        elif not getattr(self, "_skip_open_messenger_initial_request_crawl", False):
            self.crawl_message_requests_once()
        return True

    def _read_visible_ui_text_blob(self, driver) -> str:
        xml = self._dump_hierarchy_xml(driver, reason="read visible UI text")

        labels = []
        if xml:
            try:
                root = ET.fromstring(xml)
                for node in root.iter():
                    if not isinstance(node.tag, str) or not node.attrib:
                        continue
                    label = self._node_label(node)
                    if label:
                        labels.append(label)
            except Exception:
                labels = []

        if labels:
            return " | ".join(labels)

        return xml

    def get_known_account_snapshots(self) -> list[Dict[str, str]]:
        if not self.device_accounts:
            self.sync_account_from_device()

        snapshots: list[Dict[str, str]] = []
        for account in self.device_accounts or []:
            account = _safe_str(account)
            if not account:
                continue
            display_name = _safe_str(self.device_account_names.get(account)) or account
            snapshots.append({
                "account": account,
                "user_id_chat": account,
                "username": display_name,
                "displayName": display_name,
                "name": display_name,
                "device_id": _safe_str(self.device_account_device_ids.get(account) or self.active_device),
                "device_name": _safe_str(
                    self.device_account_device_names.get(account)
                    or self.active_device_name
                    or self.active_device
                ),
                "password": _safe_str(self.device_account_passwords.get(account)),
            })

        if not snapshots and _safe_str(self.user_id_chat):
            snapshots.append(self.get_active_account_snapshot())
        return snapshots

    def get_active_account_snapshot(self) -> Dict[str, str]:
        user_id_chat = _safe_str(self.user_id_chat)
        account = user_id_chat or _safe_str(self.facebook_username)
        display_name = _safe_str(self.device_account_names.get(account)) or _safe_str(self.facebook_username) or account
        device_id = _safe_str(self.device_account_device_ids.get(account) or self.active_device)
        return {
            "account": account,
            "user_id_chat": user_id_chat or account,
            "username": display_name,
            "displayName": display_name,
            "name": display_name,
            "device_id": device_id,
            "device_name": _safe_str(
                self.device_account_device_names.get(account)
                or self.active_device_name
                or device_id
            ),
            "password": _safe_str(self.device_account_passwords.get(account)),
        }

    def get_runtime_current_account_snapshot(self) -> Dict[str, str]:
        device_id = _safe_str(self.active_device)
        if not device_id:
            return {}
        try:
            from util.device_runtime_state import get_current_account
        except Exception:
            return {}

        account = _safe_str(get_current_account(device_id))
        if not account:
            return {}

        display_name = _safe_str(self.device_account_names.get(account)) or account
        return {
            "account": account,
            "user_id_chat": account,
            "username": display_name,
            "displayName": display_name,
            "name": display_name,
            "device_id": device_id,
            "device_name": _safe_str(self.device_account_device_names.get(account) or self.active_device_name or device_id),
            "password": _safe_str(self.device_account_passwords.get(account)),
        }

    def get_configured_current_account_snapshot(self) -> Dict[str, str]:
        device_id = _safe_str(self.active_device)
        if not device_id:
            return {}

        for device in self.load_configured_devices():
            if _safe_str(device.get("device_id")) != device_id:
                continue

            accounts = [item for item in device.get("accounts") or [] if isinstance(item, dict)]
            current_account = _safe_str(device.get("current_account"))
            if not current_account:
                online_account = next(
                    (item for item in accounts if _safe_str(item.get("status")).lower() == "online"),
                    None,
                )
                current_account = _safe_str(online_account.get("account")) if online_account else ""

            if not current_account:
                return {}

            account_info = next(
                (item for item in accounts if _safe_str(item.get("account")) == current_account),
                {},
            )
            display_name = _safe_str(account_info.get("name")) or _safe_str(account_info.get("username")) or current_account
            return {
                "account": current_account,
                "user_id_chat": current_account,
                "username": display_name,
                "displayName": display_name,
                "name": display_name,
                "device_id": device_id,
                "device_name": _safe_str(device.get("device_name")) or self.active_device_name or device_id,
                "password": _safe_str(account_info.get("password")) if isinstance(account_info, dict) else "",
            }

        return {}

    def get_preferred_current_account_snapshot(self) -> Dict[str, str]:
        return self.get_runtime_current_account_snapshot() or self.get_configured_current_account_snapshot()

    def resolve_active_account_snapshot(self, *, force_profile: bool = False) -> Dict[str, str]:
        """Resolve the Facebook account for the currently selected ADB device.

        Multi-device runs must never use a process-global/default account for
        message ownership. This method always scopes lookup to self.active_device.
        """
        if not self.active_device:
            self.connect_to_device()
        self.sync_account_from_device()

        device_id = _safe_str(self.active_device)
        if not device_id:
            return {}

        if not force_profile:
            runtime_snapshot = self.get_runtime_current_account_snapshot()
            if runtime_snapshot:
                self._write_account_snapshot(runtime_snapshot)
                return runtime_snapshot

            configured_snapshot = self.get_configured_current_account_snapshot()
            if configured_snapshot:
                self._write_account_snapshot(configured_snapshot)
                return configured_snapshot

            requested_account = _safe_str(self.user_id_chat)
            if requested_account and requested_account != "default" and self._account_belongs_to_active_device(requested_account):
                requested_snapshot = self._snapshot_by_account(requested_account)
                requested_device = _safe_str(requested_snapshot.get("device_id"))
                if requested_snapshot and (not requested_device or requested_device == device_id):
                    self._write_account_snapshot(requested_snapshot)
                    return requested_snapshot

            active_device_accounts = [
                snapshot
                for snapshot in self.get_known_account_snapshots()
                if _safe_str(snapshot.get("device_id")) == device_id
            ]
            if len(active_device_accounts) == 1:
                self._write_account_snapshot(active_device_accounts[0])
                return active_device_accounts[0]

        profile_snapshot = self.snapshot_facebook_account_before_messenger(force_profile=True)
        profile_account = _safe_str(profile_snapshot.get("user_id_chat") or profile_snapshot.get("account"))
        profile_device = _safe_str(profile_snapshot.get("device_id") or device_id)
        if profile_account and profile_device == device_id:
            self._write_account_snapshot(profile_snapshot)
            return profile_snapshot

        return {}

    def require_active_account_id(self, reason: str = "") -> str:
        account = self.current_account_id()
        current_device = _safe_str(self.active_device)
        snapshot_device = _safe_str(self.current_facebook_account_snapshot.get("device_id"))
        if account and current_device and snapshot_device and snapshot_device != current_device:
            account = ""
        if account:
            return account

        snapshot = self.resolve_active_account_snapshot()
        account = _safe_str(snapshot.get("user_id_chat") or snapshot.get("account"))
        if account:
            return account

        snapshot = self.resolve_active_account_snapshot(force_profile=True)
        account = _safe_str(snapshot.get("user_id_chat") or snapshot.get("account"))
        if account:
            return account

        detail = f" for {reason}" if reason else ""
        raise RuntimeError(f"Cannot determine active Facebook account{detail} on device {self.active_device or 'unknown'}.")

    def _account_match_terms(self, account_snapshot: Optional[Dict[str, str]]) -> list[str]:
        snapshot = account_snapshot or {}
        raw_terms = [
            snapshot.get("displayName"),
            snapshot.get("name"),
            snapshot.get("username"),
            snapshot.get("account"),
            snapshot.get("user_id_chat"),
        ]
        for name_value in [snapshot.get("displayName"), snapshot.get("name"), snapshot.get("username")]:
            for token in re.split(r"\s+", _safe_str(name_value)):
                if len(token) >= 2:
                    raw_terms.append(token)
        terms = []
        seen = set()
        for term in raw_terms:
            value = _safe_str(term)
            if not value or value == "default":
                continue
            normalized = self._normalize_ui_text(value)
            if len(normalized) < 2 or normalized in seen:
                continue
            seen.add(normalized)
            terms.append(value)
        return terms

    def _match_account_from_visible_text(self, visible_blob: str) -> Dict[str, str]:
        """Find the best matching configured account from the given text blob.

        This implements a scoring system similar to module.fb_friends.name_match_score.
        """
        normalized_blob = self._normalize_ui_text(visible_blob)
        if not normalized_blob:
            return {}

        blob_tokens = set(normalized_blob.split())
        known_accounts = self.get_known_account_snapshots()
        best_snapshot: Dict[str, str] = {}
        best_score = 0

        for snapshot in known_accounts:
            max_account_score = 0
            for term in self._account_match_terms(snapshot):
                term_norm = self._normalize_ui_text(term)
                if not term_norm:
                    continue

                score = 0
                if term_norm == normalized_blob:
                    score = 1000
                elif term_norm in normalized_blob:
                    score = 700
                else:
                    term_tokens = set(term_norm.split())
                    overlap = term_tokens & blob_tokens
                    if overlap:
                        ratio = len(overlap) / max(len(term_tokens), 1)
                        if ratio >= 0.8:
                            score = 450
                        elif ratio >= 0.6:
                            score = 250
                        elif ratio >= 0.5:
                            score = 120
                        else:
                            score = 50

                if len(term_norm.split()) >= 3 and len(normalized_blob.split()) >= 3:
                    score += 20

                if score > max_account_score:
                    max_account_score = score

            if max_account_score > best_score:
                best_score = max_account_score
                best_snapshot = snapshot

        return best_snapshot if best_score >= 150 else {}

    def get_facebook_profile_name(self, driver=None) -> str:
        """Identify the current Facebook account name by visiting the profile page.

        This mimics the logic in module.fb_friends.get_facebook_account_name.
        """
        driver = driver or self.connect_ui_driver()
        if driver is None:
            return "unknown_user"

        print("[ACCOUNT] Navigating to Facebook profile to identify active account...")
        try:
            if not self.open_facebook():
                return "unknown_user"

            profile_entry_texts = [
                "Đi tới trang cá nhân",
                "Trang cá nhân",
                "Profile",
                "ảnh đại diện của bạn",
            ]
            if not self._click_best_ui_text(driver, profile_entry_texts, allow_contains=True, reason="Profile entry"):
                print("[ACCOUNT] Profile entry not found on Home, trying via Menu tab.")
                if not self._click_best_ui_text(driver, ["Menu", "Trình đơn"], allow_contains=False, reason="Menu tab"):
                    return "unknown_user"
                time.sleep(2)
                self._tap_screen_ratio(0.3, 0.15, reason="Menu profile header")

            time.sleep(4)
            xml = driver.dump_hierarchy()
            root = ET.fromstring(xml)
            screen_w, screen_h = self._get_screen_size()

            blacklist = {
                'đang nghĩ về', 'thinking about', "what's on your mind", 'bài viết', 'posts', 'ảnh', 'photos',
                'bạn bè', 'friends', 'theo dõi', 'follow', 'tin nhắn', 'message', 'giới thiệu', 'about',
                'xem thêm', 'see more', 'chỉnh sửa', 'edit', 'cài đặt', 'settings', 'hoạt động', 'activity',
                'video', 'story', 'reels', 'live', 'người bạn', 'mutual friends', 'bạn chung', 'common friends',
                'thêm story', 'add story', 'tạo', 'create', 'chia sẻ', 'share', 'thông tin cá nhân', 'xem tất cả', 'tất cả',
                '1 giờ', '2 giờ', '3 giờ', '4 giờ', 'thông báo', 'notification', 'chrome'
            }
            
            vietnamese_name_patterns = ['bùi', 'nguyễn', 'trần', 'lê', 'phạm', 'hoàng', 'phan', 'vũ', 'đặng', 'võ', 'đỗ', 'hồ', 'dương']

            candidates = []
            for node in root.iter("node"):
                text = self._node_label(node)
                if not text or any(p in text.lower() for p in blacklist):
                    continue
                if not any(c.isalpha() for c in text) or len(text) < 2 or len(text) > 40:
                    continue

                bounds = _safe_str(node.attrib.get("bounds"))
                parsed = self._parse_bounds(bounds)
                if not parsed or parsed[1] < 80:
                    continue

                class_name = _safe_str(node.attrib.get("class", ""))
                words = text.split()
                if len(words) > 5:
                    continue

                score = 0
                if len(words) >= 2:
                    score += 100 if all(w[0].isupper() if w and w[0].isalpha() else True for w in words) else 60
                elif len(words) == 1:
                    score += 70 if text[0].isupper() else 30

                # Relative height scoring from fb_friends
                upper_third = int(screen_h * 0.36)
                upper_half = int(screen_h * 0.5)
                lower_bad = int(screen_h * 0.7)

                if parsed[1] <= upper_third: score += 150
                elif parsed[1] <= upper_half: score += 90
                elif parsed[1] >= lower_bad: score -= 140

                if "Button" in class_name: score -= 90
                if int(screen_w * 0.15) <= (parsed[0] + parsed[2]) // 2 <= int(screen_w * 0.85): score += 20
                
                if any(p in text.lower() for p in vietnamese_name_patterns):
                    score += 35

                candidates.append({"text": text, "score": score, "y1": parsed[1]})

            if candidates:
                candidates.sort(key=lambda x: (-x["score"], x["y1"]))
                clean_name = re.sub(r"\s+", "_", candidates[0]["text"].strip()).strip("_")
                driver.press("back")
                return clean_name
        except Exception as e:
            print(f"[ACCOUNT] Error extracting profile name: {e}")
        return "unknown_user"

    def _active_account_snapshot_file(self) -> Path:
        return Path(os.getenv("FB_1_1_ACTIVE_ACCOUNT_FILE", PROJECT_ROOT / "active_facebook_account.json"))

    def load_cached_active_account_snapshot(self, max_age_seconds: int = DEFAULT_ACTIVE_ACCOUNT_CACHE_SECONDS) -> Dict[str, str]:
        """Return a recent profile-verified account snapshot for this device.

        The Messenger priority flow creates a new MobileOneToOneMessenger instance
        in several places. Without this cache every instance re-opens the Facebook
        profile just to identify the same account. The cache is only trusted when
        it belongs to the current device and is still fresh.
        """
        snapshot_file = self._active_account_snapshot_file()
        try:
            payload = json.loads(snapshot_file.read_text(encoding="utf-8"))
        except Exception:
            return {}
        if not isinstance(payload, dict):
            return {}

        cached_device_id = _safe_str(payload.get("device_id"))
        if self.active_device and cached_device_id and cached_device_id != _safe_str(self.active_device):
            return {}

        captured_at = _safe_str(payload.get("captured_at"))
        if captured_at and max_age_seconds > 0:
            try:
                captured_dt = datetime.fromisoformat(captured_at)
                age = datetime.now(captured_dt.tzinfo).timestamp() - captured_dt.timestamp()
                if age > max_age_seconds:
                    return {}
            except Exception:
                return {}

        account = _safe_str(payload.get("user_id_chat") or payload.get("account"))
        if not account or account == "default":
            return {}

        # Rehydrate password/display metadata from the current device config.
        configured = self._snapshot_by_account(account)
        if configured:
            configured.update({key: value for key, value in payload.items() if _safe_str(value)})
            return configured
        return payload

    def _snapshot_by_account(self, account: str) -> Dict[str, str]:
        account = _safe_str(account)
        if not account:
            return {}
        for snapshot in self.get_known_account_snapshots():
            if _safe_str(snapshot.get("account")) == account or _safe_str(snapshot.get("user_id_chat")) == account:
                return snapshot
        return {}

    def _account_belongs_to_active_device(self, account: str) -> bool:
        account = _safe_str(account)
        if not account or account == "default":
            return False
        if not self.active_device:
            return True
        snapshot = self._snapshot_by_account(account)
        snapshot_device = _safe_str(snapshot.get("device_id"))
        return not snapshot_device or snapshot_device == _safe_str(self.active_device)

    def _write_account_snapshot(self, snapshot: Dict[str, str]) -> None:
        if not snapshot:
            return
        self.current_facebook_account_snapshot = snapshot
        self.active_account_verified = True
        cache_key = _safe_str(snapshot.get("device_id") or self.active_device or "default")
        _SESSION_ACTIVE_ACCOUNT_CACHE[cache_key] = dict(snapshot)
        self.user_id_chat = _safe_str(snapshot.get("user_id_chat") or snapshot.get("account") or self.user_id_chat)
        self.facebook_username = _safe_str(snapshot.get("username") or snapshot.get("name") or snapshot.get("account") or self.facebook_username)
        snapshot_password = _safe_str(snapshot.get("password"))
        if snapshot_password:
            self.facebook_password = snapshot_password
        snapshot_device_id = _safe_str(snapshot.get("device_id"))
        if snapshot_device_id:
            self.device_account_device_ids[self.user_id_chat] = snapshot_device_id
            self.device_account_device_names[self.user_id_chat] = _safe_str(snapshot.get("device_name")) or snapshot_device_id
            try:
                from util.device_runtime_state import set_online_account

                set_online_account(
                    snapshot_device_id,
                    account=self.user_id_chat,
                    username=self.facebook_username,
                )
            except Exception as exc:
                print(f"[ACCOUNT] Cannot update runtime active account state: {exc}")

        snapshot_file = self._active_account_snapshot_file()
        try:
            payload = {
                **snapshot,
                "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            }
            snapshot_file.write_text(json.dumps(payload, ensure_ascii=False, indent=4), encoding="utf-8")
            print(
                "[ACCOUNT] Saved active Facebook account snapshot: "
                f"{payload.get('displayName') or payload.get('username')} ({payload.get('user_id_chat')})"
            )
        except Exception as exc:
            print(f"[ACCOUNT] Cannot save active account snapshot to {snapshot_file}: {exc}")

    def snapshot_facebook_account_before_messenger(self, force_profile: bool = False) -> Dict[str, str]:
        """Identify the current Facebook account before opening Messenger.

        The first reliable detection may still visit the Facebook profile. After
        that, the verified snapshot is reused from runtime/file cache so repeated
        Messenger checks do not keep navigating to the profile page.
        """
        driver = self.connect_ui_driver()
        self.sync_account_from_device()

        if self.active_account_verified and self.current_facebook_account_snapshot and not force_profile:
            snapshot = self.current_facebook_account_snapshot
            print(
                "[ACCOUNT] Reusing in-memory active Facebook account snapshot: "
                f"{snapshot.get('displayName') or snapshot.get('username')} ({snapshot.get('user_id_chat')})"
            )
            return snapshot

        session_cache_key = _safe_str(self.active_device or "default")
        session_snapshot = _SESSION_ACTIVE_ACCOUNT_CACHE.get(session_cache_key)
        if session_snapshot and not force_profile:
            self.current_facebook_account_snapshot = dict(session_snapshot)
            self.active_account_verified = True
            print(
                "[ACCOUNT] Reusing session active Facebook account snapshot: "
                f"{session_snapshot.get('displayName') or session_snapshot.get('username')} ({session_snapshot.get('user_id_chat')})"
            )
            return dict(session_snapshot)

        if DEFAULT_USE_ACTIVE_ACCOUNT_FILE_CACHE and not force_profile:
            cached_snapshot = self.load_cached_active_account_snapshot()
            if cached_snapshot:
                self._write_account_snapshot(cached_snapshot)
                print(
                    "[ACCOUNT] Reusing cached active Facebook account snapshot: "
                    f"{cached_snapshot.get('displayName') or cached_snapshot.get('username')} ({cached_snapshot.get('user_id_chat')})"
                )
                return cached_snapshot

        snapshot: Dict[str, str] = {}
        preferred_snapshot = self.get_preferred_current_account_snapshot()

        should_profile_check = force_profile or DEFAULT_FORCE_PROFILE_IDENTIFY_ON_FIRST_RUN or not preferred_snapshot
        if driver is not None and should_profile_check:
            profile_name = self.get_facebook_profile_name(driver)
            if profile_name and profile_name != "unknown_user":
                matched = self._match_account_from_visible_text(profile_name.replace("_", " "))
                if matched:
                    snapshot = matched
                    print(f"[ACCOUNT] Identified active account from profile: {snapshot.get('displayName')}")

        if driver is not None and not snapshot:
            visible_blob = self._read_visible_ui_text_blob(driver)
            snapshot = self._match_account_from_visible_text(visible_blob)
            if snapshot:
                print(f"[ACCOUNT] Identified active account from visible Facebook UI: {snapshot.get('displayName')}")

        if not snapshot:
            snapshot = preferred_snapshot
            if snapshot:
                print(f"[ACCOUNT] Using preferred runtime/config account: {snapshot.get('displayName')}")

        if not snapshot:
            active_snapshot = self.get_active_account_snapshot()
            if active_snapshot.get("user_id_chat") != "default":
                snapshot = active_snapshot
                print(f"[ACCOUNT] Falling back to default runtime account: {snapshot.get('displayName')}")

        if snapshot:
            self._write_account_snapshot(snapshot)
            self.register_bot_to_crm(force=True)
            return snapshot
        return {}

    def _find_matching_account_node(self, driver, account_snapshot: Optional[Dict[str, str]]) -> Optional[Dict[str, str]]:
        terms = [self._normalize_ui_text(term) for term in self._account_match_terms(account_snapshot)]
        terms = [term for term in terms if term]
        if not terms:
            return None

        try:
            root = ET.fromstring(driver.dump_hierarchy())
        except Exception as exc:
            print(f"[ACCOUNT] Cannot dump Messenger account chooser UI: {exc}")
            return None

        blocked_terms = [
            self._normalize_ui_text("not you"),
            self._normalize_ui_text("không phải"),
            self._normalize_ui_text("switch account"),
            self._normalize_ui_text("đổi tài khoản"),
            self._normalize_ui_text("remove"),
            self._normalize_ui_text("gỡ"),
        ]
        candidates = []
        for node in root.iter("node"):
            label = self._node_label(node)
            if not label:
                continue
            normalized_label = self._normalize_ui_text(label)
            if any(blocked in normalized_label for blocked in blocked_terms):
                continue
            matched_terms = [term for term in terms if term in normalized_label]
            if not matched_terms:
                continue
            bounds = _safe_str(node.attrib.get("bounds"))
            if not bounds:
                continue
            parsed_bounds = self._parse_bounds(bounds)
            area = 0
            if parsed_bounds:
                x1, y1, x2, y2 = parsed_bounds
                area = (x2 - x1) * (y2 - y1)
            candidates.append({
                "label": label,
                "bounds": bounds,
                "clickable": node.attrib.get("clickable"),
                "class": node.attrib.get("class"),
                "score": len(matched_terms) * 10 + (5 if node.attrib.get("clickable") == "true" else 0) + min(area // 10000, 10),
            })

        if not candidates:
            return None

        candidates.sort(key=lambda item: item["score"], reverse=True)
        return candidates[0]

    def click_continue_for_saved_account(self, account_snapshot: Optional[Dict[str, str]] = None) -> bool:
        driver = self.connect_ui_driver()
        if driver is None:
            return False

        terms = self._account_match_terms(account_snapshot)
        continue_texts = []
        for term in terms:
            continue_texts.extend([
                ["Tiếp tục", "Continue", "Tiếp tục dưới tên", "Continue as"],
                f"Continue with {term}",
                f"Tiếp tục dưới tên {term}",
                f"Tiếp tục với {term}",
            ])

        if continue_texts and self._click_ui_text(driver, continue_texts):
            return True

        # Chỉ bấm nút Continue chung khi UI không hiển thị danh sách nhiều account khác nhau,
        # tránh chọn nhầm account khi Messenger hiện màn hình chọn tài khoản.
        visible_blob = self._read_visible_ui_text_blob(driver)
        normalized_blob = self._normalize_ui_text(visible_blob)
        target_visible = any(self._normalize_ui_text(term) in normalized_blob for term in terms)
        visible_known_accounts = 0
        for known_snapshot in self.get_known_account_snapshots():
            known_terms = self._account_match_terms(known_snapshot)
            if any(self._normalize_ui_text(term) in normalized_blob for term in known_terms):
                visible_known_accounts += 1

        if visible_known_accounts > 1 and not target_visible:
            print("[ACCOUNT] Skip generic Continue because multiple known accounts are visible and target account was not matched.")
            return False

        generic_continue_texts = [
                ["Tiếp tục", "Continue", "Tiếp tục dưới tên", "Continue as"],
            "Continue with",
            "Continue",
            "Tiếp tục dưới tên",
            "Tiếp tục với",
            "Tiếp tục",
        ]
        return self._click_ui_text(driver, generic_continue_texts)

    def select_matching_messenger_account(
        self,
        account_snapshot: Optional[Dict[str, str]] = None,
        timeout: int = 20,
    ) -> bool:
        driver = self.connect_ui_driver()
        if driver is None:
            return False

        snapshot = account_snapshot or self.current_facebook_account_snapshot or self.get_runtime_current_account_snapshot()
        deadline = time.time() + max(1, timeout)
        selected = False

        while time.time() < deadline:
            if self.click_required_messenger_prompt_if_visible(driver) or self.handle_messenger_recovery_or_pin_prompt_if_visible(driver):
                selected = True
                time.sleep(3)
                continue

            if self.is_messenger_home_screen(driver):
                return selected

            node = self._find_matching_account_node(driver, snapshot)
            if node:
                print(
                    "[ACCOUNT] Selecting Messenger account: "
                    f"label={node['label']} | bounds={node['bounds']}"
                )
                self._tap_bounds_center(node["bounds"], reason="matching Messenger account")
                selected = True
                time.sleep(4)
                continue

            if self.click_continue_for_saved_account(snapshot):
                selected = True
                time.sleep(5)
                continue

            time.sleep(1)

        return selected or self.is_messenger_home_screen(driver)

    def is_messenger_home_screen(self, driver=None) -> bool:
        driver = driver or self.connect_ui_driver()
        if driver is None:
            return False
        if self.get_current_package_name(driver) != MESSENGER_PKG and not self.is_current_package(MESSENGER_PKG):
            return False

        blob = self._read_visible_ui_text_blob(driver)
        normalized_blob = self._normalize_ui_text(blob)
        if not normalized_blob:
            return False

        negative_terms = [
            "log in",
            "đăng nhập",
            "password",
            "mật khẩu",
                ["Tiếp tục", "Continue", "Tiếp tục dưới tên", "Continue as"],
            "tiếp tục dưới tên",
            "tiếp tục với",
            # Blocking Messenger PIN/setup screens. These must override generic
            # labels like "Tin nhắn" that can appear in Android notifications.
            "mã pin",
            "tạo mã pin",
            "xác nhận mã pin",
            "nhập mã pin",
            "thiết lập mã pin",
            "trường mã khóa",
            "chỉnh sửa 1/6",
            "create pin",
            "confirm pin",
            "enter pin",
        ]
        if any(self._normalize_ui_text(term) in normalized_blob for term in negative_terms):
            return False

        positive_terms = [
            "search",
            "tìm kiếm",
            "chats",
            "đoạn chat",
            "message requests",
            "tin nhắn đang chờ",
            "menu",
        ]
        return any(self._normalize_ui_text(term) in normalized_blob for term in positive_terms)

    def _find_bottom_messenger_menu_tab_node(self, driver=None) -> Optional[Dict[str, Any]]:
        """Find only the real bottom-right Messenger Menu tab.

        The home screen can also contain row actions such as "Menu lựa chọn khác".
        Those are excluded by hard coordinate guards and block terms.
        """
        driver = driver or self.connect_ui_driver()
        if driver is None:
            return None

        try:
            root = ET.fromstring(driver.dump_hierarchy())
        except Exception as exc:
            print(f"[REQUESTS] Cannot dump hierarchy to find bottom Menu tab: {exc}")
            return None

        width, height = self._get_screen_size()
        dump_width = 0
        dump_height = 0
        for node in root.iter("node"):
            parsed = self._parse_bounds(_safe_str(node.attrib.get("bounds")))
            if not parsed:
                continue
            _, _, x2, y2 = parsed
            dump_width = max(dump_width, x2)
            dump_height = max(dump_height, y2)
        if dump_width and dump_height:
            width, height = dump_width, dump_height

        candidates: list[Dict[str, Any]] = []

        for node in root.iter("node"):
            label = self._node_label(node)
            if not label:
                continue

            norm = self._normalize_ui_text(label)
            if not (
                "tab menu" in norm
                or ("menu" in norm and re.search(r"\btab\s+\d+/\d+\b", norm))
                or norm == "menu"
            ):
                continue

            if "lua chon khac" in norm or "more options" in norm:
                continue

            bounds = _safe_str(node.attrib.get("bounds"))
            parsed = self._parse_bounds(bounds)
            if not parsed:
                continue

            x1, y1, x2, y2 = parsed
            cx = (x1 + x2) // 2
            cy = (y1 + y2) // 2

            # Hard guard for the real bottom navigation Menu tab.
            if cx < int(width * 0.55) or cy < int(height * 0.82):
                continue

            class_name = _safe_str(node.attrib.get("class"))
            clickable = node.attrib.get("clickable") == "true"

            score = 1000
            if "tab menu" in norm:
                score += 400
            if re.search(r"\btab\s+\d+/\d+\b", norm):
                score += 300
            if clickable:
                score += 100
            if node.attrib.get("selected") == "true":
                score += 50

            candidates.append({
                "label": label,
                "bounds": bounds,
                "clickable": node.attrib.get("clickable"),
                "class": class_name,
                "score": score,
            })

        if not candidates:
            return None

        candidates.sort(key=lambda item: item["score"], reverse=True)
        return candidates[0]

    def click_messenger_bottom_menu_tab(self, driver=None) -> bool:
        """Open the Messenger bottom-right Menu tab using window dump first."""
        driver = driver or self.connect_ui_driver()
        if driver is None:
            return False

        labels = self._dump_visible_ui_labels(driver, limit=50)
        if labels:
            print("[REQUESTS] Visible labels before bottom Menu click: " + " | ".join(labels))

        target = self._find_bottom_messenger_menu_tab_node(driver)
        if target:
            print(
                "[REQUESTS] Bottom Menu tab matched from dump: "
                f"label={target['label']} | bounds={target['bounds']} | "
                f"clickable={target['clickable']} | class={target['class']} | score={target['score']}"
            )
            self._tap_bounds_center(target["bounds"], reason="Messenger bottom-right Menu tab from dump")
            time.sleep(2)
            return True

        print("[REQUESTS] Bottom Menu text not found in hierarchy. Using bottom-right fallback tap at 0.83,0.91.")
        self._tap_screen_ratio(0.83, 0.91, reason="Messenger bottom-right Menu fallback")
        time.sleep(2)
        return True

    def _find_message_requests_menu_entry_node(self, driver=None) -> Optional[Dict[str, Any]]:
        """Find the Message Requests row/button on the Messenger Menu screen."""
        driver = driver or self.connect_ui_driver()
        if driver is None:
            return None

        try:
            root = ET.fromstring(driver.dump_hierarchy())
        except Exception as exc:
            print(f"[REQUESTS] Cannot dump hierarchy to find Message Requests entry: {exc}")
            return None

        width, height = self._get_screen_size()
        candidates: list[Dict[str, Any]] = []
        target_terms = [
            "tin nhan dang cho",
            "message requests",
            "yeu cau nhan tin",
            "loi moi nhan tin",
        ]

        for node in root.iter("node"):
            label = self._node_label(node)
            if not label:
                continue
            norm = self._normalize_ui_text(label)
            if not any(term in norm for term in target_terms):
                continue

            bounds = _safe_str(node.attrib.get("bounds"))
            parsed = self._parse_bounds(bounds)
            if not parsed:
                continue

            x1, y1, x2, y2 = parsed
            cy = (y1 + y2) // 2
            if cy < int(height * 0.18) or cy > int(height * 0.88):
                continue

            class_name = _safe_str(node.attrib.get("class"))
            clickable = node.attrib.get("clickable") == "true"
            area = max(0, (x2 - x1) * (y2 - y1))

            score = 1000
            if clickable:
                score += 150
            if "button" in class_name.lower():
                score += 120
            if x1 <= int(width * 0.08) and x2 >= int(width * 0.80):
                score += 80
            score += min(area // 10000, 50)

            candidates.append({
                "label": label,
                "bounds": bounds,
                "clickable": node.attrib.get("clickable"),
                "class": class_name,
                "score": score,
            })

        if not candidates:
            return None

        candidates.sort(key=lambda item: item["score"], reverse=True)
        return candidates[0]

    def click_message_requests_entry_from_menu(self, driver=None, max_swipes: int = 4) -> bool:
        """Click the Message Requests entry after the Messenger Menu tab is open."""
        driver = driver or self.connect_ui_driver()
        if driver is None:
            return False

        request_texts = [
            "Message requests",
            "Tin nhắn đang chờ",
            "Tin nhắn chờ",
            "Requests",
            "Yêu cầu nhắn tin",
            "Lời mời nhắn tin",
        ]

        attempts = max(1, max_swipes + 1)
        for attempt in range(attempts):
            strict_target = self._find_message_requests_menu_entry_node(driver)
            if strict_target:
                print(
                    "[REQUESTS] Message Requests strict menu entry matched: "
                    f"label={strict_target['label']} | bounds={strict_target['bounds']} | "
                    f"clickable={strict_target['clickable']} | class={strict_target['class']} | "
                    f"score={strict_target['score']}"
                )
                self._tap_bounds_center(strict_target["bounds"], reason="Message Requests strict menu entry")
                time.sleep(3)
                self.handle_message_requests_intro_prompt(driver)
                return True

            if self._click_best_ui_text(
                driver,
                request_texts,
                allow_contains=True,
                reason=f"Open Message Requests from Messenger Menu attempt {attempt + 1}",
            ):
                print("[REQUESTS] Opened Message Requests from Messenger Menu.")
                time.sleep(3)
                self.handle_message_requests_intro_prompt(driver)
                return True

            labels = self._dump_visible_ui_labels(driver, limit=30)
            if labels:
                print(
                    f"[REQUESTS] Message Requests entry not visible on menu attempt {attempt + 1}. "
                    + "Visible labels: "
                    + " | ".join(labels)
                )

            if attempt < attempts - 1:
                width, height = self._get_screen_size()
                print("[REQUESTS] Scrolling Messenger Menu to find Message Requests.")
                self.run_adb([
                    "shell",
                    "input",
                    "swipe",
                    str(width // 2),
                    str(int(height * 0.82)),
                    str(width // 2),
                    str(int(height * 0.35)),
                    "350",
                ])
                time.sleep(1.5)

        return False

    def open_message_requests_screen(self) -> bool:
        driver = self.connect_ui_driver()
        if driver is None:
            return False

        if not self.is_current_package(MESSENGER_PKG):
            return False

        ready_timeout = int(os.getenv("FB_1_1_MESSAGE_REQUESTS_READY_TIMEOUT_SECONDS", "4"))
        if not self.wait_until_messenger_ready_for_navigation(driver, timeout=ready_timeout):
            print("[REQUESTS] Messenger is not ready for message request navigation.")
            return False

        # Try bottom-right menu tab first (new layout)
        print("[REQUESTS] Accessing Messenger Menu.")
        menu_opened = self.click_messenger_bottom_menu_tab(driver)
        
        # If bottom menu fails, try top-left hamburger (older/standard layout)
        if not menu_opened:
            print("[REQUESTS] Bottom Menu not found, trying top-left Hamburger Menu.")
            menu_opened = self.click_messenger_hamburger_menu(driver)

        if not menu_opened:
            print("[REQUESTS] Cannot open Messenger Menu.")
            return False

        max_swipes = int(os.getenv("FB_1_1_MESSAGE_REQUESTS_MENU_MAX_SWIPES", "1"))
        if self.click_message_requests_entry_from_menu(driver, max_swipes=max_swipes):
            return True

        labels = self._dump_visible_ui_labels(driver, limit=40)
        print("[REQUESTS] Message Requests entry not found after opening bottom Menu. Visible labels: " + " | ".join(labels))
        return False

    def handle_message_requests_intro_prompt(self, driver=None) -> bool:
        driver = driver or self.connect_ui_driver()
        if driver is None:
            return False

        blob = self._normalize_ui_text(self._read_visible_ui_text_blob(driver))
        prompt_terms = [
            "chỉ nhận tin nhắn bạn muốn",
            "chi nhan tin nhan ban muon",
            "control who can message you",
            "choose who can message you",
        ]
        if any(term in blob for term in prompt_terms):
            # Click on the prompt text
            clicked = self._click_best_ui_text(
                driver,
                ["Chỉ nhận tin nhắn bạn muốn", "Control who can message you", "Choose who can message you"],
                allow_contains=True,
                reason="Message requests intro prompt selection",
            )
            if clicked:
                time.sleep(2)
                # Then click continue
                continue_clicked = self._click_best_ui_text(
                    driver,
                    ["Tiếp tục", "Continue", "OK", "Đã hiểu", "Got it"],
                    allow_contains=True,
                    reason="Message requests intro prompt continue",
                )
                if continue_clicked:
                    time.sleep(2)
                return clicked or continue_clicked
            return False

        # Original logic for continue button
        clicked = self._click_best_ui_text(
            driver,
            ["Tiếp tục", "Continue", "OK", "Đã hiểu", "Got it"],
            allow_contains=True,
            reason="Message requests intro prompt",
        )
        if clicked:
            time.sleep(2)
        return clicked

    def is_message_request_detail_screen(self, driver=None) -> bool:
        """Detect the detail screen of one Messenger message request.

        The request detail screen usually contains the bottom actions
        Chặn / Xóa / Chấp nhận, or the text "các bạn không phải là bạn bè".
        """
        driver = driver or self.connect_ui_driver()
        if driver is None:
            return False

        if self.get_current_package_name(driver) != MESSENGER_PKG and not self.is_current_package(MESSENGER_PKG):
            return False

        blob = self._normalize_ui_text(self._read_visible_ui_text_blob(driver))
        if not blob:
            return False

        screen_w, screen_h = self._get_screen_size()
        # Chat lists can contain request/detail-like text in row previews.
        # If list navigation/filter UI is visible, do not press Back.
        for node in self._dump_nodes(driver):
            norm = node.get("norm", "")
            x1, y1, x2, _y2 = node.get("parsed_bounds", (0, 0, 0, 0))
            if y1 >= int(screen_h * 0.82) and (
                "doan chat" in norm
                or "tab menu" in norm
                or re.search(r"\btab\s+\d+/\d+\b", norm)
            ):
                return False
            if norm in {"chua doc", "unread", "tat ca", "all"}:
                return False
            if (
                x1 <= 8
                and x2 >= int(screen_w * 0.85)
                and ("tin nhan moi" in norm or "new message" in norm or "new messages" in norm)
            ):
                return False

        detail_terms = [
            "chấp nhận", "accept",
            "chặn", "block",
            "xóa", "delete",
            "các bạn không phải là bạn bè",
            "cac ban khong phai la ban be",
            "you are not facebook friends",
            "không phải là bạn bè",
            "khong phai la ban be",
            "tin nhắn và cuộc gọi được bảo mật",
        ]
        for term in detail_terms:
            normalized_term = self._normalize_ui_text(term)
            if "bao mat" in normalized_term or "secured" in normalized_term:
                continue
            if normalized_term and normalized_term in blob:
                return True
        return False

    def _find_message_request_accept_node(self, driver=None) -> Optional[Dict[str, Any]]:
        driver = driver or self.connect_ui_driver()
        if driver is None:
            return None

        try:
            root = ET.fromstring(driver.dump_hierarchy())
        except Exception as exc:
            print(f"[REQUESTS] Cannot dump UI to find Accept button: {exc}")
            return self._find_message_request_accept_node_from_window_dump()

        accept_terms = [
            self._normalize_ui_text("Chấp nhận"),
            self._normalize_ui_text("Accept"),
            self._normalize_ui_text("Trả lời"),
            self._normalize_ui_text("Reply"),
        ]
        blocked_terms = [
            self._normalize_ui_text("Không chấp nhận"),
            self._normalize_ui_text("Don't accept"),
            self._normalize_ui_text("Chấp nhận cuộc gọi"),
        ]

        candidates: list[Dict[str, Any]] = []
        screen_w, screen_h = self._get_screen_size()
        for node in root.iter("node"):
            label = self._node_label(node)
            if not label:
                continue

            normalized_label = self._normalize_ui_text(label)
            if any(term and term in normalized_label for term in blocked_terms):
                continue

            exact_match = any(term == normalized_label for term in accept_terms)
            contains_match = any(term and term in normalized_label for term in accept_terms)
            if not exact_match and not contains_match:
                continue

            bounds = _safe_str(node.attrib.get("bounds"))
            parsed_bounds = self._parse_bounds(bounds)
            if not parsed_bounds:
                continue

            x1, y1, x2, y2 = parsed_bounds
            class_name = _safe_str(node.attrib.get("class"))
            clickable = node.attrib.get("clickable") == "true"
            enabled = node.attrib.get("enabled", "true") != "false"
            center_x = (x1 + x2) // 2
            center_y = (y1 + y2) // 2

            score = 0
            if exact_match:
                score += 120
            if contains_match:
                score += 40
            if clickable:
                score += 40
            if enabled:
                score += 20
            if "button" in class_name.lower():
                score += 35
            # On the request detail screen, Accept is normally the bottom-right action.
            if y1 >= int(screen_h * 0.72):
                score += 45
            if center_x >= int(screen_w * 0.55):
                score += 35
            # Avoid picking text links inside the profile/header area.
            if y2 <= int(screen_h * 0.45):
                score -= 45

            candidates.append({
                "label": label,
                "bounds": bounds,
                "clickable": node.attrib.get("clickable"),
                "enabled": node.attrib.get("enabled"),
                "class": class_name,
                "score": score,
            })

        if not candidates:
            return self._find_message_request_accept_node_from_window_dump()

        candidates.sort(key=lambda item: item["score"], reverse=True)
        return candidates[0]

    def _find_message_request_accept_node_from_window_dump(self) -> Optional[Dict[str, Any]]:
        root = self.refresh_window_dump_root(reason="find Accept button")
        if root is None:
            return None

        accept_terms = [
            self._normalize_ui_text("Chấp nhận"),
            self._normalize_ui_text("Accept"),
            self._normalize_ui_text("Trả lời"),
            self._normalize_ui_text("Reply"),
        ]
        blocked_terms = [
            self._normalize_ui_text("Không chấp nhận"),
            self._normalize_ui_text("Don't accept"),
            self._normalize_ui_text("Chấp nhận cuộc gọi"),
        ]

        candidates: list[Dict[str, Any]] = []
        screen_w, screen_h = self._get_screen_size()
        for node in root.iter("node"):
            label = self._node_label(node)
            if not label:
                continue

            normalized_label = self._normalize_ui_text(label)
            if any(term and term in normalized_label for term in blocked_terms):
                continue

            exact_match = any(term == normalized_label for term in accept_terms)
            contains_match = any(term and term in normalized_label for term in accept_terms)
            if not exact_match and not contains_match:
                continue

            bounds = _safe_str(node.attrib.get("bounds"))
            parsed_bounds = self._parse_bounds(bounds)
            if not parsed_bounds:
                continue

            x1, y1, x2, y2 = parsed_bounds
            class_name = _safe_str(node.attrib.get("class"))
            clickable = node.attrib.get("clickable") == "true"
            enabled = node.attrib.get("enabled", "true") != "false"
            center_x = (x1 + x2) // 2

            score = 0
            if exact_match:
                score += 120
            if contains_match:
                score += 40
            if clickable:
                score += 40
            if enabled:
                score += 20
            if "button" in class_name.lower():
                score += 35
            if y1 >= int(screen_h * 0.72):
                score += 45
            if center_x >= int(screen_w * 0.55):
                score += 35
            if y2 <= int(screen_h * 0.45):
                score -= 45

            candidates.append({
                "label": label,
                "bounds": bounds,
                "clickable": node.attrib.get("clickable"),
                "enabled": node.attrib.get("enabled"),
                "class": class_name,
                "score": score,
                "source": "window_dump",
            })

        if not candidates:
            labels = self._window_dump_labels(root, reason="find Accept button")
            print("[WINDOW_DUMP] Accept button not found. Visible labels: " + " | ".join(labels[:60]))
            return None

        candidates.sort(key=lambda item: item["score"], reverse=True)
        return candidates[0]

    def _accept_button_visible(self, driver=None) -> bool:
        return self._find_message_request_accept_node(driver) is not None

    def accept_visible_message_request_if_open(self, driver=None, timeout: int = 10) -> bool:
        """Accept the currently opened Messenger message request.

        The previous version tried only one ranked text click. On several
        Messenger builds, the bottom button is exposed poorly by UIAutomator,
        so this method waits for the detail screen and then falls back to a
        bottom-right tap only when the screen really is a request detail.
        """
        driver = driver or self.connect_ui_driver()
        if driver is None:
            return False

        deadline = time.time() + max(1, timeout)
        while time.time() < deadline:
            target = self._find_message_request_accept_node(driver)
            if target:
                print(
                    "[REQUESTS] Clicking Accept button: "
                    f"label={target['label']} | bounds={target['bounds']} | "
                    f"clickable={target['clickable']} | class={target['class']}"
                )
                self._tap_bounds_center(target["bounds"], reason="Accept message request")
                time.sleep(3)
                if not self._accept_button_visible(driver):
                    print("[REQUESTS] Accept button disappeared after click.")
                    return True
                # Some builds keep stale accessibility nodes briefly. Continue checking.
                continue

            if self.is_message_request_detail_screen(driver):
                print("[REQUESTS] Accept text node not found, using bottom-right Accept fallback tap.")
                self._tap_screen_ratio(0.80, 0.92, reason="Accept message request bottom-right fallback")
                time.sleep(3)
                if not self._accept_button_visible(driver):
                    return True

            time.sleep(1)

        print("[REQUESTS] Accept button was not clicked within timeout.")
        return False

    def _message_request_row_candidates(self, driver=None) -> list[Dict[str, Any]]:
        """Return visible message-request rows sorted from top to bottom.

        Messenger often exposes request rows as separate text nodes or as labels
        containing status words such as "Bạn mới" / "Just now". The previous
        parser rejected those as system rows, so the tool entered Message
        Requests and then immediately went back to Messenger home. This version
        keeps the strict parser first, then falls back to tappable/list-looking
        nodes in the content area.
        """
        driver = driver or self.connect_ui_driver()
        if driver is None:
            return []

        try:
            root = ET.fromstring(driver.dump_hierarchy())
        except Exception as exc:
            print(f"[REQUESTS] Cannot dump message requests UI: {exc}")
            return []

        screen_w, screen_h = self._get_screen_size()
        blocked_labels = [
            "tin nhắn đang chờ", "message requests", "requests", "spam",
            "tìm kiếm", "search", "menu", "đoạn chat", "chats", "thông báo",
            "chặn", "block", "xóa", "delete", "chấp nhận", "accept",
            "khôi phục lịch sử chat", "khôi phục ngay", "restore chat history", "restore now",
            "xem tất cả", "see all", "quay lại", "trở về", "back",
            "đang hoạt động", "dang hoat dong", "active now", "active status",
        ]

        candidates: list[Dict[str, Any]] = []
        seen_keys = set()
        for node in root.iter("node"):
            label = self._node_label(node)
            if not label:
                continue

            normalized_label = self._normalize_ui_text(label)
            if any(self._normalize_ui_text(term) in normalized_label for term in blocked_labels):
                continue

            bounds = _safe_str(node.attrib.get("bounds"))
            parsed_bounds = self._parse_bounds(bounds)
            if not parsed_bounds:
                continue

            x1, y1, x2, y2 = parsed_bounds
            height = y2 - y1
            width = x2 - x1
            # Bỏ qua header/top tabs, bottom navigation và node quá nhỏ.
            if y1 < 180 or y2 > int(screen_h * 0.90):
                continue
            if height < 24 or width < 80:
                continue

            parsed = self._parse_visible_message_label(label)
            if not parsed:
                # Fallback: request rows may expose only "Tên", or
                # "Tên\nBạn mới\nNội dung" without a normal time marker.
                parts = [_safe_str(part) for part in re.split(r"[\n,]+", label) if _safe_str(part)]
                sender = parts[0] if parts else label
                if self._is_blocked_visible_sender(sender):
                    continue
                content_parts = [part for part in parts[1:] if self._normalize_ui_text(part) not in {
                    self._normalize_ui_text("Bạn mới"),
                    self._normalize_ui_text("New"),
                    self._normalize_ui_text("Just now"),
                    self._normalize_ui_text("Vừa xong"),
                }]
                content = " - ".join(content_parts).strip() or label
                parsed = {
                    "sender": sender,
                    "content": content,
                    "time": datetime.now().astimezone().isoformat(timespec="seconds"),
                }

            row_key = (
                self._normalize_ui_text(parsed.get("sender", "")),
                self._normalize_ui_text(parsed.get("content", "")),
                _safe_str(parsed.get("time")),
                bounds,
            )
            if row_key in seen_keys:
                continue
            seen_keys.add(row_key)

            clickable = node.attrib.get("clickable") == "true"
            class_name = _safe_str(node.attrib.get("class"))
            score = 0
            if clickable:
                score += 30
            if "viewgroup" in class_name.lower() or "textview" in class_name.lower():
                score += 10
            # Message rows usually occupy the left/middle content area.
            if x1 <= int(screen_w * 0.25):
                score += 10
            if height >= 45:
                score += 15

            candidates.append({
                "label": label,
                "parsed": parsed,
                "bounds": bounds,
                "y1": y1,
                "row_key": row_key,
                "score": score,
            })

        window_candidates = self._message_request_row_candidates_from_window_dump()
        if window_candidates:
            print(f"[WINDOW_DUMP] Found {len(window_candidates)} request row candidate(s) from window_dump.xml.")

        merged: list[Dict[str, Any]] = []
        merged_keys = set()
        for item in candidates + window_candidates:
            key = item.get("row_key") or (item.get("bounds"), item.get("label"))
            if key in merged_keys:
                continue
            merged_keys.add(key)
            merged.append(item)

        merged.sort(key=lambda item: (item["y1"], -item.get("score", 0)))
        return merged

    def _message_request_row_candidates_from_window_dump(self) -> list[Dict[str, Any]]:
        """Find visible Message Requests rows from adb window_dump.xml.

        This handles screens where uiautomator2 exposes the row as separate
        TextView nodes such as sender / preview / date instead of one clickable
        row. We group nearby text nodes by vertical position, then tap the
        union bounds of the first row.
        """
        root = self.refresh_window_dump_root(reason="find Message Requests rows")
        if root is None:
            return []

        screen_w, screen_h = self._get_screen_size()
        blocked_terms = [
            "tin nhắn đang chờ", "message requests", "requests", "spam",
            "bạn có thể biết", "you may know", "chỉnh sửa", "edit",
            "hãy mở đoạn chat", "mở đoạn chat", "chỉ khi bạn trả lời",
            "chọn ai", "xem thêm", "learn more", "tìm kiếm", "search",
            "menu", "đoạn chat", "chats", "thông báo", "notifications",
            "tin", "stories", "quay lại", "trở về", "back",
            "chặn", "block", "xóa", "delete", "chấp nhận", "accept",
            "đang hoạt động", "dang hoat dong", "active now", "active status",
        ]
        status_terms = {
            self._normalize_ui_text("Bạn mới"),
            self._normalize_ui_text("New"),
            self._normalize_ui_text("Just now"),
            self._normalize_ui_text("Vừa xong"),
            self._normalize_ui_text("Đang hoạt động"),
            self._normalize_ui_text("Active now"),
        }

        raw_nodes: list[Dict[str, Any]] = []
        button_candidates: list[Dict[str, Any]] = []
        for node in root.iter("node"):
            label = self._node_label(node)
            if not label:
                continue
            normalized_label = self._normalize_ui_text(label)
            if any(
                self._normalize_ui_text(term) in normalized_label
                for term in blocked_terms
                if self._normalize_ui_text(term) != "tin"
            ):
                continue

            bounds = _safe_str(node.attrib.get("bounds"))
            parsed_bounds = self._parse_bounds(bounds)
            if not parsed_bounds:
                continue

            x1, y1, x2, y2 = parsed_bounds
            width = x2 - x1
            height = y2 - y1
            if y1 < max(180, int(screen_h * 0.18)) or y2 > int(screen_h * 0.88):
                continue
            if width < 12 or height < 10:
                continue

            class_name = _safe_str(node.attrib.get("class"))
            clickable = node.attrib.get("clickable") == "true"
            if clickable and "button" in class_name.lower() and width >= int(screen_w * 0.70) and height >= 60:
                parsed = self._parse_visible_message_label(label)
                if not parsed:
                    parts = [_safe_str(part) for part in re.split(r"[\n,]+", label) if _safe_str(part)]
                    sender = parts[0] if parts else label
                    content = " - ".join(parts[1:]).strip() or label
                    parsed = {
                        "sender": sender,
                        "content": content,
                        "time": datetime.now().astimezone().isoformat(timespec="seconds"),
                    }

                sender_norm = self._normalize_ui_text(parsed.get("sender", ""))
                if (
                    not self._is_system_message_row(parsed)
                    and not self._is_blocked_visible_sender(parsed.get("sender", ""))
                    and not any(
                        self._normalize_ui_text(term) in sender_norm
                        for term in blocked_terms
                        if self._normalize_ui_text(term) != "tin"
                    )
                ):
                    row_key = (
                        sender_norm,
                        self._normalize_ui_text(parsed.get("content", "")),
                        bounds,
                        "window_dump_button",
                    )
                    button_candidates.append({
                        "label": label,
                        "parsed": parsed,
                        "bounds": bounds,
                        "y1": y1,
                        "row_key": row_key,
                        "score": 90,
                        "source": "window_dump_button",
                    })

            raw_nodes.append({
                "label": label,
                "normalized_label": normalized_label,
                "bounds": bounds,
                "x1": x1,
                "y1": y1,
                "x2": x2,
                "y2": y2,
                "cx": (x1 + x2) // 2,
                "cy": (y1 + y2) // 2,
                "class": class_name,
                "clickable": node.attrib.get("clickable"),
            })

        if not raw_nodes and not button_candidates:
            labels = self._window_dump_labels(root, reason="find Message Requests rows")
            print("[WINDOW_DUMP] No raw row text nodes. Visible labels: " + " | ".join(labels[:80]))
            return []

        raw_nodes.sort(key=lambda item: (item["cy"], item["x1"]))
        groups: list[Dict[str, Any]] = []
        for node in raw_nodes:
            target_group = None
            for group in groups:
                # Sender and preview often differ by 25-55px vertically.
                if abs(node["cy"] - group["cy"]) <= 70 or node["y1"] <= group["y2"] + 35:
                    target_group = group
                    break

            if target_group is None:
                target_group = {
                    "nodes": [],
                    "x1": node["x1"],
                    "y1": node["y1"],
                    "x2": node["x2"],
                    "y2": node["y2"],
                    "cy": node["cy"],
                }
                groups.append(target_group)

            target_group["nodes"].append(node)
            target_group["x1"] = min(target_group["x1"], node["x1"])
            target_group["y1"] = min(target_group["y1"], node["y1"])
            target_group["x2"] = max(target_group["x2"], node["x2"])
            target_group["y2"] = max(target_group["y2"], node["y2"])
            target_group["cy"] = sum(item["cy"] for item in target_group["nodes"]) // len(target_group["nodes"])

        candidates: list[Dict[str, Any]] = list(button_candidates)
        seen = {item["row_key"] for item in button_candidates}
        for group in groups:
            nodes = sorted(group["nodes"], key=lambda item: (item["y1"], item["x1"]))
            labels = [item["label"] for item in nodes]
            useful_labels = [
                label for label in labels
                if self._normalize_ui_text(label) not in status_terms
                and not re.fullmatch(r"\d{1,2}(:\d{2})?", _safe_str(label))
            ]
            if not useful_labels:
                continue

            sender = useful_labels[0]
            if self._is_blocked_visible_sender(sender):
                continue
            normalized_sender = self._normalize_ui_text(sender)
            if any(
                self._normalize_ui_text(term) in normalized_sender
                for term in blocked_terms
                if self._normalize_ui_text(term) != "tin"
            ):
                continue

            content_parts = [label for label in useful_labels[1:] if self._normalize_ui_text(label) != normalized_sender]
            content = " - ".join(content_parts).strip() or " - ".join(labels[1:]).strip() or sender

            y1 = group["y1"]
            y2 = group["y2"]
            x1 = max(0, min(group["x1"], int(screen_w * 0.10)))
            x2 = min(screen_w, max(group["x2"], int(screen_w * 0.88)))

            # Ignore groups that are too close to the bottom nav or too tall to be a row.
            if y1 < max(180, int(screen_h * 0.18)) or y2 > int(screen_h * 0.88):
                continue
            if (y2 - y1) > int(screen_h * 0.18):
                continue

            bounds = f"[{x1},{y1}][{x2},{y2}]"
            row_key = (
                self._normalize_ui_text(sender),
                self._normalize_ui_text(content),
                bounds,
                "window_dump",
            )
            if row_key in seen:
                continue
            seen.add(row_key)

            score = 20
            if len(useful_labels) >= 2:
                score += 20
            if any(re.search(r"\b(th\d+|thg|am|pm|hôm qua|yesterday|vừa xong|just now)\b", self._normalize_ui_text(label)) for label in labels):
                score += 10
            if x1 <= int(screen_w * 0.20):
                score += 10

            candidates.append({
                "label": " | ".join(labels),
                "parsed": {
                    "sender": sender,
                    "content": content,
                    "time": datetime.now().astimezone().isoformat(timespec="seconds"),
                },
                "bounds": bounds,
                "y1": y1,
                "row_key": row_key,
                "score": score,
                "source": "window_dump",
            })

        candidates.sort(key=lambda item: (item["y1"], -item.get("score", 0)))
        return candidates

    def open_first_visible_message_request(self, driver=None) -> bool:
        row = self.open_first_visible_message_request_row(driver)
        return bool(row)

    def open_first_visible_message_request_row(
        self,
        driver=None,
        skipped_row_keys: Optional[set] = None,
    ) -> Optional[Dict[str, Any]]:
        driver = driver or self.connect_ui_driver()
        if driver is None:
            return None

        skipped_row_keys = skipped_row_keys or set()
        rows = self._message_request_row_candidates(driver)
        rows = [row for row in rows if row.get("row_key") not in skipped_row_keys]
        print(f"[REQUESTS] Found {len(rows)} visible message request row(s).")
        if not rows:
            labels = self._dump_visible_ui_labels(driver, limit=40)
            if labels:
                print("[REQUESTS] No tappable request row detected. Visible labels: " + " | ".join(labels))
            return None

        for row in rows[:5]:
            parsed = row.get("parsed") or {}
            print(
                "[REQUESTS] Opening visible message request: "
                f"{parsed.get('sender')} | bounds={row.get('bounds')}"
            )
            if not self._tap_bounds_center(row["bounds"], reason="open message request row"):
                continue

            # Wait until the conversation/request-detail screen is actually loaded.
            deadline = time.time() + 8
            while time.time() < deadline:
                if not self.is_current_package(MESSENGER_PKG):
                    print("[REQUESTS] Messenger left foreground after row tap; reopening Messenger.")
                    self.bring_messenger_to_front(driver, reason="request row tap")
                    return None
                if self.is_message_request_detail_screen(driver):
                    print("[REQUESTS] Message request detail screen detected after row click.")
                    return row
                # Some builds open a normal chat thread after accepting/opening.
                if not self.is_message_requests_list_screen(driver) and not self.is_messenger_chats_home_screen(driver):
                    blob = self._normalize_ui_text(self._read_visible_ui_text_blob(driver))
                    if blob and not any(self._normalize_ui_text(term) in blob for term in ["tin nhắn đang chờ", "message requests"]):
                        print("[REQUESTS] Conversation-like screen detected after row click.")
                        return row
                time.sleep(1)

            for x_ratio, tap_name in [(0.30, "text area"), (0.16, "avatar area")]:
                print(f"[REQUESTS] Center tap did not open row. Retrying request row {tap_name}.")
                if not self._tap_bounds_relative(
                    row["bounds"],
                    x_ratio,
                    0.50,
                    reason=f"open message request row {tap_name}",
                ):
                    continue

                deadline = time.time() + 6
                while time.time() < deadline:
                    if not self.is_current_package(MESSENGER_PKG):
                        print("[REQUESTS] Messenger left foreground after row retry tap; reopening Messenger.")
                        self.bring_messenger_to_front(driver, reason="request row retry tap")
                        return None
                    if self.is_message_request_detail_screen(driver):
                        print("[REQUESTS] Message request detail screen detected after row retry click.")
                        return row
                    if not self.is_message_requests_list_screen(driver) and not self.is_messenger_chats_home_screen(driver):
                        print("[REQUESTS] Conversation-like screen detected after row retry click.")
                        return row
                    time.sleep(1)

            print("[REQUESTS] Row tap did not open detail screen. Trying next visible row if available.")

        return None

    def _load_chat_history(self) -> Dict[str, Any]:
        if not self.use_file_chat_cache:
            return self.chat_history_db if isinstance(self.chat_history_db, dict) else {}
        try:
            content = self.db_file.read_text(encoding="utf-8").strip() if self.db_file.exists() else ""
            history = json.loads(content) if content else {}
            if not isinstance(history, dict):
                return {}
            for conv in history.values():
                if isinstance(conv, dict) and "last_5_messages" in conv and "messages" not in conv:
                    conv["messages"] = conv["last_5_messages"]
            return history
        except Exception as exc:
            print(f"[DB] Cannot load {self.db_file}: {exc}")
            return {}

    def _save_open_message_request_conversation(
        self,
        request_row: Optional[Dict[str, Any]],
        source: str = "message_requests",
    ) -> int:
        """Crawl the currently opened/accepted request, save it, then push new messages to CRM."""
        driver = self.connect_ui_driver()
        if driver is None:
            return 0

        parsed_row = (request_row or {}).get("parsed") or {}
        sender = (
            _safe_str(parsed_row.get("sender"))
            or _safe_str((request_row or {}).get("sender"))
            or _safe_str((request_row or {}).get("participant_name"))
            or self._conversation_header_sender_from_dump(driver)
            or "Unknown"
        )
        sender = self._clean_participant_name(sender) or "Unknown"
        chat_id = self._safe_chat_id_from_sender(sender)
        active_account = self.require_active_account_id("accepted message request crawl")
        account_chat_id = f"{active_account}:{chat_id}"

        history = self._load_chat_history()
        conversation = history.setdefault(
            account_chat_id,
            {
                "sender": sender,
                "participant_name": sender,
                "participant_url": chat_id,
                "conversation_url": chat_id,
                "last_5_messages": [],
                "messages": [],
                "source": source,
                "user_id_chat": active_account,
                "account": active_account,
            },
        )
        conversation["sender"] = conversation.get("sender") or sender
        conversation["participant_name"] = conversation.get("participant_name") or sender
        conversation["participant_url"] = chat_id
        conversation["conversation_url"] = chat_id
        conversation["source"] = source
        conversation["user_id_chat"] = conversation.get("user_id_chat") or active_account
        conversation["account"] = conversation.get("account") or active_account
        conversation.setdefault("last_5_messages", [])
        conversation.setdefault("messages", conversation["last_5_messages"])

        before_count = len(conversation.get("last_5_messages") or [])
        inserted = self._crawl_conversation_messages(driver, account_chat_id, conversation)

        # _crawl_conversation_messages hiện append vào last_5_messages, nên đồng bộ sang messages
        # để scan_local_chat_history_for_new_messages có dữ liệu bắn về CRM.
        conversation["messages"] = conversation.get("last_5_messages", [])
        if conversation["messages"]:
            conversation["last_message"] = conversation["messages"][-1].get("content", "")
            conversation["last_message_time"] = conversation["messages"][-1].get("time", "")

        after_count = len(conversation.get("last_5_messages") or [])
        inserted = max(inserted, after_count - before_count)

        if inserted:
            self.chat_history_db = history
            print(f"[REQUESTS] Saved {inserted} message(s) from accepted message request: {sender}.")
            self.scan_local_chat_history_for_new_messages()
            self.save_data_to_file()
        else:
            print(f"[REQUESTS] No parsable new message found after accepting request: {sender}.")

        return inserted

    def _return_to_message_requests_list(self, driver=None) -> bool:
        driver = driver or self.connect_ui_driver()
        if driver is None:
            return False

        for attempt in range(5):
            if not self.is_current_package(MESSENGER_PKG):
                self.bring_messenger_to_front(driver, reason="return to Message Requests")
                return self.open_message_requests_screen()

            if self.is_message_requests_list_screen(driver):
                return True

            # Only press Back when we are inside a request/chat detail. Do not
            # keep pressing Back from Messenger home, because that exits to
            # Facebook and then to the Android launcher.
            if self.is_message_request_detail_screen(driver):
                if not self._safe_messenger_back(driver, reason=f"return to Message Requests attempt {attempt + 1}"):
                    return self.open_message_requests_screen()
                time.sleep(1)
                continue

            blob = self._normalize_ui_text(self._read_visible_ui_text_blob(driver))
            if any(self._normalize_ui_text(term) in blob for term in ["tin nhắn đang chờ", "message requests"]):
                return True

            print("[REQUESTS] Not on request detail/list while returning. Reopening Message Requests instead of pressing Back.")
            return self.open_message_requests_screen()

        return self.open_message_requests_screen()

    def click_messenger_chats_tab(self, driver=None) -> bool:
        """Click Messenger bottom-left Chats/Đoạn chat tab after request crawling is done."""
        driver = driver or self.connect_ui_driver()
        if driver is None:
            return False

        if self._click_best_ui_text(
            driver,
            ["Đoạn chat", "Doan chat", "Chats", "Chat"],
            allow_contains=True,
            reason="Messenger Chats tab",
        ):
            time.sleep(2)
            return True

        print("[MOBILE] Chats tab text not found. Using bottom-left Chats fallback tap.")
        self._tap_screen_ratio(0.22, 0.92, reason="Messenger bottom-left Chats fallback")
        time.sleep(2)
        return self.is_messenger_home_screen(driver)

    def click_messenger_unread_tab(self, driver=None, timeout: int = 8) -> bool:
        """Select the Messenger Chats filter chip "Chưa đọc"/Unread."""
        driver = driver or self.connect_ui_driver()
        if driver is None:
            return False

        deadline = time.time() + max(1, timeout)
        attempted_reveal = False
        force_revealed_after_chats = False

        def is_unread_filter_label(label: str) -> bool:
            return self._is_unread_filter_label(label)

        def has_unread_conversation_rows(root: ET.Element) -> bool:
            for node in root.iter("node"):
                norm = self._normalize_ui_text(self._node_label(node))
                if re.search(r"(^|[. ]+)chua doc$", norm) and norm not in {"chua doc", "unread"}:
                    return True
            return False

        while time.time() < deadline:
            if not force_revealed_after_chats:
                force_revealed_after_chats = True
                attempted_reveal = True
                print("[MOBILE] Pulling chat list down before searching Messenger Unread tab.")
                self.run_adb(["shell", "input", "swipe", "360", "760", "360", "1120", "260"])
                time.sleep(1.2)

            try:
                root = ET.fromstring(driver.dump_hierarchy())
            except Exception:
                return False

            candidates: list[dict[str, Any]] = []
            for node in root.iter("node"):
                label = self._node_label(node)
                if not is_unread_filter_label(label):
                    continue

                bounds = _safe_str(node.attrib.get("bounds"))
                parsed = self._parse_bounds(bounds)
                if not parsed:
                    continue

                _, y1, _, _ = parsed
                if y1 > 1100:
                    continue

                class_name = _safe_str(node.attrib.get("class"))
                score = 1000
                if "tab" in class_name.lower():
                    score += 200
                if node.attrib.get("selected") == "true":
                    print("[MOBILE] Messenger Unread tab is already selected.")
                    return True
                candidates.append({"label": label, "bounds": bounds, "score": score})

            if candidates:
                candidates.sort(key=lambda item: item["score"], reverse=True)
                target = candidates[0]
                print(f"[MOBILE] Selecting Messenger Unread tab: label={target['label']} bounds={target['bounds']}")
                self._tap_bounds_center(target["bounds"], reason="Messenger Unread tab")
                time.sleep(2)
                return True

            if has_unread_conversation_rows(root):
                print("[MOBILE] Messenger Unread tab appears selected from unread conversation rows.")
                return True

            if attempted_reveal:
                break
            attempted_reveal = True
            print("[MOBILE] Unread tab not visible. Pulling chat list down to reveal filter tabs.")
            self.run_adb(["shell", "input", "swipe", "360", "760", "360", "1120", "260"])
            time.sleep(1.2)

        print("[MOBILE] Messenger Unread tab not found. No fallback tap used to avoid opening a chat row.")
        return False

    def _is_all_filter_label(self, label: str) -> bool:
        norm = self._normalize_ui_text(label)
        norm = re.sub(r"\s+", " ", norm).strip(" ,.")
        return norm in {"tat ca", "all"}

    def _is_all_filter_selected(self, driver=None) -> bool:
        driver = driver or self.connect_ui_driver()
        if driver is None:
            return False
        try:
            root = ET.fromstring(driver.dump_hierarchy())
        except Exception:
            return False
        for node in root.iter("node"):
            if self._is_all_filter_label(self._node_label(node)) and node.attrib.get("selected") == "true":
                return True
        return False

    def click_messenger_all_tab(self, driver=None, timeout: int = 6) -> bool:
        driver = driver or self.connect_ui_driver()
        if driver is None:
            return False

        deadline = time.time() + max(1, timeout)
        pulled_once = False
        while time.time() < deadline:
            try:
                root = ET.fromstring(driver.dump_hierarchy())
            except Exception:
                return False

            candidates: list[dict[str, Any]] = []
            for node in root.iter("node"):
                label = self._node_label(node)
                if not self._is_all_filter_label(label):
                    continue
                bounds = _safe_str(node.attrib.get("bounds"))
                parsed = self._parse_bounds(bounds)
                if not parsed:
                    continue
                _, y1, _, _ = parsed
                if y1 > 1100:
                    continue
                if node.attrib.get("selected") == "true":
                    print("[MOBILE] Messenger All tab is already selected.")
                    return True
                score = 1000
                if "tab" in _safe_str(node.attrib.get("class")).lower():
                    score += 200
                candidates.append({"label": label, "bounds": bounds, "score": score})

            if candidates:
                candidates.sort(key=lambda item: item["score"], reverse=True)
                target = candidates[0]
                print(f"[MOBILE] Selecting Messenger All tab: label={target['label']} bounds={target['bounds']}")
                self._tap_bounds_center(target["bounds"], reason="Messenger All tab")
                time.sleep(1.2)
                return True

            if pulled_once:
                break
            pulled_once = True
            print("[MOBILE] All tab not visible. Pulling chat list down to reveal filter tabs.")
            self.run_adb(["shell", "input", "swipe", "360", "760", "360", "1120", "260"])
            time.sleep(1.0)

        print("[MOBILE] Messenger All tab not found. No fallback tap used.")
        return False

    def is_messenger_menu_tab_screen(self, driver=None) -> bool:
        driver = driver or self.connect_ui_driver()
        if driver is None:
            return False
        if self.get_current_package_name(driver) != MESSENGER_PKG and not self.is_current_package(MESSENGER_PKG):
            return False
        if self._find_messenger_composer_node(driver) is not None:
            return False

        has_selected_menu = False
        has_menu_content = False
        menu_content_terms = ("tin nhan dang cho", "message requests", "kho luu tru", "archive", "trang ca nhan", "profile")
        screen_w, screen_h = self._get_screen_size()
        try:
            root = ET.fromstring(driver.dump_hierarchy())
        except Exception:
            root = None
        if root is not None:
            for node in root.iter("node"):
                norm = self._normalize_ui_text(self._node_label(node))
                parsed = self._parse_bounds(_safe_str(node.attrib.get("bounds"))) or (0, 0, 0, 0)
                x1, y1, x2, y2 = parsed
                cx = (x1 + x2) / max(1, screen_w)
                cy = (y1 + y2) / max(1, screen_h)
                if cy >= 0.82 and cx >= 0.55 and "tab menu" in norm and node.attrib.get("selected") == "true":
                    has_selected_menu = True
                if any(term in norm for term in menu_content_terms):
                    has_menu_content = True
        return has_selected_menu or (has_menu_content and not self.is_messenger_chats_home_screen(driver))

    def prepare_messenger_for_queue_send(self, driver=None, participant_name: str = "") -> bool:
        driver = driver or self.connect_ui_driver()
        if driver is None:
            return False
        if self.get_current_package_name(driver) != MESSENGER_PKG and not self.is_current_package(MESSENGER_PKG):
            return True

        participant_name = self._clean_participant_name(participant_name)
        if participant_name and self.is_messenger_thread_screen_for_participant(driver, participant_name):
            print(f"[SEND] Already in target Messenger thread for '{participant_name}'.")
            return True

        if self.is_message_request_detail_screen(driver):
            print("[SEND] Queue priority from Message Request detail: Back -> Chats tab.")
            self._safe_messenger_back(driver, reason="queue send from message request detail")
            time.sleep(0.8)
            self.click_messenger_chats_tab(driver)
            return True

        if self.is_message_requests_list_screen(driver) or self.is_messenger_menu_tab_screen(driver):
            print("[SEND] Queue priority from Messenger Menu/Requests list: clicking Chats tab.")
            self.click_messenger_chats_tab(driver)
            return True

        if self._find_messenger_composer_node(driver) is not None and not self.is_messenger_chats_home_screen(driver):
            print("[SEND] Queue priority from another Messenger thread: Back -> All tab.")
            self._safe_messenger_back(driver, reason="queue send from current thread")
            time.sleep(0.8)
            if self.is_messenger_chats_home_screen(driver):
                self.click_messenger_all_tab(driver)
            return True

        if self.is_messenger_chats_home_screen(driver) and self._is_unread_filter_selected(driver):
            print("[SEND] Queue priority from Unread tab: clicking All tab.")
            self.click_messenger_all_tab(driver)
            return True

        return True

    def navigate_to_messenger_chats_home(self, driver=None, timeout: int = 12) -> bool:
        driver = driver or self.connect_ui_driver()
        if driver is None:
            return False

        deadline = time.time() + max(1, timeout)
        while time.time() < deadline:
            if not self.bring_messenger_to_front(driver, reason="navigate to Chats tab"):
                return False

            if self.is_messenger_chats_home_screen(driver):
                return True

            if self.click_messenger_chats_tab(driver):
                if self.is_messenger_chats_home_screen(driver):
                    return True
                if self.is_messenger_home_screen(driver) and not self.is_message_requests_list_screen(driver):
                    print("[MOBILE] Messenger home confirmed after Chats tap; continue without stricter tab label match.")
                    return True

            # Use Back only from a request detail. From request list/menu/home,
            # prefer tapping Chats or reopening Messenger; do not blindly back out.
            if self.is_message_request_detail_screen(driver):
                if not self._safe_messenger_back(driver, reason="navigate from request detail to Chats"):
                    return False
                time.sleep(1)
                continue

            if self.is_message_requests_list_screen(driver):
                print("[REQUESTS] Still on Message Requests list; force tapping Chats tab.")
                self._tap_screen_ratio(0.22, 0.92, reason="Messenger bottom-left Chats from request list")
                time.sleep(0.8)
                if self.is_messenger_chats_home_screen(driver):
                    return True
                continue

            print("[MOBILE] Chats tab not confirmed and screen is not a request detail. Stop without Android Back.")
            break

        return self.is_messenger_chats_home_screen(driver)

    def crawl_message_requests_once(self) -> int:
        if self.has_pending_crm_send_queue():
            print("[SEND] CRM send queue is pending; skip Message Requests crawl.")
            return 0
        if self.message_requests_crawled_once and getattr(self, "message_requests_opened_once", False):
            driver = self.connect_ui_driver()
            if self.is_messenger_chats_home_screen(driver):
                self.click_messenger_unread_tab(driver)
            return 0
        if not self.ui_crawl_enabled:
            return 0
        if not self.is_current_package(MESSENGER_PKG):
            if not self.open_messenger():
                return 0
        if not self.open_message_requests_screen():
            print("[REQUESTS] Message Requests was not opened; will retry on the next monitor cycle.")
            self.message_requests_crawled_once = False
            setattr(self, "message_requests_opened_once", False)
            return 0
        self.message_requests_crawled_once = True
        setattr(self, "message_requests_opened_once", True)
        return self.crawl_message_requests()

    def crawl_message_requests(self) -> int:
        if self.has_pending_crm_send_queue():
            print("[SEND] CRM send queue is pending; skip Message Requests crawl.")
            return 0
        if self.message_requests_crawled_once and getattr(self, "message_requests_opened_once", False):
            driver = self.connect_ui_driver()
            if self.is_messenger_chats_home_screen(driver):
                self.click_messenger_unread_tab(driver)
            return 0
        if not self.ui_crawl_enabled:
            return 0
        if not self.is_current_package(MESSENGER_PKG):
            return 0
        if not self.open_message_requests_screen():
            return 0

        driver = self.connect_ui_driver()
        if driver is None:
            return 0

        total_inserted = 0
        processed_row_keys: set = set()
        max_requests = int(os.getenv("FB_1_1_MAX_MESSAGE_REQUESTS_PER_SCAN", "30"))
        no_progress_rounds = 0
        returned_to_chats_after_empty_requests = False

        for round_index in range(max(1, max_requests)):
            print(f"[REQUESTS] Processing message request round {round_index + 1}/{max_requests}.")
            row = self.open_first_visible_message_request_row(driver, skipped_row_keys=processed_row_keys)
            if not row:
                print("[REQUESTS] No visible message request row left.")
                if self.is_message_requests_list_screen(driver):
                    print("[REQUESTS] Message Requests list is empty; returning to Chats immediately.")
                    self._tap_screen_ratio(0.22, 0.92, reason="Messenger bottom-left Chats after empty requests")
                    time.sleep(0.8)
                    returned_to_chats_after_empty_requests = True
                break

            row_key = row.get("row_key")
            if row_key:
                processed_row_keys.add(row_key)

            accepted = self.accept_visible_message_request_if_open(driver, timeout=10)
            if accepted:
                print("[REQUESTS] Accepted current message request.")
                time.sleep(2)
            else:
                print("[REQUESTS] Accept button not clicked; skipping save for this request to avoid leaving it pending.")
                no_progress_rounds += 1
                if not self._return_to_message_requests_list(driver):
                    print("[REQUESTS] Could not return after failed accept. Reopening Message Requests.")
                    if not self.open_message_requests_screen():
                        break
                if no_progress_rounds >= 3:
                    print("[REQUESTS] Stop request loop because Accept failed/no progress for 3 rounds.")
                    break
                continue

            inserted = self._save_open_message_request_conversation(row, source="message_requests")
            total_inserted += inserted

            if inserted <= 0:
                no_progress_rounds += 1
            else:
                no_progress_rounds = 0

            if not self._return_to_message_requests_list(driver):
                print("[REQUESTS] Could not return to Message Requests list. Reopening it.")
                if not self.open_message_requests_screen():
                    break

            if no_progress_rounds >= 3:
                print("[REQUESTS] Stop request loop because no new message was collected for 3 rounds.")
                break

        if total_inserted:
            print(f"[REQUESTS] Completed message requests crawl. Total inserted={total_inserted}.")
        else:
            print("[REQUESTS] No new message request message was inserted.")

        if total_inserted == 0:
            if returned_to_chats_after_empty_requests or self.is_message_requests_list_screen(driver):
                print("[REQUESTS] Returning to Chats/Unread because no request row was processed.")
            if returned_to_chats_after_empty_requests or self.navigate_to_messenger_chats_home(driver, timeout=8):
                self.click_messenger_unread_tab(driver)
                self.collect_unread_unreplied_conversations()
                self.scan_local_chat_history_for_new_messages()
            return total_inserted

        # Sau khi xử lý được request, mới quay về tab Đoạn chat và lọc tin chưa đọc.
        if total_inserted > 0:
            if self.navigate_to_messenger_chats_home(driver, timeout=12):
                self.click_messenger_unread_tab(driver)
                self.collect_unread_unreplied_conversations()
                self.scan_local_chat_history_for_new_messages()
            else:
                print("[REQUESTS] Cannot navigate back to Chats tab for unread filtering.")

        return total_inserted

    def _visible_text_exists(self, driver, patterns: list[str]) -> bool:
        try:
            xml = driver.dump_hierarchy()
        except Exception:
            return False
        blob = xml.lower()
        return any(pattern.lower() in blob for pattern in patterns)

    def _dump_nodes(self, driver=None) -> list[Dict[str, Any]]:
        driver = driver or self.connect_ui_driver()
        if driver is None:
            return []
        try:
            root = ET.fromstring(self._dump_hierarchy_xml(driver, reason="dump nodes"))
        except Exception:
            return []

        nodes: list[Dict[str, Any]] = []
        for node in root.iter("node"):
            label = self._node_label(node)
            bounds = _safe_str(node.attrib.get("bounds"))
            parsed = self._parse_bounds(bounds)
            if not label or not parsed:
                continue
            nodes.append({
                "label": label,
                "norm": self._normalize_ui_text(label),
                "bounds": bounds,
                "parsed_bounds": parsed,
                "class": _safe_str(node.attrib.get("class")),
                "package": _safe_str(node.attrib.get("package")),
                "resource_id": _safe_str(node.attrib.get("resource-id")),
                "clickable": _safe_str(node.attrib.get("clickable")).lower() == "true",
            })
        return nodes

    def _find_messenger_search_node(self, driver=None) -> Optional[Dict[str, Any]]:
        if self._find_messenger_composer_node(driver) is not None and not self.is_messenger_chats_home_screen(driver):
            return None
        screen_w, screen_h = self._get_screen_size()
        best = None
        for node in self._dump_nodes(driver):
            if node.get("package") and node.get("package") != MESSENGER_PKG:
                continue
            x1, y1, x2, y2 = node["parsed_bounds"]
            if y2 > int(screen_h * 0.32):
                continue
            norm = node["norm"]
            if not any(term in norm for term in ("tim kiem", "search")):
                continue
            width = x2 - x1
            score = 0
            if width > screen_w * 0.35:
                score += 2
            if node["clickable"]:
                score += 1
            if "edittext" in node["class"].lower():
                score += 2
            candidate = {**node, "score": score}
            if best is None or candidate["score"] > best["score"]:
                best = candidate
        return best

    def _set_messenger_search_text(self, driver, participant_name: str) -> bool:
        search_text = self._clean_participant_name(participant_name)
        search_text = re.sub(r"\s+", " ", search_text)
        if not search_text:
            return False

        if self._input_text_via_current_ime(search_text, reason="Messenger search"):
            return True
        print("[IME] Cannot input Messenger search text with the current IME; no clipboard/raw input fallback will be used.")
        self._last_send_failure_reason = "Cannot input Messenger search text with the current IME"
        return False

    def _set_active_text(self, driver, text: str) -> bool:
        value = _safe_str(text)
        if not value:
            return False

        edit_nodes = [
            node for node in self._dump_nodes(driver)
            if "edittext" in node["class"].lower()
            and (not node.get("package") or node.get("package") == MESSENGER_PKG)
        ]
        edit_nodes.sort(key=lambda item: item["parsed_bounds"][1], reverse=True)
        if edit_nodes:
            self._tap_bounds_center(edit_nodes[0]["bounds"], reason="active text field")

        if self._input_text_via_current_ime(value, reason="active text field"):
            return True
        print("[IME] Cannot input active text field with the current IME; no clipboard/raw input fallback will be used.")
        self._last_send_failure_reason = "Cannot input active text field with the current IME"
        return False

    def _paste_text_via_clipboard(self, driver, text: str) -> bool:
        value = _safe_str(text)
        if not value:
            return False
        try:
            if driver is not None and hasattr(driver, "set_clipboard"):
                ok, _ = self._call_uiautomator_with_timeout(
                    lambda: driver.set_clipboard(value),
                    reason="set clipboard text",
                    timeout=6,
                )
                if not ok:
                    return False
                time.sleep(0.4)
                self.run_adb(["shell", "input", "keyevent", "279"])
                time.sleep(1.0)
                return True
        except Exception as exc:
            print(f"[MOBILE] Clipboard paste through uiautomator failed: {exc}")
        return False

    def _text_contains_message_prefix(self, text: str, content: str) -> bool:
        needle = self._normalize_ui_text(_safe_str(content))
        if not needle:
            return False
        prefix = needle[: min(80, len(needle))]
        return bool(prefix and prefix in self._normalize_ui_text(_safe_str(text)))

    def _edittext_contains_text_via_uiautomator(self, driver, content: str) -> bool:
        if driver is None or self._uiautomator_call_recently_timed_out():
            return False

        def worker() -> bool:
            editor = driver(className="android.widget.EditText")
            count = editor.count if isinstance(editor.count, int) else editor.count()
            for index in range(max(0, int(count))):
                candidate = editor[index]
                info = candidate.info or {}
                values = [
                    info.get("text"),
                    info.get("contentDescription"),
                ]
                with contextlib.suppress(Exception):
                    values.append(candidate.get_text())
                for value in values:
                    if self._text_contains_message_prefix(_safe_str(value), content):
                        return True
            return False

        ok, result = self._call_uiautomator_with_timeout(
            worker,
            reason="verify EditText text",
            timeout=4,
        )
        return bool(ok and result)

    def _send_keys_via_uiautomator_fastinput(self, driver, text: str, *, reason: str = "focused field") -> bool:
        value = _safe_str(text)
        if not value or driver is None or self._uiautomator_call_recently_timed_out():
            return False

        def worker() -> bool:
            driver.send_keys(value, clear=True)
            time.sleep(float(os.getenv("FB_1_1_UIA_SEND_KEYS_SETTLE_SECONDS", "0.25")))
            return True

        ok, result = self._call_uiautomator_with_timeout(
            worker,
            reason=f"send_keys {reason}",
            timeout=8,
        )
        if ok and result:
            print(f"[IME] Sent {reason} text via UIAutomator FastInput.")
            return True
        return False

    def _set_edittext_near_bounds_via_uiautomator(self, driver, bounds: str, text: str, *, reason: str = "EditText") -> bool:
        value = _safe_str(text)
        if not value or driver is None or self._uiautomator_call_recently_timed_out():
            return False

        target_bounds = self._parse_bounds(bounds) or (0, 0, 0, 0)

        def worker() -> bool:
            editor = driver(className="android.widget.EditText")
            count = editor.count if isinstance(editor.count, int) else editor.count()
            best = None
            tx1, ty1, tx2, ty2 = target_bounds
            for index in range(max(0, int(count))):
                candidate = editor[index]
                info = candidate.info or {}
                raw_bounds = info.get("bounds")
                if isinstance(raw_bounds, dict):
                    parsed = (
                        int(raw_bounds.get("left", 0)),
                        int(raw_bounds.get("top", 0)),
                        int(raw_bounds.get("right", 0)),
                        int(raw_bounds.get("bottom", 0)),
                    )
                else:
                    parsed = self._parse_bounds(_safe_str(raw_bounds))
                if not parsed:
                    continue
                x1, y1, x2, y2 = parsed
                overlaps_target = x1 < tx2 and x2 > tx1 and y1 < ty2 and y2 > ty1
                if not overlaps_target and abs(y1 - ty1) > 80:
                    continue
                score = y1 + (10000 if overlaps_target else 0)
                if best is None or score > best[0]:
                    best = (score, parsed, candidate)
            if not best:
                return False

            parsed = best[1]
            candidate = best[2]
            print(f"[IME] Setting {reason} via UIAutomator bounds=[{parsed[0]},{parsed[1]}][{parsed[2]},{parsed[3]}]")
            candidate.click()
            time.sleep(0.2)
            with contextlib.suppress(Exception):
                candidate.clear_text()
            candidate.set_text(value)
            time.sleep(float(os.getenv("FB_1_1_UIA_SET_TEXT_SETTLE_SECONDS", "0.25")))
            info = candidate.info or {}
            seen_values = [
                info.get("text"),
                info.get("contentDescription"),
            ]
            with contextlib.suppress(Exception):
                seen_values.append(candidate.get_text())
            if any(self._text_contains_message_prefix(_safe_str(item), value) for item in seen_values):
                print(f"[IME] Confirmed {reason} text via UIAutomator.")
                return True
            print(f"[IME] UIAutomator set_text returned but {reason} text was not confirmed yet.")
            return True

        ok, result = self._call_uiautomator_with_timeout(
            worker,
            reason=f"set {reason} text",
            timeout=8,
        )
        return bool(ok and result)

    def _set_messenger_composer_text(self, driver, content: str) -> bool:
        composer = self._find_messenger_composer_node(driver)
        if not composer:
            print("[SEND] Messenger composer not found; refusing to type outside a chat thread.")
            return False

        self._tap_bounds_center(composer["bounds"], reason="Messenger message composer")

        if self._set_edittext_near_bounds_via_uiautomator(
            driver,
            composer["bounds"],
            _safe_str(content),
            reason="Messenger composer",
        ) and self._wait_until_composer_contains_text(
            driver,
            content,
            timeout=float(os.getenv("FB_1_1_COMPOSER_TEXT_VERIFY_SECONDS", "1.5")),
        ):
            return True

        self._tap_bounds_center(composer["bounds"], reason="Messenger message composer before FastInput")
        if self._send_keys_via_uiautomator_fastinput(
            driver,
            _safe_str(content),
            reason="Messenger composer",
        ) and self._wait_until_composer_contains_text(
            driver,
            content,
            timeout=float(os.getenv("FB_1_1_COMPOSER_TEXT_VERIFY_SECONDS", "1.5")),
        ):
            return True

        if self._input_text_via_current_ime(
            _safe_str(content),
            reason="Messenger composer",
            verify=lambda: self._wait_until_composer_contains_text(
                driver,
                content,
                timeout=float(os.getenv("FB_1_1_COMPOSER_TEXT_VERIFY_SECONDS", "1.5")),
            ),
        ):
            return True

        if os.getenv("FB_1_1_ENABLE_CLIPBOARD_PASTE_FALLBACK", "0").lower() in {"1", "true", "yes", "on"}:
            if self._paste_text_via_clipboard(driver, _safe_str(content)) and self._wait_until_composer_contains_text(
                driver,
                content,
                timeout=float(os.getenv("FB_1_1_COMPOSER_TEXT_VERIFY_SECONDS", "1.5")),
            ):
                return True

        print("[IME] Cannot confirm Messenger composer text after UIAutomator/IME input.")
        self._last_send_failure_reason = "Cannot confirm Messenger composer text after input"
        return False

    def open_visible_messenger_chat_by_participant_name(self, driver, participant_name: str, max_scrolls: int = 3) -> bool:
        """Open a visible Messenger chat row by comparing participant_name with row labels.

        This avoids fb-messenger://user/<id> fallback because mobile_visible/thread ids
        can point to the wrong conversation. The match is based on the text/content-desc
        currently visible in window_dump / uiautomator hierarchy.
        """
        participant_name = self._clean_participant_name(participant_name)
        if not participant_name:
            return False

        target_norm = self._normalize_ui_text(participant_name)
        strict_accent_match = self._strict_accent_name_search_enabled(participant_name)
        tokens = [token for token in target_norm.split() if len(token) >= 2]
        if not target_norm or not tokens:
            return False

        screen_w, screen_h = self._get_screen_size()
        blocked_terms = (
            "tin nhan moi", "ung dung facebook", "tao tin", "them tin",
            "doan chat", "tab", "meta ai", "messenger", "tim kiem", "search",
            "thong bao", "menu", "tin nhan dang cho", "tin nhan gan day nhat",
        )

        for attempt in range(max(1, max_scrolls + 1)):
            ranked = []
            for node in self._dump_nodes(driver):
                x1, y1, x2, y2 = node["parsed_bounds"]
                norm = node["norm"]
                label = node["label"]
                if y2 < int(screen_h * 0.18) or y1 > int(screen_h * 0.90):
                    continue
                if any(term in norm for term in blocked_terms):
                    continue

                accent_match = self._accent_sensitive_name_match(participant_name, label)
                if strict_accent_match and not accent_match:
                    continue
                exact = norm == target_norm
                contains = target_norm in norm or norm in target_norm
                token_overlap = sum(1 for token in tokens if token in norm)
                full_token_match = token_overlap >= max(1, min(len(tokens), 3))
                if not exact and not contains and not full_token_match:
                    continue

                score = 0
                if accent_match:
                    score += 2000
                if exact:
                    score += 1000
                if contains:
                    score += 700
                score += token_overlap * 120
                if node["clickable"]:
                    score += 80
                if x1 == 0 and x2 >= int(screen_w * 0.85):
                    score += 120
                # Prefer real list rows over child text labels by expanding to full row.
                row_bounds = f"[0,{max(0, y1 - 20)}][{screen_w},{min(screen_h, max(y2 + 70, y1 + 105))}]"
                if node["clickable"] and x1 <= 5 and x2 >= int(screen_w * 0.85):
                    row_bounds = node["bounds"]
                ranked.append((score, y1, label, row_bounds))

            if ranked:
                ranked.sort(key=lambda item: (-item[0], item[1]))
                score, _y1, label, row_bounds = ranked[0]
                print(
                    "[SEND] Opening visible Messenger chat by participant_name: "
                    f"target='{participant_name}' matched_label='{label}' score={score} bounds={row_bounds}"
                )
                self._recent_visible_chat_open = {
                    "participant_name": participant_name,
                    "opened_at": time.time(),
                    "matched_label": label,
                }
                self._tap_bounds_center(row_bounds, reason=f"Messenger visible chat {participant_name}")
                time.sleep(float(os.getenv("FB_1_1_OPEN_CHAT_SETTLE_SECONDS", "0.6")))
                return self._wait_for_messenger_thread_composer(driver, participant_name, timeout=8)

            if attempt < max_scrolls:
                self.run_adb(["shell", "input", "swipe", "360", "1180", "360", "620", "260"])
                time.sleep(1.2)

        print(f"[SEND] No visible Messenger chat row matched participant_name='{participant_name}'.")
        return False

    def _visible_messenger_thread_participant_name(self, driver=None) -> str:
        driver = driver or self.connect_ui_driver()
        if driver is None:
            return ""
        screen_w, screen_h = self._get_screen_size()
        blocked_terms = (
            "quay lai", "chi tiet", "cuoc goi", "goi video", "dang hoat dong",
            "nhap tin nhan", "nhan tin", "them ban be", "tim kiem",
            "thong bao cua", "notification", "tien ich so", "tab",
        )
        candidates = []
        for node in self._dump_nodes(driver):
            if node.get("package") and node.get("package") != MESSENGER_PKG:
                continue
            x1, y1, x2, y2 = node["parsed_bounds"]
            norm = node["norm"]
            label = node["label"]
            if y1 > int(screen_h * 0.16) or y2 > int(screen_h * 0.22):
                continue
            if x1 < int(screen_w * 0.18) or x2 > int(screen_w * 0.72):
                continue
            if not norm or any(term in norm for term in blocked_terms):
                continue
            cleaned = self._clean_participant_name(label)
            if cleaned:
                candidates.append((y1, x1, cleaned))
        if not candidates:
            return ""
        candidates.sort(key=lambda item: (item[0], item[1]))
        return candidates[0][2]

    def is_messenger_thread_screen_for_participant(self, driver, participant_name: str = "") -> bool:
        if not self.is_current_package(MESSENGER_PKG):
            return False
        if self._find_messenger_composer_node(driver) is None:
            return False
        if self.is_messenger_chats_home_screen(driver):
            return False
        participant_name = self._clean_participant_name(participant_name)
        if not participant_name:
            return True
        current = self._visible_messenger_thread_participant_name(driver)
        if not current:
            return True
        if self._strict_accent_name_search_enabled(participant_name):
            return self._accent_sensitive_name_match(participant_name, current)
        target_norm = self._normalize_ui_text(participant_name)
        current_norm = self._normalize_ui_text(current)
        return target_norm in current_norm or current_norm in target_norm

    def _participant_name_looks_like_current_thread(self, driver, participant_name: str) -> bool:
        participant_name = self._clean_participant_name(participant_name)
        if not participant_name:
            return True
        current = self._visible_messenger_thread_participant_name(driver)
        if not current:
            return True
        recent = getattr(self, "_recent_visible_chat_open", {}) or {}
        recent_name = self._clean_participant_name(recent.get("participant_name", ""))
        recent_age = time.time() - float(recent.get("opened_at", 0) or 0)
        if recent_name and recent_age <= 20 and self._accent_sensitive_name_match(participant_name, recent_name):
            if not self._accent_sensitive_name_match(participant_name, current):
                print(
                    "[SEND] Ignoring mismatched thread header after visible row tap: "
                    f"target='{participant_name}' current='{current}'."
                )
            return True
        if self._strict_accent_name_search_enabled(participant_name):
            if self._accent_sensitive_name_match(participant_name, current):
                return True
            print(
                "[SEND] Messenger composer is visible but accented participant header differs: "
                f"target='{participant_name}' current='{current}'."
            )
            return False

        target_norm = self._normalize_ui_text(participant_name)
        current_norm = self._normalize_ui_text(current)
        if not target_norm or not current_norm:
            return True
        if target_norm in current_norm or current_norm in target_norm:
            return True

        target_tokens = {token for token in target_norm.split() if len(token) >= 2 and "?" not in token}
        current_tokens = {token for token in current_norm.split() if len(token) >= 2 and "?" not in token}
        if target_tokens and current_tokens and len(target_tokens & current_tokens) >= min(2, len(target_tokens), len(current_tokens)):
            return True

        print(
            "[SEND] Messenger composer is visible but participant header differs: "
            f"target='{participant_name}' current='{current}'. Continuing because the chat row was just selected."
        )
        return True

    def is_messenger_contact_info_screen(self, driver=None) -> bool:
        driver = driver or self.connect_ui_driver()
        if driver is None or not self.is_current_package(MESSENGER_PKG):
            return False
        if self._find_messenger_composer_node(driver) is not None:
            return False
        blob = self._normalize_ui_text(self._read_visible_ui_text_blob(driver))
        if not blob:
            return False
        if self.is_messenger_chats_home_screen(driver) or self.is_message_requests_list_screen(driver):
            return False
        if "tab menu" in blob or "tab 1/4" in blob or "doan chat" in blob:
            return False
        info_terms = (
            "them ban be",
            "trang ca nhan",
            "thong bao",
            "quyen rieng tu",
            "file phuong tien",
            "file, phuong tien",
            "anh va file phuong tien",
        )
        return "quay lai" in blob and sum(1 for term in info_terms if term in blob) >= 2

    def _wait_for_messenger_thread_composer(
        self,
        driver,
        participant_name: str = "",
        *,
        timeout: float = 8.0,
        allow_back_from_info: bool = True,
    ) -> bool:
        """Wait until the real bottom EditText composer is visible before typing."""
        deadline = time.time() + max(0.5, float(timeout))
        participant_name = self._clean_participant_name(participant_name)
        handled_info = False

        while time.time() < deadline:
            composer = self._find_messenger_composer_node(driver)
            if composer and not self.is_messenger_chats_home_screen(driver):
                if not self._participant_name_looks_like_current_thread(driver, participant_name):
                    return False
                print(
                    "[SEND] Messenger composer ready: "
                    f"{composer.get('label')} | bounds={composer.get('bounds')} | class={composer.get('class')}"
                )
                return True

            if allow_back_from_info and not handled_info and self.is_messenger_contact_info_screen(driver):
                print("[SEND] Contact info/detail screen detected while waiting for composer. Going back to chat.")
                handled_info = True
                self._safe_messenger_back(driver, reason="return from contact info while waiting for composer")
                time.sleep(1.2)
                continue

            time.sleep(0.25)

        print("[SEND] Messenger composer/EditText was not visible before typing.")
        return False

    def search_and_open_chat_by_name(self, driver, participant_name: str) -> bool:
        participant_name = self._clean_participant_name(participant_name)
        if not participant_name:
            print("[SEND] participant_name is empty; cannot search Messenger.")
            return False

        if self.is_messenger_thread_screen_for_participant(driver, participant_name):
            print(f"[SEND] Already in Messenger thread for '{participant_name}'.")
            return True

        if self.is_messenger_contact_info_screen(driver):
            print("[SEND] Contact info screen detected. Going back to the Messenger thread.")
            self._safe_messenger_back(driver, reason="return from contact info before sending")
            time.sleep(1.5)
            if self.is_messenger_thread_screen_for_participant(driver, participant_name):
                return True

        if not self.navigate_to_messenger_chats_home(driver, timeout=12):
            print("[SEND] Cannot navigate to Messenger Chats home before search.")
            return False

        # Ưu tiên match trực tiếp tên row đang hiển thị trong danh sách Đoạn chat.
        # Cách này dùng window_dump/uiautomator text thay vì URL/id nên không click nhầm chat.
        if self.open_visible_messenger_chat_by_participant_name(driver, participant_name, max_scrolls=2):
            return True

        if self._find_messenger_composer_node(driver) is not None and not self.is_messenger_chats_home_screen(driver):
            print("[SEND] Composer is visible after opening a chat row; do not tap header/search fallback.")
            return self._wait_for_messenger_thread_composer(driver, participant_name, timeout=3)

        search_node = self._find_messenger_search_node(driver)
        if search_node:
            self._tap_bounds_center(search_node["bounds"], reason="Messenger search")
        else:
            if self._find_messenger_composer_node(driver) is not None and not self.is_messenger_chats_home_screen(driver):
                print("[SEND] Refusing top search fallback because a chat composer is already visible.")
                return True
            screen_w, screen_h = self._get_screen_size()
            self.run_adb(["shell", "input", "tap", str(screen_w // 2), str(int(screen_h * 0.12))])
            time.sleep(1.0)

        if not self._set_messenger_search_text(driver, participant_name):
            return False
        time.sleep(float(os.getenv("FB_1_1_SEARCH_RESULTS_SETTLE_SECONDS", "1.0")))

        if self.is_messenger_contact_info_screen(driver):
            print("[SEND] Search landed on contact info screen; aborting search click.")
            return False

        target_norm = self._normalize_ui_text(participant_name)
        strict_accent_match = self._strict_accent_name_search_enabled(participant_name)
        tokens = [token for token in target_norm.split() if len(token) >= 2]
        screen_w, screen_h = self._get_screen_size()
        search_results_top = int(screen_h * 0.16)

        for attempt in range(3):
            candidates = []
            for node in self._dump_nodes(driver):
                x1, y1, x2, y2 = node["parsed_bounds"]
                if y1 < search_results_top or y2 > int(screen_h * 0.92):
                    continue
                norm = node["norm"]
                label = _safe_str(node.get("label"))
                if not norm or any(term in norm for term in ("tim kiem", "search", "cancel", "huy", "chi tiet", "quay lai", "them ban be")):
                    continue
                if label.rstrip().endswith(".") and y1 < int(screen_h * 0.42):
                    continue
                if x1 > int(screen_w * 0.12) and x2 < int(screen_w * 0.88):
                    continue
                accent_match = self._accent_sensitive_name_match(participant_name, label)
                if strict_accent_match and not accent_match:
                    continue
                exact = norm == target_norm or target_norm in norm
                token_match = tokens and all(token in norm for token in tokens[: min(3, len(tokens))])
                if not exact and not token_match:
                    continue
                score = 3 if exact else 1
                if accent_match:
                    score += 20
                if any(term in norm for term in ("da ket noi", "connected")):
                    score += 50
                if node["clickable"]:
                    score += 1
                candidates.append((score, y1, node))

            if candidates:
                candidates.sort(key=lambda item: (-item[0], item[1]))
                chosen = candidates[0][2]
                x1, y1, _x2, y2 = chosen["parsed_bounds"]
                row_bounds = f"[0,{max(0, y1 - 20)}][{screen_w},{min(screen_h, y2 + 70)}]"
                print(f"[SEND] Opening Messenger chat for '{participant_name}' via search result: {chosen['label']}")
                self._tap_bounds_center(row_bounds, reason=f"Messenger search result {participant_name}")
                time.sleep(2.5)
                if self._wait_for_messenger_thread_composer(driver, participant_name, timeout=8):
                    return True

            if attempt < 2:
                self.run_adb(["shell", "input", "swipe", "360", "1120", "360", "650", "260"])
                time.sleep(1.2)

        print(f"[SEND] Cannot find Messenger chat by participant_name='{participant_name}'.")
        return False

    def search_and_open_message_request_by_name(self, driver, participant_name: str) -> bool:
        participant_name = self._clean_participant_name(participant_name)
        if not participant_name:
            return False
        if not self.is_current_package(MESSENGER_PKG):
            if not self.bring_messenger_to_front(driver, reason="open message request by name"):
                return False

        print(f"[SEND] Searching Message Requests for '{participant_name}'.")
        if not self.open_message_requests_screen():
            print("[SEND] Cannot open Message Requests screen.")
            return False

        driver = self.connect_ui_driver()
        target_norm = self._normalize_ui_text(participant_name)
        strict_accent_match = self._strict_accent_name_search_enabled(participant_name)
        tokens = [token for token in target_norm.split() if len(token) >= 2]

        for attempt in range(4):
            rows = self._message_request_row_candidates(driver)
            ranked = []
            for row in rows:
                parsed = row.get("parsed") or {}
                label = _safe_str(row.get("label"))
                sender = self._clean_participant_name(parsed.get("sender") or label)
                haystack = self._normalize_ui_text(" ".join([sender, label]))
                accent_match = (
                    self._accent_sensitive_name_match(participant_name, sender)
                    or self._accent_sensitive_name_match(participant_name, label)
                )
                if strict_accent_match and not accent_match:
                    continue
                exact = target_norm and (haystack == target_norm or target_norm in haystack)
                token_match = tokens and all(token in haystack for token in tokens[: min(3, len(tokens))])
                if exact or token_match:
                    ranked.append(((30 if accent_match else 0) + (3 if exact else 1), row))

            if ranked:
                ranked.sort(key=lambda item: -item[0])
                row = ranked[0][1]
                print(
                    "[SEND] Opening matching Message Request: "
                    f"{(row.get('parsed') or {}).get('sender')} | bounds={row.get('bounds')}"
                )
                if not self._tap_bounds_center(row["bounds"], reason=f"message request {participant_name}"):
                    return False

                deadline = time.time() + 8
                while time.time() < deadline:
                    if self.is_message_request_detail_screen(driver):
                        break
                    if not self.is_message_requests_list_screen(driver) and not self.is_messenger_chats_home_screen(driver):
                        break
                    time.sleep(1)

                self.accept_visible_message_request_if_open(driver, timeout=8)
                time.sleep(1.5)
                return self.is_current_package(MESSENGER_PKG) and not self.is_message_requests_list_screen(driver)

            if attempt < 3:
                self.run_adb(["shell", "input", "swipe", "360", "1120", "360", "650", "280"])
                time.sleep(1.2)

        print(f"[SEND] Cannot find Message Request by participant_name='{participant_name}'.")
        return False

    def _find_messenger_composer_node(self, driver=None) -> Optional[Dict[str, Any]]:
        """Find the bottom Messenger message composer node."""
        driver = driver or self.connect_ui_driver()
        screen_w, screen_h = self._get_screen_size()
        best = None
        for node in self._dump_nodes(driver):
            if node.get("package") and node.get("package") != MESSENGER_PKG:
                continue
            norm = node.get("norm", "")
            cls = _safe_str(node.get("class")).lower()
            x1, y1, x2, y2 = node.get("parsed_bounds", (0, 0, 0, 0))
            if y1 < int(screen_h * 0.78):
                continue
            is_edit = "edittext" in cls
            is_composer_label = norm in {
                "nhap tin nhan",
                "nhan tin",
                "type a message",
                "message",
                "aa",
            }
            is_composer = is_edit or is_composer_label
            if not is_composer:
                continue
            # Avoid bottom navigation/system nodes.
            if y2 > int(screen_h * 0.96):
                continue
            score = 0
            if "edittext" in cls:
                score += 80
            if x1 >= int(screen_w * 0.20):
                score += 30
            if x2 <= int(screen_w * 0.90):
                score += 20
            score += y1 // 10
            candidate = {**node, "score": score}
            if best is None or candidate["score"] > best["score"]:
                best = candidate
        return best

    def _root_has_messenger_composer(self, root: ET.Element) -> bool:
        screen_w, screen_h = self._get_screen_size()
        for node in root.iter("node"):
            if _safe_str(node.attrib.get("package")) != MESSENGER_PKG:
                continue
            label = self._node_label(node)
            parsed = self._parse_bounds(_safe_str(node.attrib.get("bounds")))
            if not label or not parsed:
                continue
            x1, y1, x2, y2 = parsed
            if y1 < int(screen_h * 0.72) or y2 > int(screen_h * 0.96):
                continue
            cls = _safe_str(node.attrib.get("class")).lower()
            norm = self._normalize_ui_text(label)
            if "edittext" in cls:
                return True
            if x1 >= int(screen_w * 0.20) and x2 <= int(screen_w * 0.90) and norm in {
                "nhap tin nhan",
                "nhan tin",
                "type a message",
                "message",
                "aa",
            }:
                return True
        return False

    def _find_messenger_send_button_node(self, driver=None) -> Optional[Dict[str, Any]]:
        """Find the Messenger Send button near the composer from a fresh UI dump.

        Messenger often exposes the button as content-desc="Gửi"/"Send" at the
        right side of the composer. This helper intentionally searches the
        bottom half only, so it will not click unrelated "send" labels in the
        chat body.
        """
        driver = driver or self.connect_ui_driver()
        screen_w, screen_h = self._get_screen_size()
        candidates: list[Dict[str, Any]] = []
        composer = self._find_messenger_composer_node(driver)
        composer_bounds = composer.get("parsed_bounds") if composer else None

        for node in self._dump_nodes(driver):
            if node.get("package") and node.get("package") != MESSENGER_PKG:
                continue
            norm = node.get("norm", "")
            label = _safe_str(node.get("label"))
            cls = _safe_str(node.get("class")).lower()
            x1, y1, x2, y2 = node.get("parsed_bounds", (0, 0, 0, 0))
            if y1 < int(screen_h * 0.50):
                continue

            matched = (
                norm in {"gui", "send"}
                or " gui" in f" {norm} "
                or " send" in f" {norm} "
                or self._normalize_ui_text(label) in {"gui", "send"}
            )
            if not matched and composer_bounds and "button" in cls:
                cx1, cy1, cx2, cy2 = composer_bounds
                overlaps_composer_row = y1 < cy2 + 35 and y2 > cy1 - 35
                right_of_composer = x1 >= cx2 - 10 or x1 >= int(screen_w * 0.84)
                compact_send_area = (x2 - x1) <= int(screen_w * 0.18) and x2 >= int(screen_w * 0.92)
                matched = overlaps_composer_row and right_of_composer and compact_send_area
            if not matched:
                continue

            score = 0
            if composer_bounds:
                cx1, cy1, cx2, cy2 = composer_bounds
                if y1 < cy2 + 35 and y2 > cy1 - 35:
                    score += 180
                if x1 >= cx2 - 10:
                    score += 120
            if x1 >= int(screen_w * 0.70):
                score += 120
            if y1 >= int(screen_h * 0.75):
                score += 80
            if node.get("clickable"):
                score += 50
            if "button" in cls:
                score += 40
            score += max(0, x1 // 10)

            candidates.append({**node, "score": score})

        if not candidates:
            return None

        candidates.sort(key=lambda item: item["score"], reverse=True)
        return candidates[0]

    def _tap_messenger_send_button(self, driver=None, *, reason: str = "Messenger send button") -> bool:
        """Tap Messenger send button, with a guarded right-side fallback."""
        driver = driver or self.connect_ui_driver()
        screen_w, screen_h = self._get_screen_size()

        # Re-read after typing. The send button may only appear after composer
        # content exists.
        target = self._find_messenger_send_button_node(driver)
        if target:
            print(
                "[SEND] Tapping Messenger send button: "
                f"{target.get('label')} | bounds={target.get('bounds')} | score={target.get('score')}"
            )
            return self._tap_bounds_center(target["bounds"], reason=reason)

        # Safe fallback: only tap the usual send area if Messenger is still in a
        # thread-like screen and the composer area exists at the bottom.
        composer = self._find_messenger_composer_node(driver)
        if composer:
            print("[SEND] Send button node not exposed; using right-side composer fallback tap.")
            self.run_adb(["shell", "input", "tap", str(int(screen_w * 0.945)), str(int(screen_h * 0.86))])
            time.sleep(float(os.getenv("FB_1_1_SEND_TAP_SETTLE_SECONDS", "0.5")))
            return True

        print("[SEND] Cannot find send button or composer fallback target.")
        return False

    def type_and_send_message(self, driver, content: str) -> bool:
        content = _safe_str(content)
        if not content:
            print("[SEND] Message content is empty.")
            self._last_send_failure_reason = "Message content is empty"
            return False

        if not self.is_current_package(MESSENGER_PKG):
            if not self.bring_messenger_to_front(driver, reason="type queued message"):
                self._last_send_failure_reason = "Messenger is not foreground before typing queued message"
                return False

        screen_w, screen_h = self._get_screen_size()

        self._last_send_visible_content_count = self._visible_sent_message_content_count(driver, content)

        if not self._set_messenger_composer_text(driver, content):
            if not getattr(self, "_last_send_failure_reason", ""):
                self._last_send_failure_reason = "Cannot input Messenger composer text"
            return False

        # A broadcast can complete even when no IME consumes it. Confirm the
        # composer contains text before tapping send, otherwise Messenger sends Like.
        text_visible = self._wait_until_composer_contains_text(
            driver,
            content,
            timeout=float(os.getenv("FB_1_1_COMPOSER_TEXT_VERIFY_SECONDS", "1.5")),
        )
        if not text_visible:
            print("[SEND] Message text is not visible in composer after input; refusing to tap Send/Like.")
            self._last_send_failure_reason = "Message text is not visible in composer after input"
            return False

        if not self.is_current_package(MESSENGER_PKG):
            if not self.bring_messenger_to_front(driver, reason="send queued message after typing"):
                self._last_send_failure_reason = "Messenger is not foreground after typing queued message"
                return False

        if self._tap_messenger_send_button(driver):
            time.sleep(float(os.getenv("FB_1_1_SEND_TAP_SETTLE_SECONDS", "0.5")))
            return True

        # If text was visible but the button was not found, keep the old
        # right-side fallback as a last resort.
        if text_visible:
            print("[SEND] Falling back to default right-side send coordinate.")
            self.run_adb(["shell", "input", "tap", str(int(screen_w * 0.94)), str(int(screen_h * 0.86))])
            time.sleep(float(os.getenv("FB_1_1_SEND_TAP_SETTLE_SECONDS", "0.5")))
            return True

        print("[SEND] Message was typed but Send button was not found; refusing to mark as sent.")
        self._last_send_failure_reason = "Message was typed but Send button was not found"
        return False

    def _composer_contains_text(self, driver, content: str) -> bool:
        for node in self._dump_nodes(driver):
            cls = _safe_str(node.get("class")).lower()
            if "edittext" not in cls:
                continue
            if self._text_contains_message_prefix(node.get("norm", ""), content):
                return True
        return False

    def _wait_until_composer_contains_text(self, driver, content: str, *, timeout: float = 3.0) -> bool:
        deadline = time.time() + max(0.5, float(timeout))
        while time.time() < deadline:
            if self._composer_contains_text(driver, content):
                return True
            if self._edittext_contains_text_via_uiautomator(driver, content):
                return True
            time.sleep(0.25)
        return False

    def _visible_sent_message_content_count(self, driver, content: str) -> int:
        needle = self._normalize_ui_text(_safe_str(content))
        if not needle:
            return 0
        prefix = needle[: min(80, len(needle))]
        if not prefix:
            return 0

        count = 0
        for node in self._dump_nodes(driver):
            cls = _safe_str(node.get("class")).lower()
            if "edittext" in cls:
                continue
            norm = node.get("norm", "")
            if prefix in norm:
                count += 1
        return count

    def visible_sent_message_confirmed(self, driver, content: str) -> bool:
        needle = self._normalize_ui_text(_safe_str(content))
        if not needle or not self.is_current_package(MESSENGER_PKG):
            return False
        if self.is_messenger_chats_home_screen(driver):
            return False

        prefix = needle[: min(80, len(needle))]
        has_message = False
        for node in self._dump_nodes(driver):
            cls = _safe_str(node.get("class")).lower()
            if "edittext" in cls:
                continue
            if prefix and prefix in node.get("norm", ""):
                has_message = True
                break
        if not has_message:
            return False

        blob = self._normalize_ui_text(self._read_visible_ui_text_blob(driver))
        delivery_terms = (
            "da gui",
            "sent",
            "da xem",
            "seen",
            "delivered",
            "nguyen thanh bac da xem",
        )
        return any(term in blob for term in delivery_terms)

    def verify_message_sent(self, driver, content: str) -> bool:
        if not self.is_current_package(MESSENGER_PKG):
            self._last_send_failure_reason = "Verify failed: Messenger is no longer foreground"
            return False
        needle = self._normalize_ui_text(_safe_str(content))
        if not needle:
            self._last_send_failure_reason = "Verify failed: message content is empty"
            return False

        before_count = int(getattr(self, "_last_send_visible_content_count", 0) or 0)
        verify_seconds = float(os.getenv("FB_1_1_SEND_VERIFY_SECONDS", "4"))
        deadline = time.time() + max(1.0, verify_seconds)
        last_count = 0
        allow_cleared_duplicate_success = os.getenv(
            "FB_1_1_ACCEPT_CLEARED_COMPOSER_DUPLICATE_SEND",
            "1",
        ).lower() not in {"0", "false", "no", "off"}

        while time.time() < deadline:
            if not self.is_current_package(MESSENGER_PKG):
                print("[SEND] Verify failed: Messenger is no longer foreground.")
                self._last_send_failure_reason = "Verify failed: Messenger is no longer foreground"
                return False
            if self.is_messenger_chats_home_screen(driver):
                print("[SEND] Verify failed: returned to Messenger home before sent bubble was confirmed.")
                self._last_send_failure_reason = "Verify failed: returned to Messenger home before sent bubble was confirmed"
                return False

            last_count = self._visible_sent_message_content_count(driver, content)
            if last_count > before_count:
                print(f"[SEND] Sent bubble confirmed in Messenger thread (before={before_count}, after={last_count}).")
                return True
            if self.visible_sent_message_confirmed(driver, content):
                print("[SEND] Sent bubble and delivery state confirmed in Messenger thread.")
                return True

            if self._composer_contains_text(driver, content):
                print("[SEND] Message is still in the composer after tapping Send; not marking as sent yet.")
                self._last_send_failure_reason = "Message is still in the composer after tapping Send"
                return False

            if allow_cleared_duplicate_success and before_count > 0 and last_count >= before_count:
                print(
                    "[SEND] Composer cleared after Send and matching message text is visible; "
                    "accepting send as successful for duplicate/previously-visible content."
                )
                return True

            time.sleep(float(os.getenv("FB_1_1_SEND_VERIFY_POLL_SECONDS", "0.35")))

        print(
            "[SEND] Verify failed: sent message bubble was not confirmed "
            f"(before={before_count}, after={last_count})."
        )
        self._last_send_failure_reason = (
            "Verify failed: sent message bubble was not confirmed "
            f"(before={before_count}, after={last_count})"
        )
        return False

    def open_messenger_thread_from_url(self, participant_url: str) -> bool:
        thread_id = self._extract_thread_id(participant_url)
        if not thread_id:
            return False
        if not self.ensure_messenger_ready():
            return False
        print(f"[SEND] Fallback opening Messenger thread by URL/id: {thread_id}")
        self.run_adb([
            "shell",
            "am",
            "start",
            "-a",
            "android.intent.action.VIEW",
            "-d",
            f"fb-messenger://user/{thread_id}",
        ])
        time.sleep(3.0)
        return self.is_current_package(MESSENGER_PKG) and not self.is_messenger_chats_home_screen(self.connect_ui_driver())

    def get_messenger_login_credentials(self, account_snapshot: Optional[Dict[str, str]] = None) -> tuple[str, str]:
        """Resolve Messenger login account/password from active device metadata.

        Priority:
        1. Password matched from Facebook.devices.json / Mongo by current device + active FB account.
        2. Password already stored in the active account snapshot.
        3. Constructor/env fallback values.
        """
        snapshot = account_snapshot or self.current_facebook_account_snapshot or self.get_preferred_current_account_snapshot()
        target_account = _safe_str(snapshot.get("account") or snapshot.get("user_id_chat"))
        target_name = _safe_str(snapshot.get("displayName") or snapshot.get("username") or snapshot.get("name"))
        target_device_id = _safe_str(snapshot.get("device_id") or self.active_device)

        for device in self.load_configured_devices():
            if target_device_id and _safe_str(device.get("device_id")) != target_device_id:
                continue
            for account_info in device.get("accounts") or []:
                if not isinstance(account_info, dict):
                    continue
                account = _safe_str(account_info.get("account"))
                name = _safe_str(account_info.get("name") or account_info.get("username"))
                password = _safe_str(account_info.get("password"))
                if not account or not password:
                    continue
                if account == target_account or (target_name and self._normalize_ui_text(name) == self._normalize_ui_text(target_name)):
                    return account, password

        snapshot_password = _safe_str(snapshot.get("password"))
        if target_account and snapshot_password:
            return target_account, snapshot_password

        username = _safe_str(self.user_id_chat if self.user_id_chat != "default" else self.facebook_username)
        password = _safe_str(self.facebook_password)
        if username == "default":
            username = ""
        if password == "default":
            password = ""
        return username, password

    def login_to_messenger(self) -> bool:
        driver = self.connect_ui_driver()
        if driver is None:
            return False

        username, password = self.get_messenger_login_credentials(self.current_facebook_account_snapshot)

        if self._visible_text_exists(driver, ["Continue as", "Tiếp tục dưới tên", "Tiếp tục với"]):
            for selector in [
                '//*[contains(@text, "Continue as")]',
                '//*[contains(@content-desc, "Continue as")]',
                '//*[contains(@text, "Tiếp tục")]',
                '//*[contains(@content-desc, "Tiếp tục")]',
            ]:
                try:
                    element = driver.xpath(selector)
                    if element.wait(timeout=2):
                        element.click()
                        time.sleep(5)
                        return True
                except Exception:
                    continue

        if not username or not password:
            print("[LOGIN] Messenger may require login, but FACEBOOK_USERNAME/FACEBOOK_PASSWORD are missing.")
            return False

        try:
            fields = driver(className="android.widget.EditText")
            field_count = fields.count if isinstance(fields.count, int) else fields.count()
            if field_count < 2:
                return not self._visible_text_exists(driver, ["Log in", "Đăng nhập", "Password", "Mật khẩu"])

            fields[0].set_text(username)
            time.sleep(0.5)
            fields[1].set_text(password)
            time.sleep(0.5)

            for selector in [
                '//*[contains(@text, "Log in")]',
                '//*[contains(@content-desc, "Log in")]',
                '//*[contains(@text, "Đăng nhập")]',
                '//*[contains(@content-desc, "Đăng nhập")]',
            ]:
                try:
                    element = driver.xpath(selector)
                    if element.wait(timeout=2):
                        element.click()
                        time.sleep(8)
                        return True
                except Exception:
                    continue

            self.run_adb(["shell", "input", "keyevent", "66"])
            time.sleep(8)
            return True
        except Exception as exc:
            print(f"[LOGIN] Messenger login automation failed: {exc}")
            return False

    def ensure_messenger_ready(self) -> bool:
        if not self.is_package_installed(MESSENGER_PKG):
            return self.open_messenger()

        driver = self.connect_ui_driver()
        current_package = self.get_current_package_name(driver)

        # Nếu đang ở Messenger thì chỉ xử lý prompt/PIN/login, không gọi open_messenger()
        # vì open_messenger() có luồng mở Facebook để đồng bộ tài khoản.
        if current_package == MESSENGER_PKG:
            self._handle_pin_screen_and_back()
            if driver is not None and self._visible_text_exists(driver, ["Log in", "Đăng nhập", "Password", "Mật khẩu"]):
                self.login_to_messenger()
            self.messenger_checked = True
            return True

        if self.messenger_checked:
            return True

        if not self.open_messenger():
            return False

        self._handle_pin_screen_and_back()

        driver = self.connect_ui_driver()
        if driver is not None and self._visible_text_exists(driver, ["Log in", "Đăng nhập", "Password", "Mật khẩu"]):
            self.login_to_messenger()

        self.messenger_checked = self.is_package_installed(MESSENGER_PKG)
        return self.messenger_checked

    def connect_to_device(self) -> bool:
        print("[DEVICE] Looking for an authorized Android device...")
        device_rows = self.scan_connected_device_rows()

        devices = [device_id for device_id, status in device_rows if status == "device"]
        if not devices:
            pending_devices = [
                f"{device_id} ({status})"
                for device_id, status in device_rows
                if status in ("unauthorized", "authorizing")
            ]
            if pending_devices:
                print(f"[DEVICE] USB debugging is not authorized: {', '.join(pending_devices)}")
            else:
                print("[DEVICE] No ADB device found.")
            return False

        previous_active_device = self.active_device
        preferred_device = _safe_str(os.getenv("FB_1_1_DEVICE_ID") or os.getenv("ANDROID_SERIAL"))
        requested_account = _safe_str(self.user_id_chat)
        if not preferred_device and requested_account and requested_account != "default":
            for device in self.load_configured_devices():
                device_id = _safe_str(device.get("device_id"))
                if not device_id:
                    continue
                for account_info in device.get("accounts") or []:
                    if not isinstance(account_info, dict):
                        continue
                    if _safe_str(account_info.get("account")) == requested_account:
                        preferred_device = device_id
                        break
                if preferred_device:
                    break

        self.connected_devices = devices
        if preferred_device and preferred_device in devices:
            self.active_device = preferred_device
        else:
            self.active_device = previous_active_device if previous_active_device in devices else devices[0]
        if previous_active_device != self.active_device:
            self.ui_driver = None
            self.messenger_checked = False
        print(f"[DEVICE] Connected devices: {', '.join(self.connected_devices)}")
        print(f"[DEVICE] Selected primary device: {self.active_device}")
        return True

    def scan_connected_device_rows(self) -> list[tuple[str, str]]:
        try:
            result = subprocess.run([self.adb_path, "devices"], capture_output=True, text=True, timeout=10)
        except FileNotFoundError:
            print(f"[DEVICE] adb not found at '{self.adb_path}'.")
            return []
        except Exception as exc:
            print(f"[DEVICE] Cannot scan ADB devices: {exc}")
            return []

        device_rows: list[tuple[str, str]] = []
        for line in result.stdout.strip().splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2:
                device_rows.append((parts[0], parts[1]))
            elif line.strip():
                print(f"[DEVICE] Ignored invalid adb devices row: {line}")
        return device_rows

    def sync_account_from_device(self) -> bool:
        self.connect_to_device()

        metadata_accounts = self.get_configured_accounts_for_connected_devices()
        if metadata_accounts:
            self._set_device_account_metadata(metadata_accounts)
            print(
                "[DEVICE] Configured Facebook accounts for connected devices: "
                + ", ".join(
                    f"{item.get('name') or item['account']} ({item['account']}) on {item.get('device_id')}"
                    for item in metadata_accounts
                )
            )
            if self.facebook_username == "default":
                runtime_snapshot = self.get_runtime_current_account_snapshot()
                runtime_account = _safe_str(runtime_snapshot.get("account"))
                if runtime_account:
                    self.facebook_username = runtime_snapshot.get("username") or runtime_account
            if self.user_id_chat == "default":
                runtime_snapshot = self.get_runtime_current_account_snapshot()
                runtime_account = _safe_str(runtime_snapshot.get("user_id_chat") or runtime_snapshot.get("account"))
                if runtime_account:
                    self.user_id_chat = runtime_account
                    self.current_facebook_account_snapshot = runtime_snapshot
                    self.active_account_verified = True
            return True

        discovered_accounts = []
        for device_id in self.connected_devices or ([self.active_device] if self.active_device else []):
            accounts = self.get_facebook_accounts_on_device(device_id=device_id)
            for account in accounts:
                discovered_accounts.append({
                    "account": account,
                    "name": account,
                    "device_id": device_id,
                    "device_name": device_id,
                })

        accounts = [item["account"] for item in discovered_accounts]
        if accounts:
            self._set_device_account_metadata(discovered_accounts)
            print(f"[DEVICE] Facebook accounts on device: {', '.join(accounts)}")
            if self.facebook_username == "default":
                runtime_snapshot = self.get_runtime_current_account_snapshot()
                runtime_account = _safe_str(runtime_snapshot.get("account"))
                if runtime_account:
                    self.facebook_username = runtime_snapshot.get("username") or runtime_account
            if self.user_id_chat == "default":
                runtime_snapshot = self.get_runtime_current_account_snapshot()
                runtime_account = _safe_str(runtime_snapshot.get("user_id_chat") or runtime_snapshot.get("account"))
                if runtime_account:
                    self.user_id_chat = runtime_account
                    self.current_facebook_account_snapshot = runtime_snapshot
                    self.active_account_verified = True
            return True

        print("[DEVICE] No Facebook account found in Android Account Manager.")
        return False

    def _set_device_account_metadata(self, account_items: list[Dict[str, str]]) -> None:
        ordered_accounts: list[str] = []
        account_names: Dict[str, str] = {}
        account_device_ids: Dict[str, str] = {}
        account_device_names: Dict[str, str] = {}
        account_passwords: Dict[str, str] = {}
        seen_accounts = set()

        for item in account_items:
            account = _safe_str(item.get("account"))
            if not account or account in seen_accounts:
                continue
            seen_accounts.add(account)
            device_id = _safe_str(item.get("device_id")) or self.active_device or ""
            device_name = _safe_str(item.get("device_name")) or device_id
            ordered_accounts.append(account)
            account_names[account] = _safe_str(item.get("name")) or account
            account_device_ids[account] = device_id
            account_device_names[account] = device_name
            account_passwords[account] = _safe_str(item.get("password"))

        self.device_accounts = ordered_accounts
        self.device_account_names = account_names
        self.device_account_device_ids = account_device_ids
        self.device_account_device_names = account_device_names
        self.device_account_passwords = account_passwords

        if self.active_device:
            primary_names = [
                name
                for account, name in account_device_names.items()
                if account_device_ids.get(account) == self.active_device and name
            ]
            self.active_device_name = primary_names[0] if primary_names else self.active_device

    def get_configured_accounts_for_active_device(self) -> list[Dict[str, str]]:
        if not self.active_device:
            return []
        return self.get_configured_accounts_for_device(self.active_device)

    def get_configured_accounts_for_device(self, device_id: str) -> list[Dict[str, str]]:
        devices = self.load_configured_devices()
        for device in devices:
            if str(device.get("device_id") or "").strip() != device_id:
                continue

            accounts = []
            for account_info in device.get("accounts") or []:
                if not isinstance(account_info, dict):
                    continue
                account = _safe_str(account_info.get("account"))
                if not account:
                    continue
                accounts.append({
                    "account": account,
                    "name": _safe_str(account_info.get("name")) or account,
                    "device_id": device_id,
                    "device_name": _safe_str(device.get("device_name")),
                    "password": _safe_str(account_info.get("password")),
                })
            return accounts
        return []

    def get_configured_accounts_for_connected_devices(self) -> list[Dict[str, str]]:
        account_items: list[Dict[str, str]] = []
        for device_id in self.connected_devices or ([self.active_device] if self.active_device else []):
            account_items.extend(self.get_configured_accounts_for_device(device_id))
        return account_items

    def load_configured_devices(self) -> list[Dict[str, Any]]:
        devices = self.load_configured_devices_from_mongo()
        if devices:
            return devices

        devices_file = Path(os.getenv("FACEBOOK_DEVICES_FILE", PROJECT_ROOT / "Facebook.devices.json"))
        try:
            data = json.loads(devices_file.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except Exception as exc:
            print(f"[DEVICE] Cannot load {devices_file}: {exc}")
            return []

    def load_configured_devices_from_mongo(self) -> list[Dict[str, Any]]:
        try:
            import pymongo
            import urllib.parse
        except ImportError:
            return []

        env_file = Path(os.getenv("DATABASE_ACCOUNTS_FILE", PROJECT_ROOT / "DatabaseAccounts.env"))
        try:
            data = json.loads(env_file.read_text(encoding="utf-8")).get("Base", {})
            username = urllib.parse.quote_plus(data["username"])
            password = urllib.parse.quote_plus(data["pwd"])
            uri = f"mongodb://{username}:{password}@123.24.206.25:27017/?authSource=admin"
            client = pymongo.MongoClient(
                uri,
                serverSelectionTimeoutMS=3000,
                connectTimeoutMS=3000,
                socketTimeoutMS=3000,
            )
            try:
                target_devices = [device for device in self.connected_devices if device]
                if not target_devices and self.active_device:
                    target_devices = [self.active_device]
                query = {"device_id": {"$in": target_devices}} if target_devices else {}
                return list(client["Facebook"]["devices"].find(
                    query,
                    {
                        "device_id": 1,
                        "device_name": 1,
                        "current_account": 1,
                        "accounts.account": 1,
                        "accounts.name": 1,
                        "accounts.status": 1,
                        "accounts.emp_id": 1,
                        "accounts.password": 1,
                    },
                ))
            finally:
                client.close()
        except Exception as exc:
            print(f"[DEVICE] Cannot load account metadata from MongoDB, fallback to JSON: {exc}")
            return []

    def setup_socket_events(self) -> None:
        @self.sio.event
        def connect():
            print("[SOCKET] Connected to CRM sender API")
            self.socket_registered = False
            self.register_bot_to_crm(force=True)

        @self.sio.event
        def disconnect():
            self.socket_registered = False
            print("[SOCKET] Disconnected from CRM sender API")

        @self.sio.event
        def send_message_command(data):
            print(f"[SOCKET] Received send command: {data}")
            account_result = self.ensure_account_from_payload(data)
            if not account_result.get("success"):
                return account_result

            result = self.send_message_to_recipient(
                data.get("recipient_url"),
                data.get("message_content"),
                expected_thread_id=data.get("expected_thread_id"),
                participant_name=data.get("participant_name"),
            )

            if result.get("success"):
                self.mark_messenger_priority_hold(reason="CRM socket send")
                self._update_db_tin_nhan_after_send(
                    data.get("recipient_url"),
                    data.get("message_content"),
                    message_id=data.get("message_id"),
                    client_message_id=data.get("client_message_id"),
                )

            response = {
                "message_id": data.get("message_id"),
                "client_message_id": data.get("client_message_id"),
                "conversation_id": data.get("conversation_id"),
                "success": bool(result.get("success")),
                "error": result.get("error", ""),
            }
            if self.sio.connected:
                self.sio.emit("send_message_result", response)
            return response

        @self.sio.event
        def switch_account_command(data):
            print(f"[SOCKET] Received switch account command: {data}")
            return self.switch_account(data)

        @self.sio.event
        def get_current_account_command(data):
            print(f"[SOCKET] Received get_current_account_command: {data}")
            if not self.current_facebook_account_snapshot:
                self.snapshot_facebook_account_before_messenger()
            current_account = self.current_account_id()
            if not current_account:
                return {"success": False, "error": "No current Facebook account selected"}
            return {
                "success": True,
                "current_account": current_account,
                "user_id_chat": current_account,
            }

    def mark_messenger_priority_hold(self, *, reason: str = "") -> None:
        driver = self.ui_driver
        if driver is None:
            with contextlib.suppress(Exception):
                driver = self.connect_ui_driver()
        if driver is None:
            return

        account = _safe_str(self.current_account_id() or self.user_id_chat)
        if not account or account == "default":
            return

        if not self.current_account_id():
            self.current_facebook_account_snapshot = {
                "account": account,
                "user_id_chat": account,
                "username": self.device_account_names.get(account) or self.facebook_username or account,
                "displayName": self.device_account_names.get(account) or self.facebook_username or account,
                "name": self.device_account_names.get(account) or self.facebook_username or account,
                "device_id": _safe_str(getattr(driver, "serial", "") or self.active_device),
                "device_name": self.active_device_name or _safe_str(getattr(driver, "serial", "") or self.active_device),
            }
            self.active_account_verified = True

        setattr(driver, "_fb_1_1_priority_active", True)
        setattr(driver, "_fb_1_1_priority_account", account)
        setattr(driver, "_fb_1_1_priority_last_activity", time.time())
        device_id = _safe_str(getattr(driver, "serial", "") or self.active_device)
        if device_id:
            with contextlib.suppress(Exception):
                from util.device_runtime_state import set_messenger_priority_hold

                set_messenger_priority_hold(device_id, account=account)
        suffix = f" ({reason})" if reason else ""
        print(f"[MOBILE] Messenger 1-1 priority hold active for account {account}{suffix}.")
        self.start_priority_current_thread_scan_loop()

    def start_priority_current_thread_scan_loop(self) -> None:
        if self._priority_current_thread_scan_started:
            return
        self._priority_current_thread_scan_started = True

        def scan_loop() -> None:
            interval = max(2, int(os.getenv("FB_1_1_PRIORITY_CURRENT_CHAT_SCAN_SECONDS", "5")))
            while True:
                account = _safe_str(self.current_account_id() or self.user_id_chat)
                device_id = _safe_str(self.active_device)
                if not account or account == "default" or not device_id:
                    time.sleep(interval)
                    continue
                try:
                    from util.device_runtime_state import is_messenger_priority_hold_active

                    if not is_messenger_priority_hold_active(device_id, account):
                        self._priority_current_thread_scan_started = False
                        return
                except Exception:
                    pass

                try:
                    driver = self.ui_driver or self.connect_ui_driver()
                    self.ui_driver = driver
                    if driver is not None and self.is_current_package(MESSENGER_PKG):
                        found = self.crawl_current_open_thread_messages_to_crm(
                            allow_when_send_queue_pending=True,
                        )
                        if found:
                            print(f"[MOBILE] Priority current-thread scan sent {found} message(s) to CRM.")
                except Exception as exc:
                    print(f"[MOBILE] Priority current-thread scan failed: {type(exc).__name__}: {exc}")
                time.sleep(interval)

        threading.Thread(target=scan_loop, name="fb_1_1_priority_thread_scan", daemon=True).start()

    def _socketio_transports(self) -> list[str]:
        configured = _safe_str(os.getenv("FB_1_1_SOCKET_TRANSPORTS"))
        if configured:
            transports = [
                item.strip().lower()
                for item in re.split(r"[,; ]+", configured)
                if item.strip()
            ]
            return [item for item in transports if item in {"websocket", "polling"}] or ["polling"]

        if importlib.util.find_spec("websocket") is None:
            if not self._socket_transport_warning_printed:
                print("[SOCKET] websocket-client is not installed; using polling transport only.")
                self._socket_transport_warning_printed = True
            return ["polling"]

        return ["websocket", "polling"]

    def connect_to_crm(self) -> bool:
        try:
            if not self.sio.connected:
                transports = self._socketio_transports()
                self.sio.connect(
                    self.crm_sender_url,
                    transports=transports,
                    wait_timeout=float(os.getenv("FB_1_1_SOCKET_CONNECT_WAIT_SECONDS", "8")),
                )
                print(f"[SOCKET] Connected to {self.crm_sender_url}")
            return self.register_bot_to_crm(force=True)
        except Exception as exc:
            print(f"[SOCKET] Cannot connect to CRM sender API: {exc}")
            return False

    def register_bot_to_crm(self, force: bool = False) -> bool:
        if not self.sio.connected:
            return False
        if self.socket_registered and not force:
            latest_snapshot = self.get_preferred_current_account_snapshot()
            latest_account = _safe_str(latest_snapshot.get("user_id_chat") or latest_snapshot.get("account"))
            if latest_account and latest_account != self.current_account_id():
                self._write_account_snapshot(latest_snapshot)
                force = True
            else:
                return True

        if self.socket_registered and not force:
            return True

        # Đảm bảo nhận diện đúng tài khoản thực tế đang chạy trước khi đăng ký với CRM
        if not self.current_account_id() or self.user_id_chat == "default":
            latest_snapshot = self.get_preferred_current_account_snapshot()
            if latest_snapshot:
                self._write_account_snapshot(latest_snapshot)
            else:
                self.snapshot_facebook_account_before_messenger()

        known_account_ids = list(self.device_accounts) if self.device_accounts else []
        active_account_id = self.current_account_id()
        if active_account_id and active_account_id not in known_account_ids:
            known_account_ids.insert(0, active_account_id)

        if not active_account_id:
            runtime_snapshot = self.get_runtime_current_account_snapshot()
            active_account_id = _safe_str(runtime_snapshot.get("user_id_chat") or runtime_snapshot.get("account"))

        # CRITICAL FIX:
        # For the persistent command bot created from main_merged_2/fb_task_patched,
        # self.user_id_chat is already the exact CRM/Facebook account that must be online.
        # Do not skip registration just because profile/runtime detection failed.
        if not active_account_id and _safe_str(self.user_id_chat) and _safe_str(self.user_id_chat) != "default":
            active_account_id = _safe_str(self.user_id_chat)
            if active_account_id not in known_account_ids:
                known_account_ids.insert(0, active_account_id)
            self.current_facebook_account_snapshot = {
                "account": active_account_id,
                "user_id_chat": active_account_id,
                "username": self.facebook_username if self.facebook_username != "default" else active_account_id,
                "displayName": self.facebook_username if self.facebook_username != "default" else active_account_id,
                "name": self.facebook_username if self.facebook_username != "default" else active_account_id,
                "device_id": self.active_device,
                "device_name": self.active_device_name or self.active_device,
            }
            self.active_account_verified = True

        if not active_account_id:
            print("[SOCKET] Active Facebook account is unknown; cannot bot_register.")
            return False

        active_device_accounts = [
            {
                "user_id_chat": item,
                "account": item,
                "username": self.device_account_names.get(item) or item,
                "displayName": self.device_account_names.get(item) or item,
                "name": self.device_account_names.get(item) or item,
                "status": "Online" if item == active_account_id else "Offline",
                "device_id": self.device_account_device_ids.get(item) or self.active_device,
                "device_name": (
                    self.device_account_device_names.get(item)
                    or self.active_device_name
                    or self.active_device
                ),
            }
            for item in known_account_ids
        ]

        ok = False
        for account_id in [active_account_id]:
            account_device_id = self.device_account_device_ids.get(account_id) or self.active_device
            account_device_name = self.device_account_device_names.get(account_id) or self.active_device_name or account_device_id
            payload = {
                "user_id_chat": account_id,
                "username": self.device_account_names.get(account_id) or self.facebook_username or account_id,
                "device_id": account_device_id,
                "device_name": account_device_name,
                "active_device_accounts": active_device_accounts,
            }
            try:
                # Prefer call() so the bot knows the server actually registered it.
                ack = self.sio.call("bot_register", payload, timeout=5)
                ok = bool((ack or {}).get("success", True))
                # print(f"[SOCKET] bot_register ack: {ack}")
                print(f"[SOCKET] bot_register ack")
            except Exception as exc:
                print(f"[SOCKET] bot_register call failed, fallback emit: {exc}")
                self.sio.emit("bot_register", payload)
                ok = True

        self.socket_registered = bool(ok)
        if ok:
            print(f"[SOCKET] Registered active bot account: {active_account_id}")
            with contextlib.suppress(Exception):
                from util.device_runtime_state import is_messenger_priority_hold_active

                if self.active_device and is_messenger_priority_hold_active(self.active_device, active_account_id):
                    self.start_priority_current_thread_scan_loop()
        else:
            print(f"[SOCKET] Register active bot account failed: {active_account_id}")
        self._post_bot_http_heartbeat(active_account_id)
        return bool(ok)

    def _post_bot_http_heartbeat(self, active_account_id: str = "") -> bool:
        account_id = _safe_str(active_account_id or self.current_account_id() or self.user_id_chat)
        if not account_id or account_id == "default":
            return False
        try:
            response = requests.post(
                f"{self.crm_sender_url}/api/bot/heartbeat",
                json={
                    "user_id_chat": account_id,
                    "username": self.device_account_names.get(account_id) or self.facebook_username or account_id,
                    "device_id": self.active_device,
                    "device_name": self.active_device_name or self.active_device,
                },
                timeout=5,
            )
            return response.ok
        except Exception as exc:
            print(f"[SOCKET] HTTP heartbeat failed: {exc}")
            return False

    def start_socket_heartbeat(self, interval: int = 10) -> None:
        if self._socket_heartbeat_started:
            return
        self._socket_heartbeat_started = True

        def heartbeat():
            while True:
                try:
                    if self.sio.connected:
                        self.register_bot_to_crm(force=True)
                    else:
                        self.connect_to_crm()
                    self._post_bot_http_heartbeat()
                except Exception as exc:
                    print(f"[SOCKET] Heartbeat error: {exc}")
                time.sleep(interval)

        threading.Thread(target=heartbeat, daemon=True).start()

    def activate_device_for_account(self, account_id: str) -> None:
        device_id = _safe_str(self.device_account_device_ids.get(account_id))
        if not device_id or device_id == self.active_device:
            return

        if device_id not in self.connected_devices:
            self.connect_to_device()
        if device_id not in self.connected_devices:
            print(f"[DEVICE] Cannot switch to device {device_id} for account {account_id}: device is not connected.")
            return

        self.active_device = device_id
        self.active_device_name = self.device_account_device_names.get(account_id) or device_id
        self.ui_driver = None
        self.messenger_checked = False
        print(f"[DEVICE] Switched active device to {device_id} for account {account_id}.")

    def switch_account(self, data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        payload = data or {}
        account_context = payload.get("accountContext") or payload.get("account_context") or {}
        account = _safe_str(
            payload.get("account")
            or account_context.get("account")
            or account_context.get("username")
            or account_context.get("accountKey")
            or account_context.get("facebookAccountId")
        )
        if not account:
            return {"success": False, "error": "Missing account context"}

        user_id_chat = _safe_str(
            account_context.get("user_id_chat")
            or account_context.get("accountKey")
            or account_context.get("facebookAccountId")
            or account
        )
        account_ids = set(self.device_accounts or [])
        if not account_ids:
            self.sync_account_from_device()
            account_ids = set(self.device_accounts or [])

        if account_ids and user_id_chat not in account_ids and account not in account_ids:
            return {
                "success": False,
                "error": f"Account {user_id_chat or account} is not available on the connected device",
                "available_accounts": sorted(account_ids),
            }

        matched_account_id = user_id_chat if user_id_chat in account_ids else account
        force_reset = bool(payload.get("force_reset") or account_context.get("force_reset"))
        if user_id_chat and self.user_id_chat == user_id_chat and not force_reset:
            self.activate_device_for_account(matched_account_id)
            return {
                "success": True,
                "account": account,
                "user_id_chat": self.user_id_chat,
                "skipped": True,
            }

        self.activate_device_for_account(matched_account_id)
        ui_result = self.switch_active_ui_account(account_context, account, user_id_chat)
        if not ui_result.get("success"):
            return ui_result

        self.facebook_username = account
        self.facebook_password = payload.get("password") or account_context.get("password")
        self.user_id_chat = user_id_chat or self.user_id_chat
        self.active_account_verified = True
        print(f"[ACCOUNT] Updated current account context: {account} ({self.user_id_chat})")
        self.register_bot_to_crm(force=True)
        return {"success": True, "account": account, "user_id_chat": self.user_id_chat}

    def _account_display_name_from_context(self, account_context: Dict[str, Any], account: str = "", user_id_chat: str = "") -> str:
        for value in (
            account_context.get("displayName"),
            account_context.get("name"),
            account_context.get("username"),
            self.device_account_names.get(user_id_chat),
            self.device_account_names.get(account),
            account,
            user_id_chat,
        ):
            text = _safe_str(value)
            if text:
                return text
        return ""

    def _sync_current_account_context(self, account_context: Dict[str, Any], account: str, user_id_chat: str) -> None:
        display_name = self._account_display_name_from_context(account_context, account, user_id_chat)
        self.facebook_username = display_name or account or user_id_chat
        self.facebook_password = _safe_str(account_context.get("password") or self.facebook_password)
        self.user_id_chat = user_id_chat or account or self.user_id_chat
        self.active_account_verified = True
        self.current_facebook_account_snapshot = {
            "account": self.user_id_chat,
            "user_id_chat": self.user_id_chat,
            "username": display_name or self.user_id_chat,
            "displayName": display_name or self.user_id_chat,
            "name": display_name or self.user_id_chat,
            "device_id": self.active_device,
            "device_name": self.active_device_name or self.active_device,
            "password": self.facebook_password,
        }

    def switch_active_ui_account(self, account_context: Dict[str, Any], account: str, user_id_chat: str) -> Dict[str, Any]:
        driver = self.connect_ui_driver()
        if driver is None:
            return {"success": False, "error": "Cannot connect UI driver for account switch"}

        current_package = self.get_current_package_name(driver)
        if current_package == MESSENGER_PKG or self.is_current_package(MESSENGER_PKG):
            return self.switch_messenger_profile_account(driver, account_context, account, user_id_chat)

        if current_package == FACEBOOK_PKG or self.is_current_package(FACEBOOK_PKG):
            return self.switch_facebook_profile_account(driver, account_context, account, user_id_chat)

        print(f"[ACCOUNT] Current package is {current_package or 'unknown'}; opening Facebook for account switch.")
        try:
            driver.app_start(FACEBOOK_PKG)
        except Exception:
            self.run_adb(["shell", "monkey", "-p", FACEBOOK_PKG, "-c", "android.intent.category.LAUNCHER", "1"])
        time.sleep(4)
        return self.switch_facebook_profile_account(driver, account_context, account, user_id_chat)

    def switch_facebook_profile_account(self, driver, account_context: Dict[str, Any], account: str, user_id_chat: str) -> Dict[str, Any]:
        display_name = self._account_display_name_from_context(account_context, account, user_id_chat)
        acc = {
            "name": display_name,
            "account": user_id_chat or account,
            "password": _safe_str(account_context.get("password") or self.device_account_passwords.get(user_id_chat) or self.device_account_passwords.get(account)),
        }
        if not acc["name"]:
            return {"success": False, "error": "Missing display name for Facebook account switch"}

        try:
            from module.login import swap_account_new
        except Exception:
            try:
                from login import swap_account_new
            except Exception as exc:
                return {"success": False, "error": f"Cannot import existing Facebook switch flow: {exc}"}

        try:
            ok = asyncio.run(swap_account_new(driver, acc))
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                ok = loop.run_until_complete(swap_account_new(driver, acc))
            finally:
                loop.close()
        except Exception as exc:
            return {"success": False, "error": f"Facebook account switch failed: {exc}"}

        if not ok:
            return {"success": False, "error": f"Existing Facebook switch flow could not switch to {display_name}"}

        self._sync_current_account_context(account_context, account, user_id_chat)
        return {"success": True, "account": account, "user_id_chat": user_id_chat, "switched_via": "facebook"}

    def switch_messenger_profile_account(self, driver, account_context: Dict[str, Any], account: str, user_id_chat: str) -> Dict[str, Any]:
        display_name = self._account_display_name_from_context(account_context, account, user_id_chat)
        if not display_name:
            return {"success": False, "error": "Missing display name for Messenger account switch"}

        print(f"[ACCOUNT] Switching Messenger profile account to {display_name}.")
        if not self.open_messenger_profile_switcher(driver):
            return {"success": False, "error": "Cannot open Messenger profile switcher"}

        if not self.select_messenger_profile_account(driver, display_name):
            return {"success": False, "error": f"Cannot select Messenger account '{display_name}'"}

        self.click_messenger_continue_after_profile_switch(driver, timeout=8)
        if not self.wait_until_messenger_ready_for_navigation(driver, timeout=20):
            return {"success": False, "error": f"Messenger did not become ready after switching to {display_name}"}

        self._sync_current_account_context(account_context, account, user_id_chat)
        self.click_messenger_chats_tab(driver)
        return {"success": True, "account": account, "user_id_chat": user_id_chat, "switched_via": "messenger"}

    def open_messenger_profile_switcher(self, driver=None) -> bool:
        driver = driver or self.connect_ui_driver()
        if driver is None:
            return False

        if self.is_message_request_detail_screen(driver):
            print("[ACCOUNT] Messenger request detail is open; pressing Back before profile switch.")
            self._safe_messenger_back(driver, reason="leave message request detail before profile switch")
            time.sleep(1.2)

        if self.is_message_requests_list_screen(driver):
            print("[ACCOUNT] Messenger Message Requests list is open; pressing Back to return to Menu before profile switch.")
            if not self._safe_messenger_back(driver, reason="leave Message Requests list before profile switch"):
                return False
            time.sleep(1.2)

        if self._find_messenger_composer_node(driver) is not None and not self.is_messenger_chats_home_screen(driver):
            print("[ACCOUNT] Messenger thread is open; pressing Back before opening Menu.")
            self._safe_messenger_back(driver, reason="leave current chat before profile switch")
            time.sleep(1.2)

        if not self.is_messenger_menu_tab_screen(driver):
            if not self.click_messenger_bottom_menu_tab(driver):
                return False

        for _ in range(3):
            target = self._find_messenger_profile_switcher_entry(driver)
            if target:
                print(
                    "[ACCOUNT] Messenger profile switcher entry matched: "
                    f"label={target['label']} | bounds={target['bounds']} | score={target['score']}"
                )
                self._tap_bounds_center(target["bounds"], reason="Messenger profile switcher entry")
                time.sleep(2)
                return True
            self.run_adb(["shell", "input", "swipe", "360", "760", "360", "1120", "220"])
            time.sleep(1)

        return False

    def _find_messenger_profile_switcher_entry(self, driver=None) -> Optional[Dict[str, Any]]:
        driver = driver or self.connect_ui_driver()
        if driver is None:
            return None

        try:
            root = ET.fromstring(self._dump_hierarchy_xml(driver, reason="find Messenger profile switcher"))
        except Exception:
            return None

        candidates: list[Dict[str, Any]] = []
        switch_terms = [
            "chuyen trang ca nhan",
            "switch profile",
            "switch account",
            "da dang nhap duoi ten",
            "logged in as",
        ]
        for node in root.iter("node"):
            label = self._node_label(node)
            norm = self._normalize_ui_text(label)
            if not norm or not any(term in norm for term in switch_terms):
                continue
            bounds = _safe_str(node.attrib.get("bounds"))
            parsed = self._parse_bounds(bounds)
            if not parsed:
                continue
            x1, y1, x2, y2 = parsed
            area = max(0, (x2 - x1) * (y2 - y1))
            score = 1000 + min(area // 1000, 300)
            if node.attrib.get("clickable") == "true":
                score += 300
            if "button" in _safe_str(node.attrib.get("class")).lower():
                score += 200
            if x1 <= 8 and x2 >= 600:
                score += 150
            candidates.append({"label": label, "bounds": bounds, "score": score})

        if not candidates:
            return None
        candidates.sort(key=lambda item: item["score"], reverse=True)
        return candidates[0]

    def select_messenger_profile_account(self, driver, display_name: str, max_swipes: int = 4) -> bool:
        target_norm = self._normalize_ui_text(display_name)
        if not target_norm:
            return False

        for attempt in range(max_swipes + 1):
            target = self._find_messenger_account_row(driver, target_norm)
            if target:
                print(
                    "[ACCOUNT] Messenger account row matched: "
                    f"label={target['label']} | bounds={target['bounds']} | score={target['score']}"
                )
                self._tap_bounds_center(target["bounds"], reason=f"select Messenger account {display_name}")
                time.sleep(2.5)
                return True

            if attempt < max_swipes:
                print(f"[ACCOUNT] Messenger account '{display_name}' not visible; scrolling switcher.")
                self.run_adb(["shell", "input", "swipe", "360", "1220", "360", "430", "350"])
                time.sleep(1.2)

        return False

    def _find_messenger_account_row(self, driver, target_norm: str) -> Optional[Dict[str, Any]]:
        try:
            root = ET.fromstring(self._dump_hierarchy_xml(driver, reason="find Messenger account row"))
        except Exception:
            return None

        candidates: list[Dict[str, Any]] = []
        for node in root.iter("node"):
            label = self._node_label(node)
            norm = self._normalize_ui_text(label)
            if not norm:
                continue
            if target_norm != norm and target_norm not in norm:
                continue
            if any(block in norm for block in ["dang nhap bang tai khoan khac", "login with another account"]):
                continue
            bounds = _safe_str(node.attrib.get("bounds"))
            parsed = self._parse_bounds(bounds)
            if not parsed:
                continue
            x1, y1, x2, y2 = parsed
            area = max(0, (x2 - x1) * (y2 - y1))
            score = 1000 + min(area // 1000, 300)
            if node.attrib.get("clickable") == "true":
                score += 300
            if "chuyen sang" in norm or "switch to" in norm:
                score += 250
            if x1 <= 8 and x2 >= 600:
                score += 150
            candidates.append({"label": label, "bounds": bounds, "score": score})

        if not candidates:
            return None
        candidates.sort(key=lambda item: item["score"], reverse=True)
        return candidates[0]

    def click_messenger_continue_after_profile_switch(self, driver=None, timeout: int = 8) -> bool:
        driver = driver or self.connect_ui_driver()
        if driver is None:
            return False

        deadline = time.time() + max(1, timeout)
        while time.time() < deadline:
            if self._click_best_ui_text(
                driver,
                ["Tiếp tục", "Continue", "Tiếp tục dưới tên", "Continue as"],
                allow_contains=True,
                reason="Messenger continue after profile switch",
            ):
                time.sleep(4)
                return True
            if self.is_messenger_home_screen(driver) or self.is_messenger_chats_home_screen(driver):
                return False
            time.sleep(1)
        return False

    def ensure_account_from_payload(self, data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        payload = data or {}
        account_context = payload.get("accountContext") or payload.get("account_context") or {}
        requested_account = _safe_str(
            account_context.get("user_id_chat")
            or account_context.get("accountKey")
            or account_context.get("facebookAccountId")
            or account_context.get("account")
            or payload.get("user_id_chat")
        )
        if not requested_account:
            return {"success": True}

        actual_snapshot = self.get_preferred_current_account_snapshot()
        actual_account = _safe_str(
            actual_snapshot.get("user_id_chat")
            or actual_snapshot.get("account")
            or self.current_account_id()
        )
        if actual_account and actual_account != self.current_account_id():
            self._write_account_snapshot(actual_snapshot)

        if requested_account != actual_account:
            print(
                "[ACCOUNT] CRM send requires account switch: "
                f"requested={requested_account}, actual={actual_account or 'unknown'}, cached={self.user_id_chat}"
            )
            switch_payload = {**payload, "force_reset": True}
            return self.switch_account(switch_payload)

        if self.user_id_chat != requested_account:
            self._sync_current_account_context(account_context, requested_account, requested_account)
        return {"success": True}

    def send_message_to_recipient(self, recipient_url: str, message_content: str, **kwargs) -> Dict[str, Any]:
        if not self.active_device and not self.connect_to_device():
            return {"success": False, "error": "No authorized Android device found."}

        with self.device_lock:
            try:
                self._last_send_failure_reason = ""
                previous_skip = getattr(self, "_skip_open_messenger_initial_request_crawl", False)
                self._skip_open_messenger_initial_request_crawl = True
                try:
                    ready = self.ensure_messenger_ready()
                finally:
                    self._skip_open_messenger_initial_request_crawl = previous_skip
                if not ready:
                    return {"success": False, "error": "Messenger is not installed or cannot be opened."}

                participant_name = self._clean_participant_name(kwargs.get("participant_name", ""))
                if participant_name:
                    print(f"[MOBILE] Sending by Messenger participant_name: {participant_name}")
                    if not self.search_and_open_chat_by_name(self.connect_ui_driver(), participant_name):
                        driver = self.connect_ui_driver()
                        if self.is_messenger_thread_screen_for_participant(driver, participant_name):
                            print("[SEND] Chat search returned false but the target Messenger thread is open; continuing.")
                        else:
                            allow_request_fallback = os.getenv("FB_1_1_QUEUE_SEND_ALLOW_MESSAGE_REQUEST_FALLBACK", "0").lower() in {"1", "true", "yes", "on"}
                            if not allow_request_fallback or not self.search_and_open_message_request_by_name(driver, participant_name):
                                reason = getattr(self, "_last_send_failure_reason", "") or f"Cannot find Messenger chat for {participant_name}."
                                return {"success": False, "error": reason}
                    if not self.type_and_send_message(self.connect_ui_driver(), message_content):
                        reason = getattr(self, "_last_send_failure_reason", "") or "Cannot type or submit Messenger message."
                        return {"success": False, "error": reason}
                    if not self.verify_message_sent(self.connect_ui_driver(), message_content):
                        reason = getattr(self, "_last_send_failure_reason", "") or "Messenger send could not be verified."
                        return {"success": False, "error": reason}
                    return {"success": True, "error": ""}

                thread_id = self._extract_thread_id(recipient_url, kwargs.get("expected_thread_id"))
                if not thread_id:
                    return {"success": False, "error": "Invalid recipient URL or thread id."}

                if not _safe_str(message_content):
                    return {"success": False, "error": "Message content is empty."}

                print(f"[MOBILE] Opening Messenger thread: {thread_id}")
                self.run_adb(
                    [
                        "shell",
                        "am",
                        "start",
                        "-a",
                        "android.intent.action.VIEW",
                        "-d",
                        f"fb-messenger://user/{thread_id}",
                    ]
                )
                time.sleep(3)

                safe_content = self._format_adb_input_text(message_content)
                print(f"[MOBILE] Typing message: {_safe_str(message_content)[:30]}...")
                self.run_adb(["shell", "input", "text", safe_content])
                time.sleep(1)

                self.run_adb(["shell", "input", "keyevent", "66"])
                print("[MOBILE] Send keyevent submitted.")
                return {"success": True, "error": ""}
            except Exception as exc:
                with contextlib.suppress(Exception):
                    self._skip_open_messenger_initial_request_crawl = previous_skip
                print(f"[MOBILE] Send failed: {exc}")
                return {"success": False, "error": str(exc)}

    def _extract_thread_id(self, recipient_url: str, expected_thread_id: Optional[str] = None) -> Optional[str]:
        if expected_thread_id:
            return str(expected_thread_id)

        value = str(recipient_url or "").strip()
        match = re.search(r"/messages/t/([^/?#]+)|/t/([^/?#]+)", value)
        if not match:
            if value and not value.startswith(("http://", "https://")):
                return value
            return None
        return next((group for group in match.groups() if group), None)

    def _format_adb_input_text(self, message_content: str) -> str:
        text = str(message_content)
        text = text.replace("\\", "\\\\")
        text = text.replace(" ", "%s")
        text = text.replace('"', '\\"')
        text = text.replace("'", "\\'")
        text = text.replace("&", "\\&")
        text = text.replace("|", "\\|")
        text = text.replace("<", "\\<")
        text = text.replace(">", "\\>")
        text = text.replace(";", "\\;")
        text = text.replace("(", "\\(").replace(")", "\\)")
        text = text.replace("$", "\\$")
        text = text.replace("`", "\\`")
        return text

    def get_facebook_accounts_on_device(self, device_id: Optional[str] = None) -> list[str]:
        target_device = device_id or self.active_device
        print(f"[MOBILE] Reading Facebook accounts from device {target_device or 'default'}...")
        output = self.run_adb(["shell", "dumpsys", "account"], device_id=target_device)
        accounts = re.findall(r"Account \{name=(.+?), type=com.facebook\.(?:auth\.login|messenger)\}", output)
        return sorted(set(accounts))

    def load_db(self) -> None:
        if not self.use_file_chat_cache:
            self.chat_history_db = {}
            self.logged_chat_ids = set()
            self.logged_message_keys = set()
            print("[DB] File chat cache disabled; using in-memory chat history.")
            return
        try:
            if not self.db_file.exists():
                print(f"[DB] {self.db_file} not found. It will be created when data exists.")
                self.chat_history_db = {}
                self.logged_chat_ids = set()
                self.logged_message_keys = set()
                return

            content = self.db_file.read_text(encoding="utf-8").strip()
            self.chat_history_db = json.loads(content) if content else {}
            self.logged_chat_ids = set(self.chat_history_db)
            self.logged_message_keys = self._build_logged_message_keys(self.chat_history_db)
            print(f"[DB] Loaded {len(self.logged_chat_ids)} conversations from {self.db_file}.")
        except Exception as exc:
            print(f"[DB] Cannot load {self.db_file}: {exc}")
            self.chat_history_db = {}
            self.logged_chat_ids = set()
            self.logged_message_keys = set()

    def save_data_to_file(self) -> None:
        if not self.use_file_chat_cache:
            self.logged_chat_ids = set(self.chat_history_db)
            self.logged_message_keys = self._build_logged_message_keys(self.chat_history_db)
            return
        try:
            # Create a copy for saving, keeping enough recent messages for unread bursts.
            save_data = {}
            message_limit = max(5, DEFAULT_LOCAL_MESSAGE_LIMIT)
            for chat_id, conv in self.chat_history_db.items():
                save_conv = conv.copy()
                messages = save_conv.get("messages", save_conv.get("last_5_messages", []))
                messages = self._sanitize_and_dedupe_messages(messages)
                save_conv.pop("messages", None)
                save_conv["last_5_messages"] = messages[-message_limit:]
                if save_conv["last_5_messages"]:
                    save_conv["last_message"] = save_conv["last_5_messages"][-1].get("content", "")
                    save_conv["last_message_time"] = save_conv["last_5_messages"][-1].get("time", "")
                elif self._is_ignorable_system_content(save_conv.get("last_message", "")):
                    save_conv["last_message"] = ""
                save_data[chat_id] = save_conv
            self.db_file.write_text(
                json.dumps(save_data, ensure_ascii=False, indent=4),
                encoding="utf-8",
            )
            print(f"[DB] Saved data to {self.db_file}.")
        except Exception as exc:
            print(f"[DB] Cannot save {self.db_file}: {exc}")

    def _update_db_tin_nhan_after_send(
        self,
        recipient_url: str,
        message_content: str,
        message_id: Optional[str] = None,
        client_message_id: Optional[str] = None,
    ) -> None:
        if not self.use_file_chat_cache:
            return
        try:
            thread_id = self._extract_thread_id(recipient_url)
            if not thread_id or not self.db_file.exists():
                return

            db_data = json.loads(self.db_file.read_text(encoding="utf-8"))
            if thread_id not in db_data:
                return

            conversation = db_data[thread_id]
            now = datetime.now().astimezone().isoformat(timespec="seconds")
            active_account = self.require_active_account_id("local cache update after send")
            conversation["user_id_chat"] = conversation.get("user_id_chat") or active_account
            conversation["account"] = conversation.get("account") or active_account
            new_message = {
                "sender": "Tôi",
                "content": message_content,
                "time": now,
                "user_id_chat": active_account,
                "account": active_account,
            }
            if message_id:
                new_message["message_id"] = message_id
            if client_message_id:
                new_message["client_message_id"] = client_message_id

            conversation.setdefault("messages", [])
            for existing_message in conversation["last_5_messages"]:
                if message_id and str(existing_message.get("message_id") or "") == str(message_id):
                    return
                if client_message_id and str(existing_message.get("client_message_id") or "") == str(client_message_id):
                    return

            conversation["last_5_messages"].append(new_message)
            conversation["last_5_messages"] = conversation["last_5_messages"][-max(5, DEFAULT_LOCAL_MESSAGE_LIMIT):]
            conversation["last_message"] = "now"
            conversation["last_message_time"] = "just now"

            self.db_file.write_text(json.dumps(db_data, ensure_ascii=False, indent=4), encoding="utf-8")
            print(f"[DB] Updated local chat cache for thread {thread_id}.")
        except Exception as exc:
            print(f"[DB] Cannot update local chat cache: {exc}")

    def send_messages_to_crm(self, chat_id: str, messages_data: Dict[str, Any]) -> None:
        try:
            active_account = self.require_active_account_id("send messages to CRM")
            conversation_account = _safe_str(
                messages_data.get("user_id_chat")
                or messages_data.get("account")
                or messages_data.get("facebookAccountId")
            )
            if not conversation_account:
                print(f"[CRM] Skip chat {chat_id} because local conversation has no account.")
                return
            if conversation_account and active_account and conversation_account != active_account:
                print(
                    f"[CRM] Skip chat {chat_id} because it belongs to {conversation_account}, "
                    f"current active account is {active_account}."
                )
                return

            messages_for_crm = []
            seen_contents = set()
            crm_messages = messages_data.get("messages", [])
            all_context_messages = messages_data.get("all_messages") or messages_data.get("last_5_messages") or crm_messages
            for msg in crm_messages:
                message_account = _safe_str(msg.get("user_id_chat") or msg.get("account") or conversation_account)
                if message_account and active_account and message_account != active_account:
                    continue
                message_source = _safe_str(msg.get("source") or messages_data.get("source") or "mobile_visible")
                if message_source in {"messenger_home", "mobile_visible", "window_dump", "window_dump_button"}:
                    continue
                content = _safe_str(msg.get("content"))
                sender = msg.get("sender", "Unknown")
                timestamp = msg.get("time", "")
                if not content or sender in { "T\u00f4i", "Toi", "Me"}:
                    continue
                if self._is_system_message_row({"sender": sender, "content": content}):
                    continue

                duplicate_key = (
                    self._message_duplicate_identity({"sender": sender, "content": content})
                    if message_source == "conversation_detail"
                    else (sender, content, timestamp)
                )
                if duplicate_key in seen_contents:
                    continue
                seen_contents.add(duplicate_key)

                context_index = -1
                for index, context_message in enumerate(all_context_messages):
                    if not isinstance(context_message, dict):
                        continue
                    if self._message_duplicate_identity(context_message) == self._message_duplicate_identity(msg):
                        context_index = index
                        break
                previous_context = all_context_messages[context_index - 1] if context_index > 0 else {}
                next_context = (
                    all_context_messages[context_index + 1]
                    if 0 <= context_index < len(all_context_messages) - 1
                    else {}
                )

                messages_for_crm.append(
                    {
                        "participant_name": messages_data.get("sender", "Unknown"),
                        "participant_url": str(chat_id),
                        "conversation_url": str(chat_id),
                        "content": content,
                        "sender_name": sender,
                        "timestamp": timestamp,
                        "source": message_source,
                        "previous_message_content": _safe_str((previous_context or {}).get("content")),
                        "previous_message_is_from_crm": (previous_context or {}).get("sender") in {"TÃ´i", "Tôi", "Toi", "Me"},
                        "next_message_content": _safe_str((next_context or {}).get("content")),
                        "next_message_is_from_crm": (next_context or {}).get("sender") in {"TÃ´i", "Tôi", "Toi", "Me"},
                    }
                )

            if not messages_for_crm:
                return

            response = requests.post(
                f"{self.crm_receiver_url}/api/bot/new_messages",
                json={"user_id_chat": active_account, "messages": messages_for_crm},
                timeout=10,
            )
            response.raise_for_status()
            try:
                response_payload = response.json()
            except Exception:
                response_payload = {}
            processed_count = len(response_payload.get("messages") or []) if isinstance(response_payload, dict) else 0
            if processed_count != len(messages_for_crm):
                print(
                    f"[CRM] Receiver accepted HTTP but processed {processed_count}/{len(messages_for_crm)} "
                    f"message(s): {response.text[:500]}"
                )
            else:
                print(f"[CRM] Sent {len(messages_for_crm)} new message(s) to receiver API.")
        except Exception as exc:
            print(f"[CRM] Cannot send new messages to receiver API: {exc}")

    def _message_key(self, chat_id: str, msg: Dict[str, Any]) -> tuple[str, str, str, str]:
        return (
            str(chat_id),
            _safe_str(msg.get("sender")),
            _safe_str(msg.get("content")),
            _safe_str(msg.get("time") or msg.get("timestamp")),
        )

    def _text_decode_variants(self, value: Any) -> list[str]:
        text = _safe_str(value)
        variants = [text] if text else []
        for encoding in ("cp1252", "latin1"):
            try:
                repaired = text.encode(encoding).decode("utf-8")
            except Exception:
                continue
            if repaired and repaired not in variants:
                variants.append(repaired)
        return variants

    def _is_ignorable_system_content(self, content: Any) -> bool:
        for variant in self._text_decode_variants(content):
            normalized = self._normalize_ui_text(variant)
            compact = re.sub(r"[^a-z0-9]+", " ", normalized).strip()
            if not compact:
                continue
            system_terms = [
                "tin nhan va cuoc goi duoc bao mat bang tinh nang ma hoa dau cuoi",
                "chi nhung nguoi tham gia doan chat nay moi co the doc nghe hoac chia se",
                "messages and calls are secured with end to end encryption",
                "messages and calls are secured with end-to-end encryption",
                "only people in this chat can read listen to or share",
                "end to end encryption",
                "end-to-end encryption",
            ]
            if any(term in compact or term in normalized for term in system_terms):
                return True
            non_message_ui_terms = [
                "ten bo suu tap",
                "tao bo suu tap",
                "them nguoi dong gop",
                "bat buoc",
                "bo suu tap",
                "collection name",
                "create collection",
                "add contributors",
                "contributors",
                "required",
            ]
            if any(term in compact or term in normalized for term in non_message_ui_terms):
                return True
            if "ma hoa dau cuoi" in compact and ("tin nhan" in compact or "cuoc goi" in compact):
                return True
            if "end to end" in compact and ("encrypted" in compact or "encryption" in compact):
                return True
        return False

    def _message_duplicate_identity(self, msg: Dict[str, Any]) -> tuple[str, str]:
        sender = self._normalize_ui_text(msg.get("sender") or "")
        content = _safe_str(msg.get("content"))
        if self._is_ignorable_system_content(content):
            content = ""
        content = self._normalize_ui_text(content)
        return sender, content

    def _message_time_identity(self, msg: Dict[str, Any]) -> str:
        return self._normalize_ui_text(msg.get("time") or msg.get("timestamp") or "")

    def _is_duplicate_message_for_cache(self, existing: Dict[str, Any], incoming: Dict[str, Any]) -> bool:
        existing_identity = self._message_duplicate_identity(existing)
        incoming_identity = self._message_duplicate_identity(incoming)
        if existing_identity != incoming_identity:
            return False
        existing_source = _safe_str(existing.get("source"))
        incoming_source = _safe_str(incoming.get("source"))
        if incoming_identity[1] and "conversation_detail" in {existing_source, incoming_source}:
            return True
        existing_time = self._message_time_identity(existing)
        incoming_time = self._message_time_identity(incoming)
        if existing_time and incoming_time and existing_time != incoming_time:
            return False
        if incoming_identity[1]:
            return True
        if existing_source != incoming_source and {existing_source, incoming_source} & {"messenger_home", "conversation_detail"}:
            return True
        return self._is_ignorable_system_content(existing.get("content", "")) and self._is_ignorable_system_content(incoming.get("content", ""))

    def _sanitize_and_dedupe_messages(self, messages: Any) -> list[Dict[str, Any]]:
        cleaned: list[Dict[str, Any]] = []
        if not isinstance(messages, list):
            return cleaned
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            item = msg.copy()
            if self._is_ignorable_system_content(item.get("content", "")):
                continue
            if any(self._is_duplicate_message_for_cache(existing, item) for existing in cleaned):
                continue
            cleaned.append(item)
        return cleaned

    def _build_logged_message_keys(self, history: Dict[str, Any]) -> set:
        keys = set()
        for chat_id, messages_data in history.items():
            if not isinstance(messages_data, dict):
                continue
            messages = messages_data.get("messages")
            if not isinstance(messages, list):
                messages = messages_data.get("last_5_messages", [])
            for msg in messages:
                if isinstance(msg, dict):
                    keys.add(self._message_key(chat_id, msg))
        return keys

    def current_account_id(self) -> str:
        snapshot_device = _safe_str(self.current_facebook_account_snapshot.get("device_id"))
        if self.active_device and snapshot_device and snapshot_device != _safe_str(self.active_device):
            return ""
        account = _safe_str(
            self.current_facebook_account_snapshot.get("user_id_chat")
            or self.current_facebook_account_snapshot.get("account")
        )
        if not account and self.active_account_verified and self._account_belongs_to_active_device(self.user_id_chat):
            account = _safe_str(self.user_id_chat)
        return "" if account == "default" else account

    def _safe_chat_id_from_sender(self, sender: str) -> str:
        safe = re.sub(r"[^0-9A-Za-z_.-]+", "_", _safe_str(sender)).strip("_")
        return f"mobile_visible:{safe or 'unknown'}"

    def _node_label(self, node: ET.Element) -> str:
        if "edittext" in _safe_str(node.attrib.get("class")).lower():
            text_value = _safe_str(node.attrib.get("text"))
            if text_value:
                return text_value
        return (
            _safe_str(node.attrib.get("content-desc"))
            or _safe_str(node.attrib.get("text"))
            or _safe_str(node.attrib.get("hint"))
        )

    def _strip_message_accessibility_tail(self, value: str) -> str:
        text = _safe_str(value)
        if not text:
            return ""

        normalized = self._normalize_ui_text(text)
        tail_markers = [
            "nhan dup de xem ngay gio gui/nhan",
            "nhan dup va giu de bay to cam xuc ve tin nhan",
            "double tap to view sent",
            "double tap and hold to react",
        ]
        cut_at = len(text)
        for marker in tail_markers:
            norm_index = normalized.find(marker)
            if norm_index < 0:
                continue

            prefix_norm = normalized[:norm_index].rstrip(" ,")
            if not prefix_norm:
                cut_at = 0
                break

            # Map the normalized prefix back to an approximate original string
            # boundary by growing an original prefix until normalization matches.
            for index in range(min(len(text), cut_at) + 1):
                if self._normalize_ui_text(text[:index]).rstrip(" ,") == prefix_norm:
                    cut_at = min(cut_at, index)
                    break

        text = text[:cut_at].strip(" ,\n\t")
        return text

    def _has_message_accessibility_tail(self, value: str) -> bool:
        normalized = self._normalize_ui_text(value)
        return any(term in normalized for term in [
            "nhan dup de xem ngay gio gui/nhan",
            "nhan dup va giu de bay to cam xuc ve tin nhan",
            "double tap to view sent",
            "double tap and hold to react",
        ])

    def _clean_participant_name(self, value: str) -> str:
        text = _safe_str(value)
        if not text:
            return ""
        text = self._strip_message_accessibility_tail(text)
        first = _safe_str(text.split(",", 1)[0])
        if first and not re.search(r"\d|@|https?://", first, re.I):
            return first
        return text

    def _messenger_now(self) -> datetime:
        return datetime.now(timezone(timedelta(hours=7)))

    def _parse_messenger_time_label(self, value: str) -> str:
        text = _safe_str(value)
        if not text:
            return ""

        normalized_variants = [self._normalize_ui_text(item).replace(".", "") for item in self._text_decode_variants(text)]
        normalized = next(
            (
                item for item in normalized_variants
                if re.search(r"\b\d{1,2}\s*th(?:ang)?\s*\d{1,2}\b", item)
                or re.search(r"\b(?:luc|l\S{0,4}c)\s*\d{1,2}:\d{2}\b", item)
            ),
            normalized_variants[0] if normalized_variants else "",
        )
        match = re.search(r"\b(?:luc|l\S{0,4}c)\s*(\d{1,2}):(\d{2})\b", normalized)
        if not match:
            match = re.search(r"\b(\d{1,2}):(\d{2})\b", normalized)
        if not match:
            return ""

        hour = int(match.group(1))
        minute = int(match.group(2))
        if hour > 23 or minute > 59:
            return ""

        now = self._messenger_now()
        date_value = None

        absolute = re.search(r"\b(\d{1,2})\s*th(?:ang)?\s*(\d{1,2})\b", normalized)
        if absolute:
            day = int(absolute.group(1))
            month = int(absolute.group(2))
            try:
                date_value = datetime(now.year, month, day, hour, minute, tzinfo=now.tzinfo)
                if date_value > now + timedelta(days=1):
                    date_value = datetime(now.year - 1, month, day, hour, minute, tzinfo=now.tzinfo)
            except ValueError:
                return ""
        else:
            weekday_map = {
                "t2": 0, "thu 2": 0, "thu hai": 0,
                "t3": 1, "thu 3": 1, "thu ba": 1,
                "t4": 2, "thu 4": 2, "thu tu": 2,
                "t5": 3, "thu 5": 3, "thu nam": 3,
                "t6": 4, "thu 6": 4, "thu sau": 4,
                "t7": 5, "thu 7": 5, "thu bay": 5,
                "cn": 6, "chu nhat": 6,
            }
            target_weekday = None
            for token, weekday in weekday_map.items():
                if re.search(rf"\b{re.escape(token)}\b", normalized):
                    target_weekday = weekday
                    break
            if target_weekday is None:
                return ""

            days_back = (now.weekday() - target_weekday) % 7
            candidate_date = now - timedelta(days=days_back)
            date_value = datetime(
                candidate_date.year,
                candidate_date.month,
                candidate_date.day,
                hour,
                minute,
                tzinfo=now.tzinfo,
            )
            if date_value > now:
                date_value -= timedelta(days=7)

        return date_value.isoformat(timespec="seconds") if date_value else ""

    def _message_time_from_node_y(self, y: int, time_markers: list[tuple[int, str]]) -> str:
        selected = ""
        for marker_y, marker_time in sorted(time_markers, key=lambda item: item[0]):
            if marker_y <= y:
                selected = marker_time
            else:
                break
        return selected

    def _parse_bare_messenger_time_label(self, value: str) -> str:
        text = _safe_str(value)
        normalized = self._normalize_ui_text(text).replace(".", "")
        match = re.fullmatch(r"(\d{1,2}):(\d{2})", normalized)
        if not match:
            return ""
        hour = int(match.group(1))
        minute = int(match.group(2))
        if hour > 23 or minute > 59:
            return ""

        base = self._messenger_now()
        candidate = datetime(base.year, base.month, base.day, hour, minute, tzinfo=base.tzinfo)
        return candidate.isoformat(timespec="seconds")

    def _strip_sender_prefix_from_message(self, value: str, sender: str = "") -> str:
        text = _safe_str(value)
        if not text:
            return ""

        parts = text.split(",", 1)
        if len(parts) != 2:
            return text

        first = _safe_str(parts[0])
        rest = _safe_str(parts[1])
        if not first or not rest:
            return text

        normalized_first = self._normalize_ui_text(first)
        normalized_sender = self._normalize_ui_text(sender)
        sender_tokens = set(normalized_sender.split())
        first_tokens = set(normalized_first.split())

        looks_like_sender = False
        if normalized_sender and (
            normalized_first == normalized_sender
            or normalized_first in normalized_sender
            or normalized_sender.endswith(normalized_first)
        ):
            looks_like_sender = True
        elif first_tokens and sender_tokens and first_tokens.issubset(sender_tokens):
            looks_like_sender = True
        elif len(first.split()) <= 3 and len(first) <= 40 and not re.search(r"\d|@|https?://", first, re.I):
            looks_like_sender = True

        return rest if looks_like_sender else text

    def _conversation_header_sender_from_root(self, root: ET.Element) -> str:
        screen_w, screen_h = self._get_screen_size()
        candidates: list[tuple[int, str]] = []
        blocked_terms = [
            "quay lai", "back", "cuoc goi", "goi video", "chi tiet chuoi bai",
            "thread details", "voice call", "video call",
        ]

        for node in root.iter("node"):
            label = self._node_label(node)
            if not label:
                continue

            bounds = _safe_str(node.attrib.get("bounds"))
            parsed_bounds = self._parse_bounds(bounds)
            if not parsed_bounds:
                continue

            x1, y1, x2, y2 = parsed_bounds
            if y1 > int(screen_h * 0.16) or y2 > int(screen_h * 0.18):
                continue

            normalized = self._normalize_ui_text(label)
            if any(term in normalized for term in blocked_terms):
                if "," not in label:
                    continue

            sender = self._clean_participant_name(label)
            if not sender or self._is_blocked_visible_sender(sender):
                continue
            if self._normalize_ui_text(sender) in {"anh", "photo", "avatar"}:
                continue

            score = 0
            if 80 <= x1 <= int(screen_w * 0.35):
                score += 40
            if "button" in _safe_str(node.attrib.get("class")).lower():
                score += 15
            if "," in label:
                score += 30
            if len(sender.split()) >= 2:
                score += 25
            score -= abs(y1 - 72) // 10
            candidates.append((score, sender))

        if not candidates:
            return ""

        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]

    def _conversation_header_sender_from_dump(self, driver=None) -> str:
        driver = driver or self.connect_ui_driver()
        if driver is None:
            return ""
        try:
            root = ET.fromstring(driver.dump_hierarchy())
        except Exception:
            return ""
        return self._conversation_header_sender_from_root(root)

    def _parse_conversation_message_label(
        self,
        label: str,
        sender: str = "",
        message_time: str = "",
        is_outgoing: bool = False,
    ) -> Optional[Dict[str, str]]:
        raw = _safe_str(label)
        if not raw:
            return None
        if not self._has_message_accessibility_tail(raw):
            return None

        content = self._strip_message_accessibility_tail(raw)
        content = self._strip_sender_prefix_from_message(content, sender)
        content = html.unescape(_safe_str(content))
        if not content:
            return None
        if self._is_ignorable_system_content(content):
            return None

        normalized_content = self._normalize_ui_text(content)
        blocked_content_terms = [
            "nhan tin", "nhap tin nhan", "gui luot thich", "mo ban phim",
            "them cam xuc", "da xem", "gio day cac ban co the goi va nhan tin",
            "cuoc goi", "goi video", "chi tiet chuoi bai",
            "tin nhan va cuoc goi duoc bao mat bang tinh nang ma hoa dau cuoi",
            "chi nhung nguoi tham gia doan chat nay moi co the doc nghe hoac chia se",
            "messages and calls are secured with end-to-end encryption",
            "only people in this chat can read listen to or share",
        ]
        if any(term in normalized_content for term in blocked_content_terms):
            return None

        parsed = {
            "sender": "Tôi" if is_outgoing else (self._clean_participant_name(sender) or "Unknown"),
            "content": content,
            "time": message_time or self._messenger_now().isoformat(timespec="seconds"),
        }
        return parsed

    def _handle_pin_screen_and_back(self) -> bool:
        """Handle only a real Messenger PIN screen without leaving Messenger.

        The old logic matched the generic word "PIN" anywhere in the UI and then
        pressed Android Back as a fallback. That can back out from Messenger to
        Facebook, and a later Back can drop to the phone launcher. Here we only
        react to a blocking PIN screen and never use the unsafe Android Back
        fallback.
        """
        driver = self.connect_ui_driver()
        if driver is None:
            return False
        if self.get_current_package_name(driver) != MESSENGER_PKG and not self.is_current_package(MESSENGER_PKG):
            return False

        visible_blob = self._read_visible_ui_text_blob(driver)
        normalized_blob = self._normalize_ui_text(visible_blob)
        if not normalized_blob:
            return False

        strong_pin_terms = [
            "nhập mã pin", "enter pin", "enter your pin",
            "tạo mã pin", "create a pin", "confirm pin", "xác nhận mã pin",
            "mã pin không đúng", "incorrect pin", "forgot pin", "quên mã pin",
        ]
        if not any(self._normalize_ui_text(term) in normalized_blob for term in strong_pin_terms):
            return False

        # If this is already the normal Messenger home with a non-blocking banner,
        # do not touch Back.
        if self.is_messenger_chats_home_screen(driver) or self.is_message_requests_list_screen(driver):
            print("[LOGIN] PIN-related text is only a banner/list text. Keeping Messenger screen unchanged.")
            return False

        # Nếu đang có các ô nhập liệu, đây là màn hình nhập PIN thực sự. 
        # Tuyệt đối không bấm nút "Đóng" để tránh văng ra ngoài.
        try:
            if driver(className="android.widget.EditText").exists: return False
            if "truong ma khoa" in normalized_blob or "chinh sua 1/6" in normalized_blob: return False
        except: pass

        print("[LOGIN] Blocking Messenger PIN screen detected. Searching top-left in-app back button only.")

        try:
            root = ET.fromstring(driver.dump_hierarchy())
            candidates = []
            for node in root.iter("node"):
                label = self._node_label(node)
                normalized_label = self._normalize_ui_text(label)
                bounds = _safe_str(node.attrib.get("bounds"))
                parsed = self._parse_bounds(bounds)
                if not parsed:
                    continue
                x1, y1, x2, y2 = parsed
                if x1 > 220 or y1 > 260:
                    continue
                class_name = _safe_str(node.attrib.get("class"))
                clickable = node.attrib.get("clickable") == "true"
                score = 0
                if clickable:
                    score += 40
                
                # Chỉ cân nhắc các nút có nhãn thực sự là "Quay lại". Tránh bấm nhầm nút "Đóng".
                is_back_term = any(term in normalized_label for term in ["back", "quay lai", "tro ve", "navigate up"])
                if not is_back_term:
                    continue

                class_name = _safe_str(node.attrib.get("class"))
                score = 0
                if node.attrib.get("clickable") == "true": score += 40
                if "imagebutton" in class_name.lower() or "button" in class_name.lower(): score += 30
                score += 80
                candidates.append({"label": label, "bounds": bounds, "score": score})

            if candidates:
                candidates.sort(key=lambda item: item["score"], reverse=True)
                target = candidates[0]
                print(f"[LOGIN] Clicking in-app PIN back button: {target['label']} | bounds={target['bounds']}")
                self._tap_bounds_center(target["bounds"], reason="PIN in-app back")
                time.sleep(1.5)
                if self.get_current_package_name(driver) != MESSENGER_PKG and not self.is_current_package(MESSENGER_PKG):
                    self.bring_messenger_to_front(driver, reason="PIN in-app back recovery")
                    return False
                return True
        except Exception as exc:
            print(f"[LOGIN] PIN in-app back-button search failed: {exc}")

        print("[LOGIN] No safe in-app PIN back button found. Not pressing Android Back; keep Messenger foreground.")
        return False

    def _click_conversation_by_label(self, driver, label: str) -> bool:
        """Click on a conversation node that matches the given label."""
        try:
            for node in driver(className="android.widget.RelativeLayout"):
                node_label = self._node_label(node)
                if node_label == label:
                    node.click()
                    time.sleep(2)
                    return True
        except Exception:
            pass
        return False

    def _crawl_conversation_messages(
        self,
        driver,
        account_chat_id: str,
        conversation: Dict[str, Any],
        *,
        max_passes: int = 3,
        scroll_between_passes: bool = True,
    ) -> int:
        """Crawl messages inside an opened conversation."""
        inserted = 0
        seen_labels = set()
        active_account = _safe_str(conversation.get("user_id_chat") or conversation.get("account"))
        if not active_account:
            active_account = self.require_active_account_id("conversation detail crawl")
        sender = _safe_str(conversation.get("sender") or conversation.get("participant_name"))
        screen_w, screen_h = self._get_screen_size()
        current_message_time = ""
        time_markers: list[tuple[int, str]] = []

        def parse_current_dump() -> int:
            nonlocal sender, current_message_time, time_markers
            try:
                xml = driver.dump_hierarchy()
                root = ET.fromstring(xml)
            except Exception:
                return 0

            header_sender = self._conversation_header_sender_from_root(root)
            if header_sender and header_sender != "Unknown":
                sender = header_sender
                conversation["sender"] = header_sender
                conversation["participant_name"] = header_sender

            time_markers = []
            time_nodes = []
            for node in root.iter("node"):
                bounds = _safe_str(node.attrib.get("bounds"))
                parsed_bounds = self._parse_bounds(bounds)
                y1 = parsed_bounds[1] if parsed_bounds else 0
                time_nodes.append((y1, node))

            for _, node in sorted(time_nodes, key=lambda item: item[0]):
                label = self._node_label(node)
                parsed_time = self._parse_messenger_time_label(label)
                if not parsed_time:
                    parsed_time = self._parse_bare_messenger_time_label(label)
                if not parsed_time:
                    continue
                bounds = _safe_str(node.attrib.get("bounds"))
                parsed_bounds = self._parse_bounds(bounds)
                marker_y = parsed_bounds[1] if parsed_bounds else 0
                time_markers.append((marker_y, parsed_time))

            pass_inserted = 0
            existing_messages = [
                item
                for item in (conversation.get("messages") or conversation.get("last_5_messages", []))
                if isinstance(item, dict)
            ]
            existing_keys = {
                self._message_key(account_chat_id, item)
                for item in existing_messages
            }
            for node in root.iter("node"):
                label = self._node_label(node)
                if not label or label in seen_labels:
                    continue

                parsed_time = self._parse_messenger_time_label(label) or self._parse_bare_messenger_time_label(label)
                if parsed_time:
                    current_message_time = parsed_time
                    seen_labels.add(label)
                    continue

                seen_labels.add(label)

                bounds = _safe_str(node.attrib.get("bounds"))
                parsed_bounds = self._parse_bounds(bounds)
                node_time = self._message_time_from_node_y(parsed_bounds[1], time_markers) if parsed_bounds else current_message_time

                parsed = self._parse_conversation_message_label(
                    label,
                    sender=sender,
                    message_time=node_time or current_message_time,
                    is_outgoing=bool(
                        parsed_bounds
                        and (
                            ((parsed_bounds[0] + parsed_bounds[2]) / 2) >= int(screen_w * 0.55)
                            or (parsed_bounds[0] >= int(screen_w * 0.43) and parsed_bounds[2] >= int(screen_w * 0.88))
                        )
                    ),
                )
                if not parsed:
                    continue

                parsed["source"] = "conversation_detail"
                parsed["user_id_chat"] = active_account
                parsed["account"] = active_account
                key = self._message_key(account_chat_id, parsed)
                if key in existing_keys:
                    continue
                if any(
                    self._is_duplicate_message_for_cache(item, parsed)
                    for item in existing_messages
                ):
                    continue

                conversation["last_5_messages"].append(parsed)
                existing_messages.append(parsed)
                existing_keys.add(key)
                pass_inserted += 1
            return pass_inserted

        for pass_index in range(max(1, max_passes)):
            inserted += parse_current_dump()
            if (not scroll_between_passes) or pass_index >= max(1, max_passes) - 1:
                break
            self.run_adb([
                "shell", "input", "swipe",
                str(screen_w // 2),
                str(int(screen_h * 0.34)),
                str(screen_w // 2),
                str(int(screen_h * 0.68)),
                "280",
            ])
            time.sleep(1.2)

        return inserted

    def crawl_current_open_thread_messages_to_crm(self, *, allow_when_send_queue_pending: bool = False) -> int:
        """Crawl the currently open Messenger thread and push new inbound bubbles to CRM."""
        if not allow_when_send_queue_pending and self.has_pending_crm_send_queue():
            print("[SEND] CRM send queue is pending; skip current thread crawl.")
            return 0
        if not self.ui_crawl_enabled:
            return 0

        driver = self.connect_ui_driver()
        if driver is None:
            return 0
        if not self.is_current_package(MESSENGER_PKG):
            return 0

        try:
            root = ET.fromstring(driver.dump_hierarchy())
        except Exception as exc:
            print(f"[MOBILE] Cannot dump current Messenger thread: {exc}")
            return 0

        has_thread_composer = self._root_has_messenger_composer(root) or self._find_messenger_composer_node(driver) is not None
        header_sender = self._conversation_header_sender_from_root(root)
        is_current_thread = bool(has_thread_composer and header_sender)
        if self.is_messenger_chats_home_screen(driver):
            return 0
        if self.is_message_requests_list_screen(driver) and not is_current_thread:
            return 0
        if not has_thread_composer:
            return 0

        active_account = self.require_active_account_id("current open thread crawl")
        participant_name = (
            header_sender
            or _safe_str(getattr(self, "_last_active_thread_participant_name", ""))
            or "Unknown"
        )
        if not participant_name or participant_name == "Unknown":
            print("[MOBILE] Cannot determine current thread participant; skip current thread crawl.")
            return 0

        chat_id = self._safe_chat_id_from_sender(participant_name)
        account_chat_id = f"{active_account}:{chat_id}"

        if self.use_file_chat_cache:
            try:
                content = self.db_file.read_text(encoding="utf-8").strip() if self.db_file.exists() else ""
                history = json.loads(content) if content else {}
                if not isinstance(history, dict):
                    history = {}
            except Exception:
                history = {}
        else:
            history = self.chat_history_db if isinstance(self.chat_history_db, dict) else {}

        conversation = history.setdefault(
            account_chat_id,
            {
                "sender": participant_name,
                "participant_name": participant_name,
                "participant_url": chat_id,
                "conversation_url": chat_id,
                "last_5_messages": [],
                "source": "conversation_detail",
                "user_id_chat": active_account,
                "account": active_account,
            },
        )
        conversation["sender"] = participant_name
        conversation["participant_name"] = participant_name
        conversation["participant_url"] = conversation.get("participant_url") or chat_id
        conversation["conversation_url"] = conversation.get("conversation_url") or chat_id
        conversation["source"] = "conversation_detail"
        conversation["user_id_chat"] = active_account
        conversation["account"] = active_account
        if "messages" in conversation and "last_5_messages" not in conversation:
            conversation["last_5_messages"] = conversation.get("messages") or []
        conversation.setdefault("last_5_messages", [])

        before_keys = {
            self._message_key(account_chat_id, item)
            for item in conversation.get("last_5_messages", [])
            if isinstance(item, dict)
        }
        inserted = self._crawl_conversation_messages(
            driver,
            account_chat_id,
            conversation,
            max_passes=1,
            scroll_between_passes=False,
        )
        if inserted <= 0:
            return 0

        message_limit = max(5, DEFAULT_LOCAL_MESSAGE_LIMIT)
        conversation["last_5_messages"] = self._sanitize_and_dedupe_messages(conversation.get("last_5_messages", []))[-message_limit:]
        conversation["messages"] = conversation["last_5_messages"]
        if conversation["last_5_messages"]:
            conversation["last_message"] = conversation["last_5_messages"][-1].get("content", "")
            conversation["last_message_time"] = conversation["last_5_messages"][-1].get("time", "")

        self.chat_history_db = history
        self.logged_chat_ids = set(history)

        new_messages = []
        for msg in conversation.get("last_5_messages", []):
            if not isinstance(msg, dict):
                continue
            key = self._message_key(account_chat_id, msg)
            if key in before_keys:
                continue
            sender = _safe_str(msg.get("sender"))
            if sender in {"Tôi", "Toi", "Me"}:
                continue
            if self._is_system_message_row({"sender": sender, "content": _safe_str(msg.get("content"))}):
                continue
            if key in self.logged_message_keys:
                continue
            self.logged_message_keys.add(key)
            new_messages.append(msg)

        if new_messages:
            self.send_messages_to_crm(account_chat_id, {
                **conversation,
                "messages": new_messages,
                "all_messages": conversation.get("last_5_messages", []),
            })
        self.save_data_to_file()
        return len(new_messages)

    def _is_blocked_visible_sender(self, sender: str) -> bool:
        normalized = self._normalize_ui_text(sender)
        if not normalized:
            return True
        blocked_exact = {
            "tất cả", "tat ca", "ảnh", "anh", "reels", "vị trí", "vi tri",
            "quê quán", "que quan", "ngày sinh", "ngay sinh", "tạo", "tao",
            "all", "photos", "photo", "create", "location", "hometown", "birthday",
        }
        if normalized in blocked_exact:
            return True
        blocked_prefixes = (
            "tin của ", "tin cua ",
            "để xem tin nhắn bị thiếu", "de xem tin nhan bi thieu",
            "để sử dụng tính năng", "de su dung tinh nang",
        )
        return normalized.startswith(blocked_prefixes)

    def _looks_like_messenger_message_row(self, label: str) -> bool:
        value = _safe_str(label)
        if not value:
            return False

        normalized_value = self._normalize_ui_text(value)
        ignored_fragments = [
            "search", "tìm kiếm", "chats", "đoạn chat", "menu", "profile",
            "settings", "cài đặt", "camera", "edit", "soạn", "new message",
            "tin nhắn mới", "message requests", "tin nhắn đang chờ", "trang cá nhân",
            "bài viết", "giới thiệu", "người theo dõi", "bạn bè",
        ]
        ignored_fragments.extend([
            "ten bo suu tap", "tao bo suu tap", "them nguoi dong gop", "bat buoc",
            "bo suu tap", "collection name", "create collection", "add contributors",
        ])
        if any(item in normalized_value for item in ignored_fragments):
            return False

        # Những node kiểu Story/Profile cũng có dấu phẩy nên phải chặn trước khi parse.
        if normalized_value.startswith("tin của ") or normalized_value.startswith("tin cua "):
            return False
        if "nhấn đúp để tạo" in normalized_value or "nhan dup de tao" in normalized_value:
            return False

        has_separator = any(separator in value for separator in [",", "\n", " · ", " - "])
        has_time_hint = bool(re.search(r"\b(\d{1,2}:\d{2}|now|just now|phút|giờ|hôm qua|yesterday|am|pm)\b", normalized_value))
        parts = [_safe_str(part) for part in re.split(r"[\n,]+", value) if _safe_str(part)]
        if parts:
            sender_norm = self._normalize_ui_text(parts[0])
            if sender_norm.startswith("job #") or sender_norm.startswith("#"):
                return False

        has_accessibility_tail = self._has_message_accessibility_tail(value)
        # A comma alone is too weak: non-chat Messenger surfaces such as
        # collections/channels/job panels also expose "title, description".
        return has_time_hint or has_accessibility_tail or (has_separator and len(parts) >= 3)

    def _parse_visible_message_label(self, label: str) -> Optional[Dict[str, str]]:
        value = _safe_str(label)
        if not self._looks_like_messenger_message_row(value):
            return None

        parts = [_safe_str(part) for part in re.split(r"[\n,]+", value) if _safe_str(part)]
        if len(parts) < 2:
            return None

        sender = parts[0]
        if self._is_blocked_visible_sender(sender):
            return None

        time_hint = ""
        content_parts = []
        for part in parts[1:]:
            if not time_hint and re.search(r"\b(\d{1,2}:\d{2}|now|just now|phút|giờ|hôm qua|yesterday|am|pm)\b", part, re.I):
                time_hint = part
                continue
            content_parts.append(part)

        content = " - ".join(content_parts).strip()
        if not sender or not content:
            return None

        parsed = {
            "sender": sender,
            "content": content,
            "time": time_hint or datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        if self._is_system_message_row(parsed):
            return None
        return parsed

    def _is_system_message_row(self, message: Dict[str, str]) -> bool:
        sender = self._normalize_ui_text(message.get("sender") or "")
        content = self._normalize_ui_text(message.get("content") or "")
        combined = self._normalize_ui_text(f"{sender} {content}")
        if not content or self._is_blocked_visible_sender(sender):
            return True
        if self._is_ignorable_system_content(message.get("content") or ""):
            return True
        if self._is_ignorable_system_content(message.get("sender") or ""):
            return True
        if sender.startswith("job #") or sender.startswith("#"):
            return True

        system_patterns = [
            r"^\d+\s*th[oô]ng\s*b[aá]o$",
            r"^\d+\s*th.{0,4}ng\s*b.{0,4}o$",
            r"^\d+\s*notifications?$",
            r"^\d+\s*m[uụ]c\s*c[aậ]p\s*nh[aậ]t$",
            r"^\d+\s*updates?$",
            r"^\d+\s*trong số\s*\d+$",
            r"^\d+\s*trong so\s*\d+$",
            r"^\d+/\d+$",
            r"^tab\s+\d+/\d+$",
            r"^\d+\s*tin\s*nhan\s*moi$",
            r"^\d+\s*new\s*messages?$",
            r"^\d+\s*vach\.?$",
            r"^\d+\s*bars?\.?$",
        ]
        if any(re.search(pattern, content, re.I) for pattern in system_patterns):
            return True

        system_terms = [
            "thông báo", "thong bao", "notification", "notifications",
            "mục cập nhật", "muc cap nhat", "updates", "tab menu",
            "tab 1/", "tab 2/", "tab 3/", "tab 4/", "tab 5/", "tab 6/",
            "trang chủ", "trang chu", "nhóm", "nhom", "cộng đồng", "cong dong",
            "kho lưu trữ", "kho luu tru", "lời mời kết bạn", "loi moi ket ban",
            "facebook reels", "sự kiện trên facebook", "su kien tren facebook",
            "nhập bằng giọng nói", "nhap bang giong noi", "phím", "phim",
            "chi tiết chuỗi bài", "chi tiet chuoi bai",
            "khôi phục lịch sử chat", "khoi phuc lich su chat",
            "message requests", "tin nhắn đang chờ", "tin nhan dang cho",
            "đang hoạt động", "dang hoat dong", "hoạt động", "hoat dong",
            "tin chưa đọc", "tin chua doc", "chưa xem", "chua xem",
            "active now", "nhấn đúp để tạo", "nhan dup de tao", "trong số", "trong so",
            "tin nhan moi", "new message", "new messages",
            "cuoc goi nho", "missed call", "da bo lo cuoc goi",
            "tin hieu day du", "vach", "phan tram pin", "vpn dang bat",
            "vn vinaphone", "saymee", "wifi", "wi fi",
        ]
        extra_system_terms = [
            "loi moi tham gia kenh",
            "channel invite",
        ]
        return any(term in combined for term in system_terms + extra_system_terms)

    def _is_unread_filter_label(self, label: str) -> bool:
        norm = self._normalize_ui_text(label)
        norm = re.sub(r"\s+", " ", norm).strip(" ,.")
        if norm in {"chua doc", "unread"}:
            return True
        if norm.startswith("chua doc") and len(norm) <= 32:
            return True
        if norm.startswith("unread") and len(norm) <= 32:
            return True
        return False

    def _is_unread_filter_selected(self, driver=None) -> bool:
        driver = driver or self.connect_ui_driver()
        if driver is None:
            return False
        try:
            root = ET.fromstring(driver.dump_hierarchy())
        except Exception:
            return False
        for node in root.iter("node"):
            label = self._node_label(node)
            if self._is_unread_filter_label(label) and node.attrib.get("selected") == "true":
                return True
        return False

    def crawl_visible_messenger_messages(self, source: str = "messenger_home") -> int:
        if self.has_pending_crm_send_queue():
            print("[SEND] CRM send queue is pending; skip visible Messenger crawl.")
            return 0
        if not self.ui_crawl_enabled:
            return 0
        if source != "message_requests" and not self.ensure_messenger_ready():
            return 0
        if not self.is_current_package(MESSENGER_PKG) and not self.open_messenger():
            return 0

        driver = self.connect_ui_driver()
        if driver is None:
            return 0

        self._handle_pin_screen_and_back()
        if (
            source != "message_requests"
            and self._find_messenger_composer_node(driver) is not None
            and not self.is_messenger_chats_home_screen(driver)
        ):
            print("[MOBILE] Messenger thread is open. Using conversation-detail crawl instead of list preview crawl.")
            return self.crawl_current_open_thread_messages_to_crm()

        if source == "messenger_home" and (
            self._is_unread_filter_selected(driver)
            or bool(self._unread_conversation_row_candidates(driver))
        ):
            print("[MOBILE] Unread conversations visible. Skipping list preview crawl; opening conversations instead.")
            self.collect_unread_unreplied_conversations()
            return 0

        try:
            xml = driver.dump_hierarchy()
            root = ET.fromstring(xml)
        except Exception as exc:
            print(f"[MOBILE] Cannot dump Messenger UI: {exc}")
            return 0

        if self.use_file_chat_cache:
            try:
                content = self.db_file.read_text(encoding="utf-8").strip() if self.db_file.exists() else ""
                history = json.loads(content) if content else {}
                if not isinstance(history, dict):
                    history = {}
                for conv in history.values():
                    if "last_5_messages" in conv and "messages" not in conv:
                        conv["messages"] = conv["last_5_messages"]
            except Exception:
                history = {}
        else:
            history = self.chat_history_db if isinstance(self.chat_history_db, dict) else {}

        inserted = 0
        seen_labels = set()
        active_account = self.require_active_account_id("visible Messenger crawl")
        for node in root.iter("node"):
            label = self._node_label(node)
            if not label or label in seen_labels:
                continue
            seen_labels.add(label)

            parsed = self._parse_visible_message_label(label)
            if not parsed:
                continue
            if self._is_system_message_row(parsed):
                continue
            normalized_sender = self._normalize_ui_text(parsed["sender"])
            normalized_content = self._normalize_ui_text(parsed["content"])

            # Enhanced filtering for non-chat messages
            if normalized_content.startswith("tab ") or re.fullmatch(r"\d+/\d+", normalized_content):
                continue
            if normalized_sender in {
                "trang chủ", "trang chu", "nhóm", "nhom", "cộng đồng", "cong dong",
                "kho lưu trữ", "kho luu tru", "lời mời kết bạn", "loi moi ket ban",
                "facebook reels", "tắt", "tat", "ảnh", "anh", "reels", "vị trí", "vi tri",
                "quê quán", "que quan", "ngày sinh", "ngay sinh", "tạo", "tao",
                "tất cả", "tat ca", "bắt đầu", "bat dau", "kết thúc", "ket thuc",
                "thông báo", "thong bao", "cập nhật", "cap nhat", "hệ thống", "he thong",
                "facebook", "messenger", "meta", "instagram", "whatsapp"
            }:
                continue
            # Filter messages that look like system notifications or UI elements
            if any(keyword in normalized_content.lower() for keyword in [
                "đã gửi", "da gui", "đã nhận", "da nhan", "đang tải", "dang tai",
                "kết nối", "ket noi", "mất kết nối", "mat ket noi", "lỗi", "loi",
                "thử lại", "thu lai", "hủy", "huy", "xác nhận", "xac nhan"
            ]):
                continue
                continue

            chat_id = self._safe_chat_id_from_sender(parsed["sender"])
            account_chat_id = f"{active_account}:{chat_id}"
            conversation = history.setdefault(
                account_chat_id,
                {
                    "sender": parsed["sender"],
                    "participant_name": parsed["sender"],
                    "participant_url": chat_id,
                    "conversation_url": chat_id,
                    "last_5_messages": [],
                    "source": source,
                    "user_id_chat": active_account,
                    "account": active_account,
                },
            )
            conversation["sender"] = conversation.get("sender") or parsed["sender"]
            conversation["participant_name"] = conversation.get("participant_name") or parsed["sender"]
            conversation["participant_url"] = chat_id
            conversation["conversation_url"] = chat_id
            conversation["source"] = source or conversation.get("source") or "messenger_home"
            conversation["user_id_chat"] = conversation.get("user_id_chat") or active_account
            conversation["account"] = conversation.get("account") or active_account
            conversation.setdefault("last_5_messages", [])

            parsed["source"] = source
            parsed["user_id_chat"] = active_account
            parsed["account"] = active_account
            key = self._message_key(account_chat_id, parsed)
            existing_keys = {
                self._message_key(account_chat_id, item)
                for item in conversation["last_5_messages"]
                if isinstance(item, dict)
            }
            if key in existing_keys:
                continue
            if any(
                self._is_duplicate_message_for_cache(item, parsed)
                for item in conversation["last_5_messages"]
                if isinstance(item, dict)
            ):
                continue

            conversation["last_5_messages"].append(parsed)
            conversation["last_message"] = parsed["content"]
            conversation["last_message_time"] = parsed["time"]
            inserted += 1

            # If conversation appears unread (content starts with number) and has few messages, click to crawl more
            if re.match(r"^\d+", parsed["content"].strip()) and len(conversation["last_5_messages"]) <= 5:
                if self._click_conversation_by_label(driver, label):
                    inserted += self._crawl_conversation_messages(driver, account_chat_id, conversation)
                    # Press back to return to conversation list
                    try:
                        driver.press("back")
                        time.sleep(1)
                    except Exception:
                        pass

        if inserted:
            self.chat_history_db = history
            self.save_data_to_file()
            print(f"[MOBILE] Crawled {inserted} visible Messenger message row(s) from {source}.")
        return inserted

    def _unread_conversation_row_candidates(self, driver=None) -> list[Dict[str, Any]]:
        driver = driver or self.connect_ui_driver()
        if driver is None:
            return []
        try:
            root = ET.fromstring(driver.dump_hierarchy())
        except Exception:
            return []

        screen_w, screen_h = self._get_screen_size()
        dump_bounds = [
            parsed
            for node in root.iter("node")
            for parsed in [self._parse_bounds(_safe_str(node.attrib.get("bounds")))]
            if parsed
        ]
        if dump_bounds:
            screen_w = max(item[2] for item in dump_bounds)
            screen_h = max(item[3] for item in dump_bounds)

        def row_child_labels(row_node: ET.Element) -> list[str]:
            labels: list[str] = []
            seen_labels: set[str] = set()
            for child in row_node.iter("node"):
                label = self._node_label(child)
                if not label or label in seen_labels:
                    continue
                seen_labels.add(label)
                labels.append(label)
            return labels

        def sender_from_unread_label(label: str) -> str:
            text = _safe_str(label).strip()
            norm = self._normalize_ui_text(text)
            if not norm.endswith("chua doc") and not norm.endswith("unread"):
                return ""
            if "tin chua doc" in norm or "dang hoat dong" in norm or "active now" in norm:
                return ""
            if "." in text:
                text = text.rsplit(".", 1)[0]
            elif "," in text:
                text = text.rsplit(",", 1)[0]
            else:
                text = re.sub(r"(?i)\s+unread\s*$", "", text).strip()
            return self._clean_participant_name(text)

        rows: list[Dict[str, Any]] = []
        seen = set()
        for node in root.iter("node"):
            bounds = _safe_str(node.attrib.get("bounds"))
            parsed = self._parse_bounds(bounds)
            if not parsed:
                continue
            x1, y1, x2, y2 = parsed
            height = y2 - y1
            if y1 < int(screen_h * 0.34) or y2 > int(screen_h * 0.88):
                continue
            if x1 > 8 or x2 < int(screen_w * 0.85) or height < 72 or height > 240:
                continue
            if node.attrib.get("clickable") != "true":
                continue

            labels = row_child_labels(node)
            group_norm = self._normalize_ui_text(" | ".join(labels))
            if any(term in group_norm for term in [
                "tin cua ban",
                "tao tin",
                "ghi chu ve nhac",
                "drop a thought",
                "share a song",
                "meta ai",
            ]):
                continue
            if any(term in group_norm for term in ["tab ", "menu", "tim kiem", "search"]):
                continue

            sender = ""
            for label in labels:
                sender = sender_from_unread_label(label)
                if sender:
                    break

            row_label = self._node_label(node)
            row_norm = self._normalize_ui_text(row_label)
            if not sender and (
                "tin nhan moi" in row_norm
                or "new message" in row_norm
                or "new messages" in row_norm
            ):
                sender = self._clean_participant_name(_safe_str(row_label).split(",", 1)[0])

            if not sender or self._is_blocked_visible_sender(sender):
                continue
            norm_sender = self._normalize_ui_text(sender)
            if norm_sender in seen:
                continue
            seen.add(norm_sender)
            rows.append({
                "sender": sender,
                "label": " | ".join(labels),
                "bounds": bounds,
                "row_key": norm_sender,
                "y1": y1,
            })

        if rows:
            rows.sort(key=lambda item: item["y1"])
            return rows

        raw_nodes: list[Dict[str, Any]] = []
        for node in root.iter("node"):
            label = self._node_label(node)
            if not label:
                continue
            bounds = _safe_str(node.attrib.get("bounds"))
            parsed = self._parse_bounds(bounds)
            if not parsed:
                continue
            x1, y1, x2, y2 = parsed
            if y1 < int(screen_h * 0.18) or y2 > int(screen_h * 0.88):
                continue
            norm = self._normalize_ui_text(label)
            if norm in {"chua doc", "unread"}:
                continue
            if any(term in norm for term in ["tab ", "menu", "thong bao", "doan chat", "tim kiem", "search"]):
                continue
            raw_nodes.append({
                "label": label,
                "norm": norm,
                "bounds": parsed,
                "x1": x1,
                "y1": y1,
                "x2": x2,
                "y2": y2,
                "cy": (y1 + y2) // 2,
            })

        raw_nodes.sort(key=lambda item: (item["cy"], item["x1"]))
        groups: list[Dict[str, Any]] = []
        for item in raw_nodes:
            target = None
            for group in groups:
                if abs(item["cy"] - group["cy"]) <= 65 or item["y1"] <= group["y2"] + 28:
                    target = group
                    break
            if target is None:
                target = {"nodes": [], "x1": item["x1"], "y1": item["y1"], "x2": item["x2"], "y2": item["y2"], "cy": item["cy"]}
                groups.append(target)
            target["nodes"].append(item)
            target["x1"] = min(target["x1"], item["x1"])
            target["y1"] = min(target["y1"], item["y1"])
            target["x2"] = max(target["x2"], item["x2"])
            target["y2"] = max(target["y2"], item["y2"])
            target["cy"] = sum(node["cy"] for node in target["nodes"]) // len(target["nodes"])

        rows: list[Dict[str, Any]] = []
        seen = set()
        for group in groups:
            labels = [node["label"] for node in sorted(group["nodes"], key=lambda item: (item["y1"], item["x1"]))]
            group_norm = self._normalize_ui_text(" | ".join(labels))
            if any(term in group_norm for term in [
                "tin cua ban",
                "tao tin",
                "ghi chu ve nhac",
                "drop a thought",
                "share a song",
            ]):
                continue
            if any(self._is_unread_filter_label(label) for label in labels):
                continue
            unread_label = next((label for label in labels if "chua doc" in self._normalize_ui_text(label)), "")
            if not unread_label:
                continue
            sender = re.sub(r"(?i)[\s.]*chưa đọc\s*$", "", unread_label).strip(" .")
            sender = re.sub(r"(?i)[\s.]*unread\s*$", "", sender).strip(" .")
            sender = self._clean_participant_name(sender)
            if not sender or self._is_blocked_visible_sender(sender):
                continue
            norm_sender = self._normalize_ui_text(sender)
            if norm_sender in seen:
                continue
            seen.add(norm_sender)
            bounds = f"[0,{group['y1']}][{screen_w},{group['y2']}]"
            rows.append({
                "sender": sender,
                "label": " | ".join(labels),
                "bounds": bounds,
                "row_key": norm_sender,
                "y1": group["y1"],
            })

        rows.sort(key=lambda item: item["y1"])
        return rows

    def collect_unread_unreplied_conversations(self) -> None:
        """Open unread conversations and collect real messages from detail screens."""
        if self.has_pending_crm_send_queue():
            print("[SEND] CRM send queue is pending; skip unread Messenger crawl.")
            return
        if getattr(self, "_unread_scan_in_progress", False):
            print("[COLLECT] Unread scan is already running. Skip nested scan.")
            return
        driver = self.connect_ui_driver()
        if driver is None:
            return

        active_account = self.require_active_account_id("unread conversation scan")
        # Ensure we're on the main Chats screen. Do not blindly press Back here,
        # because when we come from Message Requests it can leave Messenger/Facebook flow.
        self._unread_scan_in_progress = True
        try:
            if not self.navigate_to_messenger_chats_home(driver, timeout=10):
                if not self.ensure_messenger_ready():
                    return
            self.click_messenger_unread_tab(driver)

            processed_senders: set[str] = set()

            initial_count = len(self._unread_conversation_row_candidates(driver))
            print(f"[COLLECT] Found {initial_count} visible unread/unreplied conversations.")

            max_unread = int(os.getenv("FB_1_1_MAX_UNREAD_CONVERSATIONS_PER_SCAN", "30"))
            empty_rounds = 0
            processed_count = 0

            while processed_count < max_unread:
                if self.has_pending_crm_send_queue():
                    print("[SEND] CRM send queue appeared during unread scan; stop crawling so send can switch account.")
                    break

                unread_conversations = [
                    item for item in self._unread_conversation_row_candidates(driver)
                    if item.get("row_key") not in processed_senders
                ]
                if not unread_conversations:
                    if empty_rounds >= 2:
                        break
                    empty_rounds += 1
                    print("[COLLECT] No unprocessed unread row visible. Scrolling unread list for more.")
                    self.run_adb(["shell", "input", "swipe", "360", "1120", "360", "650", "300"])
                    time.sleep(1.4)
                    continue

                empty_rounds = 0
                conv = unread_conversations[0]
                sender = conv["sender"]
                processed_senders.add(conv.get("row_key") or self._normalize_ui_text(sender))

                if not self._tap_bounds_center(conv["bounds"], reason="open unread conversation row"):
                    continue
                time.sleep(2)

                actual_sender = self._conversation_header_sender_from_dump(driver) or sender
                actual_sender = self._clean_participant_name(actual_sender) or sender
                if self._normalize_ui_text(actual_sender) != self._normalize_ui_text(sender):
                    print(f"[COLLECT] Correcting unread conversation sender from '{sender}' to '{actual_sender}'.")
                    sender = actual_sender

                chat_id = self._safe_chat_id_from_sender(sender)
                account_chat_id = f"{active_account}:{chat_id}"
                history = self._load_chat_history()
                conversation = history.setdefault(account_chat_id, {
                    "sender": sender,
                    "participant_name": sender,
                    "participant_url": chat_id,
                    "conversation_url": chat_id,
                    "last_5_messages": [],
                    "messages": [],
                    "source": "unread_conversation",
                    "user_id_chat": active_account,
                    "account": active_account,
                })
                conversation["sender"] = sender
                conversation["participant_name"] = sender
                conversation["participant_url"] = chat_id
                conversation["conversation_url"] = chat_id
                conversation["source"] = "unread_conversation"
                conversation["user_id_chat"] = conversation.get("user_id_chat") or active_account
                conversation["account"] = conversation.get("account") or active_account
                conversation.setdefault("last_5_messages", [])
                conversation.setdefault("messages", conversation["last_5_messages"])

                inserted = self._crawl_conversation_messages(driver, account_chat_id, conversation)
                if inserted:
                    conversation["messages"] = conversation.get("last_5_messages", [])
                    conversation["last_message"] = conversation["messages"][-1].get("content", "")
                    conversation["last_message_time"] = conversation["messages"][-1].get("time", "")
                    self.chat_history_db = history
                    self.scan_local_chat_history_for_new_messages()
                    self.save_data_to_file()
                    print(f"[COLLECT] Saved {inserted} unread message(s) from {sender}.")
                processed_count += 1

                try:
                    driver.press("back")
                    time.sleep(1.5)
                except Exception:
                    self.run_adb(["shell", "input", "keyevent", "4"])
                    time.sleep(1.5)
                self.click_messenger_unread_tab(driver)

            print(f"[COLLECT] Finished unread scan. processed={processed_count}.")
        finally:
            self._unread_scan_in_progress = False

    def _collect_unread_messages_from_conversation(self, driver, conversation: dict) -> list:
        """Collect unread messages from an opened conversation."""
        try:
            xml = driver.dump_hierarchy()
            root = ET.fromstring(xml)
        except Exception:
            return []

        messages = []
        seen_labels = set()

        for node in root.iter("node"):
            label = self._node_label(node)
            if not label or label in seen_labels:
                continue
            seen_labels.add(label)

            parsed = self._parse_visible_message_label(label)
            if not parsed:
                continue

            # Filter out system messages and non-chat content
            if self._is_system_message_row(parsed):
                continue

            normalized_sender = self._normalize_ui_text(parsed["sender"])
            normalized_content = self._normalize_ui_text(parsed["content"])

            # Skip system-like messages
            if normalized_content.startswith("tab ") or re.fullmatch(r"\d+/\d+", normalized_content):
                continue
            if normalized_sender in {
                "trang chủ", "trang chu", "nhóm", "nhom", "cộng đồng", "cong dong",
                "kho lưu trữ", "kho luu tru", "lời mời kết bạn", "loi moi ket ban",
                "facebook reels", "tắt", "tat",
            }:
                continue

            messages.append(parsed)

        return messages

    def _save_filtered_messages_to_db(self, messages_by_account: dict) -> None:
        """Save filtered messages to database, excluding non-chat messages."""
        for account, messages in (messages_by_account or {}).items():
            if not messages:
                continue
            key = f"{account}_unread"
            self.chat_history_db[key] = {
                "sender": messages[0].get("sender") if isinstance(messages[0], dict) else "Unknown",
                "messages": messages,
                "last_5_messages": messages,
                "source": "unread_conversation",
                "user_id_chat": account,
                "account": account,
            }

    def scan_local_chat_history_for_new_messages(self) -> int:
        if self.has_pending_crm_send_queue():
            print("[SEND] CRM send queue is pending; skip local chat history scan.")
            return 0
        history = self.chat_history_db if isinstance(self.chat_history_db, dict) else {}
        for conv in history.values():
            if isinstance(conv, dict) and "last_5_messages" in conv and "messages" not in conv:
                conv["messages"] = conv["last_5_messages"]

        active_account = self.require_active_account_id("local chat history scan")
        sent_count = 0
        for chat_id, messages_data in history.items():
            if not isinstance(messages_data, dict):
                continue
            conversation_account = _safe_str(
                messages_data.get("user_id_chat")
                or messages_data.get("account")
                or messages_data.get("facebookAccountId")
            )
            if not conversation_account:
                print(f"[DB] Skip orphan local chat without account: {chat_id}")
                continue
            if conversation_account and active_account and conversation_account != active_account:
                continue
            new_messages = []
            for msg in messages_data.get("messages", []):
                if not isinstance(msg, dict):
                    continue
                message_account = _safe_str(msg.get("user_id_chat") or msg.get("account") or conversation_account)
                if message_account and active_account and message_account != active_account:
                    continue
                key = self._message_key(chat_id, msg)
                if key in self.logged_message_keys:
                    continue
                self.logged_message_keys.add(key)
                sender = _safe_str(msg.get("sender"))
                if sender in {"Tôi", "Toi", "Me"}:
                    continue
                if self._is_system_message_row({"sender": sender, "content": _safe_str(msg.get("content"))}):
                    continue
                new_messages.append(msg)

            if new_messages:
                self.send_messages_to_crm(chat_id, {
                    **messages_data,
                    "messages": new_messages,
                    "all_messages": messages_data.get("messages", []),
                })
                sent_count += len(new_messages)

        self.chat_history_db = history
        self.logged_chat_ids = set(history)
        return sent_count

    def send_queued_message_by_participant_name(self, participant_name: str, content: str, participant_url: str = "") -> bool:
        self._last_send_failure_reason = ""
        driver = self.connect_ui_driver()
        if driver is None:
            self._last_send_failure_reason = "Cannot connect UI driver"
            return False

        previous_skip = getattr(self, "_skip_open_messenger_initial_request_crawl", False)
        self._skip_open_messenger_initial_request_crawl = True
        try:
            current_package = self.get_current_package_name(driver)
            if current_package == MESSENGER_PKG or self.is_current_package(MESSENGER_PKG):
                print("[SEND] Messenger is already foreground for queue send; skip Facebook entrypoint.")
                self._handle_pin_screen_and_back()
                if not self.wait_until_messenger_ready_for_navigation(driver, timeout=8):
                    self._last_send_failure_reason = "Messenger is foreground but not ready for queue send"
                    return False
            elif not self.open_messenger():
                self._last_send_failure_reason = "Cannot open Messenger"
                return False
        finally:
            self._skip_open_messenger_initial_request_crawl = previous_skip

        if not self.prepare_messenger_for_queue_send(driver, participant_name):
            self._last_send_failure_reason = "Cannot prepare Messenger screen for queue send"
            return False

        if self.is_messenger_contact_info_screen(driver):
            print("[SEND] Contact info screen is open before queue send. Returning to chat thread.")
            self._safe_messenger_back(driver, reason="return from contact info before queue send")
            time.sleep(1.5)

        if not self.is_messenger_thread_screen_for_participant(driver, participant_name) and not self.search_and_open_chat_by_name(driver, participant_name):
            allow_request_fallback = os.getenv("FB_1_1_QUEUE_SEND_ALLOW_MESSAGE_REQUEST_FALLBACK", "0").lower() in {"1", "true", "yes", "on"}
            if not allow_request_fallback:
                print("[SEND] Queue send will not use URL/id fallback or Message Requests fallback by default.")
                self._last_send_failure_reason = f"Cannot open Messenger chat by participant_name='{participant_name}'"
                return False
            if not self.search_and_open_message_request_by_name(driver, participant_name):
                self._last_send_failure_reason = f"Cannot open Messenger chat or Message Request by participant_name='{participant_name}'"
                return False

        if self.is_messenger_contact_info_screen(driver):
            print("[SEND] Search opened contact info instead of chat. Returning to thread.")
            self._safe_messenger_back(driver, reason="return from contact info after search")
            time.sleep(1.5)

        if not self._wait_for_messenger_thread_composer(driver, participant_name, timeout=8):
            print("[SEND] Messenger thread composer is not available; refusing to send.")
            if not getattr(self, "_last_send_failure_reason", ""):
                self._last_send_failure_reason = "Messenger thread composer is not available"
            return False

        setattr(self, "_last_active_thread_participant_name", participant_name)

        if not self.type_and_send_message(driver, content):
            if not getattr(self, "_last_send_failure_reason", ""):
                self._last_send_failure_reason = "type_and_send_message returned false"
            return False

        verified = self.verify_message_sent(driver, content)
        if not verified and not getattr(self, "_last_send_failure_reason", ""):
            self._last_send_failure_reason = "verify_message_sent returned false"
        return verified

    def has_pending_crm_send_queue(self) -> bool:
        accounts = {
            _safe_str(item)
            for item in (self.device_accounts or [])
            if _safe_str(item) and _safe_str(item) != "default"
        }
        current = _safe_str(self.current_account_id() or self.user_id_chat)
        if current and current != "default":
            accounts.add(current)
        if not accounts:
            return False
        try:
            response = requests.get(
                f"{self.crm_sender_url}/api/commands/pending",
                params={"accounts": ",".join(sorted(accounts))},
                timeout=3,
            )
            response.raise_for_status()
            return bool((response.json() or {}).get("pending"))
        except Exception as exc:
            print(f"[SEND] Cannot check Mongo Commands queue: {exc}")
            return False

    def run_idle_message_crawls_if_no_send_queue(self) -> None:
        if self.has_pending_crm_send_queue():
            print("[SEND] CRM send queue is pending; skip Message Requests/menu crawl until send finishes.")
            return
        self.crawl_message_requests_once()
        self.crawl_visible_messenger_messages()
        self.scan_local_chat_history_for_new_messages()

    def monitor_new_messages(self, interval: int = 30) -> None:
        print("[MOBILE] Listening for CRM 1-1 commands over USB ADB...")
        if not self.connect_to_crm():
            print("[MONITOR] CRM sender API is offline. The bot will keep retrying.")

        print("[MOBILE] Bot is online. Press Ctrl+C to stop.")
        try:
            if self.active_device:
                self.ensure_messenger_ready()
                self.run_idle_message_crawls_if_no_send_queue()

            while True:
                time.sleep(interval)
                if not self.active_device:
                    self.connect_to_device()
                if not self.sio.connected:
                    self.connect_to_crm()
                elif self.active_device:
                    self.register_bot_to_crm()
                    self.run_idle_message_crawls_if_no_send_queue()
        except KeyboardInterrupt:
            print("\n[MONITOR] Stopped.")

    def close(self) -> None:
        print("[CLOSE] Closing mobile 1-1 messenger...")
        if self.sio.connected:
            self.sio.disconnect()
