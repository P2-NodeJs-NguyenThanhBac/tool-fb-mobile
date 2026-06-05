import asyncio
import pymongo_management
import toolfacebook_lib
import logging
from util import *
from module.join_group import get_group_membership_state, join_group
from main_merged import disable_auto_rotation
import random

POST_CLICKED_CHECKING_STATUS = "Đã Đăng - đang kiểm tra kết quả"
POST_LINK_FETCH_FAILED_STATUS = "Đã đăng - Lấy link bài viết lỗi"
POST_LINK_FETCH_MAX_ATTEMPTS = 3

async def random_human_delay(driver, min_sec=3.0, max_sec=5.0, label=""):
    delay = random.uniform(min_sec, max_sec)
    log_message(
        f"{DEVICE_LIST_NAME[driver.serial]} - Chờ {delay:.2f}s {label}".strip(),
        logging.INFO,
    )
    await asyncio.sleep(delay)

async def mark_post_clicked_checking(driver, command_id):
    if not command_id:
        return

    try:
        update_msg, update_level = await pymongo_management.execute_command(
            command_id,
            POST_CLICKED_CHECKING_STATUS,
        )
        log_message(
            f"{DEVICE_LIST_NAME[driver.serial]} - Cập nhật trạng thái sau khi bấm Đăng: {update_msg}",
            update_level,
        )
    except Exception as e:
        log_message(
            f"{DEVICE_LIST_NAME[driver.serial]} - Không cập nhật được trạng thái '{POST_CLICKED_CHECKING_STATUS}': {e}",
            logging.WARNING,
        )


def _looks_like_facebook_url(s: str) -> bool:
    s = (s or "").strip().lower()
    return bool(s) and (
        s.startswith("http://")
        or s.startswith("https://")
        or "facebook.com" in s
        or "m.facebook.com" in s
        or "fb.watch" in s
        or "groups/" in s
    )


def _facebook_group_url(group_link: str) -> str:
    group_link = (group_link or "").strip()
    if group_link.startswith(("http://", "https://")):
        return group_link
    return "https://facebook.com/" + group_link.lstrip("/")


class PostNotApprovedError(RuntimeError):
    pass


async def retry_fetch_group_post_link(
    driver,
    command_id,
    user_id,
    content,
    *,
    group_link: str,
    return_result: bool = False,
):
    def _result(ok: bool, *, reason: str = "", post_link: str | None = None, post_status: str | None = None):
        payload = {
            "ok": bool(ok),
            "reason": reason or "",
            "post_link": post_link,
            "post_status": post_status,
            "group_link": group_link,
            "content": content or "",
            "user_id": user_id,
        }
        return payload if return_result else bool(ok)

    if not group_link:
        raise RuntimeError("group_link is required")
    if not content:
        raise RuntimeError("content is required")

    async def _open_manage_group_posts_for_link_fetch(link_attempt: int) -> bool:
        log_message(
            f"{DEVICE_LIST_NAME[driver.serial]} - Retry lấy link bài viết lần {link_attempt}/{POST_LINK_FETCH_MAX_ATTEMPTS}: mở group {group_link}",
            logging.INFO,
        )
        toolfacebook_lib.redirect_to(driver, _facebook_group_url(group_link))
        await asyncio.sleep(2.0)
        await disable_auto_rotation(driver, driver.serial)
        await click_post_flow_intermediate_buttons(
            driver,
            context="retry_fetch_open_group_before_refresh",
            max_clicks=3,
        )
        await toolfacebook_lib.refresh_group_page_after_post(driver, sleep_after=3.0)
        await click_post_flow_intermediate_buttons(
            driver,
            context="retry_fetch_after_refresh",
            max_clicks=3,
        )

        ok_manage = await toolfacebook_lib.open_manage_group_posts_via_member_tools(driver, timeout=8.0)
        if not ok_manage:
            log_message(
                f"{DEVICE_LIST_NAME[driver.serial]} - Retry lấy link: luồng Quản lý nội dung thất bại, fallback luồng cũ",
                logging.WARNING,
            )
            composer_opener = await find_composer_opener_with_swipes(driver, max_light_swipes=4)
            if composer_opener:
                b = composer_opener.info["bounds"]
                w_icon = int((b["right"] - b["left"]) * 0.18)
                x = max(5, int(b["left"] - w_icon * 0.6))
                y = int((b["top"] + b["bottom"]) / 2)
                await asyncio.to_thread(driver.click, x, y)
                await asyncio.sleep(1.0)
                await click_post_flow_intermediate_buttons(
                    driver,
                    context="retry_fetch_after_fallback_menu_click",
                    max_clicks=2,
                )
            ok_manage = await toolfacebook_lib.open_manage_group_posts(driver, timeout=10.0)

        return ok_manage

    async def _fetch_post_link_once(link_attempt: int):
        ok_manage = await _open_manage_group_posts_for_link_fetch(link_attempt)
        if not ok_manage:
            raise RuntimeError("Không mở được màn 'Nội dung của bạn'")

        await click_post_flow_intermediate_buttons(
            driver,
            context="retry_fetch_before_detect_status",
            max_clicks=3,
        )

        try:
            current_status, current_debug = await toolfacebook_lib.detect_group_post_status_by_content_text(
                driver,
                content,
                min_score=0.65,
            )
            log_message(f"[TEXT_MATCH][RETRY_LINK] status={current_status} debug={current_debug}", logging.INFO)
        except Exception as e:
            raise RuntimeError(f"Lỗi detect theo content: {e}") from e

        if current_status in ("rejected", "removed"):
            await pymongo_management.execute_command(command_id, "Bài viết không được duyệt")
            raise PostNotApprovedError(f"Bài viết không được duyệt (status={current_status})")

        if current_status in ("unknown", None):
            raise RuntimeError(f"Chưa xác định được trạng thái bài viết để lấy link (status={current_status})")

        await click_post_flow_intermediate_buttons(
            driver,
            context="retry_fetch_before_copy_link",
            max_clicks=2,
        )
        current_post_link = await toolfacebook_lib.copy_post_link_for_group_by_content_text(
            driver,
            content,
            detect_debug=current_debug,
        )
        if not _looks_like_facebook_url(current_post_link):
            raise RuntimeError(f"Link không hợp lệ để lưu Mongo: {current_post_link!r}")

        return current_post_link, current_status

    post_link = None
    status = None
    last_link_error = ""

    for link_attempt in range(1, POST_LINK_FETCH_MAX_ATTEMPTS + 1):
        try:
            post_link, status = await _fetch_post_link_once(link_attempt)
            break
        except PostNotApprovedError:
            raise
        except Exception as e:
            last_link_error = str(e)
            log_message(
                f"{DEVICE_LIST_NAME[driver.serial]} - Retry lấy link lần {link_attempt}/{POST_LINK_FETCH_MAX_ATTEMPTS} lỗi: {last_link_error}",
                logging.WARNING,
            )
            if link_attempt < POST_LINK_FETCH_MAX_ATTEMPTS:
                await asyncio.sleep(2.0)

    if post_link and _looks_like_facebook_url(post_link):
        update_msg, update_level = await pymongo_management.update_post_link(
            command_id,
            post_link,
            post_status=status,
        )
        log_message(
            f"{DEVICE_LIST_NAME[driver.serial]} - Retry update_post_link result: {update_msg}",
            update_level,
        )
        track_msg, track_level = await pymongo_management.ensure_group_post_tracking_record(
            command_id,
            user_id=user_id,
            content=content,
            group_link=group_link,
            link=post_link,
            post_status=status,
            device_id=driver.serial,
            source="retry_fetch_group_post_link",
        )
        log_message(
            f"{DEVICE_LIST_NAME[driver.serial]} - Retry ensure Bai-dang tracking result: {track_msg}",
            track_level,
        )
        await pymongo_management.execute_command(command_id, "Đã thực hiện", post_status=status)
        await toolfacebook_lib.back_to_facebook(driver)
        return _result(True, post_link=post_link, post_status=status)

    await pymongo_management.execute_command(
        command_id,
        POST_LINK_FETCH_FAILED_STATUS,
        post_status=status,
    )
    await toolfacebook_lib.back_to_facebook(driver)
    return _result(
        True,
        reason=POST_LINK_FETCH_FAILED_STATUS,
        post_status=status or "unknown",
    )


async def click_done_if_present(driver, settle_after_click=1.5):
    """
    Một số group sau khi nhập nội dung sẽ hiện màn phụ có nút 'Xong'.
    Bấm 'Xong' để quay về composer chính, lúc đó nút 'Đăng' mới xuất hiện.
    """
    done_candidates = (
        {"text": "Xong"},
        {"description": "Xong"},
        {"text": "Done"},
        {"description": "Done"},
    )

    for sel in done_candidates:
        try:
            el = driver(**sel)
            if not el.exists:
                continue

            info = getattr(el, "info", {}) or {}
            log_message(
                f"{DEVICE_LIST_NAME[driver.serial]} - Phát hiện nút Xong/Done: "
                f"text={info.get('text')} desc={info.get('contentDescription')} "
                f"enabled={info.get('enabled')} clickable={info.get('clickable')} "
                f"bounds={info.get('bounds')}",
                logging.INFO,
            )

            try:
                await asyncio.to_thread(el.click)
            except Exception:
                bounds = info.get("bounds") or {}
                left = int(bounds.get("left", 0))
                top = int(bounds.get("top", 0))
                right = int(bounds.get("right", 0))
                bottom = int(bounds.get("bottom", 0))
                if right > left and bottom > top:
                    x = (left + right) // 2
                    y = (top + bottom) // 2
                    await asyncio.to_thread(driver.click, x, y)
                else:
                    continue

            log_message(
                f"{DEVICE_LIST_NAME[driver.serial]} - Đã bấm nút Xong/Done để quay về màn có nút Đăng",
                logging.INFO,
            )
            await asyncio.sleep(settle_after_click)
            return True

        except Exception as e:
            log_message(
                f"{DEVICE_LIST_NAME[driver.serial]} - Lỗi khi bấm nút Xong/Done: {e}",
                logging.WARNING,
            )

    return False


async def click_post_flow_intermediate_buttons(
    driver,
    *,
    context: str = "",
    max_clicks: int = 4,
    settle_after_click: float = 1.2,
) -> int:
    """
    Bấm qua các màn trung gian Facebook có thể hiện trong flow đăng/lấy link.
    Chỉ dùng trong post_to_group để tránh ảnh hưởng các luồng khác.
    """
    candidates = (
        ("Tiếp", {"text": "Tiếp"}),
        ("Tiếp", {"description": "Tiếp"}),
        ("Tiếp tục", {"text": "Tiếp tục"}),
        ("Tiếp tục", {"description": "Tiếp tục"}),
        ("Next", {"text": "Next"}),
        ("Next", {"description": "Next"}),
        ("Continue", {"text": "Continue"}),
        ("Continue", {"description": "Continue"}),
        ("Xong", {"text": "Xong"}),
        ("Xong", {"description": "Xong"}),
        ("Done", {"text": "Done"}),
        ("Done", {"description": "Done"}),
        ("Tiếp", {"textContains": "Tiếp"}),
        ("Tiếp", {"descriptionContains": "Tiếp"}),
        ("Next", {"textContains": "Next"}),
        ("Next", {"descriptionContains": "Next"}),
        ("Xong", {"textContains": "Xong"}),
        ("Xong", {"descriptionContains": "Xong"}),
        ("Done", {"textContains": "Done"}),
        ("Done", {"descriptionContains": "Done"}),
    )

    clicked_count = 0
    context_text = f" ({context})" if context else ""

    for _ in range(max(1, int(max_clicks or 1))):
        clicked_this_round = False

        for label, sel in candidates:
            try:
                el = driver(**sel)
                if not el.exists:
                    continue

                info = getattr(el, "info", {}) or {}
                if info and info.get("enabled") is False:
                    continue
                if info and info.get("clickable") is False and "Button" not in str(info.get("className") or ""):
                    continue

                log_message(
                    f"{DEVICE_LIST_NAME[driver.serial]} - Phát hiện màn trung gian{context_text}: "
                    f"bấm '{label}' selector={sel} text={info.get('text')} "
                    f"desc={info.get('contentDescription')} bounds={info.get('bounds')}",
                    logging.INFO,
                )

                try:
                    await asyncio.to_thread(el.click)
                except Exception:
                    bounds = info.get("bounds") or {}
                    left = int(bounds.get("left", 0))
                    top = int(bounds.get("top", 0))
                    right = int(bounds.get("right", 0))
                    bottom = int(bounds.get("bottom", 0))
                    if right <= left or bottom <= top:
                        continue
                    await asyncio.to_thread(
                        driver.click,
                        (left + right) // 2,
                        (top + bottom) // 2,
                    )

                clicked_count += 1
                clicked_this_round = True
                await asyncio.sleep(settle_after_click)
                break

            except Exception as e:
                log_message(
                    f"{DEVICE_LIST_NAME[driver.serial]} - Lỗi xử lý màn trung gian{context_text} selector={sel}: {e}",
                    logging.WARNING,
                )

        if not clicked_this_round:
            break

    return clicked_count


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

async def find_composer_opener_with_swipes(driver, max_light_swipes: int = 4):
    open_candidates = (
        {"text": "Bạn viết gì đi."},
        {"text": "Bạn viết gì đi..."},
        {"textContains": "Bạn viết gì"},
        {"description": "Bạn viết gì đi."},
        {"description": "Bạn viết gì đi..."},
        {"descriptionContains": "Bạn viết gì"},
    )

    async def _swipe_up_to_see_lower_part(scale: float):
        try:
            await asyncio.to_thread(driver.swipe_ext, "up", scale)
        except Exception:
            size = driver.window_size()
            if isinstance(size, dict):
                width = int(size.get("width", 0))
                height = int(size.get("height", 0))
            else:
                width, height = size

            x = width // 2
            start_y = int(height * 0.76)
            end_y = int(height * (0.76 - scale))
            await asyncio.to_thread(driver.swipe, x, start_y, x, end_y, 250)

    for attempt in range(max_light_swipes + 1):  # lần 0: không lướt
        for sel in open_candidates:
            el = driver(**sel)
            if el.exists:
                return el

        # chưa thấy -> ưu tiên lướt rất nhẹ để lộ phần bị popup đẩy xuống dưới
        if attempt < max_light_swipes:
            swipe_scale = 0.18 if attempt < 2 else 0.28
            log_message(
                f"{DEVICE_LIST_NAME[driver.serial]} - Chưa thấy nút soạn bài, lướt xuống nhẹ lần {attempt + 1}/{max_light_swipes} (scale={swipe_scale})",
                logging.INFO,
            )
            await _swipe_up_to_see_lower_part(swipe_scale)
            await asyncio.sleep(0.35)

    return None

async def _log_composer_candidates(driver, tag="before_click"):
    candidates = (
        {"text": "Bạn viết gì đi."},
        {"text": "Bạn viết gì đi..."},
        {"textContains": "Bạn viết gì"},
        {"description": "Bạn viết gì đi..."},
        {"descriptionContains": "Bạn viết gì"},
    )

    for sel in candidates:
        try:
            el = driver(**sel)
            exists = el.exists
            log_message(
                f"{DEVICE_LIST_NAME[driver.serial]} - [{tag}] check selector={sel} exists={exists}",
                logging.INFO,
            )
            if exists:
                info = getattr(el, "info", {}) or {}
                log_message(
                    f"{DEVICE_LIST_NAME[driver.serial]} - [{tag}] MATCH selector={sel} "
                    f"text={info.get('text')} desc={info.get('contentDescription')} "
                    f"clickable={info.get('clickable')} enabled={info.get('enabled')} "
                    f"bounds={info.get('bounds')}",
                    logging.INFO,
                )
        except Exception as e:
            log_message(
                f"{DEVICE_LIST_NAME[driver.serial]} - [{tag}] lỗi đọc selector={sel}: {e}",
                logging.WARNING,
            )


# async def _click_composer_opener(driver, el) -> bool:
#     """
#     Ưu tiên click theo element; nếu không được thì click theo tâm bounds.
#     """
#     try:
#         info = getattr(el, "info", {}) or {}
#         bounds = info.get("bounds") or {}
#         log_message(
#             f"{DEVICE_LIST_NAME[driver.serial]} - Click composer opener: "
#             f"text={info.get('text')} desc={info.get('contentDescription')} "
#             f"clickable={info.get('clickable')} enabled={info.get('enabled')} bounds={bounds}",
#             logging.INFO,
#         )
#     except Exception as e:
#         log_message(
#             f"{DEVICE_LIST_NAME[driver.serial]} - Không đọc được info composer opener: {e}",
#             logging.WARNING,
#         )
#         bounds = {}

#     # Cách 1: click trực tiếp element
#     try:
#         await asyncio.to_thread(el.click)
#         log_message(
#             f"{DEVICE_LIST_NAME[driver.serial]} - Đã thử click trực tiếp composer opener",
#             logging.INFO,
#         )
#         await asyncio.sleep(1.0)
#         return True
#     except Exception as e:
#         log_message(
#             f"{DEVICE_LIST_NAME[driver.serial]} - Click trực tiếp composer opener lỗi: {e}",
#             logging.WARNING,
#         )

#     # Cách 2: click theo tâm bounds
#     try:
#         left = int(bounds.get("left", 0))
#         top = int(bounds.get("top", 0))
#         right = int(bounds.get("right", 0))
#         bottom = int(bounds.get("bottom", 0))
#         if right > left and bottom > top:
#             x = (left + right) // 2
#             y = (top + bottom) // 2
#             await asyncio.to_thread(driver.click, x, y)
#             log_message(
#                 f"{DEVICE_LIST_NAME[driver.serial]} - Đã click tọa độ composer opener tại ({x}, {y})",
#                 logging.INFO,
#             )
#             await asyncio.sleep(1.0)
#             return True
#     except Exception as e:
#         log_message(
#             f"{DEVICE_LIST_NAME[driver.serial]} - Click tọa độ composer opener lỗi: {e}",
#             logging.WARNING,
#         )

#     return False

async def _click_composer_opener(driver, el) -> bool:
    try:
        info = getattr(el, "info", {}) or {}
        bounds = info.get("bounds") or {}
        log_message(
            f"{DEVICE_LIST_NAME[driver.serial]} - Click composer opener: "
            f"text={info.get('text')} desc={info.get('contentDescription')} "
            f"clickable={info.get('clickable')} enabled={info.get('enabled')} bounds={bounds}",
            logging.INFO,
        )
    except Exception as e:
        log_message(
            f"{DEVICE_LIST_NAME[driver.serial]} - Không đọc được info composer opener: {e}",
            logging.WARNING,
        )
        bounds = {}

    direct_ok = False
    try:
        await asyncio.to_thread(el.click)
        log_message(
            f"{DEVICE_LIST_NAME[driver.serial]} - Đã thử click trực tiếp composer opener",
            logging.INFO,
        )
        await asyncio.sleep(0.8)
        direct_ok = True
    except Exception as e:
        log_message(
            f"{DEVICE_LIST_NAME[driver.serial]} - Click trực tiếp composer opener lỗi: {e}",
            logging.WARNING,
        )

    if direct_ok:
        return True

    try:
        left = int(bounds.get("left", 0))
        top = int(bounds.get("top", 0))
        right = int(bounds.get("right", 0))
        bottom = int(bounds.get("bottom", 0))
        if right > left and bottom > top:
            x = (left + right) // 2
            y = (top + bottom) // 2
            await asyncio.to_thread(driver.click, x, y)
            log_message(
                f"{DEVICE_LIST_NAME[driver.serial]} - Đã click tọa độ composer opener tại ({x}, {y})",
                logging.INFO,
            )
            await asyncio.sleep(0.8)
            return True
    except Exception as e:
        log_message(
            f"{DEVICE_LIST_NAME[driver.serial]} - Click tọa độ composer opener lỗi: {e}",
            logging.WARNING,
        )

    return False

async def _get_group_composer_open_matches(driver):
    checks = [
        ("AutoCompleteTextView", driver(className="android.widget.AutoCompleteTextView")),
        ("focused AutoCompleteTextView", driver(className="android.widget.AutoCompleteTextView", focused=True)),
        ("EditText", driver(className="android.widget.EditText")),
        ("focused EditText", driver(className="android.widget.EditText", focused=True)),
        ("textContains=Gửi bài viết công khai", driver(textContains="Gửi bài viết công khai")),
        ("textContains=Viết gì đó", driver(textContains="Viết gì đó")),
        ("textContains=Bạn đang nghĩ gì", driver(textContains="Bạn đang nghĩ gì")),
        ("textContains=Bạn viết gì", driver(textContains="Bạn viết gì")),
        ("description=Tiêu đề bài viết", driver(description="Tiêu đề bài viết")),
        ("descriptionContains=Tạo bài viết", driver(descriptionContains="Tạo bài viết")),
        ("descriptionContains=Gửi bài viết công khai", driver(descriptionContains="Gửi bài viết công khai")),
        ("descriptionContains=Viết gì đó", driver(descriptionContains="Viết gì đó")),
        ("descriptionContains=Bạn viết gì", driver(descriptionContains="Bạn viết gì")),
        ("text=Đăng", driver(text="Đăng")),
        ("description=Đăng", driver(description="Đăng")),
        ("text=Post", driver(text="Post")),
        ("description=Post", driver(description="Post")),
        ("text=Xong", driver(text="Xong")),
        ("description=Xong", driver(description="Xong")),
        ("text=Done", driver(text="Done")),
        ("description=Done", driver(description="Done")),
    ]

    found_names = []
    for name, el in checks:
        try:
            if el.exists:
                found_names.append(name)
        except Exception:
            pass
    return found_names


async def _is_group_composer_already_open(driver):
    matches = await _get_group_composer_open_matches(driver)
    editor_markers = {
        "AutoCompleteTextView",
        "focused AutoCompleteTextView",
        "EditText",
        "focused EditText",
        "textContains=Gửi bài viết công khai",
        "textContains=Viết gì đó",
        "textContains=Bạn đang nghĩ gì",
        "textContains=Bạn viết gì",
        "description=Tiêu đề bài viết",
        "descriptionContains=Tạo bài viết",
        "descriptionContains=Gửi bài viết công khai",
        "descriptionContains=Viết gì đó",
        "descriptionContains=Bạn viết gì",
    }
    action_markers = {
        "text=Đăng",
        "description=Đăng",
        "text=Post",
        "description=Post",
        "text=Xong",
        "description=Xong",
        "text=Done",
        "description=Done",
    }
    composer_specific_markers = {
        "textContains=Gửi bài viết công khai",
        "textContains=Viết gì đó",
        "textContains=Bạn đang nghĩ gì",
        "textContains=Bạn viết gì",
        "description=Tiêu đề bài viết",
        "descriptionContains=Tạo bài viết",
        "descriptionContains=Gửi bài viết công khai",
        "descriptionContains=Viết gì đó",
        "descriptionContains=Bạn viết gì",
    }

    has_editor = any(name in editor_markers for name in matches)
    has_action = any(name in action_markers for name in matches)
    has_composer_specific_marker = any(name in composer_specific_markers for name in matches)
    return has_editor and has_action and has_composer_specific_marker, matches


def _iter_ui_objects(query):
    try:
        if not query.exists:
            return []
    except Exception:
        return []

    try:
        items = list(query)
        if items:
            return items
    except Exception:
        pass

    return [query]


async def _clear_one_composer_text_field(driver, el, name: str) -> bool:
    try:
        info = getattr(el, "info", {}) or {}
    except Exception:
        info = {}

    if info and info.get("enabled") is False:
        return False

    text_before = str(info.get("text") or "")
    desc = str(info.get("contentDescription") or "")
    bounds = info.get("bounds")

    log_message(
        f"{DEVICE_LIST_NAME[driver.serial]} - Dọn ô soạn bài [{name}]: "
        f"class={info.get('className')} text_len={len(text_before)} "
        f"desc={desc} bounds={bounds}",
        logging.INFO,
    )

    success = False
    try:
        await asyncio.to_thread(el.click)
        await asyncio.sleep(0.15)
    except Exception as e:
        log_message(
            f"{DEVICE_LIST_NAME[driver.serial]} - Click ô cần dọn [{name}] lỗi: {e}",
            logging.INFO,
        )

    for action_name, action in (
        ("clear_text", lambda: el.clear_text()),
        ("set_text_empty", lambda: el.set_text("")),
        ("send_keys_clear", lambda: driver.send_keys("", clear=True)),
    ):
        try:
            await asyncio.to_thread(action)
            await asyncio.sleep(0.15)
            success = True
        except Exception as e:
            log_message(
                f"{DEVICE_LIST_NAME[driver.serial]} - Dọn ô [{name}] bằng {action_name} lỗi: {e}",
                logging.INFO,
            )

    return success


async def clear_existing_group_composer_text(driver) -> int:
    """
    Xóa draft cũ đang nằm trong composer nhóm trước khi nhập nội dung mới.
    Một số UI có cả ô tiêu đề EditText và ô nội dung AutoCompleteTextView.
    """
    selectors = [
        ("AutoCompleteTextView", driver(className="android.widget.AutoCompleteTextView")),
        ("EditText", driver(className="android.widget.EditText")),
        ("focused AutoCompleteTextView", driver(className="android.widget.AutoCompleteTextView", focused=True)),
        ("focused EditText", driver(className="android.widget.EditText", focused=True)),
        ("description=Tiêu đề bài viết", driver(description="Tiêu đề bài viết")),
        ("descriptionContains=Tạo bài viết", driver(descriptionContains="Tạo bài viết")),
        ("descriptionContains=Gửi bài viết công khai", driver(descriptionContains="Gửi bài viết công khai")),
    ]

    seen = set()
    cleared = 0

    for name, query in selectors:
        for el in _iter_ui_objects(query):
            try:
                info = getattr(el, "info", {}) or {}
            except Exception:
                info = {}

            bounds = info.get("bounds") or {}
            key = (
                info.get("className"),
                bounds.get("left"),
                bounds.get("top"),
                bounds.get("right"),
                bounds.get("bottom"),
                info.get("contentDescription"),
            )
            if key in seen:
                continue
            seen.add(key)

            class_name = str(info.get("className") or "")
            if class_name and class_name not in {
                "android.widget.AutoCompleteTextView",
                "android.widget.EditText",
            }:
                continue

            if await _clear_one_composer_text_field(driver, el, name):
                cleared += 1

    if cleared:
        log_message(
            f"{DEVICE_LIST_NAME[driver.serial]} - Đã xóa nội dung cũ trong {cleared} ô soạn bài",
            logging.INFO,
        )
        await asyncio.sleep(0.4)

    return cleared

async def _click_element_or_center(driver, el, label: str, context: str = "") -> bool:
    try:
        info = getattr(el, "info", {}) or {}
    except Exception:
        info = {}

    context_text = f" ({context})" if context else ""
    try:
        if info.get("enabled") is False:
            return False

        await asyncio.to_thread(el.click)
        log_message(
            f"{DEVICE_LIST_NAME[driver.serial]} - Đã bấm '{label}'{context_text} bằng element click",
            logging.INFO,
        )
        return True
    except Exception as e:
        log_message(
            f"{DEVICE_LIST_NAME[driver.serial]} - Click element '{label}'{context_text} lỗi: {e}",
            logging.INFO,
        )

    try:
        bounds = info.get("bounds") or {}
        left = int(bounds.get("left", 0))
        top = int(bounds.get("top", 0))
        right = int(bounds.get("right", 0))
        bottom = int(bounds.get("bottom", 0))
        if right > left and bottom > top:
            x = (left + right) // 2
            y = (top + bottom) // 2
            await asyncio.to_thread(driver.click, x, y)
            log_message(
                f"{DEVICE_LIST_NAME[driver.serial]} - Đã bấm '{label}'{context_text} tại tọa độ ({x}, {y})",
                logging.INFO,
            )
            return True
    except Exception as e:
        log_message(
            f"{DEVICE_LIST_NAME[driver.serial]} - Click tọa độ '{label}'{context_text} lỗi: {e}",
            logging.INFO,
        )

    return False


async def click_done_until_publish_button_visible(
    driver,
    *,
    context: str = "",
    max_done_clicks: int = 3,
    settle_after_click: float = 1.0,
) -> int:
    """
    Bấm nút Xong/Done trong màn thêm văn bản cho tới khi nút Đăng/Post xuất hiện.
    Facebook đôi khi yêu cầu bấm Xong 1-2 lần trước khi quay về composer chính.
    """
    clicked_count = 0

    for _ in range(max(1, int(max_done_clicks or 1))):
        if (await _find_enabled_publish_button(driver)) is not None:
            break

        clicked = False

        xpath_candidates = (
            ("Xong", '//android.widget.Button[@content-desc="Xong"]'),
            ("Xong", '//android.widget.Button[contains(@content-desc,"Xong")]'),
            ("Done", '//android.widget.Button[@content-desc="Done"]'),
            ("Done", '//android.widget.Button[contains(@content-desc,"Done")]'),
            ("Xong", '//*[@content-desc="Xong" and @clickable="true"]'),
            ("Done", '//*[@content-desc="Done" and @clickable="true"]'),
        )

        for label, xp in xpath_candidates:
            try:
                el = driver.xpath(xp)
                if not el.exists:
                    continue
                clicked = await _click_element_or_center(driver, el, label, context)
                if clicked:
                    break
            except Exception as e:
                log_message(
                    f"{DEVICE_LIST_NAME[driver.serial]} - Lỗi tìm/bấm nút {label} bằng xpath {xp}: {e}",
                    logging.INFO,
                )

        if not clicked:
            clicked = await _click_first_visible_label(
                driver,
                ("Xong", "Done"),
                context=context,
            )

        if not clicked:
            break

        clicked_count += 1
        await asyncio.sleep(settle_after_click)

    if clicked_count:
        log_message(
            f"{DEVICE_LIST_NAME[driver.serial]} - Đã bấm Xong/Done {clicked_count} lần ({context})",
            logging.INFO,
        )

    return clicked_count


async def _click_first_visible_label(driver, labels, *, context: str = "") -> bool:
    for label in labels:
        selectors = (
            {"text": label},
            {"description": label},
            {"textContains": label},
            {"descriptionContains": label},
        )
        for sel in selectors:
            try:
                el = driver(**sel)
                if not el.exists:
                    continue
                if await _click_element_or_center(driver, el, label, context):
                    await asyncio.sleep(1.0)
                    return True
            except Exception as e:
                log_message(
                    f"{DEVICE_LIST_NAME[driver.serial]} - Lỗi bấm label '{label}' selector={sel}: {e}",
                    logging.INFO,
                )
    return False


async def _has_group_draft_action_sheet(driver) -> bool:
    title_markers = (
        "Lưu làm bản nháp hoặc đăng ẩn danh",
        "Save as draft",
        "Discard post",
    )
    option_markers = (
        "Tiếp tục chỉnh sửa",
        "Bỏ bài viết",
        "Continue editing",
        "Keep editing",
        "Discard",
    )

    title_found = False
    option_found = False

    for marker in title_markers:
        try:
            if driver(textContains=marker).exists or driver(descriptionContains=marker).exists:
                title_found = True
                break
        except Exception:
            pass

    for marker in option_markers:
        try:
            if driver(textContains=marker).exists or driver(descriptionContains=marker).exists:
                option_found = True
                break
        except Exception:
            pass

    return title_found and option_found


async def handle_group_draft_action_sheet(driver, *, keep_editing: bool, context: str = "") -> bool:
    if not await _has_group_draft_action_sheet(driver):
        return False

    labels = (
        ("Tiếp tục chỉnh sửa", "Continue editing", "Keep editing")
        if keep_editing
        else ("Bỏ bài viết", "Discard post", "Discard")
    )
    action_name = "tiếp tục chỉnh sửa để đăng tiếp" if keep_editing else "bỏ bài viết/draft cũ"
    log_message(
        f"{DEVICE_LIST_NAME[driver.serial]} - Phát hiện sheet draft bài viết, sẽ {action_name} ({context})",
        logging.INFO,
    )

    clicked = await _click_first_visible_label(driver, labels, context=context)
    if not clicked:
        log_message(
            f"{DEVICE_LIST_NAME[driver.serial]} - Không bấm được option sheet draft ({context})",
            logging.WARNING,
        )
    return clicked


async def cleanup_group_composer_blocker(
    driver,
    *,
    group_link: str | None = None,
    context: str = "",
    reopen_group: bool = False,
) -> bool:
    """
    Dọn composer/draft đang kẹt rồi tùy chọn mở lại group để chạy lại bước đăng.
    Facebook thường chỉ hiện "Bỏ bài viết" sau khi bấm Back từ màn composer.
    """
    cleaned = False

    try:
        if await handle_group_draft_action_sheet(
            driver,
            keep_editing=False,
            context=f"{context}_sheet_already_visible",
        ):
            cleaned = True
        else:
            await asyncio.to_thread(driver.press, "back")
            await asyncio.sleep(1.0)
            if await handle_group_draft_action_sheet(
                driver,
                keep_editing=False,
                context=f"{context}_after_back",
            ):
                cleaned = True
            else:
                clicked = await _click_first_visible_label(
                    driver,
                    ("Bỏ bài viết", "Discard post", "Discard"),
                    context=f"{context}_direct_discard",
                )
                cleaned = cleaned or clicked
    except Exception as e:
        log_message(
            f"{DEVICE_LIST_NAME[driver.serial]} - Dọn composer/draft lỗi ({context}): {e}",
            logging.WARNING,
        )

    if reopen_group and group_link:
        try:
            toolfacebook_lib.redirect_to(driver, _facebook_group_url(group_link))
            await asyncio.sleep(2.0)
            await disable_auto_rotation(driver, driver.serial)
            await handle_group_draft_action_sheet(
                driver,
                keep_editing=False,
                context=f"{context}_after_reopen_group",
            )
        except Exception as e:
            log_message(
                f"{DEVICE_LIST_NAME[driver.serial]} - Mở lại group sau khi dọn composer lỗi ({context}): {e}",
                logging.WARNING,
            )

    return cleaned


async def _find_enabled_publish_button(driver):
    for sel in (
        {"text": "Đăng"},
        {"description": "Đăng"},
        {"text": "Chia sẻ"},
        {"description": "Chia sẻ"},
        {"text": "Post"},
        {"description": "Post"},
        {"text": "POST"},
        {"text": "Share"},
        {"description": "Share"},
    ):
        try:
            btn = driver(**sel)
            if not btn.exists:
                continue
            info = getattr(btn, "info", {}) or {}
            if info.get("enabled", True) is False:
                continue
            return btn
        except Exception:
            pass

    try:
        btns = driver(className="android.widget.Button", enabled=True)
        for btn in btns:
            info = getattr(btn, "info", {}) or {}
            label = (
                info.get("text")
                or info.get("contentDescription")
                or ""
            ).strip().lower()
            if label in {"đăng", "post", "chia sẻ", "share"}:
                return btn
    except Exception:
        pass

    return None


async def _click_publish_button(driver, *, context: str = "") -> bool:
    btn = await _find_enabled_publish_button(driver)
    if btn is None:
        return False
    return await _click_element_or_center(driver, btn, "Đăng", context)


async def _recover_publish_from_draft_action_sheet(
    driver,
    *,
    context: str = "",
    press_back_if_needed: bool = False,
) -> bool:
    if not await _has_group_draft_action_sheet(driver) and press_back_if_needed:
        try:
            await asyncio.to_thread(driver.press, "back")
            await asyncio.sleep(1.0)
        except Exception as e:
            log_message(
                f"{DEVICE_LIST_NAME[driver.serial]} - Bấm back để mở sheet draft lỗi ({context}): {e}",
                logging.WARNING,
            )

    if not await handle_group_draft_action_sheet(driver, keep_editing=True, context=context):
        return False

    await click_post_flow_intermediate_buttons(
        driver,
        context=f"{context}_after_keep_editing",
        max_clicks=2,
        settle_after_click=1.0,
    )
    await click_done_until_publish_button_visible(
        driver,
        context=f"{context}_after_keep_editing",
        max_done_clicks=3,
        settle_after_click=1.0,
    )

    for _ in range(8):
        if await _click_publish_button(driver, context=f"{context}_after_keep_editing"):
            return True

        clicked_intermediate = await click_post_flow_intermediate_buttons(
            driver,
            context=f"{context}_waiting_publish_button",
            max_clicks=2,
            settle_after_click=1.0,
        )
        if clicked_intermediate:
            await click_done_until_publish_button_visible(
                driver,
                context=f"{context}_waiting_publish_button",
                max_done_clicks=2,
                settle_after_click=1.0,
            )
        else:
            await asyncio.sleep(0.5)

    return False


async def _recover_post_button_from_draft_action_sheet(
    driver,
    *,
    context: str = "",
    press_back_if_needed: bool = False,
):
    if not await _has_group_draft_action_sheet(driver) and press_back_if_needed:
        try:
            await asyncio.to_thread(driver.press, "back")
            await asyncio.sleep(1.0)
        except Exception as e:
            log_message(
                f"{DEVICE_LIST_NAME[driver.serial]} - Bấm back để mở sheet draft lỗi ({context}): {e}",
                logging.WARNING,
            )

    if not await handle_group_draft_action_sheet(driver, keep_editing=True, context=context):
        return None

    await click_post_flow_intermediate_buttons(
        driver,
        context=f"{context}_after_keep_editing",
        max_clicks=2,
        settle_after_click=1.0,
    )
    await click_done_until_publish_button_visible(
        driver,
        context=f"{context}_after_keep_editing",
        max_done_clicks=3,
        settle_after_click=1.0,
    )

    for _ in range(8):
        btn = await _find_enabled_publish_button(driver)
        if btn is not None:
            return btn

        clicked_intermediate = await click_post_flow_intermediate_buttons(
            driver,
            context=f"{context}_waiting_publish_button",
            max_clicks=2,
            settle_after_click=1.0,
        )
        if clicked_intermediate:
            await click_done_until_publish_button_visible(
                driver,
                context=f"{context}_waiting_publish_button",
                max_done_clicks=2,
                settle_after_click=1.0,
            )
        else:
            await asyncio.sleep(0.5)

    return None

async def post_to_group(
    driver,
    command_id,
    user_id,
    content,
    files=None,
    *,
    group_link: str,
    auto_join_if_needed: bool = True,
    join_timeout_sec: int = 15,
    return_result: bool = False,
) -> bool:
    """
    Posts to a Facebook group. Returns True on success.

    Raises RuntimeError with a message on failure so fb_task.py can capture `reason`.
    """
    def _result(ok: bool, *, reason: str = "", post_link: str | None = None, post_status: str | None = None):
        payload = {
            "ok": bool(ok),
            "reason": reason or "",
            "post_link": post_link,
            "post_status": post_status,
            "group_link": group_link,
            "content": content or "",
            "user_id": user_id,
        }
        return payload if return_result else bool(ok)

    async def _safe_command_status(status: str):
        if not command_id:
            return
        try:
            await pymongo_management.execute_command(command_id, status)
        except Exception as e:
            log_message(
                f"{DEVICE_LIST_NAME[driver.serial]} - Khong cap nhat duoc status command {command_id}: {e}",
                logging.WARNING,
            )

    if not group_link:
        raise RuntimeError("group_link is required")

    # Push media to device (if any)
    pushed_remote_files = []
    if files:
        for f in files:
            pushed_name = await toolfacebook_lib.push_file_to_device(driver.serial, f)
            if pushed_name:
                pushed_remote_files.append(pushed_name)
        driver.app_start("com.miui.gallery")

    # Open group
    toolfacebook_lib.redirect_to(driver, _facebook_group_url(group_link))
    await asyncio.sleep(2.0)
    await disable_auto_rotation(driver, driver.serial)
    log_message(
        f"{DEVICE_LIST_NAME[driver.serial]} - Đã mở trang nhóm {group_link}",
        logging.INFO,
    )
    await handle_group_draft_action_sheet(
        driver,
        keep_editing=False,
        context="open_group_cleanup_before_post",
    )

    # Check membership
    membership_state = get_group_membership_state(driver)
    joined_group = membership_state == "joined"
    not_joined = membership_state == "not_joined"

    if not joined_group and not_joined:
        log_message(f"{DEVICE_LIST_NAME[driver.serial]} - Đăng bài lên nhóm: chưa là thành viên {group_link}", logging.WARNING)

        if auto_join_if_needed:
            log_message(
                f"{DEVICE_LIST_NAME[driver.serial]} - post_to_group auto join group truoc khi dang: {group_link}",
                logging.INFO,
            )

            # Khong truyen command_id cua lenh dang bai vao join_group, tranh mark nham
            # command post_to_group la "Da thuc hien" truoc khi bai that su duoc dang.
            jr = await join_group(driver, None, user_id, group_link, back_to_facebook=False)
            join_status = getattr(jr, "status", "")
            join_reason = getattr(jr, "reason", "") or ""

            if join_status not in {"joined"}:
                # pending/question states mean the join request was handled but
                # the account is not a member yet, so posting cannot continue.
                await toolfacebook_lib.back_to_facebook(driver)

                if join_status == "pending_approval":
                    await _safe_command_status("Nhóm chờ admin duyệt")
                elif join_status == "question_required":
                    await _safe_command_status("Nhóm yêu cầu trả lời câu hỏi")
                else:
                    await _safe_command_status(f"Lỗi tham gia nhóm ({join_status or 'unknown'})")

                raise RuntimeError(f"Not a member after auto join: {join_status} {join_reason}".strip())

            await _safe_command_status("Đã tham gia nhóm - tiếp tục đăng bài")

            # became a member; reopen group to refresh UI
            toolfacebook_lib.redirect_to(driver, _facebook_group_url(group_link))
            wait_after_join = max(2, min(int(join_timeout_sec or 15), 30))
            log_message(
                f"{DEVICE_LIST_NAME[driver.serial]} - Auto join OK, cho {wait_after_join}s roi chay tiep luong dang group",
                logging.INFO,
            )
            await asyncio.sleep(wait_after_join)
            await disable_auto_rotation(driver, driver.serial)
        else:
            await toolfacebook_lib.back_to_facebook(driver)
            await pymongo_management.execute_command(command_id, "Lỗi: Chưa tham gia nhóm")
            raise RuntimeError("Not a member")

    # ===== Create post =====
    composer_ready = False
    last_composer_error = ""
    max_composer_attempts = 2

    for composer_attempt in range(1, max_composer_attempts + 1):
        await _log_composer_candidates(
            driver,
            tag=f"before_find_composer_attempt_{composer_attempt}",
        )

        composer_already_open, composer_open_matches = await _is_group_composer_already_open(driver)
        composer_opener = None

        if composer_already_open:
            log_message(
                f"{DEVICE_LIST_NAME[driver.serial]} - Composer đang mở sẵn/draft cũ đang che nút mở bài, "
                f"bỏ qua bước tìm 'Bạn viết gì đi...', match={composer_open_matches}",
                logging.INFO,
            )
        else:
            composer_opener = await find_composer_opener_with_swipes(driver, max_light_swipes=4)

        if composer_opener is None:
            composer_already_open, composer_open_matches = await _is_group_composer_already_open(driver)
            if composer_already_open:
                log_message(
                    f"{DEVICE_LIST_NAME[driver.serial]} - Không thấy nút mở bài nhưng composer đã mở sẵn, "
                    f"tiếp tục dọn draft và nhập lại nội dung, match={composer_open_matches}",
                    logging.INFO,
                )
            else:
                last_composer_error = "Không tìm thấy nút mở ô soạn bài (Bạn viết gì đi...)"
                try:
                    xml = driver.dump_hierarchy()
                    open(f"composer_opener_not_found_attempt_{composer_attempt}.xml", "w", encoding="utf-8").write(xml)
                except Exception:
                    pass

                if composer_attempt < max_composer_attempts:
                    log_message(
                        f"{DEVICE_LIST_NAME[driver.serial]} - Không thấy chỗ soạn bài, bấm Back/Bỏ bài viết rồi mở lại group để thử đăng lại",
                        logging.WARNING,
                    )
                    await cleanup_group_composer_blocker(
                        driver,
                        group_link=group_link,
                        context=f"composer_opener_not_found_attempt_{composer_attempt}",
                        reopen_group=True,
                    )
                    continue

                await pymongo_management.execute_command(
                    command_id, "Lỗi: Không tìm thấy nút 'Bạn viết gì đi...'"
                )
                await cleanup_group_composer_blocker(
                    driver,
                    context="composer_opener_not_found_final",
                )
                await toolfacebook_lib.back_to_facebook(driver)
                raise RuntimeError(last_composer_error)

        if not composer_already_open:
            clicked = await _click_composer_opener(driver, composer_opener)
            if not clicked:
                last_composer_error = "Tìm thấy nút mở composer nhưng click không được"
                try:
                    xml = driver.dump_hierarchy()
                    open(f"composer_opener_click_failed_attempt_{composer_attempt}.xml", "w", encoding="utf-8").write(xml)
                except Exception:
                    pass

                if composer_attempt < max_composer_attempts:
                    log_message(
                        f"{DEVICE_LIST_NAME[driver.serial]} - Click chỗ soạn bài lỗi, dọn draft rồi thử đăng lại",
                        logging.WARNING,
                    )
                    await cleanup_group_composer_blocker(
                        driver,
                        group_link=group_link,
                        context=f"composer_click_failed_attempt_{composer_attempt}",
                        reopen_group=True,
                    )
                    continue

                await pymongo_management.execute_command(
                    command_id, "Lỗi: Tìm thấy nút 'Bạn viết gì đi...' nhưng click không được"
                )
                await cleanup_group_composer_blocker(
                    driver,
                    context="composer_click_failed_final",
                )
                await toolfacebook_lib.back_to_facebook(driver)
                raise RuntimeError(last_composer_error)

            await _log_composer_candidates(driver, tag=f"after_click_composer_attempt_{composer_attempt}")
        else:
            await _log_composer_candidates(driver, tag=f"composer_already_open_attempt_{composer_attempt}")

        log_message(
            f"{DEVICE_LIST_NAME[driver.serial]} - Bắt đầu kiểm tra màn soạn bài nhóm (lần {composer_attempt}/{max_composer_attempts})",
            logging.INFO,
        )

        ready = False
        for i in range(16):  # ~8 giây
            found_names = await _get_group_composer_open_matches(driver)

            if found_names:
                ready = True
                log_message(
                    f"{DEVICE_LIST_NAME[driver.serial]} - Composer đã mở, match={found_names}",
                    logging.INFO,
                )
                break

            log_message(
                f"{DEVICE_LIST_NAME[driver.serial]} - Chờ composer... vòng {i+1}/16 chưa thấy dấu hiệu mở",
                logging.INFO,
            )
            await asyncio.sleep(0.5)

        if ready:
            composer_ready = True
            break

        last_composer_error = "Không mở được màn soạn bài nhóm"
        try:
            xml = driver.dump_hierarchy()
            open(f"composer_group_debug_after_click_attempt_{composer_attempt}.xml", "w", encoding="utf-8").write(xml)
        except Exception:
            pass

        if composer_attempt < max_composer_attempts:
            log_message(
                f"{DEVICE_LIST_NAME[driver.serial]} - Màn soạn bài không mở, bấm Back/Bỏ bài viết rồi chạy lại bước đăng",
                logging.WARNING,
            )
            await cleanup_group_composer_blocker(
                driver,
                group_link=group_link,
                context=f"composer_not_ready_attempt_{composer_attempt}",
                reopen_group=True,
            )
            continue

        await pymongo_management.execute_command(
            command_id, "Lỗi: Không mở được màn soạn bài nhóm"
        )
        await cleanup_group_composer_blocker(
            driver,
            context="composer_not_ready_final",
        )
        await toolfacebook_lib.back_to_facebook(driver)
        raise RuntimeError(last_composer_error)

    if not composer_ready:
        await pymongo_management.execute_command(
            command_id, "Lỗi: Không mở được màn soạn bài nhóm"
        )
        await toolfacebook_lib.back_to_facebook(driver)
        raise RuntimeError(last_composer_error or "Không mở được màn soạn bài nhóm")
    
    await random_human_delay(driver, 3.0, 5.0, "trước khi nhập nội dung")
    await clear_existing_group_composer_text(driver)
    
    log_message(
        f"{DEVICE_LIST_NAME[driver.serial]} - Bắt đầu nhập nội dung bài viết vào composer",
        logging.INFO,
    )

    # 3) Focus ô nhập nội dung (editor) và gõ content
    typed = False

    editor_candidates = [
        # Ưu tiên cao nhất: case XML hiện tại
        ("class=android.widget.AutoCompleteTextView",
         driver(className="android.widget.AutoCompleteTextView")),

        ("focused AutoCompleteTextView",
         driver(className="android.widget.AutoCompleteTextView", focused=True)),

        # Một số máy khác
        ("class=android.widget.EditText",
         driver(className="android.widget.EditText")),

        ("focused EditText",
         driver(className="android.widget.EditText", focused=True)),

        # Theo text placeholder
        ("textContains=Gửi bài viết công khai",
         driver(textContains="Gửi bài viết công khai")),

        ("textContains=Viết gì đó",
         driver(textContains="Viết gì đó")),

        ("textContains=Bạn viết gì",
         driver(textContains="Bạn viết gì")),

        ("textContains=Write something",
         driver(textContains="Write something")),

        ("textContains=What's on your mind",
         driver(textContains="What's on your mind")),

        ("textContains=Tạo bài viết",
         driver(textContains="Tạo bài viết")),

        ("descriptionContains=Tạo bài viết",
         driver(descriptionContains="Tạo bài viết")),

        # Theo description nếu có ở máy khác
        ("description=Tiêu đề bài viết",
         driver(description="Tiêu đề bài viết")),

        ("descriptionContains=Gửi bài viết công khai",
         driver(descriptionContains="Gửi bài viết công khai")),

        ("descriptionContains=Viết gì đó",
         driver(descriptionContains="Viết gì đó")),

        ("descriptionContains=Bạn viết gì",
         driver(descriptionContains="Bạn viết gì")),

        ("descriptionContains=Write something",
         driver(descriptionContains="Write something")),

        ("descriptionContains=What's on your mind",
         driver(descriptionContains="What's on your mind")),
    ]

    for name, editor in editor_candidates:
        try:
            exists = editor.exists
            log_message(
                f"{DEVICE_LIST_NAME[driver.serial]} - Check editor candidate [{name}] exists={exists}",
                logging.INFO,
            )

            if not exists:
                continue

            info = getattr(editor, "info", {}) or {}
            log_message(
                f"{DEVICE_LIST_NAME[driver.serial]} - MATCH editor [{name}] "
                f"class={info.get('className')} text={info.get('text')} "
                f"desc={info.get('contentDescription')} focused={info.get('focused')} "
                f"bounds={info.get('bounds')}",
                logging.INFO,
            )

            # click focus lại nếu cần
            try:
                await asyncio.to_thread(editor.click)
                await asyncio.sleep(0.3)
                log_message(
                    f"{DEVICE_LIST_NAME[driver.serial]} - Click editor [{name}] thành công",
                    logging.INFO,
                )
            except Exception as e:
                log_message(
                    f"{DEVICE_LIST_NAME[driver.serial]} - Click editor [{name}] lỗi: {e}",
                    logging.WARNING,
                )

            # clear_text có thể fail với AutoCompleteTextView, nên chỉ thử nhẹ
            try:
                await asyncio.to_thread(editor.clear_text)
                await asyncio.sleep(0.2)
                log_message(
                    f"{DEVICE_LIST_NAME[driver.serial]} - Clear editor [{name}] thành công",
                    logging.INFO,
                )
            except Exception as e:
                log_message(
                    f"{DEVICE_LIST_NAME[driver.serial]} - Clear editor [{name}] lỗi: {e}",
                    logging.INFO,
                )

            # Ưu tiên set_text trực tiếp vào editor
            try:
                await asyncio.to_thread(editor.set_text, content or "")
                await asyncio.sleep(0.6)
                log_message(
                    f"{DEVICE_LIST_NAME[driver.serial]} - set_text vào editor [{name}] thành công",
                    logging.INFO,
                )
                typed = True
                break
            except Exception as e:
                log_message(
                    f"{DEVICE_LIST_NAME[driver.serial]} - set_text editor [{name}] lỗi: {e}",
                    logging.WARNING,
                )

            # fallback send_keys
            try:
                await asyncio.to_thread(driver.send_keys, content or "", clear=True)
                await asyncio.sleep(0.6)
                log_message(
                    f"{DEVICE_LIST_NAME[driver.serial]} - send_keys fallback [{name}] thành công",
                    logging.INFO,
                )
                typed = True
                break
            except Exception as e:
                log_message(
                    f"{DEVICE_LIST_NAME[driver.serial]} - send_keys fallback [{name}] lỗi: {e}",
                    logging.WARNING,
                )

        except Exception as e:
            log_message(
                f"{DEVICE_LIST_NAME[driver.serial]} - Lỗi editor candidate [{name}]: {e}",
                logging.WARNING,
            )
            continue
    
    # XPath fallback
    if not typed:
        xpath_candidates = [
            ("xpath AutoCompleteTextView",
             '//*[@class="android.widget.AutoCompleteTextView"]'),
            ("xpath focused AutoCompleteTextView",
             '//*[@class="android.widget.AutoCompleteTextView" and @focused="true"]'),
            ("xpath EditText",
             '//*[@class="android.widget.EditText"]'),
            ("xpath text Gửi bài viết công khai",
             '//*[contains(@text,"Gửi bài viết công khai")]'),
            ("xpath text Viết gì đó",
             '//*[contains(@text,"Viết gì đó")]'),
            ("xpath text Bạn viết gì",
             '//*[contains(@text,"Bạn viết gì")]'),
            ("xpath text Tạo bài viết",
             '//*[contains(@text,"Tạo bài viết")]'),
            ("xpath desc Tạo bài viết",
             '//*[contains(@content-desc,"Tạo bài viết")]'),
            ("xpath desc Tiêu đề bài viết",
             '//*[contains(@content-desc,"Tiêu đề bài viết")]'),
        ]

        for name, xp in xpath_candidates:
            try:
                el = driver.xpath(xp)
                exists = el.exists
                log_message(
                    f"{DEVICE_LIST_NAME[driver.serial]} - Check xpath editor [{name}] exists={exists}",
                    logging.INFO,
                )
                if not exists:
                    continue

                try:
                    await asyncio.to_thread(el.click)
                    await asyncio.sleep(0.3)
                    log_message(
                        f"{DEVICE_LIST_NAME[driver.serial]} - Click xpath editor [{name}] thành công",
                        logging.INFO,
                    )
                except Exception as e:
                    log_message(
                        f"{DEVICE_LIST_NAME[driver.serial]} - Click xpath editor [{name}] lỗi: {e}",
                        logging.WARNING,
                    )

                try:
                    await asyncio.to_thread(driver.send_keys, content or "", clear=True)
                    await asyncio.sleep(0.6)
                    log_message(
                        f"{DEVICE_LIST_NAME[driver.serial]} - send_keys xpath fallback [{name}] thành công",
                        logging.INFO,
                    )
                    typed = True
                    break
                except Exception as e:
                    log_message(
                        f"{DEVICE_LIST_NAME[driver.serial]} - send_keys xpath fallback [{name}] lỗi: {e}",
                        logging.WARNING,
                    )
            except Exception as e:
                log_message(
                    f"{DEVICE_LIST_NAME[driver.serial]} - Lỗi xpath editor [{name}]: {e}",
                    logging.WARNING,
                )

    # Tọa độ cuối cùng
    if not typed:
        try:
            size = driver.window_size()
            if isinstance(size, dict):
                width = int(size.get("width", 0))
                height = int(size.get("height", 0))
            else:
                width, height = size

            x = width // 2
            y = int(height * 0.30)   # thay vì 0.28
            await asyncio.to_thread(driver.click, x, y)
            await asyncio.sleep(0.4)

            await asyncio.to_thread(driver.send_keys, content or "", clear=True)
            await asyncio.sleep(0.6)

            log_message(
                f"{DEVICE_LIST_NAME[driver.serial]} - Fallback click tọa độ editor tại ({x}, {y}) + send_keys thành công",
                logging.INFO,
            )
            typed = True
        except Exception as e:
            log_message(
                f"{DEVICE_LIST_NAME[driver.serial]} - Fallback click tọa độ + send_keys lỗi: {e}",
                logging.WARNING,
            )
            typed = False

    if not typed:
        try:
            xml = driver.dump_hierarchy()
            open("composer_group_type_debug.xml", "w", encoding="utf-8").write(xml)
        except Exception:
            pass

        await pymongo_management.execute_command(
            command_id, "Lỗi: Mở được composer nhưng không nhập được nội dung"
        )
        await toolfacebook_lib.back_to_facebook(driver)
        raise RuntimeError("Mở được composer nhưng không nhập được nội dung")

    # Một số UI hiện màn thêm văn bản, phải bấm Xong 1-2 lần thì nút Đăng mới xuất hiện.
    done_clicks = await click_done_until_publish_button_visible(
        driver,
        context="after_type_before_post_button",
        max_done_clicks=3,
        settle_after_click=1.0,
    )
    if done_clicks:
        log_message(
            f"{DEVICE_LIST_NAME[driver.serial]} - Đã xử lý nút Xong sau khi nhập nội dung",
            logging.INFO,
        )

    # Một số UI hiện màn trung gian trước, phải bấm Tiếp/Xong thì nút Đăng mới xuất hiện
    clicked_intermediate = await click_post_flow_intermediate_buttons(
        driver,
        context="after_type_before_post_button",
        max_clicks=4,
        settle_after_click=1.5,
    )
    if clicked_intermediate:
        log_message(
            f"{DEVICE_LIST_NAME[driver.serial]} - Đã xử lý {clicked_intermediate} màn trung gian trước khi tìm nút Đăng",
            logging.INFO,
        )

    await click_done_until_publish_button_visible(
        driver,
        context="after_intermediate_before_post_button",
        max_done_clicks=3,
        settle_after_click=1.0,
    )

    await random_human_delay(driver, 3.0, 5.0, "sau khi nhập nội dung, trước khi bấm Đăng")

    post_btn_ready = False
    post_btn_to_click = None

    for _ in range(10):
        for sel in (
            {"text": "Đăng"},
            {"description": "Đăng"},
        ):
            btn = driver(**sel)
            if btn.exists:
                info = getattr(btn, "info", {}) or {}
                log_message(
                    f"{DEVICE_LIST_NAME[driver.serial]} - Trạng thái nút Đăng: "
                    f"enabled={info.get('enabled')} clickable={info.get('clickable')} "
                    f"text={info.get('text')} desc={info.get('contentDescription')}",
                    logging.INFO,
                )
                if info.get("enabled", False):
                    post_btn_ready = True
                    post_btn_to_click = btn
                    break

        if post_btn_ready:
            break

        done_clicks = await click_done_until_publish_button_visible(
            driver,
            context="waiting_post_button",
            max_done_clicks=2,
            settle_after_click=1.0,
        )
        if done_clicks:
            continue

        # Nếu chưa có Đăng mà đang có màn trung gian thì bấm qua để quay về composer chính
        clicked_intermediate = await click_post_flow_intermediate_buttons(
            driver,
            context="waiting_post_button",
            max_clicks=2,
            settle_after_click=1.0,
        )
        if clicked_intermediate:
            continue

        if await handle_group_draft_action_sheet(
            driver,
            keep_editing=True,
            context="waiting_post_button",
        ):
            continue

        await asyncio.sleep(0.5)

    if not post_btn_ready:
        recovered_post_btn = await _recover_post_button_from_draft_action_sheet(
            driver,
            context="post_button_not_ready",
            press_back_if_needed=True,
        )
        if recovered_post_btn is not None:
            post_btn_ready = True
            post_btn_to_click = recovered_post_btn
            log_message(
                f"{DEVICE_LIST_NAME[driver.serial]} - Đã lấy lại nút Đăng sau khi bấm Tiếp tục chỉnh sửa từ sheet draft",
                logging.INFO,
            )
        else:
            await handle_group_draft_action_sheet(
                driver,
                keep_editing=False,
                context="post_button_not_ready_cleanup",
            )

    if not post_btn_ready:
        try:
            xml = driver.dump_hierarchy()
            open("composer_group_post_not_ready.xml", "w", encoding="utf-8").write(xml)
        except Exception:
            pass

        await pymongo_management.execute_command(
            command_id, "Lỗi: Đã mở composer nhưng nút Đăng chưa sáng"
        )
        await toolfacebook_lib.back_to_facebook(driver)
        raise RuntimeError("Đã mở composer nhưng nút Đăng chưa sáng")

    # Attach media (optional)
    attach_media = isinstance(files, (list, tuple)) and len(files) > 0
    if attach_media:
        await asyncio.sleep(2)
        opened = False
        for sel in (
            {"description": "Ảnh/video"}, {"text": "Ảnh/video"},
            {"description": "Photo/video"},{"text": "Photo/video"},
        ):
            el = driver(**sel)
            if el.exists:
                await asyncio.to_thread(el.click)
                opened = True
                break

        if not opened:
            log_message(f"{DEVICE_LIST_NAME[driver.serial]} - Đăng nhóm: Không thấy nút Ảnh/Video", logging.WARNING)
            await pymongo_management.execute_command(
                command_id, "Lỗi: Không thấy nút Ảnh/Video"
            )
            await toolfacebook_lib.back_to_facebook(driver)
            raise RuntimeError("Không thấy nút Ảnh/Video")
        else:
            num_files = len(files)

            if num_files <= 1:
                # 1 ảnh: chọn 1 thumbnail mới nhất
                selected = await select_recent_photos(driver, 1)
                if selected < 1:
                    log_message(
                        f"{DEVICE_LIST_NAME[driver.serial]} - Đăng nhóm: Không chọn được ảnh nào (1 ảnh)",
                        logging.WARNING,
                    )
                    try:
                        await asyncio.to_thread(driver.press, "back")
                        await asyncio.sleep(1)
                    except Exception:
                        pass
            else:
                # Nhiều ảnh: bật "Chọn nhiều file" giống post_to_wall
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
                        xp = "//*[contains(@text,'Chọn nhiều')]/.."
                        vg = driver.xpath(xp)
                        if vg.exists:
                            await asyncio.to_thread(vg.click)
                            multi_clicked = True
                            await asyncio.sleep(0.5)

                if not multi_clicked:
                    size = driver.window_size()
                    if isinstance(size, dict):
                        w = size.get("width", 0)
                        h = size.get("height", 0)
                    else:
                        w, h = size
                    x = int(w * 0.7)
                    y = int(h * 0.15)
                    await asyncio.to_thread(driver.click, x, y)
                    multi_clicked = True
                    await asyncio.sleep(0.5)

                log_message(
                    f"{DEVICE_LIST_NAME[driver.serial]} - Đăng nhóm: Multi-select bật={multi_clicked}",
                    logging.INFO,
                )

                selected = await select_recent_photos(driver, num_files)
                if selected < num_files:
                    log_message(
                        f"{DEVICE_LIST_NAME[driver.serial]} - Đăng nhóm: Chỉ chọn {selected}/{num_files} ảnh",
                        logging.WARNING,
                    )

                if selected > 0:
                    clicked_intermediate = await click_post_flow_intermediate_buttons(
                        driver,
                        context="after_media_selection",
                        max_clicks=3,
                        settle_after_click=1.0,
                    )
                    if not clicked_intermediate:
                        try:
                            await asyncio.to_thread(driver.press, "back")
                            await asyncio.sleep(1)
                        except Exception:
                            pass

    # ===== 4) Bấm nút ĐĂNG (reuse logic từ post_to_wall) =====
    published = False
    ok = False

    if post_btn_to_click is not None:
        try:
            await asyncio.to_thread(post_btn_to_click.click)
            published = True
        except Exception as e:
            log_message(
                f"{DEVICE_LIST_NAME[driver.serial]} - Click nút Đăng đã-ready lỗi: {e}",
                logging.WARNING,
            )

    post_candidates = (
        {"text": "Đăng"}, {"description": "Đăng"},
        {"text": "Chia sẻ"}, {"description": "Chia sẻ"},
        {"text": "Post"}, {"text": "POST"},
        {"text": "Share"}, {"description": "Share"},
        # {"text": "Xong"}, {"text": "Done"},
    )
    for sel in post_candidates:
        if published:
            break
        el = driver(**sel)
        if el.exists:
            await asyncio.to_thread(el.click)
            published = True
            break

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
        published = await _recover_publish_from_draft_action_sheet(
            driver,
            context="publish_button_not_found",
            press_back_if_needed=True,
        )

    if not published:
        await handle_group_draft_action_sheet(
            driver,
            keep_editing=False,
            context="publish_button_not_found_cleanup",
        )
        try:
            xml = driver.dump_hierarchy()
            open("composer_group_post_debug.xml", "w", encoding="utf-8").write(xml)
        except Exception:
            pass
        await pymongo_management.execute_command(command_id, "Lỗi: Không tìm thấy nút Đăng trong group")
        await toolfacebook_lib.back_to_facebook(driver)
        raise RuntimeError("Không tìm thấy nút Đăng")

    log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Đăng bài lên nhóm {group_link}: đã bấm ĐĂNG", logging.INFO)
    await mark_post_clicked_checking(driver, command_id)

    async def _handle_publish_wait_intermediate():
        await click_post_flow_intermediate_buttons(
            driver,
            context="waiting_publish_snackbar",
            max_clicks=2,
            settle_after_click=1.0,
        )

    await click_post_flow_intermediate_buttons(
        driver,
        context="after_click_post_before_wait",
        max_clicks=2,
        settle_after_click=1.0,
    )

    ok = await toolfacebook_lib.wait_published_snackbar(
        driver,
        timeout=60,
        poll=2.0,
        on_poll=_handle_publish_wait_intermediate,
    )
    if ok:
        log_message(
            f"{DEVICE_LIST_NAME[driver.serial]} - Đăng bài lên nhóm: Facebook đã báo đăng thành công",
            logging.INFO,
        )
    else:
        log_message(
            f"{DEVICE_LIST_NAME[driver.serial]} - Đăng bài lên nhóm: chưa thấy snackbar, vẫn tiếp tục luồng lấy link",
            logging.WARNING,
        )

    await click_post_flow_intermediate_buttons(
        driver,
        context="after_publish_wait",
        max_clicks=3,
        settle_after_click=1.0,
    )

    log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Chờ 7 giây để Facebook xử lý đăng bài trước khi lấy link", logging.INFO)
    await asyncio.sleep(7)
    await click_post_flow_intermediate_buttons(
        driver,
        context="after_post_settle_before_link_fetch",
        max_clicks=3,
        settle_after_click=1.0,
    )

    def _looks_like_facebook_url(s: str) -> bool:
        s = (s or "").strip().lower()
        return bool(s) and (
            s.startswith("http://")
            or s.startswith("https://")
            or "facebook.com" in s
            or "m.facebook.com" in s
            or "fb.watch" in s
            or "groups/" in s
        )

    class PostNotApprovedError(RuntimeError):
        pass

    async def _open_manage_group_posts_for_link_fetch(link_attempt: int) -> bool:
        if link_attempt > 1:
            log_message(
                f"{DEVICE_LIST_NAME[driver.serial]} - Lấy link bài viết lần {link_attempt}/{POST_LINK_FETCH_MAX_ATTEMPTS}: mở lại group {group_link}",
                logging.WARNING,
            )
            toolfacebook_lib.redirect_to(driver, _facebook_group_url(group_link))
            await asyncio.sleep(2.0)
            await disable_auto_rotation(driver, driver.serial)

        await click_post_flow_intermediate_buttons(
            driver,
            context="open_manage_before_refresh",
            max_clicks=3,
        )

        # refresh lại màn group trước khi bấm 3 chấm
        await toolfacebook_lib.refresh_group_page_after_post(driver, sleep_after=3.0)
        await click_post_flow_intermediate_buttons(
            driver,
            context="open_manage_after_refresh",
            max_clicks=3,
        )

        # ===== LUỒNG MỚI CHÍNH: mở từ nút 3 chấm góc trên phải -> Quản lý nội dung =====
        ok_manage = await toolfacebook_lib.open_manage_group_posts_via_member_tools(driver, timeout=8.0)

        # ===== FALLBACK: giữ nguyên luồng cũ, không xóa =====
        if not ok_manage:
            log_message(
                f"{DEVICE_LIST_NAME[driver.serial]} - Luồng mới vào 'Quản lý nội dung' thất bại, fallback sang luồng cũ",
                logging.WARNING,
            )

            composer_opener2 = await find_composer_opener_with_swipes(driver, max_light_swipes=4)
            if composer_opener2:
                b = composer_opener2.info["bounds"]
                w_icon = int((b["right"] - b["left"]) * 0.18)
                x = max(5, int(b["left"] - w_icon * 0.6))
                y = int((b["top"] + b["bottom"]) / 2)
                await asyncio.to_thread(driver.click, x, y)
                await asyncio.sleep(1.0)
                await click_post_flow_intermediate_buttons(
                    driver,
                    context="open_manage_after_fallback_menu_click",
                    max_clicks=2,
                )
            else:
                log_message(
                    f"{DEVICE_LIST_NAME[driver.serial]} - Không tìm thấy composer opener để fallback luồng cũ",
                    logging.WARNING,
                )

            ok_manage = await toolfacebook_lib.open_manage_group_posts(driver, timeout=10.0)

        return ok_manage

    async def _fetch_post_link_once(link_attempt: int):
        ok_manage = await _open_manage_group_posts_for_link_fetch(link_attempt)
        if not ok_manage:
            raise RuntimeError("Không mở được màn 'Nội dung của bạn'")

        await click_post_flow_intermediate_buttons(
            driver,
            context="before_detect_status",
            max_clicks=3,
        )

        try:
            current_status, current_debug = await toolfacebook_lib.detect_group_post_status_by_content_text(driver, content, min_score=0.65)
            log_message(f"[TEXT_MATCH] status={current_status} debug={current_debug}", logging.INFO)
        except Exception as e:
            log_message(
                f"{DEVICE_LIST_NAME[driver.serial]} - Lỗi detect theo content ở lần lấy link {link_attempt}: {e}",
                logging.WARNING,
            )
            raise RuntimeError(f"Lỗi detect theo content: {e}") from e

        # Nếu rejected/removed -> coi như QTV từ chối / đã gỡ, không retry lấy link nữa.
        if current_status in ("rejected", "removed"):
            log_message(
                f"{DEVICE_LIST_NAME[driver.serial]} - Bài viết không được duyệt",
                logging.WARNING,
            )
            await pymongo_management.execute_command(command_id, "Bài viết không được duyệt")
            raise PostNotApprovedError(f"Bài viết không được duyệt (status={current_status})")

        if current_status in ("unknown", None):
            raise RuntimeError(f"Chưa xác định được trạng thái bài viết để lấy link (status={current_status})")

        await click_post_flow_intermediate_buttons(
            driver,
            context="before_copy_link",
            max_clicks=2,
        )
        current_post_link = await toolfacebook_lib.copy_post_link_for_group_by_content_text(
            driver,
            content,
            detect_debug=current_debug,
        )

        if not _looks_like_facebook_url(current_post_link):
            raise RuntimeError(f"Link không hợp lệ để lưu Mongo: {current_post_link!r}")

        return current_post_link, current_status

    post_link = None
    status = None
    last_link_error = ""

    for link_attempt in range(1, POST_LINK_FETCH_MAX_ATTEMPTS + 1):
        try:
            post_link, status = await _fetch_post_link_once(link_attempt)
            break
        except PostNotApprovedError:
            raise
        except Exception as e:
            last_link_error = str(e)
            log_message(
                f"{DEVICE_LIST_NAME[driver.serial]} - Lấy link bài viết lần {link_attempt}/{POST_LINK_FETCH_MAX_ATTEMPTS} lỗi: {last_link_error}",
                logging.WARNING,
            )
            if link_attempt < POST_LINK_FETCH_MAX_ATTEMPTS:
                log_message(
                    f"{DEVICE_LIST_NAME[driver.serial]} - Sẽ mở lại group và thử lấy link thêm lần nữa",
                    logging.INFO,
                )
                await asyncio.sleep(2.0)

    # if post_link and _looks_like_facebook_url(post_link):
    #     update_msg, update_level = await pymongo_management.update_post_link(command_id, post_link)
    #     log_message(
    #         f"{DEVICE_LIST_NAME[driver.serial]} - update_post_link result: {update_msg}",
    #         update_level,
    #     )
    #     log_message(
    #         f"{DEVICE_LIST_NAME[driver.serial]} - Đã cập nhật link bài viết: {post_link}",
    #         logging.INFO,
    #     )
    #     await pymongo_management.execute_command(command_id, "Đã thực hiện")
        
    if post_link and _looks_like_facebook_url(post_link):
        update_msg, update_level = await pymongo_management.update_post_link(
            command_id,
            post_link,
            post_status=status,
        )
        log_message(
            f"{DEVICE_LIST_NAME[driver.serial]} - update_post_link result: {update_msg}",
            update_level,
        )
        log_message(
            f"{DEVICE_LIST_NAME[driver.serial]} - Đã cập nhật link bài viết: {post_link}",
            logging.INFO,
        )
        await pymongo_management.execute_command(
            command_id,
            "Đã thực hiện",
            post_status=status,
        )
        track_msg, track_level = await pymongo_management.ensure_group_post_tracking_record(
            command_id,
            user_id=user_id,
            content=content,
            files=files,
            group_link=group_link,
            link=post_link,
            post_status=status,
            device_id=driver.serial,
            source="post_to_group",
        )
        log_message(
            f"{DEVICE_LIST_NAME[driver.serial]} - ensure Bai-dang tracking result: {track_msg}",
            track_level,
        )
    else:
        log_message(
            f"{DEVICE_LIST_NAME[driver.serial]} - {POST_LINK_FETCH_FAILED_STATUS}. Lỗi cuối: {last_link_error}",
            logging.WARNING,
        )
        await pymongo_management.execute_command(
            command_id,
            POST_LINK_FETCH_FAILED_STATUS,
            post_status=status,
        )
        await toolfacebook_lib.back_to_facebook(driver)
        if pushed_remote_files:
            for remote_name in pushed_remote_files:
                await toolfacebook_lib.delete_file(driver.serial, remote_name)
        if return_result:
            return _result(True, reason=POST_LINK_FETCH_FAILED_STATUS, post_status=status or "unknown")
        return True

    # Clean up UI + device files
    await toolfacebook_lib.back_to_facebook(driver)
    if pushed_remote_files:
        for remote_name in pushed_remote_files:
            await toolfacebook_lib.delete_file(driver.serial, remote_name)

    return _result(True, post_link=post_link, post_status=status)
