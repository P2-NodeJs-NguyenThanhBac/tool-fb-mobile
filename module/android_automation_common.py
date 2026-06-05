# -*- coding: utf-8 -*-
"""Shared Android automation helpers.

Centralizes small ADB/UI helpers that were previously duplicated across
main_merged_2.py, mobile_1_1_messenger.py and mobile_dump_automation_rebuilt.py.
Keep this module dependency-light so it can be imported by both root files and
files inside module/.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
import time
import unicodedata
from pathlib import Path
from typing import Any, Callable, Optional


_ROTATION_LAST_APPLIED: dict[str, float] = {}
DEFAULT_ROTATION_THROTTLE_SECONDS = float(os.getenv("FB_ROTATION_GUARD_MIN_INTERVAL", "20"))


def safe_str(value: Any) -> str:
    return ("" if value is None else str(value)).strip()


def repair_mojibake(value: Any) -> str:
    """Repair common UTF-8-as-Latin-1 mojibake from Android XML/log output."""
    text = safe_str(value)
    if not text:
        return ""

    mojibake_markers = ("Ã", "Â", "Ä", "Å", "Æ", "áº", "á»")
    if not any(marker in text for marker in mojibake_markers) and not any(
        0x80 <= ord(ch) <= 0x9F or ch in "ÄÅÆÂÃ"
        for ch in text
    ):
        return text

    def _encode_mojibake_bytes(source: str) -> bytes:
        raw = bytearray()
        for ch in source:
            code = ord(ch)
            if code <= 0xFF:
                raw.append(code)
                continue
            try:
                raw.extend(ch.encode("cp1252"))
            except UnicodeEncodeError:
                raw.extend(ch.encode("utf-8"))
        return bytes(raw)

    try:
        repaired = _encode_mojibake_bytes(text).decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text

    return repaired if repaired else text


def normalize_text(value: Any, *, remove_accents: bool = False) -> str:
    text = repair_mojibake(value)
    text = unicodedata.normalize("NFKC", text)
    text = " ".join(text.split())
    if remove_accents:
        text = "".join(
            ch for ch in unicodedata.normalize("NFD", text)
            if unicodedata.category(ch) != "Mn"
        )
        text = text.replace("đ", "d").replace("Đ", "D")
    return text.casefold()


def normalize_ui_text(value: Any) -> str:
    """Default UI text normalization for Vietnamese Android labels."""
    return normalize_text(value, remove_accents=True)


def normalize_variants(value: Any) -> set[str]:
    accented = normalize_text(value, remove_accents=False)
    unaccented = normalize_text(value, remove_accents=True)
    return {item for item in {accented, unaccented} if item}


def shell_response_text(result: Any) -> str:
    if hasattr(result, "output"):
        return safe_str(result.output)
    if hasattr(result, "text"):
        return safe_str(result.text)
    if hasattr(result, "stdout"):
        return safe_str(result.stdout)
    return safe_str(result)


def parse_bounds(bounds: str) -> Optional[tuple[int, int, int, int]]:
    match = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", safe_str(bounds))
    if not match:
        return None
    x1, y1, x2, y2 = map(int, match.groups())
    if x2 <= x1 or y2 <= y1:
        return None
    return x1, y1, x2, y2


def bounds_center(bounds: tuple[int, int, int, int]) -> tuple[int, int]:
    x1, y1, x2, y2 = bounds
    return (x1 + x2) // 2, (y1 + y2) // 2


def resolve_launch_activity_from_text(output: Any, package_name: str) -> str:
    package_name = safe_str(package_name)
    for line in reversed(shell_response_text(output).splitlines()):
        line = line.strip()
        if "/" not in line or line.startswith("priority="):
            continue
        resolved_package, activity = line.split("/", 1)
        if resolved_package == package_name and activity:
            return f"{resolved_package}/{activity}"
        if package_name in line:
            return line
    return ""


def resolve_launch_activity_driver(driver: Any, package_name: str) -> Optional[str]:
    try:
        raw = driver.shell(["cmd", "package", "resolve-activity", "--brief", package_name])
    except Exception:
        return None
    return resolve_launch_activity_from_text(raw, package_name) or None


def resolve_launch_activity_adb(run_adb: Callable[..., Any], package_name: str) -> str:
    output = run_adb(["shell", "cmd", "package", "resolve-activity", "--brief", package_name])
    return resolve_launch_activity_from_text(output, package_name)


def is_package_installed_adb(run_adb: Callable[..., Any], package_name: str) -> bool:
    output = shell_response_text(run_adb(["shell", "pm", "path", package_name]))
    return output.startswith("package:")


def notification_shade_has_focus(window_dump: str) -> bool:
    if not window_dump:
        return False
    for line in window_dump.splitlines():
        if ("mCurrentFocus" in line or "mFocusedWindow" in line) and "NotificationShade" in line:
            return True
    return False


def collapse_statusbar_driver_sync(driver: Any) -> bool:
    raw = driver.shell("dumpsys window")
    window_dump = shell_response_text(raw)
    if not notification_shade_has_focus(window_dump):
        return False
    driver.shell("cmd statusbar collapse")
    return True


async def collapse_statusbar_driver_async(driver: Any) -> bool:
    return await asyncio.to_thread(collapse_statusbar_driver_sync, driver)


def collapse_statusbar_adb(run_adb: Callable[..., Any]) -> bool:
    window_dump = shell_response_text(run_adb(["shell", "dumpsys", "window"], timeout=10))
    if not notification_shade_has_focus(window_dump):
        return False
    run_adb(["shell", "cmd", "statusbar", "collapse"], timeout=10)
    return True


def _should_apply_rotation(device_id: str, throttle_seconds: float) -> bool:
    now = time.monotonic()
    last = _ROTATION_LAST_APPLIED.get(device_id, 0.0)
    if now - last < throttle_seconds:
        return False
    _ROTATION_LAST_APPLIED[device_id] = now
    return True


async def disable_auto_rotation_driver(
    driver: Any,
    device_id: str,
    *,
    log_func: Optional[Callable[[str, int], None]] = None,
    device_label: Optional[str] = None,
    throttle_seconds: float = DEFAULT_ROTATION_THROTTLE_SECONDS,
) -> bool:
    """Lock device to portrait with throttling to avoid ADB spam."""
    device_id = safe_str(device_id)
    if not device_id:
        return False
    if not _should_apply_rotation(device_id, throttle_seconds):
        return False

    label = device_label or device_id
    commands = [
        "settings put system accelerometer_rotation 0",
        "settings put system user_rotation 0",
        "cmd window user-rotation lock 0",
    ]
    ok = True
    for cmd in commands:
        try:
            await asyncio.to_thread(driver.shell, cmd)
        except Exception as exc:
            ok = False
            if log_func:
                log_func(f"[{label}] Rotation guard command failed: {cmd} | {exc}", logging.WARNING)
    if log_func:
        log_func(f"[{label}] Rotation guard applied portrait lock", logging.INFO)
    return ok
