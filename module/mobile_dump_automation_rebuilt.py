# -*- coding: utf-8 -*-
"""
mobile_dump_automation_rebuilt.py

Dump-first UI automation layer for Android Facebook/Messenger.

Recommended location in your project:
    tool-fb-mobile/module/mobile_dump_automation_rebuilt.py

Expected project layout:
    tool-fb-mobile/
    ├── module/
    │   ├── mobile_1_1_messenger.py
    │   └── mobile_dump_automation_rebuilt.py
    ├── platform-tools/
    │   └── adb.exe
    ├── window_dump.xml
    └── screen.png

Main idea:
    - Always delete old /sdcard/window_dump.xml and local window_dump.xml.
    - Dump a fresh XML for every UI decision.
    - Parse text/content-desc/resource-id/bounds.
    - Score candidates instead of using hard-coded coordinates.
    - Click the center of the best node/ancestor bounds.
    - Use ratio-coordinate fallback only when explicitly enabled.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import types
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

try:
    from module.android_automation_common import (
        bounds_center,
        collapse_statusbar_adb,
        is_package_installed_adb,
        normalize_text,
        normalize_variants,
        parse_bounds,
        resolve_launch_activity_adb,
        safe_str,
        shell_response_text,
    )
except Exception:
    from android_automation_common import (
        bounds_center,
        collapse_statusbar_adb,
        is_package_installed_adb,
        normalize_text,
        normalize_variants,
        parse_bounds,
        resolve_launch_activity_adb,
        safe_str,
        shell_response_text,
    )


CURRENT_FILE = Path(__file__).resolve()
MODULE_DIR = CURRENT_FILE.parent
PROJECT_ROOT = MODULE_DIR.parent
PLATFORM_TOOLS_DIR = PROJECT_ROOT / "platform-tools"

MESSENGER_PKG = "com.facebook.orca"
FACEBOOK_PKG = "com.facebook.katana"
PLAY_STORE_PKG = "com.android.vending"

DEFAULT_REMOTE_DUMP_PATH = os.getenv("FB_1_1_WINDOW_DUMP_DEVICE_PATH", "/sdcard/window_dump.xml")
DEFAULT_LOCAL_DUMP_PATH = Path(os.getenv("FB_1_1_WINDOW_DUMP_FILE", str(PROJECT_ROOT / "window_dump.xml")))
DEFAULT_SCREENSHOT_PATH = Path(os.getenv("FB_1_1_SCREENSHOT_FILE", str(PROJECT_ROOT / "screen.png")))

ALLOW_COORDINATE_FALLBACK = os.getenv("FB_1_1_ALLOW_COORDINATE_FALLBACK", "0").lower() in {"1", "true", "yes", "on"}
VERBOSE_VISIBLE_LABEL_LIMIT = int(os.getenv("FB_1_1_DUMP_LABEL_LIMIT", "80"))


# safe_str imported from android_automation_common


# normalize_text imported from android_automation_common


# normalize_variants imported from android_automation_common


# parse_bounds imported from android_automation_common


# bounds_center imported from android_automation_common


def bounds_area(bounds: tuple[int, int, int, int]) -> int:
    x1, y1, x2, y2 = bounds
    return max(0, x2 - x1) * max(0, y2 - y1)


def union_bounds(bounds_list: Iterable[tuple[int, int, int, int]]) -> Optional[tuple[int, int, int, int]]:
    items = list(bounds_list)
    if not items:
        return None
    return (
        min(item[0] for item in items),
        min(item[1] for item in items),
        max(item[2] for item in items),
        max(item[3] for item in items),
    )


def bounds_contains(outer: tuple[int, int, int, int], inner: tuple[int, int, int, int], padding: int = 2) -> bool:
    return (
        outer[0] <= inner[0] + padding
        and outer[1] <= inner[1] + padding
        and outer[2] >= inner[2] - padding
        and outer[3] >= inner[3] - padding
    )


def person_name_match_score(candidate_text: str, allowed_names: list[str]) -> tuple[int, Optional[str]]:
    cand_norm = normalize_text(candidate_text, remove_accents=True)
    if not cand_norm:
        return 0, None

    cand_tokens = set(cand_norm.split())
    cand_list = cand_norm.split()
    best_score = 0
    best_name: Optional[str] = None

    for base_name in allowed_names or []:
        base_norm = normalize_text(base_name, remove_accents=True)
        if not base_norm:
            continue
        if cand_norm == base_norm:
            return 1000, base_name

        base_tokens = set(base_norm.split())
        base_list = base_norm.split()
        if (
            len(cand_list) >= 2
            and len(cand_list) == len(base_list)
            and cand_tokens == base_tokens
        ):
            if cand_list == list(reversed(base_list)):
                score = 930
            else:
                score = 820
            if score > best_score:
                best_score = score
                best_name = base_name
            continue

        inter = cand_tokens & base_tokens
        if not inter:
            continue

        overlap_ratio = len(inter) / max(len(base_tokens), 1)
        score = 0
        if overlap_ratio >= 1.0:
            score += 700
        elif overlap_ratio >= 0.8:
            score += 450
        elif overlap_ratio >= 0.6:
            score += 250
        elif overlap_ratio >= 0.5:
            score += 120

        if cand_list and base_list:
            if cand_list[0] == base_list[0]:
                score += 30
            if cand_list[-1] == base_list[-1]:
                score += 30

        if score > best_score:
            best_score = score
            best_name = base_name

    return best_score, best_name


PROFILE_NAME_BLOCK_EXACT = {
    "anh",
    "photos",
    "posts",
    "friends",
    "followers",
    "about",
    "edit",
    "video",
    "story",
    "reels",
    "live",
    "avatar",
    "create",
    "add",
    "settings",
    "notification",
    "chrome",
    "vpn",
}

PROFILE_NAME_BLOCK_CONTAINS = [
    "dang nghi ve",
    "tam trang hien tai",
    "chia se suy nghi",
    "chia se ghi chu",
    "chia se bai hat",
    "thinking about",
    "what's on your mind",
    "bai viet",
    "ban be",
    "theo doi",
    "follow",
    "tin nhan",
    "message",
    "gioi thieu",
    "xem them",
    "see more",
    "chinh sua",
    "cai dat",
    "hoat dong",
    "activity",
    "nguoi ban",
    "mutual friends",
    "ban chung",
    "common friends",
    "them vao tin",
    "them story",
    "add story",
    "tao ghi chu",
    "cover photo",
    "profile picture",
    "anh bia",
    "anh dai dien",
    "edit profile",
    "chinh sua trang ca nhan",
    "loi chao",
    "greeting",
    "noi bat",
    "featured",
    "thong tin ca nhan",
    "xem tat ca",
    "tat ca",
    "truong",
    "dai hoc",
    "hoc vien",
    "university",
    "college",
    "school",
    "ha noi",
    "tp.",
    "thanh pho",
    "que quan",
    "song tai",
    "den tu",
]


def is_profile_note_prompt_label(label: str) -> bool:
    norm = normalize_text(label, remove_accents=True)
    if not norm:
        return False
    if "tao ghi chu" in norm:
        return True
    return norm.startswith("chia se ") and safe_str(label).endswith("...")


def is_profile_non_name_label(label: str) -> bool:
    norm = normalize_text(label, remove_accents=True)
    if not norm:
        return True
    if is_profile_note_prompt_label(label):
        return True
    if norm in PROFILE_NAME_BLOCK_EXACT:
        return True
    if any(term in norm for term in PROFILE_NAME_BLOCK_CONTAINS):
        return True
    if "·" in label or "Â·" in label or "|" in label or "," in label:
        return True
    if re.fullmatch(r"[\d,.]+[km]?", norm):
        return True
    return False


def is_truthy_attr(value: Any) -> bool:
    return safe_str(value).lower() == "true"


class UiNode:
    def __init__(
        self,
        index: int,
        parent_index: Optional[int],
        text: str,
        desc: str,
        resource_id: str,
        class_name: str,
        package: str,
        clickable: bool,
        enabled: bool,
        selected: bool,
        focused: bool,
        scrollable: bool,
        bounds_text: str,
        bounds: tuple[int, int, int, int],
        depth: int = 0,
    ) -> None:
        self.index = index
        self.parent_index = parent_index
        self.text = text
        self.desc = desc
        self.resource_id = resource_id
        self.class_name = class_name
        self.package = package
        self.clickable = clickable
        self.enabled = enabled
        self.selected = selected
        self.focused = focused
        self.scrollable = scrollable
        self.bounds_text = bounds_text
        self.bounds = bounds
        self.depth = depth

    @property
    def label(self) -> str:
        return self.desc or self.text or self.resource_id

    @property
    def all_text(self) -> str:
        parts = [self.text, self.desc, self.resource_id]
        return " | ".join(item for item in parts if item)

    @property
    def center(self) -> tuple[int, int]:
        return bounds_center(self.bounds)

    @property
    def area(self) -> int:
        return bounds_area(self.bounds)

    @property
    def height(self) -> int:
        return self.bounds[3] - self.bounds[1]

    @property
    def width(self) -> int:
        return self.bounds[2] - self.bounds[0]

    def to_result(self, *, score: int = 0, source: str = "window_dump") -> dict[str, Any]:
        return {
            "index": self.index,
            "label": self.label,
            "text": self.text,
            "content_desc": self.desc,
            "resource_id": self.resource_id,
            "bounds": self.bounds_text,
            "clickable": "true" if self.clickable else "false",
            "enabled": "true" if self.enabled else "false",
            "class": self.class_name,
            "package": self.package,
            "score": score,
            "source": source,
        }


class SelectorSpec:
    def __init__(
        self,
        name: str,
        texts: Optional[list[str]] = None,
        contains: Optional[list[str]] = None,
        resource_ids: Optional[list[str]] = None,
        class_contains: Optional[list[str]] = None,
        priority_terms: Optional[list[str]] = None,
        weak_terms: Optional[list[str]] = None,
        block_terms: Optional[list[str]] = None,
        preferred_zones: Optional[list[str]] = None,
        require_clickable_or_parent: bool = True,
        allow_contains: bool = True,
        min_y_ratio: Optional[float] = None,
        max_y_ratio: Optional[float] = None,
        min_x_ratio: Optional[float] = None,
        max_x_ratio: Optional[float] = None,
        prefer_exact: bool = True,
    ) -> None:
        self.name = name
        self.texts = list(texts or [])
        self.contains = list(contains or [])
        self.resource_ids = list(resource_ids or [])
        self.class_contains = list(class_contains or [])
        self.priority_terms = list(priority_terms or [])
        self.weak_terms = list(weak_terms or [])
        self.block_terms = list(block_terms or [])
        self.preferred_zones = list(preferred_zones or [])
        self.require_clickable_or_parent = require_clickable_or_parent
        self.allow_contains = allow_contains
        self.min_y_ratio = min_y_ratio
        self.max_y_ratio = max_y_ratio
        self.min_x_ratio = min_x_ratio
        self.max_x_ratio = max_x_ratio
        self.prefer_exact = prefer_exact

class DumpUiDevice:
    def __init__(
        self,
        adb_path: str | os.PathLike[str] | None = None,
        device_id: str | None = None,
        local_dump_path: str | os.PathLike[str] | None = None,
        remote_dump_path: str = DEFAULT_REMOTE_DUMP_PATH,
        screenshot_path: str | os.PathLike[str] | None = None,
    ) -> None:
        self.adb_path = str(adb_path or self.resolve_adb_path())
        self.device_id = safe_str(device_id) or None
        self.local_dump_path = Path(local_dump_path or DEFAULT_LOCAL_DUMP_PATH)
        self.remote_dump_path = remote_dump_path
        self.screenshot_path = Path(screenshot_path or DEFAULT_SCREENSHOT_PATH)
        self._screen_size_cache: Optional[tuple[int, int]] = None
        self._last_nodes: list[UiNode] = []
        self._last_node_by_index: dict[int, UiNode] = {}
        # Optional fallback used only when `adb shell uiautomator dump` cannot
        # create a remote XML file on a specific ROM/API level. The adapter
        # can provide `driver.dump_hierarchy()` here, and we still save the
        # XML into PROJECT_ROOT/window_dump.xml so the debug workflow remains
        # the same.
        self.fallback_hierarchy_provider: Optional[Callable[[], str]] = None

    @staticmethod
    def resolve_adb_path() -> str:
        candidates = [
            os.getenv("ADB_PATH"),
            str(PLATFORM_TOOLS_DIR / "adb.exe"),
            str(PLATFORM_TOOLS_DIR / "adb"),
            shutil.which("adb"),
        ]
        for candidate in candidates:
            if candidate and Path(candidate).exists():
                return str(candidate)
        return "adb"

    def set_device(self, device_id: str | None) -> None:
        value = safe_str(device_id)
        if value:
            self.device_id = value

    def adb_prefix(self, device_id: str | None = None) -> list[str]:
        cmd = [self.adb_path]
        target = safe_str(device_id) or self.device_id
        if target:
            cmd += ["-s", target]
        return cmd

    def run_adb(
        self,
        command: str | list[str] | tuple[str, ...],
        *,
        timeout: int = 20,
        device_id: str | None = None,
        binary_stdout: bool = False,
        log_errors: bool = True,
    ) -> str | bytes:
        prefix = self.adb_prefix(device_id)
        if isinstance(command, (list, tuple)):
            full_cmd = prefix + [str(item) for item in command]
        else:
            full_cmd = prefix + shlex.split(command)
        try:
            result = subprocess.run(
                full_cmd,
                capture_output=True,
                text=not binary_stdout,
                timeout=timeout,
                encoding=None if binary_stdout else "utf-8",
                errors=None if binary_stdout else "replace",
            )
            if result.returncode != 0 and log_errors:
                err = result.stderr if not binary_stdout else result.stderr.decode("utf-8", "replace")
                err = safe_str(err)
                if err:
                    print(f"[ADB] Command failed ({result.returncode}): {err}")
            return result.stdout if binary_stdout else safe_str(result.stdout)
        except FileNotFoundError:
            print(f"[ADB] adb not found at '{self.adb_path}'. Set ADB_PATH or keep platform-tools/adb.exe in project root.")
            return b"" if binary_stdout else ""
        except subprocess.TimeoutExpired:
            print(f"[ADB] Timeout after {timeout}s: {' '.join(full_cmd)}")
            return b"" if binary_stdout else ""
        except Exception as exc:
            print(f"[ADB] Execution error: {exc}")
            return b"" if binary_stdout else ""

    def list_devices(self) -> list[str]:
        output = self.run_adb(["devices"])
        devices: list[str] = []
        for line in str(output).splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                devices.append(parts[0])
        return devices

    def ensure_device(self) -> bool:
        if self.device_id:
            return True
        devices = self.list_devices()
        if not devices:
            print("[ADB] No Android device found. Check USB debugging and run: platform-tools/adb devices")
            return False
        self.device_id = devices[0]
        if len(devices) > 1:
            print(f"[ADB] Multiple devices found. Using first device: {self.device_id}")
        return True

    def get_screen_size(self, *, refresh: bool = False) -> tuple[int, int]:
        if self._screen_size_cache and not refresh:
            return self._screen_size_cache
        output = str(self.run_adb(["shell", "wm", "size"]))
        match = re.search(r"(?:Physical|Override) size:\s*(\d+)x(\d+)", output)
        if match:
            self._screen_size_cache = (int(match.group(1)), int(match.group(2)))
        else:
            self._screen_size_cache = (1080, 2400)
        return self._screen_size_cache

    def is_package_installed(self, package_name: str) -> bool:
        return is_package_installed_adb(self.run_adb, package_name)

    def resolve_launch_activity(self, package_name: str) -> str:
        return resolve_launch_activity_adb(self.run_adb, package_name)

    def current_package(self) -> str:
        timeout = int(os.getenv("FB_1_1_CURRENT_PACKAGE_TIMEOUT_SECONDS", "2"))
        output = str(self.run_adb(["shell", "dumpsys", "window", "windows"], timeout=timeout))
        patterns = [
            r"mCurrentFocus=Window\{[^ ]+ [^ ]+ ([^/}\s]+)/",
            r"mFocusedApp=.*? ([^/\s]+)/",
            r"mFocusedWindow=Window\{[^ ]+ [^ ]+ ([^/}\s]+)/",
        ]
        for pattern in patterns:
            match = re.search(pattern, output)
            if match:
                return safe_str(match.group(1))
        output2 = str(self.run_adb(["shell", "dumpsys", "activity", "activities"], timeout=timeout))
        match = re.search(r"topResumedActivity=.*? ([^/\s]+)/", output2)
        return safe_str(match.group(1)) if match else ""

    def is_current_package(self, package_name: str) -> bool:
        current = self.current_package()
        if current:
            return current == package_name

        # Last-resort fallback: only trust the foreground UI dump root package.
        # A raw substring search in dumpsys is unsafe because Android often
        # includes background/back-stack packages there; that made Facebook
        # foreground look like Messenger was still open.
        try:
            nodes = self.read_nodes(reason=f"foreground package fallback: {package_name}")
            packages = [safe_str(getattr(node, "package", "")) for node in nodes[:20]]
            packages = [pkg for pkg in packages if pkg]
            if packages:
                return packages[0] == package_name
        except Exception:
            pass
        return False

    def start_app(self, package_name: str, *, wait_seconds: float = 3.0) -> bool:
        if not self.is_package_installed(package_name):
            print(f"[APP] Package not installed: {package_name}")
            return False
        activity = self.resolve_launch_activity(package_name)
        if activity:
            self.run_adb(["shell", "am", "start", "-n", activity], timeout=15)
        else:
            self.run_adb([
                "shell", "monkey", "-p", package_name,
                "-c", "android.intent.category.LAUNCHER", "1",
            ], timeout=15)
        time.sleep(wait_seconds)
        return self.is_current_package(package_name)

    def collapse_statusbar_if_open(self) -> bool:
        return collapse_statusbar_adb(self.run_adb)

    def press_back(self, *, reason: str = "") -> bool:
        current = self.current_package()
        print(f"[ADB] Back pressed. current={current}; reason={reason}")
        self.run_adb(["shell", "input", "keyevent", "4"], timeout=10)
        time.sleep(1.2)
        return True

    def tap(self, x: int, y: int, *, reason: str = "") -> bool:
        width, height = self.get_screen_size()
        x = max(0, min(width - 1, int(x)))
        y = max(0, min(height - 1, int(y)))
        print(f"[ADB] tap ({x}, {y}) reason={reason}")
        self.run_adb(["shell", "input", "tap", str(x), str(y)], timeout=10)
        time.sleep(1.4)
        return True

    def tap_bounds(self, bounds: tuple[int, int, int, int] | str, *, reason: str = "") -> bool:
        parsed = parse_bounds(bounds) if isinstance(bounds, str) else bounds
        if not parsed:
            print(f"[ADB] Cannot tap invalid bounds: {bounds}")
            return False
        x, y = bounds_center(parsed)
        return self.tap(x, y, reason=f"{reason}; bounds={parsed}")

    def tap_ratio(self, x_ratio: float, y_ratio: float, *, reason: str = "") -> bool:
        if not ALLOW_COORDINATE_FALLBACK:
            print(f"[ADB] Ratio fallback disabled. Skip tap_ratio({x_ratio}, {y_ratio}) for {reason}")
            return False
        width, height = self.get_screen_size()
        return self.tap(int(width * x_ratio), int(height * y_ratio), reason=f"ratio fallback: {reason}")

    def force_tap_ratio(self, x_ratio: float, y_ratio: float, *, reason: str = "") -> bool:
        """Tap by screen ratio even when general coordinate fallback is disabled.

        This is reserved for the Facebook Messenger entry point because recent
        Facebook builds often render the top-right Messenger icon without a
        stable text/content-desc in the accessibility dump.
        """
        width, height = self.get_screen_size(refresh=True)
        return self.tap(int(width * x_ratio), int(height * y_ratio), reason=f"forced ratio fallback: {reason}")

    def swipe_ratio(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        duration_ms: int = 280,
        *,
        reason: str = "",
    ) -> bool:
        width, height = self.get_screen_size()
        sx = int(width * x1)
        sy = int(height * y1)
        ex = int(width * x2)
        ey = int(height * y2)
        print(f"[ADB] swipe ({sx},{sy}) -> ({ex},{ey}) duration={duration_ms} reason={reason}")
        self.run_adb(["shell", "input", "swipe", str(sx), str(sy), str(ex), str(ey), str(duration_ms)], timeout=15)
        time.sleep(1.0)
        return True

    def screenshot(self, path: str | os.PathLike[str] | None = None) -> Path:
        if not self.ensure_device():
            raise RuntimeError("No Android device connected")
        output_path = Path(path or self.screenshot_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        data = self.run_adb(["exec-out", "screencap", "-p"], timeout=20, binary_stdout=True)
        if isinstance(data, str):
            data = data.encode("latin1", "ignore")
        output_path.write_bytes(data)
        print(f"[SCREENSHOT] Saved: {output_path}")
        return output_path

    def _remote_dump_candidates(self) -> list[str]:
        """Return remote paths to try for uiautomator dump.

        `/sdcard/window_dump.xml` is common, but several Android builds fail to
        create that file from `uiautomator dump`. `/data/local/tmp` is usually
        more reliable because it is writable through shell without depending on
        shared-storage behavior.
        """
        candidates = [
            self.remote_dump_path,
            "/sdcard/window_dump.xml",
            "/data/local/tmp/window_dump.xml",
            "/storage/emulated/0/window_dump.xml",
        ]
        output: list[str] = []
        seen: set[str] = set()
        for item in candidates:
            value = safe_str(item)
            if value and value not in seen:
                seen.add(value)
                output.append(value)
        return output

    def _remote_file_exists(self, remote_path: str) -> bool:
        path = safe_str(remote_path)
        if not path:
            return False

        # Không dùng: adb shell sh -c "if [ -f ... ]; then ...; fi"
        # Vì trên một số máy Windows/ADB, chuỗi sau -c bị tách sai,
        # dẫn đến lỗi: /system/bin/sh: syntax error: unexpected 'then'
        #
        # Dùng ls -l đơn giản hơn, không cần shell if/then.
        output = str(self.run_adb(["shell", "ls", "-l", path], timeout=8, log_errors=False))
        if not output:
            return False

        lower_output = output.lower()
        if (
            "no such file" in lower_output
            or "not found" in lower_output
            or "permission denied" in lower_output
        ):
            return False

        file_name = path.rsplit("/", 1)[-1]
        return path in output or file_name in output

    def _pull_remote_dump(self, remote_path: str) -> bool:
        """Pull the XML exactly like the manual workflow.

        Do not depend on `ls -l` before pulling. On some Android/ADB builds,
        `uiautomator dump /sdcard/window_dump.xml` succeeds but the immediate
        `ls` check can still return stale/empty output. The manual workflow
        works because it dumps first, then pulls directly.
        """
        try:
            if self.local_dump_path.exists():
                self.local_dump_path.unlink()
        except Exception:
            pass

        before_exists = self.local_dump_path.exists()
        output = str(self.run_adb(["pull", remote_path, str(self.local_dump_path)], timeout=25, log_errors=False))
        ok = self.local_dump_path.exists() and self.local_dump_path.stat().st_size > 0
        if ok:
            return True

        # Secondary quiet existence check only for diagnostics/fallback logic.
        if self._remote_file_exists(remote_path):
            self.run_adb(["pull", remote_path, str(self.local_dump_path)], timeout=25, log_errors=True)
            ok = self.local_dump_path.exists() and self.local_dump_path.stat().st_size > 0
            if ok:
                return True

        if output and not before_exists:
            print(f"[DUMP] Pull did not create local XML from {remote_path}: {output}")
        return False

    def _read_remote_dump_via_exec_out(self, remote_path: str) -> bool:
        """Copy a dumped XML via stdout when `adb pull` is unreliable."""
        raw = self.run_adb(["exec-out", "cat", remote_path], timeout=20, binary_stdout=True, log_errors=False)
        if isinstance(raw, bytes):
            xml = raw.decode("utf-8", "replace").strip()
        else:
            xml = safe_str(raw)
        if not xml or "<node" not in xml:
            return False
        try:
            ET.fromstring(xml)
        except Exception as exc:
            print(f"[DUMP] exec-out XML from {remote_path} is invalid: {exc}")
            return False
        self.local_dump_path.write_text(xml, encoding="utf-8")
        print(f"[DUMP] Saved fresh XML via exec-out cat: remote={remote_path} local={self.local_dump_path}")
        return True

    def _try_adb_uiautomator_dump(self, remote_path: str, *, compressed: bool, reason: str) -> bool:
        self.run_adb(["shell", "rm", "-f", remote_path], timeout=8)
        dump_cmd = ["shell", "uiautomator", "dump"]
        if compressed:
            dump_cmd.append("--compressed")
        dump_cmd.append(remote_path)

        output = str(self.run_adb(dump_cmd, timeout=35))
        if output:
            mode = "compressed" if compressed else "normal"
            print(f"[DUMP] {reason}: uiautomator {mode} -> {remote_path}: {output}")

        if self._pull_remote_dump(remote_path):
            print(f"[DUMP] Saved fresh XML: remote={remote_path} local={self.local_dump_path}")
            return True
        if self._read_remote_dump_via_exec_out(remote_path):
            return True
        return False

    def _try_default_adb_uiautomator_dump(self, *, compressed: bool, reason: str) -> bool:
        default_remote = "/sdcard/window_dump.xml"
        self.run_adb(["shell", "rm", "-f", default_remote], timeout=8)
        dump_cmd = ["shell", "uiautomator", "dump"]
        if compressed:
            dump_cmd.append("--compressed")

        output = str(self.run_adb(dump_cmd, timeout=35))
        if output:
            mode = "compressed" if compressed else "normal"
            print(f"[DUMP] {reason}: uiautomator default {mode}: {output}")
        if self._pull_remote_dump(default_remote):
            print(f"[DUMP] Saved fresh XML from default path: local={self.local_dump_path}")
            return True
        if self._read_remote_dump_via_exec_out(default_remote):
            return True
        return False

    def _try_fallback_hierarchy_provider(self, *, reason: str) -> bool:
        provider = self.fallback_hierarchy_provider
        if provider is None:
            return False
        try:
            xml = safe_str(provider())
        except Exception as exc:
            print(f"[DUMP] Fallback hierarchy provider failed for {reason}: {exc}")
            return False
        if not xml or "<node" not in xml:
            print(f"[DUMP] Fallback hierarchy provider returned empty XML for {reason}.")
            return False
        try:
            ET.fromstring(xml)
        except Exception as exc:
            print(f"[DUMP] Fallback hierarchy XML is invalid for {reason}: {exc}")
            return False
        self.local_dump_path.write_text(xml, encoding="utf-8")
        print(f"[DUMP] Saved XML via fallback hierarchy provider: {self.local_dump_path}")
        return True

    def fresh_dump(self, *, reason: str = "") -> Optional[ET.Element]:
        if not self.ensure_device():
            return None
        try:
            if self.local_dump_path.exists():
                self.local_dump_path.unlink()
        except Exception as exc:
            print(f"[DUMP] Cannot delete local dump {self.local_dump_path}: {exc}")

        self.local_dump_path.parent.mkdir(parents=True, exist_ok=True)

        if (
            self.fallback_hierarchy_provider is not None
            and os.getenv("FB_1_1_PREFER_FALLBACK_HIERARCHY_PROVIDER", "1").lower() in {"1", "true", "yes", "on"}
            and self._try_fallback_hierarchy_provider(reason=reason)
        ):
            try:
                return ET.parse(self.local_dump_path).getroot()
            except Exception as exc:
                print(f"[DUMP] Cannot parse fallback XML {self.local_dump_path}: {exc}")
                return None

        # Try robust shell dumps first. Do not call `adb pull` until the remote
        # file is verified, otherwise logs are flooded with
        # `failed to stat remote object`.
        for remote_path in self._remote_dump_candidates():
            if self._try_adb_uiautomator_dump(remote_path, compressed=False, reason=reason):
                break
            if self._try_adb_uiautomator_dump(remote_path, compressed=True, reason=reason):
                break
        else:
            # Some builds only support the default path when no file argument is
            # provided. Try this before giving up.
            if not self._try_default_adb_uiautomator_dump(compressed=False, reason=reason):
                if not self._try_default_adb_uiautomator_dump(compressed=True, reason=reason):
                    # Final fallback: use uiautomator2 hierarchy if the adapter
                    # supplied it. The local file is still window_dump.xml.
                    if not self._try_fallback_hierarchy_provider(reason=reason):
                        print(
                            "[DUMP] Cannot create window_dump.xml by adb or fallback provider. "
                            "Try manually: platform-tools/adb shell uiautomator dump --compressed /data/local/tmp/window_dump.xml"
                        )
                        return None

        if not self.local_dump_path.exists():
            print(f"[DUMP] File was not created: {self.local_dump_path}")
            return None
        try:
            return ET.parse(self.local_dump_path).getroot()
        except Exception as exc:
            print(f"[DUMP] Cannot parse {self.local_dump_path}: {exc}")
            return None

    def _collect_nodes_recursive(
        self,
        element: ET.Element,
        nodes: list[UiNode],
        parent_index: Optional[int],
        depth: int,
    ) -> None:
        if element.tag == "node":
            bounds_text = safe_str(element.attrib.get("bounds"))
            parsed = parse_bounds(bounds_text)
            current_index: Optional[int] = None
            if parsed:
                current_index = len(nodes)
                node = UiNode(
                    index=current_index,
                    parent_index=parent_index,
                    text=safe_str(element.attrib.get("text")),
                    desc=safe_str(element.attrib.get("content-desc")),
                    resource_id=safe_str(element.attrib.get("resource-id")),
                    class_name=safe_str(element.attrib.get("class")),
                    package=safe_str(element.attrib.get("package")),
                    clickable=is_truthy_attr(element.attrib.get("clickable")),
                    enabled=not safe_str(element.attrib.get("enabled", "true")).lower() == "false",
                    selected=is_truthy_attr(element.attrib.get("selected")),
                    focused=is_truthy_attr(element.attrib.get("focused")),
                    scrollable=is_truthy_attr(element.attrib.get("scrollable")),
                    bounds_text=bounds_text,
                    bounds=parsed,
                    depth=depth,
                )
                nodes.append(node)
            parent_for_children = current_index if current_index is not None else parent_index
        else:
            parent_for_children = parent_index

        for child in list(element):
            self._collect_nodes_recursive(child, nodes, parent_for_children, depth + 1)

    def read_nodes(self, *, reason: str = "") -> list[UiNode]:
        root = self.fresh_dump(reason=reason or "read nodes")
        if root is None:
            self._last_nodes = []
            self._last_node_by_index = {}
            return []
        nodes: list[UiNode] = []
        self._collect_nodes_recursive(root, nodes, None, 0)
        self._last_nodes = nodes
        self._last_node_by_index = {node.index: node for node in nodes}
        return nodes

    def node_by_index(self, index: Optional[int]) -> Optional[UiNode]:
        if index is None:
            return None
        return self._last_node_by_index.get(index)

    def labels(self, *, limit: int = VERBOSE_VISIBLE_LABEL_LIMIT, reason: str = "") -> list[str]:
        nodes = self.read_nodes(reason=reason or "labels")
        output: list[str] = []
        seen: set[str] = set()
        for node in nodes:
            label = node.label
            if not label or label in seen:
                continue
            seen.add(label)
            output.append(label)
            if len(output) >= limit:
                break
        return output

    def text_blob(self, *, reason: str = "") -> str:
        return " | ".join(self.labels(limit=300, reason=reason or "text blob"))

    def visible_debug_line(self, *, limit: int = 60, reason: str = "") -> str:
        return " | ".join(self.labels(limit=limit, reason=reason or "visible debug"))

    def find_clickable_ancestor(self, node: UiNode, *, max_area_ratio: float = 0.85) -> UiNode:
        width, height = self.get_screen_size()
        max_area = int(width * height * max_area_ratio)
        current = node
        best = node
        while current.parent_index is not None:
            parent = self.node_by_index(current.parent_index)
            if parent is None:
                break
            if parent.enabled and parent.clickable and parent.area <= max_area and bounds_contains(parent.bounds, node.bounds):
                best = parent
                break
            current = parent
        return best

    def _node_text_matches(
        self,
        node: UiNode,
        spec: SelectorSpec,
    ) -> tuple[bool, int, str]:
        label_sources = [node.text, node.desc, node.resource_id]
        node_values: set[str] = set()
        for source in label_sources:
            node_values.update(normalize_variants(source))
        node_join = " ".join(sorted(node_values))

        if not node_values:
            return False, 0, ""

        block_variants: set[str] = set()
        for term in spec.block_terms:
            block_variants.update(normalize_variants(term))
        if any(term and term in node_join for term in block_variants):
            return False, -9999, "blocked"

        score = 0
        matched_by = ""

        for wanted in spec.texts:
            wanted_values = normalize_variants(wanted)
            if node_values & wanted_values:
                score += 240 if spec.prefer_exact else 180
                matched_by = f"exact:{wanted}"
            elif spec.allow_contains:
                for w in wanted_values:
                    if not w:
                        continue
                    # Cải tiến: Với các chuỗi cực ngắn (<=3 ký tự như 'Lưu', 'OK'), 
                    # yêu cầu khớp nguyên từ và nhãn mục tiêu phải ngắn (tránh paragraph).
                    if len(w) <= 3:
                        match_found = any(len(val) < 60 and re.search(rf"\b{re.escape(w)}\b", val) for val in node_values)
                    else:
                        # Match primarily in the natural direction: requested
                        # text inside the UI label. The old symmetric check
                        # also accepted tiny UI labels inside a longer wanted
                        # phrase, e.g. label "A" matched wanted "Create PIN",
                        # which caused Messenger story/contact chips to be
                        # tapped as the backup intro button.
                        match_found = any(w in val for val in node_values)
                        if not match_found:
                            match_found = any(
                                len(val) >= 4
                                and len(val.split()) >= 2
                                and val in w
                                for val in node_values
                            )

                    if match_found:
                        score += 90
                        matched_by = f"contains:{wanted}"
                        break

        for wanted in spec.contains:
            wanted_values = normalize_variants(wanted)
            for w in wanted_values:
                if w and any(w in value for value in node_values):
                    score += 80
                    matched_by = f"contains:{wanted}"
                    break

        for rid in spec.resource_ids:
            rid_norms = normalize_variants(rid)
            node_rid_norms = normalize_variants(node.resource_id)
            if node_rid_norms & rid_norms:
                score += 180
                matched_by = f"resource-id:{rid}"
            elif any(r in v for r in rid_norms for v in node_rid_norms):
                score += 90
                matched_by = f"resource-id-contains:{rid}"

        if spec.class_contains:
            cls_norms = normalize_variants(node.class_name)
            matched_class = False
            for item in spec.class_contains:
                item_norms = normalize_variants(item)
                if any(i in c for i in item_norms for c in cls_norms):
                    matched_class = True
                    score += 40
            if spec.texts or spec.contains or spec.resource_ids:
                # Class is only a boost when text/id already matched.
                pass
            elif matched_class:
                matched_by = "class"

        if not matched_by:
            return False, 0, ""

        priority_variants: set[str] = set()
        for term in spec.priority_terms:
            priority_variants.update(normalize_variants(term))
        weak_variants: set[str] = set()
        for term in spec.weak_terms:
            weak_variants.update(normalize_variants(term))

        if any(term and term in node_join for term in priority_variants):
            score += 220
        if any(term and term == value for term in weak_variants for value in node_values):
            score -= 100

        return True, score, matched_by

    def _zone_score(self, node: UiNode, spec: SelectorSpec) -> int:
        width, height = self.get_screen_size()
        x, y = node.center
        xr = x / max(1, width)
        yr = y / max(1, height)

        if spec.min_y_ratio is not None and yr < spec.min_y_ratio:
            return -500
        if spec.max_y_ratio is not None and yr > spec.max_y_ratio:
            return -500
        if spec.min_x_ratio is not None and xr < spec.min_x_ratio:
            return -500
        if spec.max_x_ratio is not None and xr > spec.max_x_ratio:
            return -500

        score = 0
        for zone in spec.preferred_zones:
            z = zone.lower()
            if z == "top" and yr <= 0.22:
                score += 35
            elif z == "bottom" and yr >= 0.72:
                score += 35
            elif z == "left" and xr <= 0.36:
                score += 35
            elif z == "right" and xr >= 0.64:
                score += 35
            elif z == "center" and 0.25 <= xr <= 0.75 and 0.20 <= yr <= 0.82:
                score += 25
            elif z == "top_right" and xr >= 0.55 and yr <= 0.35:
                score += 90
            elif z == "top_left" and xr <= 0.35 and yr <= 0.22:
                score += 90
            elif z == "bottom_right" and xr >= 0.55 and yr >= 0.68:
                score += 90
            elif z == "bottom_left" and xr <= 0.42 and yr >= 0.70:
                score += 70
        return score

    def find_best_node(self, spec: SelectorSpec, *, reason: str = "") -> Optional[dict[str, Any]]:
        nodes = self.read_nodes(reason=reason or spec.name)
        if not nodes:
            return None

        candidates: list[tuple[int, UiNode, UiNode, str]] = []
        for node in nodes:
            if not node.enabled:
                continue

            # Loại trừ vùng thanh trạng thái hệ thống (giảm xuống 20px để tránh hụt header app)
            if node.bounds[1] < 20:
                continue

            matched, base_score, matched_by = self._node_text_matches(node, spec)
            if not matched:
                continue

            target = self.find_clickable_ancestor(node) if spec.require_clickable_or_parent else node
            if spec.require_clickable_or_parent and not target.clickable and not node.clickable:
                # Still allow TextView if no clickable parent, but lower score.
                target = node
                base_score -= 45

            zone_score = self._zone_score(target, spec)
            if zone_score <= -500:
                continue

            score = base_score + zone_score
            score += 45 if target.clickable else 0
            score += 20 if "button" in target.class_name.lower() else 0
            score += 8 if target.selected else 0
            score += min(target.area // 10000, 30)
            score -= min(target.depth, 20)

            # Avoid huge full-screen containers unless they are the only clickable ancestor.
            width, height = self.get_screen_size()
            if target.area > width * height * 0.65:
                score -= 80

            candidates.append((score, target, node, matched_by))

        if not candidates:
            visible = self.visible_debug_line(limit=70, reason=f"not found: {spec.name}")
            print(f"[SELECT] {spec.name}: not found. Visible labels: {visible}")
            return None

        candidates.sort(key=lambda item: item[0], reverse=True)
        best_score, target, matched_node, matched_by = candidates[0]
        result = target.to_result(score=best_score)
        result["matched_node"] = matched_node.to_result(score=best_score)
        result["matched_by"] = matched_by
        result["candidate_count"] = len(candidates)
        print(
            f"[SELECT] {spec.name}: label='{result['label']}' bounds={result['bounds']} "
            f"clickable={result['clickable']} class={result['class']} score={best_score} "
            f"matched_by={matched_by} candidates={len(candidates)} reason={reason}"
        )
        return result

    def click_content_desc_exact(
        self,
        desc_options: list[str],
        *,
        reason: str = "",
        min_x_ratio: Optional[float] = None,
        max_y_ratio: Optional[float] = None,
    ) -> bool:
        """Fast path for exact content-desc matches from window_dump.xml."""
        nodes = self.read_nodes(reason=reason or "click content-desc exact")
        if not nodes:
            return False
        wanted: set[str] = set()
        for item in desc_options:
            wanted.update(normalize_variants(item))
        width, height = self.get_screen_size()
        candidates: list[tuple[int, UiNode]] = []
        for node in nodes:
            node_descs = normalize_variants(node.desc)
            if not node_descs or not (node_descs & wanted):
                continue
            x, y = node.center
            xr = x / max(1, width)
            yr = y / max(1, height)
            if min_x_ratio is not None and xr < min_x_ratio:
                continue
            if max_y_ratio is not None and yr > max_y_ratio:
                continue
            target = self.find_clickable_ancestor(node)
            score = 1000
            score += 100 if target.clickable else 0
            score += 80 if xr >= 0.55 else 0
            score += 80 if yr <= 0.25 else 0
            score -= min(target.depth, 30)
            candidates.append((score, target))
        if not candidates:
            print(f"[SELECT] {reason or 'content-desc exact'}: not found for {desc_options}")
            return False
        candidates.sort(key=lambda item: item[0], reverse=True)
        _, target = candidates[0]
        print(f"[SELECT] {reason or 'content-desc exact'}: content-desc match label='{target.label}' bounds={target.bounds_text} clickable={target.clickable}")
        return self.tap_bounds(target.bounds, reason=reason or "content-desc exact")

    def click_best(self, spec: SelectorSpec, *, reason: str = "", sleep_after: float = 1.4) -> bool:
        target = self.find_best_node(spec, reason=reason or spec.name)
        if not target:
            return False
        ok = self.tap_bounds(target["bounds"], reason=reason or spec.name)
        if ok and sleep_after > 0:
            time.sleep(sleep_after)
        return ok

    def click_text(
        self,
        text_options: list[str],
        *,
        allow_contains: bool = True,
        reason: str = "",
        preferred_zones: Optional[list[str]] = None,
        block_terms: Optional[list[str]] = None,
    ) -> bool:
        spec = SelectorSpec(
            name=reason or "click_text",
            texts=text_options,
            allow_contains=allow_contains,
            preferred_zones=preferred_zones or [],
            block_terms=block_terms or [],
        )
        return self.click_best(spec, reason=reason or "click_text")


class MessengerDumpBot:
    def __init__(self, device: DumpUiDevice) -> None:
        self.device = device
        self.owner = None
        self._last_unreachable_request_deleted = False

    def _blob(self, reason: str = "") -> str:
        return normalize_text(self.device.text_blob(reason=reason), remove_accents=True)

    def _looks_like_account_name(self, label: str) -> bool:
        norm = normalize_text(label, remove_accents=True)
        if not norm or not any(ch.isalpha() for ch in norm):
            return False
        words = norm.split()
        if not (1 <= len(words) <= 5):
            return False
        if len(label.strip()) < 2 or len(label.strip()) > 45:
            return False
        if is_profile_non_name_label(label):
            return False
        if len(label) >= 8 and label.upper() == label and any(ch.isalpha() for ch in label):
            return False
        return True

    def wait_for_package(self, package_name: str, timeout: int = 20) -> bool:
        deadline = time.time() + max(1, timeout)
        while time.time() < deadline:
            if self.device.is_current_package(package_name):
                return True
            time.sleep(0.8)
        return False

    def open_facebook(self) -> bool:
        return self.device.start_app(FACEBOOK_PKG, wait_seconds=4)

    def open_messenger_app(self) -> bool:
        return self.device.start_app(MESSENGER_PKG, wait_seconds=4)

    def is_messenger_home(self) -> bool:
        if not self.device.is_current_package(MESSENGER_PKG):
            return False
        blob = self._blob("messenger home detection")
        if self._blob_is_messenger_home(blob):
            return True
        negative = [
            "tin nhan dang cho", "message requests", "yeu cau nhan tin", "loi moi nhan tin",
            "chap nhan", "accept", "chan", "block", "xoa", "delete",
            "dang nhap", "log in", "password", "mat khau",
            # Blocking PIN/setup screens. Without these, the broad "messenger"
            # positive can mark "Xác nhận mã PIN" as ready and the tool then
            # starts Message Requests navigation too early.
            "ma pin", "tao ma pin", "xac nhan ma pin", "nhap ma pin",
            "thiet lap ma pin", "truong ma khoa", "chinh sua 1/6",
            "create pin", "confirm pin", "enter pin",
        ]
        if any(term in blob for term in negative):
            return False
        positive = ["doan chat", "chats", "tim kiem", "search", "menu"]
        if any(term in blob for term in positive):
            return True
        return self.has_bottom_messenger_menu_tab()

    @staticmethod
    def _blob_is_messenger_home(blob: str) -> bool:
        if not blob:
            return False
        has_bottom_nav = "tab menu" in blob and any(term in blob for term in [
            "doan chat",
            "chats",
            "tab 1/4",
            "tin,",
            "thong bao",
        ])
        has_chat_list_chrome = any(term in blob for term in [
            "hoi meta ai hoac tim kiem",
            "messenger | tin nhan moi",
            "meta ai | doan chat",
        ])
        return has_bottom_nav or has_chat_list_chrome

    @staticmethod
    def _blob_has_blocking_pin_prompt(blob: str) -> bool:
        if not blob:
            return False
        if MessengerDumpBot._blob_is_messenger_home(blob):
            return False
        input_terms = [
            "android.widget.edittext", "edittext",
            "truong ma khoa", "chinh sua 1/6", "chinh sua 1/5",
        ]
        title_terms = [
            "tao ma pin", "thiet lap ma pin",
            "xac nhan ma pin", "confirm pin", "confirm your pin",
            "nhap ma pin", "nhap lai ma pin", "enter pin", "enter your pin",
            "create pin", "set up pin", "incorrect pin", "wrong pin",
        ]
        return any(term in blob for term in input_terms + title_terms)

    def handle_pin_if_visible(self) -> bool:
        """Delegate Messenger PIN setup/confirmation to the owner bot.

        This stays isolated to Messenger PIN screens, so it will not affect
        Facebook feed, Play Store, chat crawling, or message request actions.
        """
        if not self.device.is_current_package(MESSENGER_PKG):
            return False
        blob = self._blob("dump bot PIN guard")
        if not self._blob_has_blocking_pin_prompt(blob):
            return False
        owner = getattr(self, "owner", None)
        if owner is not None and hasattr(owner, "handle_messenger_pin_prompt_if_visible"):
            owner.handle_messenger_pin_prompt_if_visible(driver=None)
            return True
        return False

    def is_messenger_menu_screen(self) -> bool:
        """Detect Messenger Menu screen after tapping the bottom-right Menu tab."""
        if not self.device.is_current_package(MESSENGER_PKG):
            return False
        if self._has_message_requests_list_chrome():
            return False
        blob = self._blob("messenger menu screen detection")
        request_terms = [
            "tin nhan dang cho", "message requests", "yeu cau nhan tin", "loi moi nhan tin",
        ]
        menu_terms = [
            "cai dat", "settings",
            "cong dong", "community",
            "kho luu tru", "archive",
            "loi moi ket ban", "friend requests",
            "chuyen trang ca nhan",
        ]
        indexed_menu_terms = [", 1/3", ", 2/3", ", 3/3", " 1/3", " 2/3", " 3/3"]
        has_request_entry = any(term in blob for term in request_terms)
        has_menu_context = any(term in blob for term in menu_terms) or any(term in blob for term in indexed_menu_terms)
        return has_request_entry and has_menu_context

    def _has_selected_bottom_menu_with_menu_rows(self, nodes: Optional[list[UiNode]] = None) -> bool:
        """Detect the active Messenger Menu tab even when stale request-list nodes remain in XML."""
        if not self.device.is_current_package(MESSENGER_PKG):
            return False

        nodes = nodes or self.device.read_nodes(reason="selected bottom Menu guard")
        width, height = self.device.get_screen_size(refresh=True)
        dump_w = max((node.bounds[2] for node in nodes), default=0)
        dump_h = max((node.bounds[3] for node in nodes), default=0)
        if dump_w and dump_h:
            width, height = dump_w, dump_h

        selected_menu_tab = False
        menu_row_hits = 0
        for node in nodes:
            norm = normalize_text(node.label, remove_accents=True)
            if not norm:
                continue
            x, y = node.center
            xr = x / max(1, width)
            yr = y / max(1, height)
            is_bottom_menu_tab = (
                "tab menu" in norm
                or ("menu" in norm and re.search(r"\btab\s+\d+/\d+\b", norm))
                or norm == "menu"
            )
            if is_bottom_menu_tab and node.selected and xr >= 0.55 and yr >= 0.82:
                selected_menu_tab = True
                continue

            x1, y1, x2, y2 = node.bounds
            full_width_row = x1 <= int(width * 0.08) and x2 >= int(width * 0.80)
            if full_width_row and 0.08 <= yr <= 0.72 and any(term in norm for term in [
                "cai dat", "settings",
                "cong dong", "community",
                "kho luu tru", "archive",
                "loi moi ket ban", "friend requests",
                "facebook reels",
                "su kien tren facebook",
            ]):
                menu_row_hits += 1

        return selected_menu_tab and menu_row_hits >= 2

    def _has_message_requests_list_chrome(self) -> bool:
        """Return true for the active Message Requests list chrome.

        Messenger sometimes leaves a stale Menu subtree in the XML after opening
        Message Requests. Blob-based detection then sees both Menu entries and
        request rows. The active request list is identified by its top header
        and tabs instead of by global text presence.
        """
        nodes = self.device.read_nodes(reason="message requests list chrome detection")
        if not any(node.package == MESSENGER_PKG for node in nodes):
            return False
        width, height = self.device.get_screen_size(refresh=True)
        dump_w = max((node.bounds[2] for node in nodes), default=0)
        dump_h = max((node.bounds[3] for node in nodes), default=0)
        if dump_w and dump_h:
            width, height = dump_w, dump_h

        has_title = False
        has_request_tab = False
        has_back = False
        has_request_hint = False
        for node in nodes:
            norm = normalize_text(node.label, remove_accents=True)
            if not norm:
                continue

            x1, y1, x2, y2 = node.bounds
            cx, cy = node.center
            xr = cx / max(1, width)
            yr = cy / max(1, height)

            if "tin nhan dang cho" in norm or "message requests" in norm:
                if 0.03 <= yr <= 0.25 and 0.08 <= xr <= 0.78:
                    has_title = True
            if "quay lai" in norm or norm == "back":
                if 0.03 <= yr <= 0.22 and x2 <= int(width * 0.24):
                    has_back = True
            if any(term in norm for term in ["ban co the biet", "you may know", "spam"]):
                if 0.08 <= yr <= 0.36 and y2 <= int(height * 0.38):
                    has_request_tab = True
            if any(term in norm for term in [
                "hay mo doan chat",
                "chi khi ban tra loi",
                "chon ai co the nhan tin",
                "open the chat",
                "only if you reply",
                "control who can message you",
            ]):
                if 0.10 <= yr <= 0.42:
                    has_request_hint = True

        return has_title and (has_request_tab or has_back or has_request_hint)

    def has_bottom_messenger_menu_tab(self) -> bool:
        """Return true when Messenger home bottom navigation exposes the Menu tab."""
        if not self.device.is_current_package(MESSENGER_PKG):
            return False
        nodes = self.device.read_nodes(reason="Messenger bottom Menu tab visibility")
        width, height = self.device.get_screen_size(refresh=True)
        dump_w = max((node.bounds[2] for node in nodes), default=0)
        dump_h = max((node.bounds[3] for node in nodes), default=0)
        if dump_w and dump_h:
            width, height = dump_w, dump_h

        for node in nodes:
            norm = normalize_text(node.label, remove_accents=True)
            if not norm:
                continue
            is_menu_tab = (
                "tab menu" in norm
                or ("menu" in norm and re.search(r"\btab\s+\d+/\d+\b", norm))
                or norm == "menu"
            )
            if not is_menu_tab:
                continue
            if "lua chon khac" in norm or "more options" in norm:
                continue
            x, y = node.center
            if x / max(1, width) >= 0.55 and y / max(1, height) >= 0.82:
                print(f"[REQUESTS] Messenger bottom Menu tab visible: '{node.label}' {node.bounds_text}")
                return True
        return False

    def _has_messenger_bottom_navigation(self, nodes: Optional[list[UiNode]] = None) -> bool:
        """Detect Messenger's bottom tab bar, which is absent on request detail."""
        if not self.device.is_current_package(MESSENGER_PKG):
            return False
        nodes = nodes or self.device.read_nodes(reason="Messenger bottom navigation guard")
        width, height = self.device.get_screen_size(refresh=True)
        dump_w = max((node.bounds[2] for node in nodes), default=0)
        dump_h = max((node.bounds[3] for node in nodes), default=0)
        if dump_w and dump_h:
            width, height = dump_w, dump_h

        tab_hits = 0
        for node in nodes:
            norm = normalize_text(node.label, remove_accents=True)
            if not norm:
                continue
            x, y = node.center
            if y / max(1, height) < 0.82:
                continue
            if not re.search(r"\btab\s+\d+/\d+\b", norm):
                continue
            if any(term in norm for term in [
                "doan chat", "chat", "tin", "stories", "thong bao",
                "notification", "menu",
            ]):
                tab_hits += 1
        return tab_hits >= 2

    def is_message_requests_list(self) -> bool:
        if not self._has_message_requests_list_chrome():
            return False
        blob = self._blob("message requests list detection")
        has_detail_action = any(term in blob for term in ["chap nhan", "accept", "block", "xoa", "delete"])
        return not has_detail_action

    def _has_thread_details_header(self, nodes: Optional[list[UiNode]] = None) -> bool:
        nodes = nodes or self.device.read_nodes(reason="thread details header detection")
        width, height = self.device.get_screen_size(refresh=True)
        dump_w = max((node.bounds[2] for node in nodes), default=0)
        dump_h = max((node.bounds[3] for node in nodes), default=0)
        if dump_w and dump_h:
            width, height = dump_w, dump_h

        for node in nodes:
            norm = normalize_text(node.desc or node.text, remove_accents=True)
            if not norm:
                continue
            if not any(term in norm for term in ["chi tiet chuoi bai", "thread details", "conversation details"]):
                continue
            x, y = node.center
            if x / max(1, width) >= 0.45 and y / max(1, height) <= 0.22:
                return True
        return False

    def is_message_request_detail(self) -> bool:
        if not self.device.is_current_package(MESSENGER_PKG):
            return False
        nodes = self.device.read_nodes(reason="message request detail bottom-nav guard")
        if self._has_messenger_bottom_navigation(nodes):
            return False
        if self._has_message_requests_list_chrome():
            return False
        blob = normalize_text(" | ".join(node.label for node in nodes if node.label), remove_accents=True)
        has_request_context = any(term in blob for term in ["tin nhan dang cho", "message request", "yeu cau nhan tin"])
        has_action = any(term in blob for term in ["chap nhan", "accept", "chan", "block", "xoa", "delete"])
        has_request_detail_text = any(term in blob for term in [
            "cac ban khong phai la ban be",
            "khong phai la ban be",
            "you are not facebook friends",
            "chi khi ban tra loi",
            "only if you reply",
        ])
        has_unreachable_text = any(term in blob for term in [
            "hien khong lien lac duoc voi nguoi nay tren messenger",
            "khong lien lac duoc voi nguoi nay tren messenger",
            "you can no longer contact this person on messenger",
            "this person is unavailable on messenger",
            "this person isn't available on messenger",
        ])
        has_encryption_text = any(term in blob for term in [
            "tin nhan va cuoc goi duoc bao mat bang tinh nang ma hoa dau cuoi",
            "chi nhung nguoi tham gia doan chat nay moi co the doc nghe hoac chia se",
            "messages and calls are secured",
            "end to end encrypted",
            "end-to-end encrypted",
            "end to end encryption",
            "end-to-end encryption",
        ])
        has_thread_details_header = self._has_thread_details_header(nodes)
        return (
            has_action
            or has_unreachable_text
            or (has_request_context and has_request_detail_text)
            or (has_thread_details_header and has_encryption_text)
        )

    def is_current_request_unreachable(self) -> bool:
        if not self.device.is_current_package(MESSENGER_PKG):
            return False
        blob = self._blob("message request unreachable detection")
        unreachable_terms = [
            "hien khong lien lac duoc voi nguoi nay tren messenger",
            "khong lien lac duoc voi nguoi nay tren messenger",
            "you can no longer contact this person on messenger",
            "this person is unavailable on messenger",
            "this person isn't available on messenger",
        ]
        return any(term in blob for term in unreachable_terms)

    def click_thread_details_button(self, timeout: int = 5) -> bool:
        wanted = [
            "chi tiet chuoi bai",
            "thread details",
            "conversation details",
        ]
        details = SelectorSpec(
            name="Messenger Thread Details",
            texts=["Chi tiết chuỗi bài", "Chi tiet chuoi bai", "Thread details", "Conversation details"],
            contains=["Chi tiết chuỗi bài", "Thread details", "Conversation details"],
            priority_terms=["Chi tiết chuỗi bài", "Thread details", "Conversation details"],
            preferred_zones=["top_right", "top"],
            min_x_ratio=0.45,
            max_y_ratio=0.22,
            allow_contains=True,
        )
        deadline = time.time() + max(1, timeout)
        while time.time() < deadline:
            nodes = self.device.read_nodes(reason="Message Request Thread Details direct button")
            width, height = self.device.get_screen_size()
            candidates: list[tuple[int, UiNode]] = []
            for node in nodes:
                if not node.enabled or not node.clickable:
                    continue
                norm = normalize_text(node.desc or node.text, remove_accents=True)
                if not norm or not any(term in norm for term in wanted):
                    continue
                x, y = node.center
                xr = x / max(1, width)
                yr = y / max(1, height)
                if xr < 0.45 or yr > 0.22:
                    continue
                score = 1000
                score += 220 if xr >= 0.80 else 0
                score += 80 if "button" in node.class_name.lower() else 0
                score -= min(node.depth, 30)
                candidates.append((score, node))
            if candidates:
                candidates.sort(key=lambda item: item[0], reverse=True)
                _, target = candidates[0]
                print(f"[SELECT] Message Request Thread Details direct button: label='{target.label}' bounds={target.bounds_text}")
                if self.device.tap_bounds(target.bounds, reason="Message Request Thread Details direct button"):
                    time.sleep(1.4)
                    return True
            if self.device.click_best(details, reason="Message Request Thread Details"):
                time.sleep(1.4)
                return True
            time.sleep(0.6)
        return False

    def click_delete_chat_from_thread_details(self, timeout: int = 6) -> bool:
        delete_chat = SelectorSpec(
            name="Delete Chat From Thread Details",
            texts=["Xóa đoạn chat", "Xoá đoạn chat", "Xoa doan chat", "Delete chat", "Delete conversation"],
            contains=["Xóa đoạn chat", "Xoá đoạn chat", "Delete chat", "Delete conversation"],
            priority_terms=["Xóa đoạn chat", "Xoá đoạn chat", "Delete chat", "Delete conversation"],
            block_terms=["Hủy", "Huy", "Cancel"],
            preferred_zones=["center", "bottom"],
            min_y_ratio=0.20,
            allow_contains=True,
        )
        deadline = time.time() + max(1, timeout)
        while time.time() < deadline:
            if self.device.click_best(delete_chat, reason="Delete unreachable Message Request chat"):
                time.sleep(1.2)
                return True
            time.sleep(0.6)
        return False

    def click_confirm_delete_chat_dialog(self, timeout: int = 6) -> bool:
        confirm_terms = {"xoa", "delete"}
        dialog_terms = [
            "xoa toan bo lich su chat",
            "thao tac nay se xoa toan bo cuoc tro chuyen",
            "delete entire chat history",
            "delete conversation",
        ]
        deadline = time.time() + max(1, timeout)
        while time.time() < deadline:
            nodes = self.device.read_nodes(reason="Confirm delete chat dialog")
            width, height = self.device.get_screen_size()
            blob = normalize_text(" | ".join(node.label for node in nodes if node.label), remove_accents=True)
            has_dialog = any(term in blob for term in dialog_terms)
            candidates: list[tuple[int, UiNode]] = []
            for node in nodes:
                if not node.enabled or not node.clickable:
                    continue
                norm = normalize_text(node.text or node.desc, remove_accents=True)
                rid = normalize_text(node.resource_id, remove_accents=True)
                if rid.endswith("android:id/button1") or "android:id/button1" in rid:
                    score = 1200
                elif norm in confirm_terms and has_dialog:
                    score = 900
                else:
                    continue
                x, y = node.center
                score += 80 if x / max(1, width) >= 0.55 else 0
                score += 50 if y / max(1, height) >= 0.45 else 0
                score += 30 if "button" in node.class_name.lower() else 0
                candidates.append((score, node))
            if candidates:
                candidates.sort(key=lambda item: item[0], reverse=True)
                _, target = candidates[0]
                print(f"[SELECT] Confirm delete chat dialog: label='{target.label}' bounds={target.bounds_text}")
                if self.device.tap_bounds(target.bounds, reason="Confirm delete chat dialog"):
                    time.sleep(1.4)
                    return True
            time.sleep(0.6)
        return False

    def delete_unreachable_message_request_if_open(self) -> bool:
        if not self.is_current_request_unreachable():
            return False
        print("[REQUESTS] Current request is unreachable on Messenger. Opening thread details to delete chat.")
        if not self.click_thread_details_button(timeout=5):
            print("[REQUESTS] Could not open thread details for unreachable request.")
            return False
        if not self.click_delete_chat_from_thread_details(timeout=6):
            visible = self.device.visible_debug_line(reason="delete unreachable request failed")
            print(f"[REQUESTS] Delete chat option not found after opening details. Visible labels: {visible}")
            return False
        if not self.click_confirm_delete_chat_dialog(timeout=6):
            visible = self.device.visible_debug_line(reason="confirm delete unreachable request failed")
            print(f"[REQUESTS] Delete confirmation button not found. Visible labels: {visible}")
            return False
        print("[REQUESTS] Confirmed Delete Chat for unreachable message request.")
        return True

    def click_facebook_home_tab(self) -> bool:
        # Chỉ được bấm Home tab của Facebook. Không được khớp nút Home của Android
        # vì trên Samsung nút navigation bar cũng có label "Trang chính" ở đáy màn hình,
        # gây văng ra Launcher rồi watchdog phải mở lại Facebook.
        spec = SelectorSpec(
            name="Facebook Home tab",
            texts=["Trang chủ", "Home", "News Feed", "Bảng tin"],
            contains=["Home, tab", "Trang chủ, tab", "Trang chính, tab"],
            block_terms=[
                "Trang chủ của",
                "Home page of",
                "com.android.systemui",
                "navigation_bar",
                "navigationBar",
                "Gần đây",
                "Trở về",
            ],
            preferred_zones=["top_left"],
            max_y_ratio=0.22,
            allow_contains=True,
        )
        if self.device.click_best(spec, reason="Facebook Home tab"):
            return True

        # Fallback có kiểm soát: tab Home của Facebook thường nằm hàng tab trên cùng.
        # Không dùng y > 0.25 để tránh đụng Android navigation bar.
        return False

    def is_facebook_profile_surface(self) -> bool:
        """Detect Facebook profile pages where the Messenger header icon is absent."""
        if not self.device.is_current_package(FACEBOOK_PKG):
            return False
        blob = self._blob("Facebook profile surface detection")
        profile_terms = [
            "profile picture",
            "cover photo",
            "edit cover photo",
            "chinh sua anh dai dien",
            "chinh sua trang ca nhan",
            "nguoi ban",
            "bai viet",
            "thong tin ca nhan",
            "trang ca nhan, tab",
        ]
        return any(term in blob for term in profile_terms)

    def return_to_facebook_home_from_profile(self, *, reason: str = "") -> bool:
        """Return from a Facebook profile to a surface where Messenger can be opened."""
        if not self.device.is_current_package(FACEBOOK_PKG):
            return False

        if self.click_facebook_home_tab():
            time.sleep(1)
        else:
            print("[FACEBOOK] Home tab not visible on profile. Pressing Back to return to previous Facebook surface.")
            self.device.press_back(reason=reason or "return from profile after account snapshot")
            time.sleep(1.5)
            if self.device.is_current_package(FACEBOOK_PKG):
                self.click_facebook_home_tab()
                time.sleep(1)

        if not self.device.is_current_package(FACEBOOK_PKG):
            print("[FACEBOOK] Return from profile left Facebook foreground.")
            return False

        self.device.swipe_ratio(0.50, 0.62, 0.50, 0.82, 260, reason=reason or "reveal Facebook header after profile")
        self.device.collapse_statusbar_if_open()
        time.sleep(1)
        return True

    def click_facebook_messenger_entry(self) -> bool:
        """
        Chỉ tập trung tìm Icon Messenger ở góc trên phải.
        Trang trung gian sẽ được xử lý ở bước sau trong normalize.
        """
        icon = SelectorSpec(
            name="Facebook Messenger icon",
            texts=[
                "Nhắn tin",
                "Tin nhắn",
                "Messenger",
                "Messages",
                "Trò chuyện",
                "Chats",
            ],
            contains=[
                "Nhắn tin",
                "Tin nhắn",
                "Messenger",
                "Messages",
            ],
            weak_terms=[],
            block_terms=["Tin nháº¯n má»›i", "New message"],
            priority_terms=["Nhắn tin", "Messenger"],
            preferred_zones=["top_right"],
            # Nới lỏng tỷ lệ để phù hợp với nhiều độ phân giải màn hình khác nhau
            max_y_ratio=0.45,
            min_x_ratio=0.55,
            allow_contains=True,
            require_clickable_or_parent=False,
        )
        if self.device.click_best(icon, reason="Facebook Messenger icon"):
            print("[DUMP_AUTOMATION] Found and clicked the messenger icon.")
            return True

        return False

    def click_facebook_messenger_cta(self) -> bool:
        """Xử lý riêng nút 'MỞ MESSENGER' to ở giữa màn hình."""
        cta = SelectorSpec(
            name="Facebook Open Messenger CTA",
            texts=[
                "MỞ MESSENGER", "Mở Messenger", "Mở ứng dụng Messenger",
                "Open Messenger", "OPEN MESSENGER", "Open in Messenger",
                "Go to Messenger", "Use Messenger", "Dùng Messenger",
                "Tiếp tục", "Continue", "Tiếp tục dưới tên",
            ],
            priority_terms=["MỞ MESSENGER", "Open Messenger", "OPEN MESSENGER"],
            preferred_zones=["center", "bottom"],
            allow_contains=True,
        )
        if self.device.click_best(cta, reason="Facebook Open Messenger CTA"):
            print("[DUMP_AUTOMATION] Found and clicked the big 'OPEN MESSENGER' button.")
            return True
        return False

    def normalize_facebook_home_for_messenger(self) -> bool:
        if not self.device.is_current_package(FACEBOOK_PKG):
            return False
        # self.device.collapse_statusbar_if_open()

        # 1. Thử Icon trước
        if self.is_facebook_profile_surface():
            print("[FACEBOOK] Profile surface detected. Clicking Home tab before Messenger icon search.")
            if not self.return_to_facebook_home_from_profile(reason="reveal Facebook header after profile Home click"):
                return False

        if self.click_facebook_messenger_entry():
            time.sleep(3)
            # Nếu vẫn ở Facebook, có thể màn hình CTA đã hiện ra
            if self.device.is_current_package(FACEBOOK_PKG):
                self.click_facebook_messenger_cta()
            return True

        # 2. Nếu CTA đã xuất hiện sẵn từ trước
        if self.click_facebook_messenger_cta():
            return True

        # 3. Về Home của Facebook và thử lại. Sau khi bấm phải xác minh vẫn còn ở Facebook;
        # nếu lỡ bấm Android Home thì dừng ngay, không vuốt kéo status bar/notification shade.
        self.click_facebook_home_tab()
        time.sleep(1)
        if not self.device.is_current_package(FACEBOOK_PKG):
            print("[DUMP_AUTOMATION] Facebook Home normalization aborted: app left Facebook after Home click.")
            return False

        # Kéo feed xuống để header Facebook hiện lại. Chỉ chạy khi chắc chắn foreground vẫn là Facebook.
        self.device.swipe_ratio(0.50, 0.62, 0.50, 0.82, 260, reason="reveal Facebook header after home click")
        self.device.collapse_statusbar_if_open()

        time.sleep(2)
        if self.click_facebook_messenger_entry():
            time.sleep(3)
            if self.device.is_current_package(FACEBOOK_PKG):
                self.click_facebook_messenger_cta()
            return True

        # 4. Scroll và thử lần cuối
        self.device.swipe_ratio(0.50, 0.62, 0.50, 0.78, 260, reason="Facebook feed scroll to top")
        if self.click_facebook_messenger_entry():
            time.sleep(3)
            if self.device.is_current_package(FACEBOOK_PKG):
                self.click_facebook_messenger_cta()
            return True
            
        return self.click_facebook_messenger_entry() or self.click_facebook_messenger_cta()

    def handle_system_permission_dialog(self) -> bool:
        """Xử lý hộp thoại cấp quyền của hệ thống Android (Thông báo, Danh bạ...)."""
        nodes = self.device.read_nodes(reason="System Permission Dialog strict")
        permission_packages = {
            "com.android.permissioncontroller",
            "com.google.android.permissioncontroller",
            "com.android.packageinstaller",
        }
        allow_resource_ids = {
            "com.android.permissioncontroller:id/permission_allow_button",
            "com.android.permissioncontroller:id/permission_allow_foreground_only_button",
            "com.google.android.permissioncontroller:id/permission_allow_button",
            "com.google.android.permissioncontroller:id/permission_allow_foreground_only_button",
            "com.android.packageinstaller:id/permission_allow_button",
            "android:id/button1",
        }
        allow_terms = [
            "cho phep",
            "allow",
            "trong khi dung ung dung",
            "while using the app",
            "chi lan nay",
            "only this time",
            "ok",
        ]

        candidates: list[tuple[int, UiNode]] = []
        for node in nodes:
            if not node.enabled:
                continue

            package = safe_str(node.package)
            resource_id = safe_str(node.resource_id)
            package_is_permission = package in permission_packages
            resource_is_permission = resource_id in allow_resource_ids
            if not package_is_permission and not resource_is_permission:
                continue

            text_blob = normalize_text(node.all_text, remove_accents=True)
            matched = resource_is_permission or any(term in text_blob for term in allow_terms)
            if not matched:
                continue

            target = self.device.find_clickable_ancestor(node)
            target_is_permission = (
                safe_str(target.package) in permission_packages
                or safe_str(target.resource_id) in allow_resource_ids
            )
            if not target_is_permission:
                target = node
            if not target.clickable and not node.clickable:
                continue

            score = 1000
            if resource_is_permission:
                score += 300
            if any(term in text_blob for term in ["cho phep", "allow", "while using the app", "trong khi dung ung dung"]):
                score += 150
            if target.clickable:
                score += 100
            score -= min(target.depth, 30)
            candidates.append((score, target))

        if not candidates:
            print("[SELECT] System Permission Dialog: not found by strict permission package/resource guard.")
            return False

        candidates.sort(key=lambda item: item[0], reverse=True)
        score, target = candidates[0]
        print(
            "[SELECT] System Permission Dialog STRICT: "
            f"label='{target.label}' bounds={target.bounds_text} package={target.package} "
            f"resource_id={target.resource_id} clickable={target.clickable} score={score}"
        )
        if self.device.tap_bounds(target.bounds, reason="System permission dialog strict"):
            print("[DUMP_AUTOMATION] Đã bấm Cho phép trên hộp thoại hệ thống.")
            time.sleep(2)
            return True
        return False

    def handle_messenger_backup_intro(self) -> bool:
        """Xử lý màn hình giới thiệu sao lưu trước khi vào form nhập PIN."""
        blob = self._blob("Messenger backup intro guard")
        # Nếu đã có ô nhập PIN thì không bấm lại tiêu đề/nút "Tạo mã PIN" nữa.
        # Để complete_messenger_auto_login_from_facebook gọi handler nhập PIN 000000.
        if any(term in blob for term in ["truong ma khoa", "chinh sua 1/6", "edittext", "xac nhan ma pin", "confirm pin"]):
            return False

        backup_intro_terms = [
            "tao ma pin",
            "thiet lap ma pin",
            "create pin",
            "create a pin",
            "set up pin",
            "secure storage",
            "end-to-end encrypted backup",
            "backup",
        ]
        if not any(term in blob for term in backup_intro_terms):
            return False

        spec = SelectorSpec(
            name="Messenger Backup Intro",
            texts=["Tạo mã PIN", "Thiết lập mã PIN", "Create PIN", "Set up PIN"],
            preferred_zones=["bottom", "center"],
            require_clickable_or_parent=True,
            block_terms=[
                "dang hoat dong",
                "active",
                "tao tin",
                "them tin",
                "story",
                "tin nhan va cuoc goi duoc bao mat",
            ],
            min_y_ratio=0.35,
            max_y_ratio=0.90,
        )
        if self.device.click_best(spec, reason="Messenger backup intro"):
            print("[DUMP_AUTOMATION] Đã bấm nút bắt đầu thiết lập mã PIN.")
            time.sleep(3)
            return True
        return False

    def handle_save_login_info(self) -> bool:
        """Xử lý popup 'Lưu thông tin đăng nhập'."""
        spec = SelectorSpec(
            name="Save Login Info",
            texts=["Lưu", "Save", "Lưu thông tin", "Save login info"],
            preferred_zones=["bottom", "center"],
            require_clickable_or_parent=True
        )
        if self.device.click_best(spec, reason="Save login info"):
            print("[DUMP_AUTOMATION] Đã bấm Lưu thông tin đăng nhập.")
            time.sleep(2)
            return True
        return False

    def open_messenger_from_facebook(self, timeout: int = 30) -> bool:
        self.device.ensure_device()
        current = self.device.current_package()
        if current == MESSENGER_PKG or self.device.is_current_package(MESSENGER_PKG):
            print("[MOBILE] Messenger already foreground.")
            return True
        if not self.device.is_package_installed(MESSENGER_PKG):
            print("[MOBILE] Messenger package is not installed. Install Messenger first.")
            return False
        if not self.open_facebook():
            return False
        if not self.normalize_facebook_home_for_messenger():
            print("[FACEBOOK] Messenger entry not found in dump. Forced top-right tap fallback will be used once.")
            self.device.force_tap_ratio(0.92, 0.105, reason="Facebook Messenger top-right icon")
            time.sleep(4)
        return self.wait_for_package(MESSENGER_PKG, timeout=timeout)

    def handle_simple_prompts(self) -> bool:
        """Handle only real blocking Messenger prompts.

        Important: do not click generic home-screen actions such as
        "Bỏ qua gợi ý" in the "Những người bạn có thể biết" carousel. That was
        the cause of the wrong avatar tap after Messenger home opened.
        """
        blob = self._blob(reason="simple prompt guard")
        if "tab menu" in blob and any(term in blob for term in ["doan chat", "chats", "tin, ", "tab 1/4"]):
            print("[PROMPT] Messenger Home bottom navigation detected. Skip prompt scan.")
            return False

        home_noise_terms = [
            "nhung nguoi ban co the biet",
            "you may know",
            "bo qua goi y",
            "skip suggestion",
            "tin nhan moi",
            "hoi meta ai",
        ]

        real_prompt_terms = [
            "dong bo danh ba",
            "tai danh ba",
            "tim nguoi lien he",
            "danh ba dien thoai",
            "nguoi lien he trong danh ba",
            "sync contacts",
            "upload contacts",
            "tim ban be",
            "find contacts",
            "luu thong tin dang nhap",
            "save login info",
            "thong bao",
            "notification",
            "khoi phuc",
            "restore",
            "ma hoa dau cuoi",
            "end-to-end encryption",
            "secure storage",
            "ma pin",
            "pin",
        ]

        if any(term in blob for term in home_noise_terms) and not any(term in blob for term in real_prompt_terms):
            print("[PROMPT] Messenger home/suggestion screen detected. Skip generic prompt action.")
            return False

        is_contact_prompt = any(term in blob for term in [
            "tim nguoi lien he",
            "danh ba dien thoai",
            "nguoi lien he trong danh ba",
            "sync contacts",
            "upload contacts",
            "find contacts",
        ])

        if is_contact_prompt:
            nodes = self.device.read_nodes(reason="Messenger contacts prompt normalized skip")
            width, height = self.device.get_screen_size(refresh=True)
            candidates: list[tuple[int, UiNode]] = []
            for node in nodes:
                norm = normalize_text(node.label, remove_accents=True)
                if norm not in {"luc khac", "de sau", "khong phai bay gio", "not now", "skip", "bo qua"}:
                    continue
                x1, y1, x2, y2 = node.bounds
                cy = (y1 + y2) // 2
                yr = cy / max(1, height)
                if yr < 0.18 or yr > 0.95:
                    continue
                target = self.device.find_clickable_ancestor(node)
                target_norm = normalize_text(target.label, remove_accents=True)
                if target_norm in {"bat", "turn on"}:
                    continue
                score = 500
                score += 80 if target.clickable else 0
                score += 40 if yr >= 0.72 else 0
                score -= min(target.depth, 30)
                candidates.append((score, target))

            if candidates:
                candidates.sort(key=lambda item: item[0], reverse=True)
                _, target = candidates[0]
                print(
                    f"[PROMPT] Clicking Messenger contacts prompt skip: "
                    f"label='{target.label}' bounds={target.bounds_text}"
                )
                if self.device.tap_bounds(target.bounds, reason="Messenger contacts prompt skip"):
                    time.sleep(2.5)
                    return True

            skip_spec = SelectorSpec(
                name="Messenger contacts prompt skip",
                texts=[
                    "Lúc khác", "LÃºc khÃ¡c",
                    "Để sau", "Äá»ƒ sau",
                    "Không phải bây giờ", "KhÃ´ng pháº£i bÃ¢y giá»",
                    "Not now", "Skip", "Bỏ qua", "Bá» qua",
                ],
                require_clickable_or_parent=True,
                block_terms=["Bật", "Báºt", "Turn on"],
                preferred_zones=["bottom", "center"],
                min_y_ratio=0.18,
                max_y_ratio=0.95,
                prefer_exact=True,
            )
            if self.device.click_best(skip_spec, reason="Messenger contacts prompt skip"):
                time.sleep(2.5)
                return True

        ok_spec = SelectorSpec(
            name="Messenger prompt OK",
            texts=["OK"],
            require_clickable_or_parent=True,
            allow_contains=False,
            preferred_zones=["bottom", "center"],
            min_y_ratio=0.18,
            max_y_ratio=0.95,
            prefer_exact=True,
        )
        if self.device.click_best(ok_spec, reason="Messenger prompt OK"):
            time.sleep(2.5)
            return True

        actions = [
            "Cho phép", "Cho phÃ©p", "Allow",
            "Lưu", "LÆ°u", "Save", "Lưu thông tin", "LÆ°u thÃ´ng tin", "Save login info",
            "Tiếp tục", "Tiáº¿p tá»¥c", "Continue",
            "Lúc khác", "LÃºc khÃ¡c", "Để sau", "Äá»ƒ sau",
            "Không phải bây giờ", "KhÃ´ng pháº£i bÃ¢y giá»", "Not now",
            "Đã hiểu", "ÄÃ£ hiá»ƒu", "Got it",
        ]

        if any(term in blob for term in real_prompt_terms):
            actions += ["Skip", "Bỏ qua", "Bá» qua"]

        if "khoi phuc" in blob or "restore" in blob:
            close_spec = SelectorSpec(
                name="Messenger restore bottom sheet close",
                texts=[
                    "Nhấp để bỏ qua trang dưới cùng này",
                    "Bỏ qua trang dưới cùng này",
                    "Đóng",
                    "Close",
                    "Dismiss bottom sheet",
                    "Close bottom sheet",
                ],
                require_clickable_or_parent=True,
                block_terms=["Khôi phục ngay", "Restore now"],
                preferred_zones=["bottom", "left", "center"],
                min_y_ratio=0.18,
                max_y_ratio=0.98,
                prefer_exact=False,
            )
            if self.device.click_best(close_spec, reason="Messenger restore bottom sheet close"):
                time.sleep(2.0)
                return True

        block = [
            "Không cho phép", "Don't allow", "Deny", "Từ chối",
            "Bỏ qua gợi ý", "Skip suggestion",
            "Menu lựa chọn khác", "More options",
            "Bật", "Báºt", "Turn on",
            "Khôi phục ngay", "Restore now",
        ]

        spec = SelectorSpec(
            name="Messenger prompt action",
            texts=actions,
            require_clickable_or_parent=True,
            block_terms=block,
            preferred_zones=["bottom", "center"],
            min_y_ratio=0.18,
            max_y_ratio=0.95,
            prefer_exact=False,
        )
        if self.device.click_best(spec, reason="Messenger prompt action"):
            time.sleep(2.5)
            return True
        return False

    def click_continue_under_name_prompt(self, target_name: str = "") -> bool:
        """Fast path for the "Continue as/under name" confirmation dialog.

        handle_simple_prompts() checks OK first, so this dialog otherwise costs
        an extra dump cycle before the generic Continue action is tried.
        """
        nodes = self.device.read_nodes(reason="Messenger continue-under-name fast path")
        if not nodes:
            return False

        target_norm = normalize_text(target_name, remove_accents=True)
        labels_norm = [normalize_text(node.label, remove_accents=True) for node in nodes if node.label]
        title_visible = False
        for label in labels_norm:
            if any(term in label for term in [
                "tiep tuc duoi ten",
                "tiep tuc voi",
                "continue as",
                "continue with",
            ]):
                title_visible = True
                if target_norm and target_norm not in label:
                    print(
                        "[LOGIN] Continue-under-name prompt visible; target name was not in title, "
                        f"target='{target_name}' title='{label}'"
                    )
                break
        if not title_visible:
            return False

        width, height = self.device.get_screen_size(refresh=True)
        candidates: list[tuple[int, UiNode]] = []
        for node in nodes:
            norm = normalize_text(node.label, remove_accents=True)
            if norm not in {"tiep tuc", "continue"}:
                continue
            target = self.device.find_clickable_ancestor(node)
            if not target.enabled:
                continue
            x, y = target.center
            xr = x / max(1, width)
            yr = y / max(1, height)
            if yr < 0.40 or yr > 0.95:
                continue
            score = 700
            score += 120 if target.clickable else 0
            score += 80 if xr >= 0.45 else 0
            score += 40 if yr >= 0.50 else 0
            score -= min(target.depth, 30)
            candidates.append((score, target))

        if not candidates:
            print("[LOGIN] Continue-under-name prompt visible but Continue button was not found.")
            return False

        candidates.sort(key=lambda item: item[0], reverse=True)
        score, target = candidates[0]
        print(
            "[LOGIN] Clicking continue-under-name prompt: "
            f"label='{target.label}' bounds={target.bounds_text} score={score}"
        )
        if self.device.tap_bounds(target.bounds, reason="Messenger continue-under-name prompt"):
            time.sleep(1.2)
            return True
        return False

    def click_messenger_menu(self, *, allow_fallback: bool = True) -> bool:
        """Click Messenger's bottom-right Menu tab only.

        The Messenger home screen may also contain "Menu lựa chọn khác" inside
        chat/suggestion rows. This method ignores those nodes and accepts only
        a bottom navigation tab around the bottom-right area.
        """
        nodes = self.device.read_nodes(reason="Messenger bottom-right Menu tab strict")
        width, height = self.device.get_screen_size(refresh=True)
        dump_w = max((node.bounds[2] for node in nodes), default=0)
        dump_h = max((node.bounds[3] for node in nodes), default=0)
        if dump_w and dump_h:
            width, height = dump_w, dump_h
        candidates: list[tuple[int, UiNode]] = []

        for node in nodes:
            label = node.label
            norm = normalize_text(label, remove_accents=True)
            if not norm:
                continue

            is_menu_tab = (
                "tab menu" in norm
                or ("menu" in norm and re.search(r"\btab\s+\d+/\d+\b", norm))
                or norm == "menu"
            )
            if not is_menu_tab:
                continue

            if "lua chon khac" in norm or "more options" in norm:
                continue

            x, y = node.center
            xr = x / max(1, width)
            yr = y / max(1, height)

            # Hard guard: real Messenger Menu tab lives in bottom-right nav.
            if xr < 0.55 or yr < 0.82:
                continue

            score = 1000
            if "tab menu" in norm:
                score += 400
            if re.search(r"\btab\s+\d+/\d+\b", norm):
                score += 300
            if node.clickable:
                score += 100
            if node.selected:
                score += 50
            score -= min(node.depth, 30)

            candidates.append((score, node))

        if candidates:
            candidates.sort(key=lambda item: item[0], reverse=True)
            score, target = candidates[0]
            print(
                "[SELECT] Messenger bottom-right Menu tab STRICT: "
                f"label='{target.label}' bounds={target.bounds_text} clickable={target.clickable} "
                f"class={target.class_name} score={score}"
            )
            self.device.tap_bounds(target.bounds, reason="Messenger bottom-right Menu tab STRICT")
            time.sleep(float(os.getenv("FB_1_1_MENU_TAB_SETTLE_SECONDS", "0.8")))
            return True

        if not allow_fallback:
            return False

        # Diagnostic: show bottom app nodes so we can see whether Messenger hides the tab label.
        bottom_debug = []
        for node in nodes:
            x, y = node.center
            xr = x / max(1, width)
            yr = y / max(1, height)
            if yr >= 0.82 and getattr(node, "package", "") == MESSENGER_PKG:
                bottom_debug.append(f"{node.label!r}@{node.bounds_text}")
        if bottom_debug:
            print("[SELECT] Bottom Messenger nodes before Menu fallback: " + " | ".join(bottom_debug[:20]))

        print("[SELECT] Messenger bottom-right Menu tab STRICT: not found. Using forced bottom-right fallback.")
        ok = self.device.force_tap_ratio(0.83, 0.91, reason="Messenger bottom-right Menu fallback")
        time.sleep(float(os.getenv("FB_1_1_MENU_TAB_SETTLE_SECONDS", "0.8")))

        # Verify whether fallback really opened Menu. If not, try the exact logical bounds
        # observed from window_dump: [480,1405][720,1510] on a 720x1600 screen.
        # This is scaled to the current dump coordinate system instead of using stale raw pixels.
        if self.is_messenger_menu_screen():
            return True
        dump_w = max((n.bounds[2] for n in nodes), default=width)
        dump_h = max((n.bounds[3] for n in nodes), default=height)
        x = int(dump_w * 0.833)
        y = int(dump_h * 0.91)
        print(f"[SELECT] Menu fallback verification failed. Retrying tap by dump-size coordinate ({x}, {y}) from dump={dump_w}x{dump_h}.")
        self.device.tap(x, y, reason="Messenger bottom-right Menu fallback by dump-size")
        time.sleep(float(os.getenv("FB_1_1_MENU_TAB_SETTLE_SECONDS", "0.8")))
        return True

    def click_messenger_primary_menu(self) -> bool:
        """Click Messenger's top-left main menu button on 3-tab Messenger layouts."""
        if not self.device.is_current_package(MESSENGER_PKG):
            return False

        nodes = self.device.read_nodes(reason="Messenger top-left primary Menu")
        width, height = self.device.get_screen_size(refresh=True)
        dump_w = max((node.bounds[2] for node in nodes), default=0)
        dump_h = max((node.bounds[3] for node in nodes), default=0)
        if dump_w and dump_h:
            width, height = dump_w, dump_h

        candidates: list[tuple[int, UiNode]] = []
        for node in nodes:
            norm = normalize_text(node.label, remove_accents=True)
            if not norm:
                continue
            if "lua chon khac" in norm or "more options" in norm:
                continue

            is_primary_menu = norm in {"menu chinh", "main menu"} or (
                norm == "menu" and "tab" not in norm
            )
            if not is_primary_menu:
                continue

            x, y = node.center
            xr = x / max(1, width)
            yr = y / max(1, height)
            if xr > 0.28 or yr > 0.18:
                continue

            target = self.device.find_clickable_ancestor(node)
            tx, ty = target.center
            txr = tx / max(1, width)
            tyr = ty / max(1, height)
            if txr > 0.32 or tyr > 0.20:
                continue

            score = 900
            if norm == "menu chinh":
                score += 250
            if target.clickable:
                score += 100
            if "button" in target.class_name.lower() or "viewgroup" in target.class_name.lower():
                score += 40
            score -= min(target.depth, 30)
            candidates.append((score, target))

        if not candidates:
            return False

        candidates.sort(key=lambda item: item[0], reverse=True)
        score, target = candidates[0]
        print(
            "[SELECT] Messenger top-left primary Menu: "
            f"label='{target.label}' bounds={target.bounds_text} clickable={target.clickable} "
            f"class={target.class_name} score={score}"
        )
        self.device.tap_bounds(target.bounds, reason="Messenger top-left primary Menu")
        time.sleep(2)
        return True

    def click_message_requests_entry_from_menu(self) -> bool:
        """Click the Message Requests row while Messenger's Menu screen is open."""
        spec = SelectorSpec(
            name="Message Requests menu entry",
            texts=[
                "Tin nhắn đang chờ", "Tin nhan dang cho",
                "Message requests",
                "Yêu cầu nhắn tin", "Yeu cau nhan tin",
                "Lời mời nhắn tin", "Loi moi nhan tin",
            ],
            contains=[
                "Tin nhắn đang chờ", "tin nhan dang cho",
                "Message requests",
                "Yêu cầu nhắn tin", "yeu cau nhan tin",
                "Lời mời nhắn tin", "loi moi nhan tin",
            ],
            require_clickable_or_parent=True,
            block_terms=[
                "cai dat", "settings",
                "cong dong", "community",
                "kho luu tru", "archive",
                "loi moi ket ban", "friend requests",
            ],
            preferred_zones=["center", "top"],
            min_y_ratio=0.18,
            max_y_ratio=0.88,
            allow_contains=True,
            prefer_exact=True,
        )
        if self.device.click_best(spec, reason="Message Requests menu entry"):
            time.sleep(2)
            return True
        return False

    def get_profile_name(self, allowed_names: Optional[list[str]] = None) -> str:
        """
        Enter profile and extract name with robust scoring.
        Prioritizes names in allowed_names (from device config).
        """
        if not self.device.is_current_package(FACEBOOK_PKG):
            return ""

        allowed_norms = {}
        if allowed_names:
            for n in allowed_names:
                norm = normalize_text(n, remove_accents=True)
                if norm:
                    allowed_norms[norm] = n

        # 1. Click entry to profile (usually on Home screen)
        spec = SelectorSpec(
            name="Facebook Profile entry",
            texts=["Đi tới trang cá nhân", "Go to profile", "Trang cá nhân của bạn"],
            contains=["Trang cá nhân", "Profile"],
            preferred_zones=["top_left", "top"],
            max_y_ratio=0.35,
            allow_contains=True,
        )
        print("[ACCOUNT] Navigating to profile to identify account name...")
        # Attempt to find button
        if not self.device.click_best(spec, reason="navigate to profile"):
            # Fallback tap top-left area where profile icon usually resides
            self.device.tap_ratio(0.12, 0.18, reason="profile area fallback tap")

        time.sleep(4)
        self.device.collapse_statusbar_if_open()

        # 2. Extract name from profile header
        nodes = self.device.read_nodes(reason="read profile name")
        width, height = self.device.get_screen_size()
        dump_w = max((node.bounds[2] for node in nodes), default=0)
        dump_h = max((node.bounds[3] for node in nodes), default=0)
        if dump_w and dump_h:
            width, height = dump_w, dump_h
        candidates: list[tuple[int, UiNode]] = []
        profile_picture_bottom = 0
        metrics_top = 0
        for node in nodes:
            node_norm = normalize_text(node.all_text, remove_accents=True)
            if "profile picture" in node_norm or "anh dai dien" in node_norm:
                profile_picture_bottom = max(profile_picture_bottom, node.bounds[3])
            if any(term in node_norm for term in ["nguoi ban", "bai viet", "friends", "posts"]):
                metrics_top = min(metrics_top or node.bounds[1], node.bounds[1])
        
        # Blacklist terms that are definitely not the account name
        blacklist = {
            "bài viết", "ảnh", "bạn bè", "theo dõi", "giới thiệu", "chỉnh sửa", 
            "posts", "photos", "friends", "followers", "about", "edit",
            "xem thêm", "see more", "story", "reels", "thông tin", "video",
            "cover photo", "profile picture", "ảnh bìa", "ảnh đại diện",
            "avatar", "edit profile", "chỉnh sửa trang cá nhân", "tạo", "create",
            "thêm", "add", "lời chào", "greeting", "nổi bật", "featured"
        }

        for node in nodes:
            label = node.text or node.desc
            if not label or len(label) < 2 or len(label) > 60:
                continue
            
            label_low = label.lower()
            label_norm = normalize_text(label, remove_accents=True)
            blacklist_norm = [
                "bai viet", "ban be", "theo doi", "gioi thieu", "chinh sua",
                "posts", "photos", "friends", "followers", "about", "edit",
                "xem them", "see more", "story", "reels", "thong tin", "video",
                "cover photo", "profile picture", "anh bia", "anh dai dien",
                "avatar", "edit profile", "chinh sua trang ca nhan", "tao", "create",
                "tao ghi chu", "chia se suy nghi", "chia se ghi chu", "chia se bai hat",
                "them", "add", "loi chao", "greeting", "noi bat", "featured",
                "nguoi ban",
                "truong", "dai hoc", "hoc vien", "university", "college", "school",
                "ha noi", "tp.", "thanh pho", "que quan", "song tai", "den tu",
            ]
            if (
                is_profile_note_prompt_label(label)
                or any(term in label_low for term in blacklist)
                or any(term in label_norm for term in blacklist_norm)
            ):
                continue
            if "·" in label or "Â·" in label or "|" in label:
                continue
            if re.fullmatch(r"[\d,.]+[km]?", label_norm):
                continue
                
            x, y = node.center
            yr = y / height
            if profile_picture_bottom and not metrics_top and y > profile_picture_bottom + int(height * 0.010):
                continue
            if metrics_top and node.bounds[1] >= metrics_top:
                continue
            if not profile_picture_bottom and not metrics_top and yr > 0.34:
                continue
            
            # Tên tài khoản thường là TextView, không phải Button to đùng như Cover Photo
            is_button = "button" in node.class_name.lower()
            
            # Logic: Header name thường nằm ở vùng yr từ 0.30 đến 0.48 (dưới ảnh bìa, trên nút chỉnh sửa)
            # Ratios adjusted to hit the big name above "Add Story" / "Edit Profile".
            # New centered-avatar profile layouts can place the name just below
            # the avatar at ~0.52. Only accept that lower row when the metrics
            # row is visible below it, which keeps compact old headers stable.
            header_bottom_ratio = 0.56 if metrics_top else 0.50
            if 0.16 <= yr <= header_bottom_ratio:
                score = 80
                if not is_button:
                    score += 50 # Ưu tiên node văn bản thuần túy
                else:
                    score -= 40 # Phạt node là Button (tránh nút Cover Photo)

                words = label.split()
                
                # Proper name bonus (Capitalized words)
                if all(w[0].isupper() for w in words if w and w[0].isalpha()):
                    score += 50
                    
                if 2 <= len(words) <= 4:
                    score += 50
                    
                # Allowed name bonus (Huge priority)
                norm = normalize_text(label, remove_accents=True)
                if norm in allowed_norms:
                    score += 1000
                if 0.17 <= yr <= 0.26 and x >= int(width * 0.22):
                    score += 220
                if 0.26 < yr <= 0.34 and x >= int(width * 0.18):
                    score += 120
                if y > int(height * 0.25) and node.bounds[0] <= int(width * 0.08) and node.width > int(width * 0.60):
                    score -= 180
                
                score += min(node.area // 10000, 30)
                candidates.append((score, node))

        name = ""
        if candidates:
            candidates.sort(key=lambda x: x[0], reverse=True)
            name = candidates[0][1].text or candidates[0][1].desc
            print(f"[ACCOUNT] [STEP 2] Final name chosen from profile header: '{name}'")

        # Bỏ qua press_back để tránh văng về Launcher điện thoại. 
        # Hàm normalize_facebook_home_for_messenger sẽ lo việc nhấn Tab Home.
        if not name:
            fallback_candidates: list[tuple[int, str, UiNode, Optional[str]]] = []
            for node in nodes:
                for label in [node.text, node.desc]:
                    if not label or not self._looks_like_account_name(label):
                        continue
                    x1, y1, x2, y2 = node.bounds
                    if y1 < 80 or y1 > int(height * 0.52):
                        continue

                    words = label.split()
                    score = 0
                    if len(words) >= 2:
                        proper = all((w and (not w[0].isalpha() or w[0].isupper())) for w in words)
                        score += 100 if proper else 60
                    elif len(words) == 1:
                        score += 70 if label and label[0].isupper() else 30
                    if 3 <= len(words) <= 4:
                        score += 35
                    elif len(words) == 2:
                        score += 10
                    if "button" in node.class_name.lower():
                        score -= 70
                    if y1 <= int(height * 0.36):
                        score += 150
                    elif y1 <= int(height * 0.50):
                        score += 60
                    center_x = (x1 + x2) // 2
                    if int(width * 0.18) <= center_x <= int(width * 0.78):
                        score += 25
                    if 80 <= (x2 - x1) <= int(width * 0.75):
                        score += 20

                    match_bonus, matched_name = person_name_match_score(label, allowed_names or [])
                    if match_bonus < 200 or not matched_name:
                        continue
                    score += match_bonus
                    fallback_candidates.append((score, label, node, matched_name))

            if fallback_candidates:
                fallback_candidates.sort(key=lambda item: (-item[0], item[2].bounds[1]))
                score, label, node, matched_name = fallback_candidates[0]
                name = matched_name or label
                print(
                    "[ACCOUNT] [STEP 2B] Fallback profile name chosen: "
                    f"'{name}' from label='{label}' bounds={node.bounds_text} score={score}"
                )

        return name

    def click_chats_tab(self) -> bool:
        spec = SelectorSpec(
            name="Messenger Chats tab",
            texts=["Đoạn chat", "Chats", "Chat"],
            contains=["Đoạn chat", "Chats"],
            preferred_zones=["bottom_left", "top_left"],
            allow_contains=True,
        )
        spec.texts.extend(["Đoạn chat", "Doan chat"])
        spec.contains.extend(["Đoạn chat", "Doan chat"])
        if self.device.click_best(spec, reason="Messenger Chats tab"):
            time.sleep(0.6)
            return True
        return self.device.tap_ratio(0.22, 0.92, reason="Messenger bottom-left Chats fallback")

    def click_unread_tab(self, timeout: int = 8) -> bool:
        """Click the Chats filter chip "Chưa đọc"/Unread without touching Menu."""
        deadline = time.time() + max(1, timeout)
        attempted_reveal = False
        force_revealed_after_chats = False

        def is_unread_filter_label(label: str) -> bool:
            norm = normalize_text(label, remove_accents=True)
            norm = re.sub(r"\s+", " ", norm).strip(" ,.")
            if norm in {"chua doc", "unread"}:
                return True
            if norm.startswith("chua doc") and len(norm) <= 24:
                return True
            if norm.startswith("unread") and len(norm) <= 24:
                return True
            return False

        while time.time() < deadline:
            if not force_revealed_after_chats:
                force_revealed_after_chats = True
                attempted_reveal = True
                print("[SELECT] Pulling chat list down before searching Messenger Unread tab.")
                self.device.swipe_ratio(0.50, 0.48, 0.50, 0.70, 260, reason="reveal Messenger Unread tab")
                time.sleep(1.2)

            nodes = self.device.read_nodes(reason="Messenger Unread tab")
            width, height = self.device.get_screen_size(refresh=True)
            dump_w = max((node.bounds[2] for node in nodes), default=0)
            dump_h = max((node.bounds[3] for node in nodes), default=0)
            if dump_w and dump_h:
                width = dump_w
                height = dump_h

            candidates: list[tuple[int, UiNode]] = []
            for node in nodes:
                if not is_unread_filter_label(node.label):
                    continue

                x, y = node.center
                if y / max(1, height) > 0.76:
                    continue
                if x / max(1, width) > 0.70:
                    continue

                score = 1000
                if "tab" in node.class_name.lower():
                    score += 200
                if node.clickable:
                    score += 100
                parent = self.device.node_by_index(node.parent_index)
                target = parent if parent and parent.clickable and bounds_contains(parent.bounds, node.bounds) else node
                if node.selected:
                    print("[SELECT] Messenger Unread tab is already selected.")
                    return True
                score -= min(target.depth, 30)
                candidates.append((score, target))

            if candidates:
                candidates.sort(key=lambda item: item[0], reverse=True)
                score, target = candidates[0]
                print(
                    "[SELECT] Messenger Unread tab: "
                    f"label='{target.label}' bounds={target.bounds_text} clickable={target.clickable} "
                    f"class={target.class_name} score={score}"
                )
                self.device.tap_bounds(target.bounds, reason="Messenger Unread tab")
                time.sleep(2)
                return True

            if attempted_reveal:
                break
            attempted_reveal = True
            print("[SELECT] Messenger Unread tab not visible. Pulling chat list down to reveal filter tabs.")
            self.device.swipe_ratio(0.50, 0.48, 0.50, 0.70, 260, reason="reveal Messenger Unread tab")
            time.sleep(1.2)

        print("[SELECT] Messenger Unread tab: not found. No fallback tap used to avoid opening a chat row.")
        return False

    def open_message_requests(self, timeout: int = 8) -> bool:
        print("[REQUESTS] open_message_requests started.")
        timeout = int(os.getenv("FB_1_1_OPEN_MESSAGE_REQUESTS_TIMEOUT_SECONDS", str(timeout)))
        if not self.device.is_current_package(MESSENGER_PKG):
            if not self.open_messenger_from_facebook():
                return False

        # Do not start Message Requests navigation while Messenger is still on
        # create/confirm PIN. The log showed "Xác nhận mã PIN" remained visible
        # but the tool continued into Message Requests checks.
        if self.handle_pin_if_visible():
            time.sleep(1)
            return False

        # Fast path: if the previous action already opened Message Requests,
        # do not tap the bottom Menu tab again. That tab is still visible in
        # some dumps and would navigate away from the request list.
        if self.is_message_requests_list():
            print("[REQUESTS] Message Requests list already open.")
            return True
        if self.is_message_request_detail():
            print("[REQUESTS] Message Request detail already open.")
            return True

        opened_menu_from_home = False

        # Fast path: on Messenger Home the bottom Menu tab is already visible.
        # Click it before generic prompt handling so Home does not spend an
        # extra dump cycle logging "Messenger prompt action: not found".
        if self.click_messenger_menu(allow_fallback=False):
            opened_menu_from_home = True
        elif self.click_messenger_primary_menu():
            opened_menu_from_home = True
        else:
            self.handle_simple_prompts()

        if not opened_menu_from_home and self.is_messenger_home():
            print("[REQUESTS] Messenger home detected. Clicking Menu before searching requests.")
            if self.click_messenger_primary_menu() or self.click_messenger_menu():
                opened_menu_from_home = True
            else:
                print("[REQUESTS] Messenger Menu click failed from Messenger home.")

        direct = SelectorSpec(
            name="Message Requests direct",
            texts=[
                "Tin nhắn đang chờ", "Tin nhan dang cho",
                "Message requests", "Yêu cầu nhắn tin", "Yeu cau nhan tin",
                "Lời mời nhắn tin", "Loi moi nhan tin",
            ],
            contains=[
                "Tin nhắn đang chờ", "tin nhan dang cho",
                "Message requests", "Yêu cầu nhắn tin", "yeu cau nhan tin",
                "Lời mời nhắn tin", "loi moi nhan tin",
            ],
            preferred_zones=["center", "top", "bottom"],
            allow_contains=True,
        )
        deadline = time.time() + max(2, timeout)
        opened_menu = opened_menu_from_home
        while time.time() < deadline:
            if self.device.current_package() != MESSENGER_PKG:
                print("[REQUESTS] Stop opening Message Requests because Messenger is no longer foreground.")
                return False

            if self.handle_pin_if_visible():
                time.sleep(1)
                return False

            if self.is_message_requests_list():
                print("[REQUESTS] Message Requests list detected.")
                return True
            if self.is_messenger_menu_screen():
                print("[REQUESTS] Messenger Menu screen detected. Clicking Message Requests entry.")
                if self.click_message_requests_entry_from_menu():
                    wait_until = time.time() + float(os.getenv("FB_1_1_MESSAGE_REQUESTS_OPEN_SETTLE_SECONDS", "2.5"))
                    while time.time() < wait_until:
                        if self.is_message_requests_list() or self.is_message_request_detail():
                            return True
                        time.sleep(0.4)
                    continue
            if self.device.click_best(direct, reason="Open Message Requests"):
                wait_until = time.time() + float(os.getenv("FB_1_1_MESSAGE_REQUESTS_OPEN_SETTLE_SECONDS", "2.5"))
                while time.time() < wait_until:
                    if self.is_message_requests_list() or self.is_message_request_detail():
                        return True
                    time.sleep(0.4)
                continue
            if not opened_menu:
                opened_menu = self.click_messenger_primary_menu() or self.click_messenger_menu()
                time.sleep(0.8)
                continue
            # Some Messenger builds put requests after scrolling menu a little.
            self.device.swipe_ratio(0.50, 0.78, 0.50, 0.45, 260, reason="scroll Messenger menu for Message Requests")
            time.sleep(0.6)
        visible = self.device.visible_debug_line(reason="open message requests failed")
        print(f"[REQUESTS] Could not open Message Requests. Visible labels: {visible}")
        return False

    def _ignored_row_label(self, label: str) -> bool:
        norm = normalize_text(label, remove_accents=True)
        if "com.facebook.orca:id" in norm or "android:id/content" in norm:
            return True
        ignored = [
            "tin nhan dang cho", "message requests", "yeu cau nhan tin", "loi moi nhan tin",
            "ban co the biet", "you may know", "spam", "quay lai", "back",
            "hay mo doan chat", "chon ai co the nhan tin", "chi khi ban tra loi", "nguoi gui",
            "tim kiem", "search", "menu", "doan chat", "chats", "chat",
            "chap nhan", "accept", "block", "xoa", "delete", "tu choi", "decline",
            "meta ai", "nhap bang giong noi", "keyboard", "phim",
            "cai dat", "settings", "cong dong", "community", "kho luu tru", "archive",
            "loi moi ket ban", "friend requests", "loi moi tham gia kenh", "channel invite",
            "chuyen trang ca nhan",
            "dang hoat dong", "active now", "active status",
            "marketplace", "xem them", "learn more",
            "chat voi ai", "tao ai", "meta ai",
            "tao tin", "share_a_song", "drop_a_thought", "ghi chu", "tin cua ban",
            "story", "note",
            "cung cua meta", "facebook reels", "su kien tren facebook", "hen ho tren facebook",
        ]
        if any(term in norm for term in ignored):
            return True
        if norm in {"chan", "block", "blocked"}:
            return True
        if re.fullmatch(r"\d+", norm):
            return True
        return False

    def _is_system_message_preview(self, text: str) -> bool:
        norm = normalize_text(text, remove_accents=True)
        if not norm:
            return False
        system_terms = [
            "tin nhan va cuoc goi duoc bao mat bang tinh nang ma hoa dau cuoi",
            "chi nhung nguoi tham gia doan chat nay moi co the doc nghe hoac chia se",
            "ma hoa dau cuoi",
            "end to end encrypted",
            "end-to-end encrypted",
            "end to end encryption",
            "end-to-end encryption",
        ]
        return any(term in norm for term in system_terms)

    def _message_requests_content_min_y(self, nodes: Optional[list[UiNode]] = None) -> int:
        nodes = nodes or self.device.read_nodes(reason="message requests content start")
        _, height = self.device.get_screen_size()
        min_y = int(height * 0.16)
        for node in nodes:
            norm = normalize_text(node.label, remove_accents=True)
            if not norm:
                continue
            _, _, _, y2 = node.bounds
            if any(term in norm for term in [
                "hay mo doan chat",
                "chi khi ban tra loi",
                "chon ai co the nhan tin",
                "open the chat",
                "only if you reply",
                "control who can message you",
            ]):
                min_y = max(min_y, y2)
            elif any(term in norm for term in ["ban co the biet", "you may know", "spam"]):
                min_y = max(min_y, y2)
        return min_y

    def clickable_message_request_rows(self) -> list[dict[str, Any]]:
        nodes = self.device.read_nodes(reason="clickable message request rows")
        width, height = self.device.get_screen_size()
        min_y = self._message_requests_content_min_y(nodes)
        rows: list[dict[str, Any]] = []
        for node in nodes:
            label = safe_str(node.label)
            if not label:
                continue
            x1, y1, x2, y2 = node.bounds
            if y1 < min_y - 3 or y2 > int(height * 0.91):
                continue
            if x1 > int(width * 0.08) or x2 < int(width * 0.82):
                continue
            if (x2 - x1) < int(width * 0.55) or (y2 - y1) < 44:
                continue
            if not node.clickable and "button" not in normalize_text(node.class_name, remove_accents=True):
                continue

            norm = normalize_text(label, remove_accents=True)
            if any(term in norm for term in [
                "tin nhan dang cho", "ban co the biet", "spam",
                "hay mo doan chat", "chon ai co the nhan tin",
                "hoi meta ai", "tao tin", "share_a_song", "drop_a_thought", "ghi chu",
                "dang hoat dong", "active now",
            ]):
                continue

            sender = label.split(",", 1)[0].strip()
            if not sender or self._ignored_row_label(sender):
                continue

            preview = ""
            if "," in label:
                raw_preview = label.split(",", 1)[1].strip()
                if not self._is_system_message_preview(raw_preview):
                    preview = raw_preview
            row_key = f"{normalize_text(sender, remove_accents=True)}:{x1}:{y1}:{x2}:{y2}"[:180]
            rows.append({
                "row_key": row_key,
                "label": label,
                "sender": sender.rstrip(". "),
                "preview": preview,
                "time": "",
                "bounds": node.bounds,
                "center_y": node.center[1],
                "node_count": 1,
                "parsed": {
                    "sender": sender.rstrip(". "),
                    "content": preview,
                    "time": "",
                },
            })

        rows.sort(key=lambda row: row["center_y"])
        return rows

    def group_text_rows(self) -> list[dict[str, Any]]:
        nodes = self.device.read_nodes(reason="group message request rows")
        width, height = self.device.get_screen_size()
        min_y = self._message_requests_content_min_y(nodes)
        usable: list[UiNode] = []
        for node in nodes:
            label = node.label
            if not label or self._ignored_row_label(label):
                continue
            x1, y1, x2, y2 = node.bounds
            cy = node.center[1]
            if cy < min_y or cy > int(height * 0.86):
                continue
            if x2 < int(width * 0.12):
                continue
            usable.append(node)

        usable.sort(key=lambda n: (n.center[1], n.bounds[0]))
        groups: list[list[UiNode]] = []
        threshold = max(34, int(height * 0.018))
        for node in usable:
            placed = False
            for group in groups:
                avg_y = sum(item.center[1] for item in group) / len(group)
                if abs(node.center[1] - avg_y) <= threshold:
                    group.append(node)
                    placed = True
                    break
            if not placed:
                groups.append([node])

        rows: list[dict[str, Any]] = []
        for group in groups:
            group.sort(key=lambda n: (n.bounds[0], n.center[1]))
            combined = " | ".join(node.label for node in group if node.label)
            if not combined or self._ignored_row_label(combined):
                continue
            ub = union_bounds(node.bounds for node in group)
            if not ub:
                continue
            y_center = bounds_center(ub)[1]
            # Avoid too-thin single utility labels.
            if len(combined) < 2:
                continue
            row_key = normalize_text(combined, remove_accents=True)[:180]
            labels = [node.label for node in group if node.label]
            sender = labels[0] if labels else ""
            preview = " | ".join(labels[1:]) if len(labels) > 1 else ""
            if self._is_system_message_preview(preview):
                preview = ""
            rows.append({
                "row_key": row_key,
                "label": combined,
                "sender": sender,
                "preview": preview,
                "time": "",
                "bounds": ub,
                "center_y": y_center,
                "node_count": len(group),
                "parsed": {
                    "sender": sender,
                    "content": preview,
                    "time": "",
                },
            })

        rows.sort(key=lambda row: row["center_y"])
        return rows

    def open_first_visible_message_request_row(self, skipped_row_keys: Optional[set[str]] = None) -> Optional[dict[str, Any]]:
        skipped_row_keys = skipped_row_keys or set()
        if self.is_messenger_menu_screen() and not self._has_message_requests_list_chrome():
            print("[REQUESTS] Still on Messenger Menu; refusing to open menu rows as request conversations.")
            return None
        rows = self.clickable_message_request_rows() or self.group_text_rows()
        if not rows:
            print("[REQUESTS] No real message request row found on list.")
            return None
        width, _ = self.device.get_screen_size()
        for row in rows:
            if row["row_key"] in skipped_row_keys:
                continue
            x1, y1, x2, y2 = row["bounds"]
            y = (y1 + y2) // 2
            # Dynamic row click. X is centered in content area, Y is derived from the row dump.
            x = min(max(width // 2, x1 + 20), width - 20)
            print(f"[REQUESTS] Opening first request row: {row['label']} | tap=({x},{y}) | bounds={row['bounds']}")
            self.device.tap(x, y, reason="open first Message Request row")
            deadline = time.time() + 4
            while time.time() < deadline:
                if self.is_current_request_unreachable():
                    return row
                if self.is_message_request_detail():
                    return row
                if not self.is_message_requests_list() and not self.is_messenger_home():
                    return row
                time.sleep(0.7)
            print("[REQUESTS] Row tap did not open a request detail. Skipping this row.")
            return None
        print("[REQUESTS] No request row candidate found.")
        return None

    def accept_current_request(self, timeout: int = 10) -> bool:
        self._last_unreachable_request_deleted = False
        deadline = time.time() + max(1, timeout)
        accept = SelectorSpec(
            name="Accept Message Request",
            texts=["Chấp nhận", "Chap nhan", "Accept", "Đồng ý", "Dong y"],
            contains=["Chấp nhận", "Accept"],
            block_terms=["Không chấp nhận", "Từ chối", "Decline", "Delete", "Xóa", "Block", "Chặn"],
            priority_terms=["Chấp nhận", "Accept"],
            preferred_zones=["bottom_right", "bottom", "right"],
            min_y_ratio=0.48,
            allow_contains=True,
        )
        while time.time() < deadline:
            if self.device.click_best(accept, reason="Accept Message Request"):
                time.sleep(2)
                return True
            if self.delete_unreachable_message_request_if_open():
                self._last_unreachable_request_deleted = True
                return False
            time.sleep(0.8)
        print("[REQUESTS] Accept button not found within timeout.")
        return False

    def return_to_message_requests_list(self, timeout: int = 10) -> bool:
        if self.is_message_requests_list():
            return True
        deadline = time.time() + max(1, timeout)
        while time.time() < deadline:
            if not self.device.is_current_package(MESSENGER_PKG):
                self.open_messenger_app()
            self.device.press_back(reason="return to Message Requests list")
            time.sleep(1.2)
            if self.is_message_requests_list():
                return True
            if self.open_message_requests(timeout=4):
                return True
        return False

    def crawl_message_requests(
        self,
        max_requests: int = 30,
        on_request_accepted: Optional[Callable[[dict[str, Any]], Any]] = None,
    ) -> int:
        if not self.open_message_requests():
            return 0
        processed = 0
        no_progress = 0
        skipped: set[str] = set()
        for index in range(max(1, max_requests)):
            print(f"[REQUESTS] Processing request {index + 1}/{max_requests}")
            row = self.open_first_visible_message_request_row(skipped_row_keys=skipped)
            if not row:
                if self.is_message_requests_list():
                    print("[REQUESTS] Message Requests list is empty; returning to Chats immediately.")
                    if self.click_chats_tab():
                        self.click_unread_tab()
                break
            skipped.add(row.get("row_key", ""))
            saved_before_accept = False
            if on_request_accepted and os.getenv("FB_1_1_SAVE_MESSAGE_REQUEST_BEFORE_ACCEPT", "1").lower() not in {"0", "false", "no", "off"}:
                try:
                    on_request_accepted(row)
                    saved_before_accept = True
                except Exception as exc:
                    print(f"[REQUESTS] pre-accept message request save callback failed: {exc}")
            if not self.accept_current_request(timeout=10):
                if self._last_unreachable_request_deleted:
                    no_progress = 0
                    self.return_to_message_requests_list(timeout=6)
                    continue
                no_progress += 1
                self.return_to_message_requests_list(timeout=6)
                if no_progress >= 3:
                    print("[REQUESTS] Stop crawl after 3 rounds without Accept.")
                    break
                continue
            processed += 1
            no_progress = 0
            if on_request_accepted and not saved_before_accept:
                try:
                    on_request_accepted(row)
                except Exception as exc:
                    print(f"[REQUESTS] on_request_accepted callback failed: {exc}")
            if not self.return_to_message_requests_list(timeout=8):
                if not self.open_message_requests(timeout=8):
                    break
        print(f"[REQUESTS] Crawl done. accepted={processed}")
        if self.is_message_requests_list():
            print("[REQUESTS] Returning to Messenger Chats tab after Message Requests crawl.")
            if self.click_chats_tab():
                self.click_unread_tab()
        return processed


class ExistingMessengerAdapter:
    """Adapter that lets this rebuilt dump layer patch MobileOneToOneMessenger."""

    def __init__(self, messenger: Any) -> None:
        self.messenger = messenger
        self._fallback_driver: Any = None
        adb_path = getattr(messenger, "adb_path", None) or DumpUiDevice.resolve_adb_path()
        device_id = getattr(messenger, "active_device", None)
        local_dump_path = None
        if hasattr(messenger, "_window_dump_local_path"):
            try:
                local_dump_path = messenger._window_dump_local_path()
            except Exception:
                local_dump_path = DEFAULT_LOCAL_DUMP_PATH
        self.device = DumpUiDevice(adb_path=adb_path, device_id=device_id, local_dump_path=local_dump_path)
        self.device.fallback_hierarchy_provider = self._fallback_dump_hierarchy
        self.bot = MessengerDumpBot(self.device)
        self.bot.owner = messenger

    def _fallback_dump_hierarchy(self) -> str:
        """Fallback used when `adb shell uiautomator dump` cannot write XML.

        This keeps the debug contract intact: the XML is still written to the
        same local `window_dump.xml`, but it is sourced from the existing
        uiautomator2 driver instead of `adb pull`.
        """
        driver = None
        try:
            if hasattr(self.messenger, "connect_ui_driver"):
                driver = self.messenger.connect_ui_driver()
        except Exception as exc:
            print(f"[DUMP] Cannot connect fallback UI driver: {exc}")
            driver = None
        if driver is None:
            driver = self._connect_uiautomator2_fallback_driver()
        if driver is None:
            return ""
        for kwargs in ({}, {"compressed": True}):
            try:
                xml = safe_str(driver.dump_hierarchy(**kwargs))
            except TypeError:
                if kwargs:
                    continue
                xml = safe_str(driver.dump_hierarchy())
            except Exception as exc:
                print(f"[DUMP] fallback dump_hierarchy failed: {exc}")
                continue
            if xml and "<node" in xml:
                return xml
        return ""

    def _connect_uiautomator2_fallback_driver(self) -> Any:
        cached = self._fallback_driver
        if cached is not None:
            return cached
        device_id = safe_str(getattr(self.messenger, "active_device", "")) or self.device.device_id
        try:
            import uiautomator2 as u2
        except Exception as exc:
            print(f"[DUMP] uiautomator2 fallback is unavailable: {exc}")
            return None
        try:
            self._fallback_driver = u2.connect(device_id) if device_id else u2.connect()
            print(f"[DUMP] Connected uiautomator2 fallback driver for {device_id or 'default device'}.")
            return self._fallback_driver
        except Exception as exc:
            print(f"[DUMP] Cannot connect uiautomator2 fallback driver: {exc}")
            return None

    def refresh(self) -> None:
        self.device.adb_path = str(getattr(self.messenger, "adb_path", self.device.adb_path) or self.device.adb_path)
        active = safe_str(getattr(self.messenger, "active_device", ""))
        if active:
            self.device.set_device(active)

    def patch(self, *, patch_crawl: bool = True) -> Any:
        m = self.messenger
        adapter = self

        def _dump_visible_ui_labels(this, driver=None, limit: int = 20):
            adapter.refresh()
            return adapter.device.labels(limit=limit, reason="patched _dump_visible_ui_labels")

        def _read_visible_ui_text_blob(this, driver=None):
            adapter.refresh()
            return adapter.device.text_blob(reason="patched _read_visible_ui_text_blob")

        def _find_best_ui_text_node(this, driver, text_options, allow_contains=True, reason=""):
            adapter.refresh()
            spec = SelectorSpec(
                name=reason or "patched find text",
                texts=list(text_options or []),
                allow_contains=allow_contains,
                priority_terms=["MỞ MESSENGER", "Open Messenger", "OPEN MESSENGER", "Chấp nhận", "Accept"],
                weak_terms=["Tin nhắn", "Messages", "Chats"],
            )
            return adapter.device.find_best_node(spec, reason=reason or "patched find")

        def _click_best_ui_text(this, driver, text_options, allow_contains=True, reason=""):
            adapter.refresh()
            return adapter.device.click_text(list(text_options or []), allow_contains=allow_contains, reason=reason or "patched click")

        def _click_window_dump_text(this, text_options, allow_contains=True, reason=""):
            adapter.refresh()
            return adapter.device.click_text(list(text_options or []), allow_contains=allow_contains, reason=reason or "patched window_dump click")

        def refresh_window_dump_root(this, reason=""):
            adapter.refresh()
            return adapter.device.fresh_dump(reason=reason or "patched refresh_window_dump_root")

        def click_facebook_messenger_icon(this):
            adapter.refresh()
            dev_id = adapter.device.device_id
            print(f"[DUMP_AUTOMATION] [{dev_id}] >>> START: Task to open Messenger icon")
            
            # Chỉ giữ lại 1 cách xử lý duy nhất và mạnh nhất: Normalize + Click
            success = adapter.bot.normalize_facebook_home_for_messenger()
            
            print(f"[DUMP_AUTOMATION] [{dev_id}] <<< END: Task open Messenger result: {success}")
            return success

        def open_message_requests_screen(this):
            adapter.refresh()
            return adapter.bot.open_message_requests()

        def open_first_visible_message_request_row(this, driver=None, skipped_row_keys=None):
            adapter.refresh()
            return adapter.bot.open_first_visible_message_request_row(skipped_row_keys=skipped_row_keys or set())

        def accept_visible_message_request_if_open(this, driver=None, timeout: int = 10):
            adapter.refresh()
            return adapter.bot.accept_current_request(timeout=timeout)

        def _return_to_message_requests_list(this, driver=None):
            adapter.refresh()
            return adapter.bot.return_to_message_requests_list()

        def click_messenger_chats_tab(this, driver=None):
            adapter.refresh()
            return adapter.bot.click_chats_tab()

        def complete_messenger_auto_login_from_facebook(this, timeout=35, account_snapshot=None):
            """Bản vá thay thế hoàn toàn luồng auto-login cũ để xử lý popup tốt hơn."""
            adapter.refresh()
            deadline = time.time() + timeout
            snapshot = account_snapshot or this.get_preferred_current_account_snapshot()

            while time.time() < deadline:
                pkg = adapter.device.current_package()
                
                # Check popup hệ thống liên tục.
                if adapter.bot.handle_system_permission_dialog():
                    time.sleep(1)
                    continue

                if pkg == MESSENGER_PKG:
                    # Ưu tiên tuyệt đối xử lý PIN trước backup_intro/simple_prompts.
                    # Màn "Xác nhận mã PIN" có layout giống màn tạo PIN, nên nếu để
                    # prompt handler chạy trước thì nó dễ chỉ dump lặp mà không nhập lại PIN.
                    blob_raw = adapter.device.text_blob(reason="check pin entry during auto-login")
                    blob = normalize_text(blob_raw, remove_accents=True)

                    # If the bottom nav already exposes the real Menu tab,
                    # Messenger is on its home surface. Stop prompt/login
                    # polling so the caller can immediately open the Menu.
                    if "tab menu" in blob or adapter.bot.has_bottom_messenger_menu_tab():
                        print("[LOGIN] Messenger home detected by bottom Menu tab.")
                        return True

                    if adapter.bot._blob_has_blocking_pin_prompt(blob) and any(term in blob for term in [
                        "android.widget.edittext", "edittext",
                        "truong ma khoa", "chinh sua 1/6", "chinh sua 1/5",
                        "ma pin", "tao ma pin", "thiet lap ma pin",
                        "create pin", "set up pin",
                        "xac nhan ma pin", "confirm pin", "confirm your pin", "nhap lai ma pin"
                    ]):
                        if adapter.bot._blob_has_blocking_pin_prompt(blob) and hasattr(this, "handle_messenger_pin_prompt_if_visible"):
                            this.handle_messenger_pin_prompt_if_visible(driver=None)
                            time.sleep(1)
                            continue

                    snapshot_name = snapshot.get("displayName") or snapshot.get("name") or snapshot.get("username") or ""
                    if adapter.bot.click_continue_under_name_prompt(snapshot_name):
                        continue

                    # Fast path: account chooser is not a generic prompt. Select
                    # the matched account as soon as the switch-profile screen is visible.
                    chooser_header_visible = any(term in blob for term in [
                        "chuyen trang ca nhan",
                        "tai khoan facebook khac",
                        "dang nhap bang tai khoan khac",
                        "switch profile",
                        "switch account",
                    ])
                    chooser_row_visible = any(term in blob for term in [
                        "chuyen sang",
                        "continue as",
                        "switch to",
                    ])
                    if chooser_header_visible and chooser_row_visible:
                        print("[LOGIN] Messenger account chooser detected. Selecting matching account before generic prompts.")
                        if this.select_matching_messenger_account(snapshot, timeout=4):
                            continue

                    # Chỉ xử lý intro sau khi chắc chắn chưa vào form PIN.
                    if adapter.bot.handle_messenger_backup_intro():
                        time.sleep(1)
                        continue

                    if adapter.bot.handle_simple_prompts():
                        time.sleep(1)
                        continue

                    if adapter.bot.is_messenger_home():
                        return True
                    if this.select_matching_messenger_account(snapshot, timeout=8):
                        continue
                elif pkg == FACEBOOK_PKG:
                    # Nếu kẹt ở Facebook (màn hình MỞ MESSENGER trung gian)
                    if adapter.bot.click_facebook_messenger_cta():
                        time.sleep(3)
                        continue
                
                time.sleep(2)
            
            return adapter.bot.is_messenger_home()

        def select_matching_messenger_account(this, account_snapshot: dict = None, timeout=20):
            """Sử dụng Dump-first để chọn đúng tài khoản trong danh sách chuyển đổi."""
            adapter.refresh()
            # 1. Lấy snapshot tài khoản đã nhận diện từ bước nhận diện Profile trước đó
            snapshot = account_snapshot or getattr(this, "current_facebook_account_snapshot", {})
            if not snapshot:
                snapshot = this.get_preferred_current_account_snapshot()
            
            # 2. Lấy tên hiển thị chính xác nhất để đối soát (tránh token hóa để không nhầm Lin/Linh)
            target_name = snapshot.get("displayName") or snapshot.get("name") or snapshot.get("username")
            if not target_name:
                print("[DUMP_AUTOMATION] select_matching_messenger_account: No target name identified.")
                return False

            # 3. Tạo danh sách các mẫu khớp dựa trên TÊN ĐẦY ĐỦ
            def _visible_switch_account_name(label: str) -> str:
                raw = safe_str(label)
                if not raw:
                    return ""

                # Messenger rows are usually "Chuyen sang <name>, ..." or
                # "Continue as <name>". Strip row chrome before comparing names.
                name_part = raw.split(",", 1)[0].strip()
                norm = normalize_text(name_part, remove_accents=True)
                words = name_part.split()
                for prefix in ("chuyen sang", "continue as", "switch to"):
                    prefix_words = prefix.split()
                    if norm == prefix or norm.startswith(prefix + " "):
                        return " ".join(words[len(prefix_words):]).strip()
                return name_part

            def _click_name_permutation_match() -> bool:
                nodes = adapter.device.read_nodes(reason=f"name permutation account match: {target_name}")
                if not nodes:
                    return False

                candidates: list[tuple[int, Any, str, str]] = []
                for node in nodes:
                    if not node.enabled:
                        continue
                    label = node.label
                    visible_name = _visible_switch_account_name(label)
                    if not visible_name:
                        continue

                    label_norm = normalize_text(label, remove_accents=True)
                    visible_norm = normalize_text(visible_name, remove_accents=True)
                    if not visible_norm:
                        continue

                    is_switch_row = any(term in label_norm for term in ("chuyen sang", "continue as", "switch to"))
                    if not is_switch_row and not adapter.bot._looks_like_account_name(visible_name):
                        continue

                    match_score, matched_name = person_name_match_score(visible_name, [target_name])
                    if match_score < 700 or not matched_name:
                        continue

                    target = adapter.device.find_clickable_ancestor(node)
                    if not target.enabled:
                        continue

                    score = match_score
                    score += 120 if is_switch_row else 0
                    score += 80 if target.clickable else 0
                    score -= min(target.depth, 25)
                    candidates.append((score, target, label, visible_name))

                if not candidates:
                    return False

                candidates.sort(key=lambda item: (-item[0], item[1].bounds[1]))
                score, target, label, visible_name = candidates[0]
                print(
                    "[DUMP_AUTOMATION] Account name permutation matched: "
                    f"target='{target_name}' visible='{visible_name}' label='{label}' "
                    f"score={score} bounds={target.bounds_text}"
                )
                return adapter.device.tap_bounds(
                    target.bounds,
                    reason=f"select reordered account '{target_name}' visible '{visible_name}'",
                )

            search_texts = [
                target_name,
                f"Chuyển sang {target_name}",
                f"Continue as {target_name}",
            ]

            spec = SelectorSpec(
                name=f"Strict Account Match: {target_name}",
                texts=search_texts,
                preferred_zones=["center"],
                block_terms=["Tài khoản Facebook khác", "Đăng nhập bằng tài khoản khác"],
                allow_contains=True,  # Cho phép chứa để khớp với nhãn "Chuyển sang ..., đã đăng nhập"
                prefer_exact=True     # Ưu tiên tuyệt đối nếu khớp hoàn toàn chuỗi tên
            )
            
            print(f"[DUMP_AUTOMATION] Searching for account matching: '{target_name}'")
            if adapter.device.click_best(spec, reason=f"select account '{target_name}'") or _click_name_permutation_match():
                # SAU KHI CLICK CHỌN: Phải đợi và xử lý các popup phát sinh (như Tạo mã PIN)
                print(f"[DUMP_AUTOMATION] Account '{target_name}' selected. Handling post-login prompts...")
                deadline = time.time() + 15
                while time.time() < deadline:
                    if adapter.bot.click_continue_under_name_prompt(target_name):
                        continue

                    # Nếu đã vào được màn hình chính (danh sách chat) thì thoát vòng lặp
                    if adapter.bot.is_messenger_home():
                        return True
                    # Thử xử lý lần lượt các loại prompt
                    if adapter.bot.handle_system_permission_dialog():
                        continue
                    
                    # Ưu tiên xử lý mã PIN nếu thấy ô nhập liệu xuất hiện trong lúc đổi tài khoản
                    blob = normalize_text(adapter.device.text_blob(reason="check pin entry during account selection"), remove_accents=True)
                    if adapter.bot._blob_has_blocking_pin_prompt(blob) and any(term in blob for term in [
                        "android.widget.edittext", "truong ma khoa", "chinh sua 1/6", 
                        "tao ma pin", "thiet lap ma pin", "create pin",
                        "xac nhan ma pin", "confirm pin", "confirm your pin", "nhap lai ma pin"
                    ]):
                        if hasattr(this, "handle_messenger_pin_prompt_if_visible"):
                            this.handle_messenger_pin_prompt_if_visible(driver=None)
                            time.sleep(1)
                            continue

                    if not adapter.bot.handle_simple_prompts():
                        if adapter.bot._blob_is_messenger_home(blob):
                            return True
                        if adapter.bot._blob_has_blocking_pin_prompt(blob) and hasattr(this, "handle_messenger_pin_prompt_if_visible"):
                            this.handle_messenger_pin_prompt_if_visible(driver=None)
                    time.sleep(2)
                return True
            return False

        def _match_configured_account_from_profile_name(this, profile_name: str) -> dict:
            """Match the visible Facebook profile name to configured device accounts."""
            visible_name = safe_str(profile_name).replace("_", " ")
            account_names = getattr(this, "device_account_names", {}) or {}
            if not visible_name or not isinstance(account_names, dict) or not account_names:
                return {}

            visible_norm = normalize_text(visible_name, remove_accents=True)
            candidates: list[tuple[int, str, str]] = []
            for account_id, display_name in account_names.items():
                account_id = safe_str(account_id)
                display_name = safe_str(display_name) or account_id
                if not account_id or not display_name:
                    continue

                display_norm = normalize_text(display_name, remove_accents=True)
                if visible_norm and display_norm and visible_norm == display_norm:
                    score = 1000
                else:
                    score, _ = person_name_match_score(visible_name, [display_name])
                if score >= 700:
                    candidates.append((score, account_id, display_name))

            if not candidates:
                configured = ", ".join(f"{name} ({account})" for account, name in account_names.items())
                print(f"[ACCOUNT] No configured account matched profile='{visible_name}'. configured=[{configured}]")
                return {}

            candidates.sort(key=lambda item: (-item[0], item[1]))
            score, account_id, display_name = candidates[0]
            return {
                "account": account_id,
                "user_id_chat": account_id,
                "username": display_name,
                "displayName": display_name,
                "name": display_name,
                "device_id": getattr(this, "active_device", None),
                "device_name": getattr(this, "active_device_name", None) or getattr(this, "active_device", None),
                "match_score": score,
                "match_source": "profile_name",
            }

        def snapshot_facebook_account_before_messenger(this):
            """Patched snapshot to use dump-first navigation to profile."""
            adapter.refresh()
            if hasattr(this, "sync_account_from_device"):
                this.sync_account_from_device()

            # Ưu tiên tuyệt đối việc truy cập profile thực tế thay vì dùng cache hay đoán từ Home Feed
            allowed_names = list(this.device_account_names.values()) if hasattr(this, "device_account_names") else []
            print("[DUMP_AUTOMATION] Forcing profile visit to verify account identity...")
            
            profile_name = adapter.bot.get_profile_name(allowed_names=allowed_names)
            print(f"[ACCOUNT] [STEP 1] Raw profile name found on screen: '{profile_name}'")
            snapshot = {}
            
            if profile_name and hasattr(this, "_match_account_from_visible_text"):
                snapshot = this._match_account_from_visible_text(profile_name.replace("_", " "))
                if snapshot:
                    print(f"[ACCOUNT] [STEP 3] Success! Matched '{profile_name}' to configured account: {snapshot.get('displayName')} ({snapshot.get('user_id_chat')})")

            # Chỉ sử dụng cache nếu không thể truy cập profile
            if profile_name and not snapshot:
                snapshot = _match_configured_account_from_profile_name(this, profile_name)
                if snapshot:
                    print(
                        "[ACCOUNT] [STEP 3] Success! Matched "
                        f"'{profile_name}' to configured account: "
                        f"{snapshot.get('displayName')} ({snapshot.get('user_id_chat')})"
                    )

            if not profile_name:
                print("[ACCOUNT] Profile name could not be identified; refusing to reuse cached account snapshot.")
            elif not snapshot:
                print(f"[ACCOUNT] Profile name '{profile_name}' did not match configured accounts; refusing to reuse cached account snapshot.")

            if snapshot and hasattr(this, "_write_account_snapshot"):
                this._write_account_snapshot(snapshot)
            if snapshot:
                setattr(this, "current_facebook_account_snapshot", snapshot)
                if snapshot.get("user_id_chat"):
                    this.user_id_chat = snapshot["user_id_chat"]
                if snapshot.get("displayName"):
                    this.facebook_username = snapshot["displayName"]

            if hasattr(this, "register_bot_to_crm"):
                this.register_bot_to_crm(force=True)

            # The profile visit above is only for account verification. Leave
            # Facebook on Home immediately so the next Messenger-open task does
            # not waste cycles failing to find the header Messenger icon on the
            # profile page before falling back to Home.
            if adapter.device.is_current_package(FACEBOOK_PKG):
                print("[FACEBOOK] Returning to Home after profile account snapshot.")
                adapter.bot.return_to_facebook_home_from_profile(reason="reveal Facebook header after profile snapshot")

            return snapshot

        def open_messenger(this):
            """Dump-first replacement for the old open_messenger().

            The old method still called several Facebook-home normalization
            helpers before reaching the patched Messenger icon click. Those
            helpers could repeatedly dump /sdcard/window_dump.xml and produce
            `failed to stat remote object` on devices where that path is not
            writable. This replacement keeps the same high-level behavior but
            routes the whole UI navigation through the robust dump adapter.
            """
            adapter.refresh()

            def collect_unread_after_requests_checked() -> None:
                if getattr(this, "_dump_collecting_unread_after_requests", False):
                    return
                if not hasattr(this, "collect_unread_unreplied_conversations"):
                    return
                try:
                    setattr(this, "_dump_collecting_unread_after_requests", True)
                    if adapter.bot.is_messenger_home():
                        adapter.bot.click_unread_tab(timeout=4)
                    this.collect_unread_unreplied_conversations()
                except Exception as exc:
                    print(f"[DUMP_AUTOMATION] collect unread after Message Requests check failed: {exc}")
                finally:
                    setattr(this, "_dump_collecting_unread_after_requests", False)

            def open_requests_after_ready() -> None:
                if hasattr(this, "has_pending_crm_send_queue") and this.has_pending_crm_send_queue():
                    print("[SEND] CRM send queue is pending; skip auto Message Requests crawl after Messenger ready.")
                    return
                if getattr(this, "_skip_open_messenger_initial_request_crawl", False):
                    print("[REQUESTS] Skip auto Message Requests crawl because send flow is opening Messenger.")
                    return
                if os.getenv("FB_1_1_AUTO_OPEN_REQUESTS_AFTER_READY", "1").lower() in {"0", "false", "no", "off"}:
                    return
                if getattr(this, "message_requests_crawled_once", False) and getattr(this, "message_requests_opened_once", False):
                    print("[REQUESTS] Message Requests already checked in this session. Staying on Chats/Unread.")
                    if adapter.bot.is_messenger_home():
                        adapter.bot.click_unread_tab(timeout=4)
                    collect_unread_after_requests_checked()
                    return
                try:
                    print("[REQUESTS] Auto opening Message Requests after Messenger ready.")
                    this.crawl_message_requests()
                except Exception as exc:
                    print(f"[DUMP_AUTOMATION] crawl_message_requests after ready failed: {exc}")

            if hasattr(this, "ensure_messenger_installed"):
                if not this.ensure_messenger_installed():
                    return False
            elif not adapter.device.is_package_installed(MESSENGER_PKG):
                print("[MOBILE] Messenger package is not installed.")
                return False

            if adapter.device.is_current_package(MESSENGER_PKG):
                print(f"[DUMP_AUTOMATION] [{adapter.device.device_id}] Messenger is ALREADY OPEN. Skipping click task.")
                print("[MOBILE] Messenger is already foreground. Dump-first open_messenger skips Facebook.")
                if hasattr(this, "wait_until_messenger_ready_for_navigation"):
                    try:
                        this.wait_until_messenger_ready_for_navigation(timeout=8)
                    except TypeError:
                        this.wait_until_messenger_ready_for_navigation()
                    except Exception as exc:
                        print(f"[DUMP_AUTOMATION] wait_until_messenger_ready_for_navigation failed: {exc}")
                setattr(this, "messenger_checked", True)
                if adapter.bot.has_bottom_messenger_menu_tab() or adapter.bot.is_messenger_home():
                    open_requests_after_ready()
                return True

            print("[MOBILE] Messenger is installed. Opening Facebook with dump-first navigation.")
            if not adapter.bot.open_facebook():
                return False

            account_snapshot = {}
            if hasattr(this, "snapshot_facebook_account_before_messenger"):
                try:
                    account_snapshot = this.snapshot_facebook_account_before_messenger() or {}
                except Exception as exc:
                    print(f"[DUMP_AUTOMATION] Cannot snapshot Facebook account before Messenger: {exc}")

            # Bước 1: Thử nhấn Icon/CTA thông qua normalize
            this.click_facebook_messenger_icon()

            # Bước 2: Đợi ứng dụng chuyển sang Messenger, đồng thời liên tục check CTA nếu vẫn kẹt ở FB
            deadline = time.time() + 35
            while time.time() < deadline:
                if adapter.device.is_current_package(MESSENGER_PKG):
                    break
                
                # Nếu vẫn đang ở Facebook, có khả năng đang đứng ở màn hình CTA "MỞ MESSENGER"
                if adapter.device.is_current_package(FACEBOOK_PKG):
                    if adapter.bot.click_facebook_messenger_cta():
                        time.sleep(5) # Đã bấm nút to, đợi ứng dụng nhảy
                        continue
                
                # QUAN TRỌNG: Nếu đang hiện popup hệ thống (Cho phép thông báo), phải xử lý ngay trong loop này
                if adapter.bot.handle_system_permission_dialog():
                    time.sleep(2)
                    continue

                time.sleep(1.5)
            else:
                print("[MOBILE] Messenger was not opened after dump-first Facebook navigation.")
                return False

            if hasattr(this, "complete_messenger_auto_login_from_facebook"):
                try:
                    ok = this.complete_messenger_auto_login_from_facebook(
                        timeout=35,
                        account_snapshot=account_snapshot,
                    )
                    if not ok:
                        print("[MOBILE] Messenger opened but auto-login may not be complete; continuing with prompt handling.")
                except Exception as exc:
                    print(f"[DUMP_AUTOMATION] complete_messenger_auto_login_from_facebook failed: {exc}")

            adapter.bot.handle_simple_prompts()

            if hasattr(this, "wait_until_messenger_ready_for_navigation"):
                try:
                    this.wait_until_messenger_ready_for_navigation(timeout=10)
                except TypeError:
                    this.wait_until_messenger_ready_for_navigation()
                except Exception as exc:
                    print(f"[DUMP_AUTOMATION] Messenger ready check failed: {exc}")

            print("[MOBILE] Messenger is ready through dump-first navigation.")
            setattr(this, "messenger_checked", True)

            # Sau khi Messenger đã vào Home, mở Tin nhắn đang chờ NGAY bằng dump-first.
            # Không dùng crawl_message_requests_once ở đây nữa vì flag message_requests_crawled_once
            # có thể đã được set từ vòng trước trong cùng process, khiến luồng bỏ qua hoàn toàn
            # nên log chỉ dừng ở "Messenger is ready" mà không hề thử click tab Menu.
            open_requests_after_ready()

            return True

        def crawl_message_requests(this):
            adapter.refresh()
            if hasattr(this, "has_pending_crm_send_queue") and this.has_pending_crm_send_queue():
                print("[SEND] CRM send queue is pending; skip Message Requests crawl.")
                return 0
            if getattr(this, "message_requests_crawled_once", False) and getattr(this, "message_requests_opened_once", False):
                print("[REQUESTS] Message Requests already checked in this session. Skip reopening Menu.")
                if adapter.device.is_current_package(MESSENGER_PKG) and adapter.bot.is_messenger_home():
                    adapter.bot.click_unread_tab(timeout=4)
                    if not getattr(this, "_dump_collecting_unread_after_requests", False) and hasattr(this, "collect_unread_unreplied_conversations"):
                        try:
                            setattr(this, "_dump_collecting_unread_after_requests", True)
                            this.collect_unread_unreplied_conversations()
                        except Exception as exc:
                            print(f"[DUMP_AUTOMATION] collect unread after skipped Message Requests failed: {exc}")
                        finally:
                            setattr(this, "_dump_collecting_unread_after_requests", False)
                return 0
            if not getattr(this, "ui_crawl_enabled", True):
                return 0
            if not adapter.device.is_current_package(MESSENGER_PKG):
                if hasattr(this, "open_messenger") and not this.open_messenger():
                    return 0
            opened = adapter.bot.open_message_requests()
            if not opened:
                print("[REQUESTS] Message Requests was not opened; keep it eligible for retry.")
                setattr(this, "message_requests_crawled_once", False)
                setattr(this, "message_requests_opened_once", False)
                return 0
            max_requests = int(os.getenv("FB_1_1_MAX_MESSAGE_REQUESTS_PER_SCAN", "30"))

            def save_callback(row: dict[str, Any]) -> None:
                if hasattr(this, "_save_open_message_request_conversation"):
                    patched_row = dict(row or {})
                    row_sender = safe_str((row or {}).get("sender")) or safe_str((row or {}).get("label")).split("|", 1)[0]
                    row_sender = row_sender.split(",", 1)[0].strip() or row_sender
                    row_preview = safe_str((row or {}).get("preview"))
                    if adapter.bot._is_system_message_preview(row_preview):
                        row_preview = ""
                    elif "preview" not in (row or {}):
                        row_preview = safe_str((row or {}).get("label"))
                    patched_row.setdefault("parsed", {
                        "sender": row_sender,
                        "content": row_preview,
                        "time": safe_str((row or {}).get("time")),
                    })
                    this._save_open_message_request_conversation(patched_row, source="message_requests")

            setattr(this, "message_requests_opened_once", True)
            setattr(this, "message_requests_crawled_once", True)
            count = adapter.bot.crawl_message_requests(max_requests=max_requests, on_request_accepted=save_callback)
            if adapter.device.is_current_package(MESSENGER_PKG) and adapter.bot.is_messenger_home():
                adapter.bot.click_unread_tab(timeout=4)
                if not getattr(this, "_dump_collecting_unread_after_requests", False) and hasattr(this, "collect_unread_unreplied_conversations"):
                    try:
                        setattr(this, "_dump_collecting_unread_after_requests", True)
                        this.collect_unread_unreplied_conversations()
                    except Exception as exc:
                        print(f"[DUMP_AUTOMATION] collect unread after Message Requests crawl failed: {exc}")
                    finally:
                        setattr(this, "_dump_collecting_unread_after_requests", False)
            return count

        def crawl_message_requests_once(this):
            if hasattr(this, "has_pending_crm_send_queue") and this.has_pending_crm_send_queue():
                print("[SEND] CRM send queue is pending; skip Message Requests crawl.")
                return 0
            if getattr(this, "message_requests_crawled_once", False) and getattr(this, "message_requests_opened_once", False):
                if adapter.device.is_current_package(MESSENGER_PKG) and adapter.bot.is_messenger_home():
                    adapter.bot.click_unread_tab(timeout=4)
                    if not getattr(this, "_dump_collecting_unread_after_requests", False) and hasattr(this, "collect_unread_unreplied_conversations"):
                        try:
                            setattr(this, "_dump_collecting_unread_after_requests", True)
                            this.collect_unread_unreplied_conversations()
                        except Exception as exc:
                            print(f"[DUMP_AUTOMATION] collect unread after skipped Message Requests once failed: {exc}")
                        finally:
                            setattr(this, "_dump_collecting_unread_after_requests", False)
                return 0

            adapter.refresh()
            if not getattr(this, "ui_crawl_enabled", True):
                return 0
            if not adapter.device.is_current_package(MESSENGER_PKG):
                if hasattr(this, "open_messenger") and not this.open_messenger():
                    return 0

            opened = adapter.bot.open_message_requests()
            if not opened:
                print("[REQUESTS] Message Requests was not opened; will retry on the next monitor cycle.")
                setattr(this, "message_requests_crawled_once", False)
                setattr(this, "message_requests_opened_once", False)
                return 0

            setattr(this, "message_requests_opened_once", True)
            setattr(this, "message_requests_crawled_once", True)

            max_requests = int(os.getenv("FB_1_1_MAX_MESSAGE_REQUESTS_PER_SCAN", "30"))

            def save_callback(row: dict[str, Any]) -> None:
                if hasattr(this, "_save_open_message_request_conversation"):
                    patched_row = dict(row or {})
                    row_sender = safe_str((row or {}).get("sender")) or safe_str((row or {}).get("label")).split("|", 1)[0]
                    row_sender = row_sender.split(",", 1)[0].strip() or row_sender
                    row_preview = safe_str((row or {}).get("preview"))
                    if adapter.bot._is_system_message_preview(row_preview):
                        row_preview = ""
                    elif "preview" not in (row or {}):
                        row_preview = safe_str((row or {}).get("label"))
                    patched_row.setdefault("parsed", {
                        "sender": row_sender,
                        "content": row_preview,
                        "time": safe_str((row or {}).get("time")),
                    })
                    this._save_open_message_request_conversation(patched_row, source="message_requests")

            count = adapter.bot.crawl_message_requests(max_requests=max_requests, on_request_accepted=save_callback)
            if adapter.device.is_current_package(MESSENGER_PKG) and adapter.bot.is_messenger_home():
                adapter.bot.click_unread_tab(timeout=4)
                if not getattr(this, "_dump_collecting_unread_after_requests", False) and hasattr(this, "collect_unread_unreplied_conversations"):
                    try:
                        setattr(this, "_dump_collecting_unread_after_requests", True)
                        this.collect_unread_unreplied_conversations()
                    except Exception as exc:
                        print(f"[DUMP_AUTOMATION] collect unread after Message Requests once failed: {exc}")
                    finally:
                        setattr(this, "_dump_collecting_unread_after_requests", False)
            return count

        patches = {
            "open_message_requests_screen": open_message_requests_screen,
            "open_first_visible_message_request_row": open_first_visible_message_request_row,
            "accept_visible_message_request_if_open": accept_visible_message_request_if_open,
            "_return_to_message_requests_list": _return_to_message_requests_list,
            "click_messenger_chats_tab": click_messenger_chats_tab,
            "click_facebook_messenger_icon": click_facebook_messenger_icon,
            "_click_best_ui_text": _click_best_ui_text,
            "_find_best_ui_text_node": _find_best_ui_text_node,
            "select_matching_messenger_account": select_matching_messenger_account,
            "open_messenger": open_messenger,
            "complete_messenger_auto_login_from_facebook": complete_messenger_auto_login_from_facebook,
            "snapshot_facebook_account_before_messenger": snapshot_facebook_account_before_messenger,
        }
        if patch_crawl:
            patches["crawl_message_requests"] = crawl_message_requests
            patches["crawl_message_requests_once"] = crawl_message_requests_once

        for name, func in patches.items():
            setattr(m, name, types.MethodType(func, m))

        setattr(m, "dump_ui_adapter", adapter)
        print("[DUMP_AUTOMATION] Attached rebuilt dump-first automation layer to MobileOneToOneMessenger.")
        print(f"[DUMP_AUTOMATION] adb={adapter.device.adb_path}")
        print(f"[DUMP_AUTOMATION] local_dump={adapter.device.local_dump_path}")
        return m


def attach_dump_automation(messenger: Any, *, patch_crawl: bool = True) -> Any:
    """Patch an existing MobileOneToOneMessenger instance in-place.
    """
    return ExistingMessengerAdapter(messenger).patch(patch_crawl=patch_crawl)


# Backward-compatible alias names.
install_dump_automation = attach_dump_automation
patch_mobile_messenger_instance = attach_dump_automation


def _make_cli_device(args: argparse.Namespace) -> DumpUiDevice:
    return DumpUiDevice(
        adb_path=args.adb or None,
        device_id=args.device or None,
        local_dump_path=args.dump_file or None,
        screenshot_path=args.screenshot_file or None,
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Dump-first Android UI automation for Facebook/Messenger")
    parser.add_argument("command", choices=[
        "devices", "labels", "screenshot", "click", "open-messenger", "message-requests", "crawl-requests", "accept",
    ])
    parser.add_argument("--adb", default=None, help="Path to adb.exe. Default: project/platform-tools/adb.exe or PATH adb")
    parser.add_argument("--device", default=None, help="ADB device id")
    parser.add_argument("--dump-file", default=None, help="Local window_dump.xml output path")
    parser.add_argument("--screenshot-file", default=None, help="Screenshot output path")
    parser.add_argument("--text", default="", help="Text to click for command=click")
    parser.add_argument("--contains", action="store_true", help="Allow contains matching for command=click")
    parser.add_argument("--max-requests", type=int, default=30, help="Max message requests for crawl-requests")
    args = parser.parse_args(argv)

    device = _make_cli_device(args)
    bot = MessengerDumpBot(device)

    if args.command == "devices":
        print(json.dumps(device.list_devices(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "labels":
        labels = device.labels(limit=200, reason="CLI labels")
        print("\n".join(f"- {label}" for label in labels))
        return 0
    if args.command == "screenshot":
        device.screenshot()
        return 0
    if args.command == "click":
        if not args.text:
            print("--text is required for command=click")
            return 2
        return 0 if device.click_text([args.text], allow_contains=args.contains, reason=f"CLI click {args.text}") else 1
    if args.command == "open-messenger":
        return 0 if bot.open_messenger_from_facebook() else 1
    if args.command == "message-requests":
        return 0 if bot.open_message_requests() else 1
    if args.command == "crawl-requests":
        count = bot.crawl_message_requests(max_requests=args.max_requests)
        return 0 if count >= 0 else 1
    if args.command == "accept":
        return 0 if bot.accept_current_request() else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
