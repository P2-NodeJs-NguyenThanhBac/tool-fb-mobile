import asyncio
import logging
import urllib.parse
from typing import Optional, Sequence

import pymongo_management
import toolfacebook_lib
from util import log_message, DEVICE_LIST_NAME


# =====================================================
# Deep-link helper (reuse idea from delete_post.py)
# =====================================================

def redirect_to(driver, link: str):
    """Open a Facebook post link (including short /share/p/ links) via facewebmodal."""
    encoded_link = urllib.parse.quote(link)
    deep_link = f"fb://facewebmodal/f?href={encoded_link}"
    # Force open in FB webview
    driver.shell(f"am start -a android.intent.action.VIEW -d '{deep_link}'")


# =====================================================
# UI finders (pattern from delete_post.py)
# =====================================================

def _find_more_button(driver):
    candidates = (
        {"descriptionContains": "Tùy chọn"},
        {"descriptionContains": "Tuy chon"},
        {"descriptionContains": "More options"},
        {"descriptionContains": "Khác"},
        {"descriptionContains": "More"},
        {"text": "⋯"},
        {"text": "..."},
    )
    for sel in candidates:
        el = driver(**sel)
        if el.exists:
            return el
    return None


def _find_edit_option(driver):
    # VN/EN, tuy version FB co the khac nhau
    texts = (
        "Chỉnh sửa",
        "Chinh sua",
        "Chỉnh sửa bài viết",
        "Chỉnh sửa bài",
        "Edit post",
        "Edit",
        "Edit Post",
        "Chỉnh sửa bài đăng",
        "Chỉnh sửa bài đăng",
    )
    for t in texts:
        el = driver(textContains=t)
        if el.exists:
            return el
    return None


def _find_save_button(driver):
    # Man hinh edit thuong co "Lưu" / "Save"
    for t in ("Lưu", "Luu", "Save", "Cập nhật", "Cap nhat", "Update"):
        el = driver(text=t) if len(t) <= 4 else driver(textContains=t)
        if el.exists:
            return el
    # Fallback to common FB button ids
    for rid in (
        "com.facebook.katana:id/primary_named_button",
        "com.facebook.katana:id/done_button",
    ):
        el = driver(resourceId=rid)
        if el.exists:
            return el
    return None


def _find_done_button(driver):
    # Close keyboard / finish editing text
    for t in ("Xong", "Done", "OK"):
        el = driver(text=t)
        if el.exists:
            return el
    return None


def _area_from_bounds(bounds) -> int:
    try:
        # bounds can be dict {left,top,right,bottom}
        l = int(bounds.get("left", 0))
        t = int(bounds.get("top", 0))
        r = int(bounds.get("right", 0))
        b = int(bounds.get("bottom", 0))
        return max(0, r - l) * max(0, b - t)
    except Exception:
        return 0


def _pick_best_edittext(driver):
    """Pick the most likely content editor EditText on the edit-post screen."""
    xp = driver.xpath("//android.widget.EditText")
    if not xp.exists:
        return None

    # Prefer the largest visible EditText
    try:
        nodes = xp.all()
    except Exception:
        nodes = []

    best = None
    best_score = -1

    # If .all() is unavailable, fall back to .get()
    if not nodes:
        try:
            return xp.get()
        except Exception:
            return None

    for n in nodes:
        try:
            info = getattr(n, "info", {}) or {}
            bounds = info.get("bounds") or {}
            score = _area_from_bounds(bounds)

            # Bonus if focusable/editable
            if info.get("focusable"):
                score += 10_000
            if info.get("clickable"):
                score += 5_000
            if info.get("enabled"):
                score += 1_000

            if score > best_score:
                best = n
                best_score = score
        except Exception:
            continue

    return best


async def _replace_text(driver, new_text: str) -> bool:
    editor = _pick_best_edittext(driver)
    if not editor:
        return False

    try:
        await asyncio.to_thread(editor.click)
        await asyncio.sleep(0.3)

        # Try clear + set
        try:
            await asyncio.to_thread(editor.clear_text)
            await asyncio.sleep(0.2)
        except Exception:
            # Some FB builds don't expose clear_text reliably
            pass

        try:
            await asyncio.to_thread(editor.set_text, new_text)
        except Exception:
            # Fallback: driver.send_keys
            try:
                await asyncio.to_thread(driver.send_keys, new_text, True)
            except Exception:
                return False

        await asyncio.sleep(0.3)

        done = _find_done_button(driver)
        if done:
            await asyncio.to_thread(done.click)
            await asyncio.sleep(0.5)

        return True
    except Exception:
        return False


async def edit_post(
    driver,
    command_id: str,
    post_link: str,
    new_text: Optional[str] = None,
    files: Optional[Sequence[str]] = None,
) -> bool:
    """Edit a Facebook post.

    Current version: text-only (edit caption/content). Media replacement will be added next.

    Concept rules:
    - If both new_text and files are empty -> do not run.
    - If new_text is provided -> replace entire old text with new_text.
    """
    dev = DEVICE_LIST_NAME.get(driver.serial, driver.serial)

    new_text = (new_text or "").strip()
    files = list(files) if files else []

    if not post_link:
        msg = "❌ Thiếu post_link"
        log_message(f"[{dev}] Chỉnh sửa bài: {msg}", logging.WARNING)
        await pymongo_management.execute_command(command_id, msg)
        return False

    if not new_text and not files:
        msg = "❌ Không có thay đổi (new_text và files đều rỗng)"
        log_message(f"[{dev}] Chỉnh sửa bài: {msg}", logging.WARNING)
        await pymongo_management.execute_command(command_id, msg)
        return False

    # Text-first version: if only files provided, we don't do partial edit silently.
    if files and not new_text:
        msg = "❌ Hiện mới hỗ trợ sửa text. Nếu muốn thay media, hãy gửi kèm new_text hoặc đợi bản media." 
        log_message(f"[{dev}] Chỉnh sửa bài: {msg}", logging.WARNING)
        await pymongo_management.execute_command(command_id, msg)
        return False

    log_message(f"[{dev}] ✏️ Chỉnh sửa bài theo link: {post_link}", logging.INFO)

    # 1) Open post detail
    try:
        redirect_to(driver, post_link)
        await asyncio.sleep(5)
    except Exception as e:
        msg = f"❌ Không mở được post_link: {e}"
        log_message(f"[{dev}] Chỉnh sửa bài: {msg}", logging.WARNING)
        await pymongo_management.execute_command(command_id, msg)
        return False

    # 2) Open menu ...
    more_btn = _find_more_button(driver)
    if not more_btn:
        msg = "❌ Không tìm thấy nút '...' (More options)"
        log_message(f"[{dev}] Chỉnh sửa bài: {msg}", logging.WARNING)
        await pymongo_management.execute_command(command_id, msg)
        return False

    await asyncio.to_thread(more_btn.click)
    await asyncio.sleep(1)

    # 3) Find 'Edit' option (may require scroll)
    edit_el = None
    for _ in range(6):
        edit_el = _find_edit_option(driver)
        if edit_el:
            break
        driver.swipe_ext("up", scale=0.6)
        await asyncio.sleep(0.4)

    if not edit_el:
        msg = "❌ Không thấy lựa chọn 'Chỉnh sửa/Edit' trong menu"
        log_message(f"[{dev}] Chỉnh sửa bài: {msg}", logging.WARNING)
        await pymongo_management.execute_command(command_id, msg)
        try:
            driver.press("back")
        except Exception:
            pass
        return False

    await asyncio.to_thread(edit_el.click)
    await asyncio.sleep(2)

    # 4) Replace text (text-first)
    if new_text:
        ok_text = await _replace_text(driver, new_text)
        if not ok_text:
            msg = "❌ Không tìm/không set được ô nội dung để chỉnh sửa"
            log_message(f"[{dev}] Chỉnh sửa bài: {msg}", logging.WARNING)
            await pymongo_management.execute_command(command_id, msg)
            return False

    # 5) Save
    save_btn = _find_save_button(driver)
    if not save_btn:
        msg = "❌ Không tìm thấy nút 'Lưu/Save'"
        log_message(f"[{dev}] Chỉnh sửa bài: {msg}", logging.WARNING)
        await pymongo_management.execute_command(command_id, msg)
        return False

    await asyncio.to_thread(save_btn.click)
    await asyncio.sleep(2)

    # 6) Report success
    await pymongo_management.execute_command(command_id, "Đã thực hiện")
    log_message(f"[{dev}] ✅ Chỉnh sửa bài xong: {post_link}", logging.INFO)

    # 7) Cleanup UI
    try:
        await toolfacebook_lib.back_to_facebook(driver)
    except Exception:
        pass

    return True
