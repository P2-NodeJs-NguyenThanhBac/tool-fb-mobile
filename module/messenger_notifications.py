import asyncio
import logging
import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

from util import log_message, DEVICE_LIST_NAME

MESSENGER_PKG = "com.facebook.orca"


def _dev(driver) -> str:
    serial = getattr(driver, "serial", "") or "unknown"
    return DEVICE_LIST_NAME.get(serial, serial)


def _safe_str(v: Any) -> str:
    return ("" if v is None else str(v)).strip()


def _parse_first_int(text: str) -> int:
    m = re.search(r"(\d+)", _safe_str(text))
    return int(m.group(1)) if m else 0


def _node_text(node: ET.Element) -> str:
    text = _safe_str(node.attrib.get("text"))
    desc = _safe_str(node.attrib.get("content-desc"))
    return text or desc


def _dump_xml(driver) -> str:
    return driver.dump_hierarchy()


def _parse_main_unread_summary(xml: str) -> Dict[str, Any]:
    data = {
        "new_messages_label": None,
        "unread_chat_tab_label": None,
        "unread_chat_count": 0,
        "story_updates_label": None,
        "menu_updates_label": None,
    }
    try:
        root = ET.fromstring(xml)
    except Exception:
        return data

    for node in root.iter("node"):
        text = _safe_str(node.attrib.get("text"))
        desc = _safe_str(node.attrib.get("content-desc"))
        blob = f"{text} | {desc}"

        if not data["new_messages_label"] and ("Tin nhắn mới" in blob or re.search(r"\d+\s+tin nhắn mới", blob, re.I)):
            data["new_messages_label"] = desc or text

        if ("Đoạn chat" in blob and "Tab" in blob) or ("Đoạn chat" in blob and "chưa đọc" in blob):
            if not data["unread_chat_tab_label"]:
                data["unread_chat_tab_label"] = desc or text
            if "chưa đọc" in blob:
                data["unread_chat_count"] = max(data["unread_chat_count"], _parse_first_int(blob))

        if "Tin," in blob and "mục cập nhật mới" in blob and not data["story_updates_label"]:
            data["story_updates_label"] = desc or text

        if "Tab Menu" in blob and "mục cập nhật mới" in blob and not data["menu_updates_label"]:
            data["menu_updates_label"] = desc or text

    return data


def _parse_menu_summary(xml: str) -> Dict[str, Any]:
    data = {
        "menu_title": None,
        "logged_in_as": None,
        "pending_messages_label": None,
        "pending_messages_count": 0,
        "archive_label": None,
    }
    try:
        root = ET.fromstring(xml)
    except Exception:
        return data

    for node in root.iter("node"):
        text = _safe_str(node.attrib.get("text"))
        desc = _safe_str(node.attrib.get("content-desc"))
        blob = f"{text} | {desc}"

        if not data["menu_title"] and text == "Menu":
            data["menu_title"] = text

        if not data["logged_in_as"] and "Đã đăng nhập dưới tên" in blob:
            data["logged_in_as"] = desc or text

        if "Tin nhắn đang chờ" in blob:
            label = desc or text
            data["pending_messages_label"] = label
            data["pending_messages_count"] = max(data["pending_messages_count"], _parse_first_int(label))

        if "Kho lưu trữ" in blob and not data["archive_label"]:
            data["archive_label"] = desc or text

    return data


def _looks_like_pending_message_row(node: ET.Element) -> bool:
    text = _safe_str(node.attrib.get("text"))
    desc = _safe_str(node.attrib.get("content-desc"))
    cls = _safe_str(node.attrib.get("class"))
    blob = f"{text} | {desc}"

    ignore = [
        "Tin nhắn đang chờ",
        "Kho lưu trữ",
        "Cài đặt",
        "Marketplace",
        "Tab Menu",
        "Đoạn chat, Tab",
        "Thông báo",
        "Quay lại",
        "Chỉnh sửa",
        "BẠN CÓ THỂ BIẾT",
        "SPAM",
        "Đã đăng nhập dưới tên",
        "Hãy mở đoạn chat",
        "Chọn ai có thể nhắn tin cho bạn",
    ]
    if not blob.strip():
        return False
    if any(x in blob for x in ignore):
        return False
    if cls not in {"android.widget.Button", "android.view.ViewGroup", "android.view.View", "android.widget.TextView"}:
        return False

    bounds = _safe_str(node.attrib.get("bounds"))
    if bounds:
        m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds)
        if m:
            x1, y1, x2, y2 = map(int, m.groups())
            h = y2 - y1
            w = x2 - x1
            if h < 40 or w < 180:
                return False
    return True


def _extract_pending_items(xml: str) -> List[Dict[str, Any]]:
    try:
        root = ET.fromstring(xml)
    except Exception:
        return []

    items: List[Dict[str, Any]] = []
    seen = set()
    for node in root.iter("node"):
        if not _looks_like_pending_message_row(node):
            continue
        text = _safe_str(node.attrib.get("text"))
        desc = _safe_str(node.attrib.get("content-desc"))
        label = desc or text
        if not label:
            continue
        key = (label, _safe_str(node.attrib.get("bounds")))
        if key in seen:
            continue
        seen.add(key)
        items.append({
            "label": label,
            "text": text,
            "content_desc": desc,
            "class": _safe_str(node.attrib.get("class")),
            "bounds": _safe_str(node.attrib.get("bounds")),
        })
    return items


async def _wait_for_pkg(driver, pkg: str, timeout: float = 8.0) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        try:
            info = await asyncio.to_thread(driver.app_current)
            if (info or {}).get("package") == pkg:
                return True
        except Exception:
            pass
        await asyncio.sleep(0.4)
    return False


async def _click_xpath(driver, xpath: str, timeout: float = 5.0) -> bool:
    try:
        el = driver.xpath(xpath)
        ok = await asyncio.to_thread(el.wait, timeout=timeout)
        if ok:
            await asyncio.to_thread(el.click)
            return True
    except Exception:
        pass
    return False


async def _open_messenger(driver) -> bool:
    try:
        await asyncio.to_thread(driver.app_start, MESSENGER_PKG)
        ok = await _wait_for_pkg(driver, MESSENGER_PKG, timeout=10.0)
        if ok:
            await asyncio.sleep(1.2)
        return ok
    except Exception:
        return False


async def _open_menu(driver) -> bool:
    selectors = [
        '//*[contains(@content-desc, "Tab Menu")]',
        '//*[@content-desc="Menu"]',
        '//*[contains(@content-desc, "Menu")]',
        '//*[@text="Menu"]',
    ]
    for xp in selectors:
        if await _click_xpath(driver, xp, timeout=3.0):
            await asyncio.sleep(1.2)
            return True
    return False


async def _open_pending_messages(driver) -> bool:
    selectors = [
        '//*[contains(@content-desc, "Tin nhắn đang chờ")]',
        '//*[@text="Tin nhắn đang chờ, 2/3"]',
        '//*[contains(@text, "Tin nhắn đang chờ")]',
    ]
    for xp in selectors:
        if await _click_xpath(driver, xp, timeout=3.0):
            await asyncio.sleep(1.5)
            return True
    return False


async def crawl_messenger_notifications(driver, command_id: Optional[str] = None, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Luồng:
      1) Mở Messenger
      2) Dump màn hình chính -> lấy unread summary
      3) Vào Menu -> dump menu -> lấy pending count
      4) Vào Tin nhắn đang chờ -> dump -> bóc item hiển thị

    Trả về dict để fb_task publish lên RabbitMQ cho NodeJS.
    """
    params = params or {}
    result: Dict[str, Any] = {
        "command_id": command_id or "",
        "device_id": getattr(driver, "serial", "") or "",
        "app": "messenger",
        "ok": False,
        "reason": "",
        "main_screen": {},
        "menu_screen": {},
        "pending_screen": {},
    }

    try:
        if not await _open_messenger(driver):
            result["reason"] = "Không mở được Messenger"
            return result

        main_xml = await asyncio.to_thread(_dump_xml, driver)
        result["main_screen"] = _parse_main_unread_summary(main_xml)

        menu_ok = await _open_menu(driver)
        if not menu_ok:
            result["reason"] = "Không tìm thấy nút Menu trong Messenger"
            return result

        menu_xml = await asyncio.to_thread(_dump_xml, driver)
        result["menu_screen"] = _parse_menu_summary(menu_xml)

        pending_ok = await _open_pending_messages(driver)
        if not pending_ok:
            result["ok"] = True
            result["reason"] = "Đã đọc được menu nhưng chưa mở được mục Tin nhắn đang chờ"
            result["pending_screen"] = {
                "items": [],
                "visible_count": 0,
            }
            return result

        pending_xml = await asyncio.to_thread(_dump_xml, driver)
        items = _extract_pending_items(pending_xml)
        result["pending_screen"] = {
            "items": items,
            "visible_count": len(items),
            "raw_pending_count": result.get("menu_screen", {}).get("pending_messages_count", 0),
        }

        result["ok"] = True
        result["reason"] = "success"
        log_message(
            f"[{_dev(driver)}] Messenger crawl OK | unread_chat_count={result['main_screen'].get('unread_chat_count', 0)} | pending_count={result['menu_screen'].get('pending_messages_count', 0)} | visible_pending_items={len(items)}",
            logging.INFO,
        )
        return result

    except Exception as e:
        result["ok"] = False
        result["reason"] = f"{type(e).__name__}: {e}"
        log_message(f"[{_dev(driver)}] crawl_messenger_notifications lỗi: {result['reason']}", logging.ERROR)
        return result
