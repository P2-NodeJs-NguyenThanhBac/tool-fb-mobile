import asyncio
import logging
import re
import hashlib
import os
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import pymongo_management
import toolfacebook_lib
from util import DEVICE_LIST_NAME, WINDOW_ADB_PATH, LINUX_ADB_PATH, get_current_account, go_to_home_page, log_message

FACEBOOK_PKG = "com.facebook.katana"
COMMENT_COLLECTION = "Binh-luan-trong-bai-dang-chi-tiet"
COMMENT_CRAWL_LOG_COLLECTION = "Binh-luan-trong-bai-dang-crawl"

TIME_RE = re.compile(
    r"^(vừa xong|\d+\s*(giây|phút|giờ|ngày|tuần|tháng|năm)|\d+\s*h|\d+\s*d)$",
    re.IGNORECASE,
)

ACTION_WORDS = {
    "thích", "trả lời", "gửi", "xem thêm", "xem thêm câu trả lời",
    "xem câu trả lời", "ẩn", "bình luận", "comment", "reply", "like",
    "send", "reels", "phù hợp nhất", "mới nhất", "tất cả bình luận",
    "most relevant", "newest", "all comments",
}

PROFILE_OPEN_LABELS = (
    "Xem trang cá nhân", "See profile", "Open profile", "Đi tới trang cá nhân"
)


def _dev(driver) -> str:
    serial = getattr(driver, "serial", "unknown")
    return DEVICE_LIST_NAME.get(serial, serial)


def _parse_bounds(bounds: str) -> Optional[Tuple[int, int, int, int]]:
    m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds or "")
    if not m:
        return None
    return tuple(map(int, m.groups()))


def _center(bounds: Tuple[int, int, int, int]) -> Tuple[int, int]:
    l, t, r, b = bounds
    return (l + r) // 2, (t + b) // 2


def _norm_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def _is_noise_text(s: str) -> bool:
    s0 = _norm_text(s).lower()
    if not s0:
        return True
    if s0 in ACTION_WORDS:
        return True
    if s0.startswith("#"):
        return True
    if TIME_RE.match(s0):
        return True
    if re.fullmatch(r"\d+[\.,]?\d*[kKmM]?", s0):
        return True
    return False


def _looks_like_name(s: str) -> bool:
    s0 = _norm_text(s)
    if not s0 or _is_noise_text(s0):
        return False
    if len(s0) < 2 or len(s0) > 80:
        return False
    if re.search(r"https?://|facebook\.com|@", s0, re.IGNORECASE):
        return False
    if sum(ch.isdigit() for ch in s0) > 3:
        return False
    return True


def _looks_like_comment_text(s: str) -> bool:
    s0 = _norm_text(s)
    if not s0 or _is_noise_text(s0):
        return False
    if len(s0) < 2:
        return False
    return True


def _iter_children_with_xpath(elem, current_xpath: str):
    counts: Dict[str, int] = {}
    for child in list(elem):
        cls = child.attrib.get("class", "node")
        counts[cls] = counts.get(cls, 0) + 1
        child_xpath = f"{current_xpath}/{cls}[{counts[cls]}]"
        yield child, child_xpath


def _flatten_descendants(elem, base_xpath: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []

    def walk(node, xpath):
        info = {
            "xpath": xpath,
            "class": node.attrib.get("class", ""),
            "text": _norm_text(node.attrib.get("text", "")),
            "desc": _norm_text(node.attrib.get("content-desc", "")),
            "clickable": (node.attrib.get("clickable", "").lower() == "true"),
            "bounds": _parse_bounds(node.attrib.get("bounds", "")),
            "index": node.attrib.get("index", ""),
        }
        out.append(info)
        for child, child_xpath in _iter_children_with_xpath(node, xpath):
            walk(child, child_xpath)

    walk(elem, base_xpath)
    return out


def _candidate_from_element(elem, xpath: str) -> Optional[Dict[str, Any]]:
    bounds = _parse_bounds(elem.attrib.get("bounds", ""))
    if not bounds:
        return None

    l, t, r, b = bounds
    width, height = r - l, b - t
    if width < 200 or height < 70 or height > 900:
        return None

    descendants = _flatten_descendants(elem, xpath)
    texts: List[Tuple[str, Dict[str, Any]]] = []
    descs: List[Tuple[str, Dict[str, Any]]] = []

    for d in descendants:
        if d["text"]:
            texts.append((d["text"], d))
        if d["desc"]:
            descs.append((d["desc"], d))

    name_node = None
    for txt, meta in texts:
        if _looks_like_name(txt):
            name_node = meta
            break

    if not name_node:
        for txt, meta in descs:
            if _looks_like_name(txt):
                name_node = meta
                break

    if not name_node:
        return None

    name = name_node["text"] or name_node["desc"]
    time_text = None

    for txt, meta in texts + descs:
        if TIME_RE.match(_norm_text(txt).lower()):
            time_text = _norm_text(txt)
            break

    comment_text = None

    sorted_descs = sorted(descs, key=lambda x: len(x[0]), reverse=True)
    for txt, meta in sorted_descs:
        t0 = _norm_text(txt)
        if not _looks_like_comment_text(t0):
            continue
        if t0 == name:
            continue
        if time_text and t0 == time_text:
            continue
        comment_text = t0
        break

    if not comment_text:
        sorted_texts = sorted(texts, key=lambda x: len(x[0]), reverse=True)
        for txt, meta in sorted_texts:
            t0 = _norm_text(txt)
            if not _looks_like_comment_text(t0):
                continue
            if t0 == name:
                continue
            if time_text and t0 == time_text:
                continue
            comment_text = t0
            break

    if not comment_text:
        return None

    name_bounds = name_node.get("bounds") or bounds
    level = 1 if (name_bounds[0] - l) > 70 else 0

    return {
        "xpath": xpath,
        "bounds": bounds,
        "name": name,
        "name_bounds": name_bounds,
        "comment": comment_text,
        "time_text": time_text,
        "level": level,
    }


def _extract_visible_comment_candidates(xml: str) -> List[Dict[str, Any]]:
    root = ET.fromstring(xml)
    candidates: List[Dict[str, Any]] = []

    def walk(node, xpath):
        cand = _candidate_from_element(node, xpath)
        if cand:
            candidates.append(cand)
        for child, child_xpath in _iter_children_with_xpath(node, xpath):
            walk(child, child_xpath)

    walk(root, "/hierarchy")

    best: Dict[Tuple[str, str, str, int], Dict[str, Any]] = {}
    for c in candidates:
        key = (c["name"], c["comment"], c.get("time_text") or "", c.get("level", 0))
        area = (c["bounds"][2] - c["bounds"][0]) * (c["bounds"][3] - c["bounds"][1])
        old = best.get(key)
        if not old:
            best[key] = c
            continue
        old_area = (old["bounds"][2] - old["bounds"][0]) * (old["bounds"][3] - old["bounds"][1])
        if area < old_area:
            best[key] = c

    out = list(best.values())
    out.sort(key=lambda x: (x["bounds"][1], x["bounds"][0]))
    return out


async def _tap_bounds(driver, bounds: Tuple[int, int, int, int]):
    x, y = _center(bounds)
    driver.click(x, y)
    await asyncio.sleep(1.0)


async def _disable_auto_rotation_quick(driver):
    try:
        serial = getattr(driver, "serial", "")
        adb_path = WINDOW_ADB_PATH if os.name == "nt" else LINUX_ADB_PATH

        def adb_shell_text(cmd: str) -> str:
            result = subprocess.run(
                [adb_path, "-s", serial, "shell", *cmd.split()],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
            )
            return ((result.stdout or "").strip() or (result.stderr or "").strip())

        cur = await asyncio.to_thread(adb_shell_text, "settings get system accelerometer_rotation")
        wm_cur = (await asyncio.to_thread(adb_shell_text, "cmd window user-rotation")).lower()

        actual_rotation = None
        try:
            dump_text = await asyncio.to_thread(adb_shell_text, "dumpsys input")
            for pattern in (
                r"SurfaceOrientation:\s*(\d+)",
                r"surfaceOrientation[=:]\s*(\d+)",
                r"mCurrentRotation[=:]\s*(?:ROTATION_)?(\d+)",
                r"\bmRotation[=:]\s*(?:ROTATION_)?(\d+)",
                r"\brotation[=:]\s*(?:ROTATION_)?(\d+)",
            ):
                import re
                match = re.search(pattern, str(dump_text), re.IGNORECASE)
                if match:
                    actual_rotation = int(match.group(1))
                    break
        except Exception:
            actual_rotation = None

        visual_orientation = None
        try:
            shot = await asyncio.to_thread(driver.screenshot)
            width, height = shot.size
            visual_orientation = "portrait" if height >= width else "landscape"
        except Exception:
            visual_orientation = None

        if cur != "0" or "lock 0" not in wm_cur or actual_rotation not in (None, 0) or visual_orientation == "landscape":
            await asyncio.to_thread(adb_shell_text, "settings put system accelerometer_rotation 0")
            await asyncio.to_thread(adb_shell_text, "settings put system user_rotation 0")
            await asyncio.to_thread(adb_shell_text, "cmd window user-rotation lock 0")
            await asyncio.to_thread(adb_shell_text, "cmd window set-ignore-orientation-request true")
    except Exception:
        pass


async def _ensure_facebook_foreground(driver):
    try:
        info = await asyncio.to_thread(driver.app_current)
        pkg = (info or {}).get("package", "") or ""
    except Exception:
        pkg = ""
    if pkg != FACEBOOK_PKG:
        await asyncio.to_thread(driver.app_start, FACEBOOK_PKG)
        await asyncio.sleep(2.0)


async def _click_first_existing(driver, selectors: List[Dict[str, str]], sleep_after: float = 1.5) -> bool:
    for sel in selectors:
        try:
            el = driver(**sel)
            if el.exists:
                el.click()
                await asyncio.sleep(sleep_after)
                return True
        except Exception:
            pass
    return False


def _exists_any(driver, selectors: List[Dict[str, str]]) -> bool:
    for sel in selectors:
        try:
            if driver(**sel).exists:
                return True
        except Exception:
            pass
    return False


async def _wait_until_any_exists(driver, selectors: List[Dict[str, str]], timeout: float = 8.0, poll: float = 0.5) -> bool:
    start = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - start < timeout:
        if _exists_any(driver, selectors):
            return True
        await asyncio.sleep(poll)
    return False


async def _open_post_and_comment_sheet(driver, post_link: str) -> None:
    toolfacebook_lib.redirect_to(driver, post_link)
    await asyncio.sleep(6.0)
    await _disable_auto_rotation_quick(driver)

    filter_ready_selectors = [
        {"descriptionContains": "thay đổi bộ lọc bình luận"},
        {"textContains": "thay đổi bộ lọc bình luận"},
        {"descriptionContains": "Đang hiển thị Phù hợp nhất bình luận"},
        {"textContains": "Đang hiển thị Phù hợp nhất bình luận"},
        {"descriptionContains": "Đang hiển thị Tất cả bình luận"},
        {"textContains": "Đang hiển thị Tất cả bình luận"},
    ]

    if _exists_any(driver, filter_ready_selectors):
        return

    comment_btn_selectors = [
        {"description": "Bình luận"},
        {"descriptionContains": "Bình luận"},
        {"text": "Bình luận"},
        {"textContains": "Bình luận"},
        {"descriptionContains": "Comment"},
        {"textContains": "Comment"},
    ]

    for _ in range(5):
        clicked = await _click_first_existing(driver, comment_btn_selectors, sleep_after=2.0)

        if _exists_any(driver, filter_ready_selectors):
            return

        if not clicked:
            try:
                driver.swipe_ext("up", scale=0.25)
            except Exception:
                pass
            await asyncio.sleep(1.0)

    raise RuntimeError("Không mở được màn hình bình luận của bài viết")


async def _switch_comment_mode_to_all_comments(driver) -> bool:
    all_mode_selectors = [
        {"descriptionContains": "Đang hiển thị Tất cả bình luận"},
        {"textContains": "Đang hiển thị Tất cả bình luận"},
    ]
    if _exists_any(driver, all_mode_selectors):
        return True

    filter_button_selectors = [
        {"descriptionContains": "Đang hiển thị Phù hợp nhất bình luận"},
        {"textContains": "Đang hiển thị Phù hợp nhất bình luận"},
        {"descriptionContains": "thay đổi bộ lọc bình luận"},
        {"textContains": "thay đổi bộ lọc bình luận"},
    ]

    opened_menu = await _click_first_existing(driver, filter_button_selectors, sleep_after=1.5)
    if not opened_menu:
        return False

    all_comment_option_selectors = [
        {"descriptionContains": "Tất cả bình luận"},
        {"textContains": "Tất cả bình luận"},
    ]

    selected = await _click_first_existing(driver, all_comment_option_selectors, sleep_after=2.0)
    if not selected:
        return False

    return await _wait_until_any_exists(driver, all_mode_selectors, timeout=8.0, poll=0.5)


async def _expand_comment_sections(driver):
    try:
        await toolfacebook_lib.expand_collapse_section(driver)
    except Exception:
        pass

    labels = [
        "Xem thêm bình luận",
        "Xem thêm câu trả lời",
        "Xem câu trả lời",
        "Phản hồi trước đó",
        "View more comments",
        "View replies",
        "See more comments",
    ]

    clicked = True
    rounds = 0
    while clicked and rounds < 8:
        clicked = False
        rounds += 1

        for lab in labels:
            try:
                el = driver(textContains=lab)
                if el.exists:
                    el.click()
                    await asyncio.sleep(1.2)
                    clicked = True
                    break
            except Exception:
                pass

            try:
                el = driver(descriptionContains=lab)
                if el.exists:
                    el.click()
                    await asyncio.sleep(1.2)
                    clicked = True
                    break
            except Exception:
                pass


async def _return_to_comment_sheet_if_needed(driver):
    sheet_selectors = [
        {"descriptionContains": "thay đổi bộ lọc bình luận"},
        {"textContains": "thay đổi bộ lọc bình luận"},
        {"descriptionContains": "Đang hiển thị Tất cả bình luận"},
        {"textContains": "Đang hiển thị Tất cả bình luận"},
        {"descriptionContains": "Đang hiển thị Phù hợp nhất bình luận"},
        {"textContains": "Đang hiển thị Phù hợp nhất bình luận"},
    ]

    if _exists_any(driver, sheet_selectors):
        return

    for _ in range(3):
        try:
            await asyncio.to_thread(driver.press, "back")
        except Exception:
            pass
        await asyncio.sleep(1.0)
        if _exists_any(driver, sheet_selectors):
            return


async def _extract_profile_link_for_commenter(driver, name: str, bounds: Tuple[int, int, int, int]) -> Optional[str]:
    try:
        await _tap_bounds(driver, bounds)

        for lab in PROFILE_OPEN_LABELS:
            try:
                el = driver(textContains=lab)
                if el.exists:
                    el.click()
                    await asyncio.sleep(1.5)
                    break
            except Exception:
                pass

            try:
                el = driver(descriptionContains=lab)
                if el.exists:
                    el.click()
                    await asyncio.sleep(1.5)
                    break
            except Exception:
                pass

        link = await toolfacebook_lib.extract_facebook_user_link(driver)
        if link and isinstance(link, str) and "facebook.com" in link.lower():
            return link.strip()
        return None

    except Exception as e:
        log_message(f"[{_dev(driver)}] Không lấy được link profile của '{name}': {e}", logging.DEBUG)
        return None

    finally:
        try:
            await _return_to_comment_sheet_if_needed(driver)
        except Exception:
            pass


async def _save_crawl_result(
    post_link: str,
    comments: List[Dict[str, Any]],
    device_id: str,
    current_account: str,
    replace_existing: bool = True,
) -> None:
    detail_col = pymongo_management.get_async_collection(COMMENT_COLLECTION)
    crawl_col = pymongo_management.get_async_collection(COMMENT_CRAWL_LOG_COLLECTION)

    now = datetime.now()

    if replace_existing:
        await detail_col.delete_many({"Post_link": post_link})

    if comments:
        docs = []
        for c in comments:
            fingerprint = hashlib.sha1(
                f"{post_link}|{c.get('commenter_name','')}|{c.get('comment_text','')}|{c.get('time_text','')}|{c.get('level',0)}".encode("utf-8")
            ).hexdigest()

            docs.append({
                "Post_link": post_link,
                "Comment_fingerprint": fingerprint,
                "Commenter": c.get("commenter_name", ""),
                "Commenter_profile_link": c.get("commenter_profile_link"),
                "Comment": c.get("comment_text", ""),
                "Time": c.get("time_text"),
                "Level": c.get("level", 0),
                "Visible_top": c.get("visible_top"),
                "Device_id": device_id,
                "Crawled_by_account": current_account,
                "Crawled_at": now,
                "Raw": c,
            })

        await detail_col.insert_many(docs)

    await crawl_col.update_one(
        {"Post_link": post_link},
        {"$set": {
            "Post_link": post_link,
            "Total_comments": len(comments),
            "Device_id": device_id,
            "Crawled_by_account": current_account,
            "Last_crawled_at": now,
        }},
        upsert=True,
    )


async def crawl_post_comments(
    driver,
    command_id: str | None = None,
    post_link: str | None = None,
    include_profile_link: bool = True,
    max_comments: int = 200,
    max_no_new_rounds: int = 2,
    replace_existing: bool = True,
    back_to_facebook: bool = True,
):
    """
    Luồng đúng theo UI hiện tại:
    1) vào link bài viết
    2) mở bình luận
    3) bấm filter 'Phù hợp nhất'
    4) chọn 'Tất cả bình luận'
    5) bắt đầu cào comment
    """
    if not post_link:
        raise ValueError("Thiếu post_link để cào bình luận")

    device_id = getattr(driver, "serial", "") or ""
    current_account = get_current_account(device_id) or ""

    await _ensure_facebook_foreground(driver)
    log_message(f"[{_dev(driver)}] Bắt đầu cào comment cho bài: {post_link}", logging.INFO)

    await _open_post_and_comment_sheet(driver, post_link)

    ok_mode = await _switch_comment_mode_to_all_comments(driver)
    if ok_mode:
        log_message(f"[{_dev(driver)}] Đã chuyển sang chế độ 'Tất cả bình luận'", logging.INFO)
    else:
        log_message(
            f"[{_dev(driver)}] Không chuyển được sang 'Tất cả bình luận', sẽ tiếp tục cào theo màn hình hiện tại",
            logging.WARNING,
        )

    collected: Dict[Tuple[str, str, str, int], Dict[str, Any]] = {}
    profile_cache: Dict[str, Optional[str]] = {}
    no_new_rounds = 0
    round_idx = 0

    while len(collected) < max_comments and no_new_rounds < max_no_new_rounds:
        round_idx += 1

        await _expand_comment_sections(driver)

        try:
            xml = await asyncio.to_thread(driver.dump_hierarchy)
        except Exception as e:
            log_message(f"[{_dev(driver)}] dump_hierarchy lỗi khi cào comment: {e}", logging.WARNING)
            break

        visible = _extract_visible_comment_candidates(xml)
        new_in_round = 0

        for c in visible:
            key = (c["name"], c["comment"], c.get("time_text") or "", c.get("level", 0))
            if key in collected:
                continue

            item = {
                "commenter_name": c["name"],
                "comment_text": c["comment"],
                "time_text": c.get("time_text"),
                "level": c.get("level", 0),
                "visible_top": c["bounds"][1],
                "commenter_profile_link": None,
            }

            if include_profile_link:
                if c["name"] in profile_cache:
                    item["commenter_profile_link"] = profile_cache[c["name"]]
                else:
                    link = await _extract_profile_link_for_commenter(driver, c["name"], c["name_bounds"])
                    profile_cache[c["name"]] = link
                    item["commenter_profile_link"] = link
                    await asyncio.sleep(0.8)
                    await _expand_comment_sections(driver)

            collected[key] = item
            new_in_round += 1

            if len(collected) >= max_comments:
                break

        log_message(
            f"[{_dev(driver)}] Round {round_idx}: thấy {len(visible)} comment candidate, mới {new_in_round}, tổng {len(collected)}",
            logging.INFO,
        )

        if new_in_round == 0:
            no_new_rounds += 1
        else:
            no_new_rounds = 0

        try:
            driver.swipe_ext("up", scale=0.72)
        except Exception:
            try:
                w, h = driver.window_size()
                driver.swipe(w * 0.5, h * 0.90, w * 0.5, h * 0.15, 0.3)
            except Exception:
                pass

        await asyncio.sleep(1.8)

    comments = list(collected.values())
    comments.sort(
        key=lambda x: (
            x.get("level", 0),
            x.get("visible_top") or 0,
            x.get("commenter_name") or "",
        )
    )

    await _save_crawl_result(
        post_link=post_link,
        comments=comments,
        device_id=device_id,
        current_account=current_account,
        replace_existing=replace_existing,
    )

    if command_id and re.fullmatch(r"[0-9a-fA-F]{24}", str(command_id).strip()):
        try:
            await pymongo_management.execute_command(command_id, "Đã thực hiện")
        except Exception:
            pass

    if back_to_facebook:
        try:
            await go_to_home_page(driver)
        except Exception:
            pass

    log_message(f"[{_dev(driver)}] Cào comment xong: {len(comments)} comment", logging.INFO)
    return {
        "ok": True,
        "total_comments": len(comments),
        "post_link": post_link,
        "device_id": device_id,
        "current_account": current_account,
    }
