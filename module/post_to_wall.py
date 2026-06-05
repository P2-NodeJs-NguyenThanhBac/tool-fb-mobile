import asyncio
import pymongo_management
import toolfacebook_lib
import logging
import re
import unicodedata
import xml.etree.ElementTree as ET
from util import log_message, DEVICE_LIST_NAME, load_state

FB_PKG = "com.facebook.katana"


async def ensure_facebook_foreground(driver, timeout=10):
    # Always (re)open FB
    await asyncio.to_thread(driver.app_start, FB_PKG)
    # Wait until FB really in foreground
    for _ in range(timeout * 2):  # check every 0.5s
        try:
            cur = await asyncio.to_thread(driver.app_current)  # returns dict
            if (isinstance(cur, dict) and cur.get("package") == FB_PKG) or \
               (isinstance(cur, str) and FB_PKG in cur):
                return True
        except Exception:
            pass
        await asyncio.sleep(0.5)
    return False


def _parse_bounds_str(bounds: str):
    m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds or "")
    if not m:
        return None
    return tuple(map(int, m.groups()))


def _normalize_match_text(value: str) -> str:
    value = (value or "").replace("\u00a0", " ").replace("\u200b", " ")
    value = re.sub(r"\s+", " ", value).strip().lower()
    return value


def _normalize_compare_text(value: str) -> str:
    value = _normalize_match_text(value)
    value = "".join(
        ch for ch in unicodedata.normalize("NFD", value)
        if unicodedata.category(ch) != "Mn"
    )
    return value.replace("đ", "d")


def _text_match_score(target_text: str, node_text: str) -> float:
    target = _normalize_match_text(target_text)
    node = _normalize_match_text(node_text)
    if not target or not node:
        return 0.0

    if target == node:
        return 1.0
    if target in node:
        return min(0.99, 0.75 + min(len(target), len(node)) / max(len(target), len(node), 1) * 0.2)
    if len(node) >= 12 and node in target:
        return min(0.96, 0.72 + len(node) / max(len(target), 1) * 0.22)

    target_tokens = set(tok for tok in re.split(r"\W+", target) if tok)
    node_tokens = set(tok for tok in re.split(r"\W+", node) if tok)
    if not target_tokens or not node_tokens:
        return 0.0

    overlap = target_tokens & node_tokens
    if not overlap:
        return 0.0

    precision = len(overlap) / max(len(node_tokens), 1)
    recall = len(overlap) / max(len(target_tokens), 1)
    return (precision * 0.65) + (recall * 0.35)


def _overflow_desc(node: ET.Element) -> str:
    return ((node.attrib.get("content-desc") or "") + " " + (node.attrib.get("text") or "")).strip().lower()


def _is_post_overflow_node(node: ET.Element) -> bool:
    hint = _overflow_desc(node)
    if not hint:
        return False
    return (
        "lựa chọn khác cho bài viết" in hint
        or "l?a ch?n khác cho bài viết" in hint
        or "more options for post" in hint
        or ("more options" in hint and "post" in hint)
    )


def _choose_best_overflow_node(candidates: list[ET.Element], content_bounds, screen_w: int):
    if not candidates:
        return None

    left_c, top_c, right_c, bottom_c = content_bounds
    content_top = top_c
    content_mid_y = (top_c + bottom_c) // 2

    best = None
    best_score = None

    for node in candidates:
        bounds = _parse_bounds_str(node.attrib.get("bounds", ""))
        if not bounds:
            continue

        left, top, right, bottom = bounds
        cx = (left + right) // 2
        cy = (top + bottom) // 2

        penalty = 0.0
        if cx < int(screen_w * 0.58):
            penalty += 500

        if cy <= content_top:
            penalty += content_top - cy
        else:
            penalty += (cy - content_top) + 120

        penalty += abs(content_mid_y - cy) * 0.15
        penalty += max(0, int(screen_w * 0.92) - cx) * 0.03

        if best_score is None or penalty < best_score:
            best = node
            best_score = penalty

    return best


def _find_post_overflow_node_by_content_xml(xml: str, content: str, screen_w: int):
    target_text = (content or "").strip()
    if not target_text:
        return None

    root = ET.fromstring(xml)
    matches = []

    def walk(node: ET.Element, ancestors: list[ET.Element]):
        raw_text = " ".join(
            x for x in (
                node.attrib.get("text") or "",
                node.attrib.get("content-desc") or "",
            ) if x
        )
        score = _text_match_score(target_text, raw_text)
        if score >= 0.65:
            content_bounds = _parse_bounds_str(node.attrib.get("bounds", ""))
            if content_bounds:
                for depth, ancestor in enumerate(reversed(ancestors + [node])):
                    overflow_nodes = [child for child in ancestor.iter() if _is_post_overflow_node(child)]
                    if not overflow_nodes:
                        continue

                    chosen = _choose_best_overflow_node(overflow_nodes, content_bounds, screen_w)
                    if chosen is None:
                        continue

                    matches.append({
                        "score": score,
                        "depth": depth,
                        "content_bounds": content_bounds,
                        "overflow_node": chosen,
                        "matched_text": raw_text,
                    })
                    break

        next_ancestors = ancestors + [node]
        for child in list(node):
            walk(child, next_ancestors)

    walk(root, [])

    if not matches:
        return None

    matches.sort(
        key=lambda item: (
            -item["score"],
            item["depth"],
            item["content_bounds"][1],
        )
    )
    return matches[0]


async def _bring_feed_towards_top(driver):
    try:
        size = driver.window_size()
        if isinstance(size, dict):
            w = int(size.get("width", 0))
            h = int(size.get("height", 0))
        else:
            w, h = size

        x = int(w * 0.5)
        start_y = int(h * 0.32)
        end_y = int(h * 0.82)
        await asyncio.to_thread(driver.swipe, x, start_y, x, end_y, 300)
        await asyncio.sleep(1.0)
    except Exception:
        pass


async def _dismiss_post_sheet_if_needed(driver):
    close_btn = driver(description="Đóng")
    if close_btn.exists:
        try:
            await asyncio.to_thread(close_btn.click)
            await asyncio.sleep(0.8)
            return
        except Exception:
            pass

    try:
        await asyncio.to_thread(driver.press, "back")
        await asyncio.sleep(0.8)
    except Exception:
        pass


def _looks_like_facebook_url(value: str) -> bool:
    s = (value or "").strip().lower()
    return bool(s) and (
        s.startswith("http://")
        or s.startswith("https://")
        or "facebook.com" in s
        or "m.facebook.com" in s
        or "fb.watch" in s
    )


async def copy_post_link_for_wall_by_content_text(driver, content: str):
    content = (content or "").strip()
    if not content:
        return None

    await asyncio.sleep(5.0)

    for attempt in range(1, 4):
        try:
            xml = await asyncio.to_thread(driver.dump_hierarchy)
            size = driver.window_size()
            if isinstance(size, dict):
                screen_w = int(size.get("width", 0))
            else:
                screen_w = int(size[0])

            match = _find_post_overflow_node_by_content_xml(xml, content, screen_w)
            if not match:
                log_message(
                    f"{DEVICE_LIST_NAME[driver.serial]} - Không match được bài theo content ở attempt {attempt}",
                    logging.WARNING,
                )
                if attempt == 3:
                    try:
                        open("wall_copy_link_match_failed.xml", "w", encoding="utf-8").write(xml)
                    except Exception:
                        pass
                    return None

                await _bring_feed_towards_top(driver)
                continue

            overflow_bounds = _parse_bounds_str(match["overflow_node"].attrib.get("bounds", ""))
            if not overflow_bounds:
                return None

            left, top, right, bottom = overflow_bounds
            x = (left + right) // 2
            y = (top + bottom) // 2

            log_message(
                f"{DEVICE_LIST_NAME[driver.serial]} - Match bài để copy link: "
                f"score={match['score']:.2f} text={match['matched_text']!r} overflow=({x},{y}) attempt={attempt}",
                logging.INFO,
            )

            await asyncio.to_thread(driver.click, x, y)
            await asyncio.sleep(1.2)

            clicked_copy = await toolfacebook_lib.click_copy_link_from_sheet(driver)
            if not clicked_copy:
                log_message(
                    f"{DEVICE_LIST_NAME[driver.serial]} - Mở được menu nhưng không click được 'Sao chép liên kết' ở attempt {attempt}",
                    logging.WARNING,
                )
                await _dismiss_post_sheet_if_needed(driver)
                continue

            await asyncio.sleep(1.5 if attempt == 1 else 2.0)

            txt = await toolfacebook_lib.get_clipboard_content_via_termux_adb_typing(
                driver,
                "com.facebook.katana",
                device_id=driver.serial,
            )
            if _looks_like_facebook_url(txt):
                return txt.strip()

            log_message(
                f"{DEVICE_LIST_NAME[driver.serial]} - Clipboard sau copy link attempt {attempt} không hợp lệ: {txt!r}",
                logging.WARNING,
            )
        except Exception as e:
            log_message(
                f"{DEVICE_LIST_NAME[driver.serial]} - Lỗi copy link bài tường theo content ở attempt {attempt}: {e}",
                logging.WARNING,
            )
            try:
                xml = await asyncio.to_thread(driver.dump_hierarchy)
                open(f"wall_copy_link_attempt_{attempt}.xml", "w", encoding="utf-8").write(xml)
            except Exception:
                pass

        await _dismiss_post_sheet_if_needed(driver)
        if attempt < 3:
            await _bring_feed_towards_top(driver)

    return None


def _find_post_overflow_node_by_account_name_xml(xml: str, account_name: str, screen_w: int):
    target_name = _normalize_compare_text(account_name)
    if not target_name:
        return None

    root = ET.fromstring(xml)
    candidates = []

    for node in root.iter():
        if not _is_post_overflow_node(node):
            continue

        desc = (node.attrib.get("content-desc") or "") + " " + (node.attrib.get("text") or "")
        desc_norm = _normalize_compare_text(desc)
        if target_name not in desc_norm:
            continue

        bounds = _parse_bounds_str(node.attrib.get("bounds", ""))
        if not bounds:
            continue

        left, top, right, bottom = bounds
        cx = (left + right) // 2
        cy = (top + bottom) // 2
        if cx < int(screen_w * 0.58):
            continue

        candidates.append({
            "bounds": bounds,
            "desc": desc,
            "cy": cy,
            "cx": cx,
        })

    if not candidates:
        return None

    candidates.sort(key=lambda item: (item["cy"], -item["cx"]))
    return candidates[0]


async def copy_post_link_for_wall_by_account_name(driver, account_name: str):
    account_name = (account_name or "").strip()
    if not account_name:
        return None

    await asyncio.sleep(2.0)

    for attempt in range(1, 4):
        try:
            xml = await asyncio.to_thread(driver.dump_hierarchy)
            size = driver.window_size()
            if isinstance(size, dict):
                screen_w = int(size.get("width", 0))
            else:
                screen_w = int(size[0])

            match = _find_post_overflow_node_by_account_name_xml(xml, account_name, screen_w)
            if not match:
                log_message(
                    f"{DEVICE_LIST_NAME[driver.serial]} - Không tìm thấy overflow theo account '{account_name}' ở attempt {attempt}",
                    logging.WARNING,
                )
                if attempt == 3:
                    try:
                        open("wall_copy_link_account_match_failed.xml", "w", encoding="utf-8").write(xml)
                    except Exception:
                        pass
                    return None

                await _bring_feed_towards_top(driver)
                continue

            left, top, right, bottom = match["bounds"]
            x = (left + right) // 2
            y = (top + bottom) // 2

            log_message(
                f"{DEVICE_LIST_NAME[driver.serial]} - Match overflow theo account '{account_name}' "
                f"desc={match['desc']!r} at=({x},{y}) attempt={attempt}",
                logging.INFO,
            )

            await asyncio.to_thread(driver.click, x, y)
            await asyncio.sleep(1.2)

            clicked_copy = await toolfacebook_lib.click_copy_link_from_sheet(driver)
            if not clicked_copy:
                log_message(
                    f"{DEVICE_LIST_NAME[driver.serial]} - Không click được 'Sao chép liên kết' sau khi match theo account ở attempt {attempt}",
                    logging.WARNING,
                )
                await _dismiss_post_sheet_if_needed(driver)
                continue

            await asyncio.sleep(1.5 if attempt == 1 else 2.0)

            txt = await toolfacebook_lib.get_clipboard_content_via_termux_adb_typing(
                driver,
                "com.facebook.katana",
                device_id=driver.serial,
            )
            if _looks_like_facebook_url(txt):
                return txt.strip()

            log_message(
                f"{DEVICE_LIST_NAME[driver.serial]} - Clipboard sau fallback account attempt {attempt} không hợp lệ: {txt!r}",
                logging.WARNING,
            )
        except Exception as e:
            log_message(
                f"{DEVICE_LIST_NAME[driver.serial]} - Lỗi copy link bài tường theo account ở attempt {attempt}: {e}",
                logging.WARNING,
            )

        await _dismiss_post_sheet_if_needed(driver)
        if attempt < 3:
            await _bring_feed_towards_top(driver)

    return None


async def select_recent_photos(driver, count):
    """
    Chọn `count` tấm ảnh trong màn hình chọn ảnh của Facebook
    bằng cách chia vùng giữa màn hình thành lưới và tap theo toạ độ.

    - Không phụ thuộc vào cây UI (FrameLayout/ImageView).
    - Giới hạn số ảnh trong khung đầu tiên (không lướt).
    - Thứ tự: trái -> phải, trên -> dưới.
    """
    try:
        await asyncio.sleep(2)  # đợi UI load grid ảnh

        # 1) Lấy kích thước màn hình
        size = driver.window_size()
        if isinstance(size, dict):
            screen_w = size.get("width", 0)
            screen_h = size.get("height", 0)
        elif isinstance(size, (list, tuple)) and len(size) >= 2:
            screen_w, screen_h = size[0], size[1]
        else:
            screen_w = screen_h = 0

        if not screen_w or not screen_h:
            log_message(
                f"{DEVICE_LIST_NAME[driver.serial]} - Không lấy được kích thước màn hình, bỏ qua chọn ảnh theo lưới",
                logging.WARNING,
            )
            return 0

        # 2) Xác định vùng chứa grid ảnh (bỏ header + nav bar)
        top = int(screen_h * 0.20)   # bỏ 20% trên cùng
        bottom = int(screen_h * 0.85)  # bỏ 15% dưới cùng
        left = 0
        right = screen_w

        # 3) Cấu hình lưới: 3 cột x 4 hàng = tối đa 12 ảnh
        cols = 3
        rows = 4
        max_slots = rows * cols

        # Số ảnh thực sự cần chọn (không vượt quá số ô)
        need = min(count, max_slots)
        if need <= 0:
            return 0

        cell_w = (right - left) / cols
        cell_h = (bottom - top) / rows

        log_message(
            f"{DEVICE_LIST_NAME[driver.serial]} - Grid ảnh: {rows}x{cols}, need={need}, "
            f"screen=({screen_w}x{screen_h}), vùng=({left},{top})-({right},{bottom})",
            logging.INFO,
        )

        selected = 0
        for i in range(need):
            row = i // cols
            col = i % cols
            if row >= rows:
                break  # an toàn

            # Tính tâm ô (x, y)
            x = int(left + (col + 0.5) * cell_w)
            y = int(top + (row + 0.5) * cell_h)

            try:
                # uiautomator2: driver.click(x, y)
                await asyncio.to_thread(driver.click, x, y)
                selected += 1
                await asyncio.sleep(0.5)
            except Exception as e:
                log_message(
                    f"{DEVICE_LIST_NAME[driver.serial]} - Lỗi click ô ({row},{col}) tại ({x},{y}): {e}",
                    logging.WARNING,
                )
                continue

        return selected
    except Exception as e:
        log_message(
            f"{DEVICE_LIST_NAME[driver.serial]} - Lỗi khi chọn ảnh theo lưới: {e}",
            logging.WARNING,
        )
        return 0


async def post_to_wall(driver, command_id, user_id, content, files=None, *, return_result: bool = False):
    def _result(ok: bool, *, reason: str = "", post_link: str | None = None, post_status: str | None = None):
        payload = {
            "ok": bool(ok),
            "reason": reason or "",
            "post_link": post_link,
            "post_status": post_status,
            "content": content or "",
            "user_id": user_id,
        }
        return payload if return_result else bool(ok)

    # 0) Đẩy file sang máy nếu có
    pushed_remote_files = []
    if files:
        for file in files:
            pushed_name = await toolfacebook_lib.push_file_to_device(driver.serial, file)
            if pushed_name:
                pushed_remote_files.append(pushed_name)
        # Mở sẵn Gallery (tuỳ UI FB có dùng hay không, giữ nguyên hành vi cũ)
        driver.app_start("com.miui.gallery")

    # 1) Quay lại Facebook + đảm bảo FB foreground
    await toolfacebook_lib.back_to_facebook(driver)

    ok = await ensure_facebook_foreground(driver, timeout=10)
    if not ok:
        log_message(
            f"{DEVICE_LIST_NAME[driver.serial]} - Không mở được Facebook ở foreground",
            logging.WARNING,
        )
        await pymongo_management.execute_command(
            command_id, "Lỗi: FB không lên foreground"
        )
        return _result(False, reason="Lỗi: FB không lên foreground")

    # 2) Tìm ô soạn bài (composer) – có thể feed đang không ở đỉnh nên phải kéo lên
    composer = None
    entry_candidates = (
    {"description": "Bạn đang nghĩ gì?"},
    {"descriptionContains": "Bạn đang nghĩ gì"},
    {"text": "Bạn đang nghĩ gì?"},

    {"description": "What's on your mind?"},
    {"descriptionContains": "on your mind"},
    {"text": "What's on your mind?"},
    {"text": "What’s on your mind?"},
    {"text": "Create post"},
    )

    # Thử tối đa 4 lần: mỗi lần tìm, nếu chưa thấy thì kéo feed + bấm lại Home (nếu có)
    for attempt in range(4):
        # 2.1) Tìm trực tiếp theo text
        for sel in entry_candidates:
            el = driver(**sel)
            if el.exists:
                composer = el
                break

        # 2.2) Nếu vẫn chưa thấy thì thử XPath rộng hơn
        if composer is None:
            xp = (
              "//*["
              "contains(@text,'nghĩ gì') or contains(@content-desc,'nghĩ gì') or "
              "contains(@text,'Viết bài') or contains(@content-desc,'Viết bài') or "
              "contains(@text,'Tạo bài') or contains(@content-desc,'Tạo bài') or "
              "contains(@text,\"What's on your mind\") or contains(@content-desc,\"on your mind\") or "
              "contains(@text,'Create post') or contains(@content-desc,'Create post')"
              "]"
            )
            el = driver.xpath(xp)
            if el.exists:
                composer = el

        # Nếu đã tìm thấy thì dừng vòng lặp
        if composer is not None:
            break

        # 2.3) Nếu chưa thấy composer: kéo feed để lộ phần đầu (swipe từ trên xuống dưới)
        try:
            size = driver.window_size()
            if isinstance(size, dict):
                w = size.get("width", 0)
                h = size.get("height", 0)
            else:
                w, h = size

            x = int(w * 0.5)
            start_y = int(h * 0.3)
            end_y = int(h * 0.8)
            # Swipe từ trên xuống dưới để cuộn list lên trên (đưa phần đầu feed ra)
            await asyncio.to_thread(driver.swipe, x, start_y, x, end_y, 300)
            await asyncio.sleep(1.0)
        except Exception:
            pass

        # 2.4) Thử bấm lại tab Trang chủ nếu có (một số UI có icon/tab Home)
        for sel_home in (
            {"description": "Trang chủ"},
            {"text": "Trang chủ"},
            {"description": "Home"},
            {"text": "Home"},
        ):
            home_btn = driver(**sel_home)
            if home_btn.exists:
                try:
                    await asyncio.to_thread(home_btn.click)
                    await asyncio.sleep(1.0)
                except Exception:
                    pass
                break  # tránh bấm nhiều lần

    # Sau khi thử nhiều lần mà vẫn không tìm được composer -> báo lỗi
    if composer is None:
        try:
            xml = driver.dump_hierarchy()
            open("composer_debug.xml", "w", encoding="utf-8").write(xml)
        except Exception:
            pass
        log_message(
            f"{DEVICE_LIST_NAME[driver.serial]} - Đăng bài lên tường: Không thấy ô soạn bài (composer)",
            logging.WARNING,
        )
        await pymongo_management.execute_command(
            command_id, "Lỗi: Không thấy ô soạn bài"
        )
        return _result(False, reason="Lỗi: Không thấy ô soạn bài")

    # Tới đây chắc chắn đã có composer trên màn hình
    composer.click()
    await asyncio.sleep(1.0)

    # ===== Thiết lập option media-first / text-first =====
    attach_media = isinstance(files, (list, tuple)) and len(files) > 0
    content = content or ""

    media_first = False
    if attach_media:
        # Rule đơn giản: nếu tất cả file là video -> media-first
        lower_files = [str(f).lower() for f in files]
        if all(f.endswith((".mp4", ".mov", ".mkv", ".avi")) for f in lower_files) and not content.strip():
            media_first = True
    # (Sau này nếu muốn dùng cờ từ CRM, có thể override media_first ở đây.)

    async def do_attach_media():
        """Chọn ảnh/video trong picker theo logic hiện tại (reuse cho cả 2 flow)."""
        if not attach_media:
            return

        # Open photo/video picker (accept VN/EN, desc/text)
        opened = False
        for sel in (
            {"description": "Ảnh/video"}, {"text": "Ảnh/video"},
            {"description": "Photo/video"}, {"text": "Photo/video"},
            {"text": "Thư viện"}, {"description": "Thư viện"},
        ):
            el = driver(**sel)
            if el.exists:
                el.click()
                opened = True
                await asyncio.sleep(1.0)
                break

        # Fallback: tìm theo textContains/descriptionContains (phòng UI đổi chữ)
        if not opened:
            for el in [
                driver(textContains="Ảnh"),
                driver(textContains="ảnh"),
                driver(textContains="Video"),
                driver(textContains="video"),
                driver(descriptionContains="Ảnh"),
                driver(descriptionContains="ảnh"),
                driver(descriptionContains="Video"),
                driver(descriptionContains="video"),
            ]:
                try:
                    if el.exists:
                        await asyncio.to_thread(el.click)
                        opened = True
                        await asyncio.sleep(1.0)
                        break
                except Exception:
                    continue

        if not opened:
            log_message(
                f"{DEVICE_LIST_NAME[driver.serial]} - Không thấy nút Ảnh/Video",
                logging.WARNING,
            )
            await pymongo_management.execute_command(
                command_id, "Lỗi: Không thấy nút Ảnh/Video"
            )
            return

        num_files = len(files)

        # ===== TRƯỜNG HỢP 1: ĐĂNG 1 ẢNH/VIDEO =====
        if num_files <= 1:
            # Chọn 1 thumbnail -> FB tự thoát về màn hình soạn bài
            selected = await select_recent_photos(driver, 1)
            if selected < 1:
                log_message(
                    f"{DEVICE_LIST_NAME[driver.serial]} - Không chọn được media nào (1 file)",
                    logging.WARNING,
                )
                # fallback: quay lại màn soạn bài cho đỡ kẹt
                try:
                    await asyncio.to_thread(driver.press, "back")
                    await asyncio.sleep(1)
                except Exception:
                    pass
            return

        # ===== TRƯỜNG HỢP 2: ĐĂNG NHIỀU ẢNH/VIDEO =====
        # 1) Bật chế độ "Chọn nhiều file"
        multi_clicked = False

        multi_txt = driver(textContains="Chọn nhiều")
        if multi_txt.exists:
            info = getattr(multi_txt, "info", {}) or {}
            clickable = info.get("clickable", False)

            if clickable:
                await asyncio.to_thread(multi_txt.click)
                multi_clicked = True
                await asyncio.sleep(0.5)
            else:
                size = driver.window_size()
                if isinstance(size, dict):
                    w = size.get("width", 0)
                    h = size.get("height", 0)
                else:
                    w, h = size

                # nút nằm gần giữa theo chiều ngang, hơi dưới header 1 chút
                x = int(w * 0.7)
                y = int(h * 0.15)

                await asyncio.to_thread(driver.click, x, y)
                multi_clicked = True
                await asyncio.sleep(0.5)

        # 1B. Nếu KHÔNG CÓ nút "Chọn nhiều" → giả định UI cho phép chọn nhiều file trực tiếp
        if not multi_clicked:
            log_message(
                f"{DEVICE_LIST_NAME[driver.serial]} - Không thấy nút 'Chọn nhiều', "
                f"giả định có thể chọn nhiều file trực tiếp rồi nhấn 'Tiếp'.",
                logging.INFO,
            )
            # Không bắn toạ độ nữa, cứ để nguyên UI

        # 2) Chọn num_files thumbnail đầu tiên bằng lưới (dù có hay không có 'Chọn nhiều')
        selected = await select_recent_photos(driver, num_files)
        if selected < num_files:
            log_message(
                f"{DEVICE_LIST_NAME[driver.serial]} - Chỉ chọn được {selected}/{num_files} media trong picker",
                logging.WARNING,
            )

        # 3) Bấm "Tiếp"/"Next" để xác nhận
        if selected > 0:
            for sel2 in (
                {"text": "Tiếp"},
                {"description": "Tiếp"},
                {"text": "Tiếp tục"},
                {"description": "Tiếp tục"},
                {"text": "Next"},
                {"description": "Next"},
            ):
                btn = driver(**sel2)
                if btn.exists:
                    await asyncio.to_thread(btn.click)
                    await asyncio.sleep(1)
                    break
            else:
                try:
                    await asyncio.to_thread(driver.press, "back")
                    await asyncio.sleep(1)
                except Exception:
                    pass

    # ================== NHÁNH 1: MEDIA TRƯỚC - TEXT SAU (flow mới, tuỳ chọn) ==================
    if media_first and attach_media:
        # 1) Chọn media trước
        await do_attach_media()

        # 2) Điền caption sau khi đã gắn media (nếu có content)
        if content.strip():
            caption_candidates = (
                {"textContains": "Bạn đang nghĩ gì?"},
                {"textContains": "Hãy nói gì đó"},
                {"textContains": "viết gì đó"},
                {"textContains": "Write something"},
            )
            target = None
            for sel in caption_candidates:
                el = driver(**sel)
                if el.exists:
                    target = el
                    break

            if target and target.exists:
                try:
                    target.click()
                    log_message(
                        f"{DEVICE_LIST_NAME[driver.serial]} - (media-first) Bắt đầu set caption: {content!r}",
                        logging.INFO,
                    )
                    target.set_text(content)
                    for sel in (
                        {"text": "Xong"},
                        {"description": "Xong"},
                        {"text": "Done"},
                        {"description": "Done"},
                    ):
                        btn = driver(**sel)
                        if btn.exists:
                            await asyncio.to_thread(btn.click)
                            await asyncio.sleep(1.0)
                            break
                except Exception as e:
                    log_message(
                        f"{DEVICE_LIST_NAME[driver.serial]} - (media-first) Lỗi khi set caption: {e}",
                        logging.WARNING,
                    )

    # ================== NHÁNH 2: TEXT TRƯỚC - MEDIA SAU (flow cũ, mặc định) ==================
    else:
        # 1) Điền text trước
        await asyncio.sleep(2)
        el = driver(textContains="Bạn đang nghĩ gì?")
        if el.exists:
            for _ in range(3):
                if el.exists:
                    await asyncio.to_thread(el.click)
                    await asyncio.sleep(0.4)
            log_message(
                f"{DEVICE_LIST_NAME[driver.serial]} - Bắt đầu set text (text-first): {content!r}",
                logging.INFO,
            )
            el.set_text(content)
            for sel in (
                {"text": "Xong"},
                {"description": "Xong"},
                {"text": "Done"},
                {"description": "Done"},
            ):
                btn = driver(**sel)
                if btn.exists:
                    await asyncio.to_thread(btn.click)
                    await asyncio.sleep(1.0)
                    break

        # 2) Sau đó mới gắn media (nếu có)
        if attach_media:
            await do_attach_media()

    # 3) Sau khi đã có nội dung + media, FB đôi khi yêu cầu bấm "Tiếp"/"Next" thêm một lần
    for sel2 in (
        {"text": "Tiếp"},
        {"description": "Tiếp"},
        {"text": "Tiếp tục"},
        {"description": "Tiếp tục"},
        {"text": "Next"},
        {"description": "Next"},
    ):
        btn = driver(**sel2)
        if btn.exists:
            await asyncio.to_thread(btn.click)
            await asyncio.sleep(1)
            break

    # 4) Tìm nút "Đăng"/"Post"/"Chia sẻ"/"Share" để publish
    published = False
    post_candidates = (
        {"text": "Đăng"}, {"description": "Đăng"},
        {"text": "Chia sẻ"}, {"description": "Chia sẻ"},
        {"text": "Post"}, {"text": "POST"},
        {"text": "Share"}, {"description": "Share"},
    )
    for sel in post_candidates:
        el = driver(**sel)
        if el.exists:
            await asyncio.to_thread(el.click)
            published = True
            break

    # Second: resource-id fallbacks (Facebook Katana common ids)
    if not published:
        for rid in (
            "com.facebook.katana:id/primary_named_button",
            "com.facebook.katana:id/done_button",
            "com.facebook.katana:id/composer_post",
        ):
            el = driver(resourceId=rid)
            if el.exists:
                await asyncio.to_thread(el.click)
                published = True
                break

    # Third: generic enabled Button with matching text (regex, case-insensitive)
    if not published:
        try:
            btns = driver(className="android.widget.Button", enabled=True)
            for b in btns:
                info = getattr(b, "info", {}) or {}
                label = (info.get("text") or "").strip().lower()
                if label in {"đăng", "post", "chia sẻ", "share"}:
                    await asyncio.to_thread(b.click)
                    published = True
                    break
        except Exception:
            pass

    if not published:
        # Optional: dump UI to inspect the exact button label on this build
        try:
            xml = driver.dump_hierarchy()
            open("composer_post_debug.xml", "w", encoding="utf-8").write(xml)
        except Exception:
            pass
        log_message(
            f"{DEVICE_LIST_NAME[driver.serial]} - Đăng bài lên tường: Không tìm thấy nút ĐĂNG",
            logging.WARNING,
        )
        await pymongo_management.execute_command(
            command_id, "Lỗi: Không tìm thấy nút ĐĂNG"
        )
        return _result(False, reason="Lỗi: Không tìm thấy nút ĐĂNG")

    log_message(
        f"[{DEVICE_LIST_NAME[driver.serial]}] Đăng bài lên tường: Đợi bài viết hoàn tất",
        logging.INFO,
    )
    ok = await toolfacebook_lib.wait_published_snackbar(driver, timeout=300, poll=2.0)
    if ok:
        log_message(
        f"{DEVICE_LIST_NAME[driver.serial]} - Đăng bài lên tường: Đã đăng bài viết lên tường",
        logging.INFO,
    )
    else:
        log_message(
        f"{DEVICE_LIST_NAME[driver.serial]} - Đăng bài lên tường: Đã đăng bài viết lên tường muộn",
        logging.INFO,
    )
    post_link = await copy_post_link_for_wall_by_content_text(driver, content)
    if not post_link:
        current_username = ""
        try:
            state = load_state(driver.serial)
            if isinstance(state, dict):
                current_username = str(state.get("current_username") or "").strip()
        except Exception:
            current_username = ""

        if current_username:
            log_message(
                f"{DEVICE_LIST_NAME[driver.serial]} - Fallback lấy link theo account đang online: {current_username}",
                logging.INFO,
            )
            post_link = await copy_post_link_for_wall_by_account_name(driver, current_username)

    if not post_link:
        log_message(
            f"{DEVICE_LIST_NAME[driver.serial]} - Không lấy được link theo content, fallback sang luồng copy link cũ",
            logging.WARNING,
        )
        post_link = await toolfacebook_lib.copy_latest_post_link_for_wall(driver)

    await asyncio.sleep(2.0)
    
    if post_link:
        await pymongo_management.update_post_link(command_id, post_link)
        log_message(
            f"{DEVICE_LIST_NAME[driver.serial]} - Đã cập nhật link bài viết: {post_link}",
            logging.INFO,
        )
        await pymongo_management.execute_command(command_id, "Đã thực hiện")
        final_result = _result(True, post_link=post_link, post_status="posted")
    else:
        log_message(
            f"{DEVICE_LIST_NAME[driver.serial]} - Không lấy được link bài viết sau khi đăng",
            logging.WARNING,
        )
        await pymongo_management.execute_command(command_id, "Lỗi: Đăng bài thành công nhưng không lấy được link bài viết")
        final_result = _result(False, reason="Lỗi: Đăng bài thành công nhưng không lấy được link bài viết")

    if pushed_remote_files:
        for remote_name in pushed_remote_files:
            await toolfacebook_lib.delete_file(driver.serial, remote_name)

    return final_result

