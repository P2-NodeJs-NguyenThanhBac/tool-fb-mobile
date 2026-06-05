import asyncio
import logging

import toolfacebook_lib
import pymongo_management
import urllib.parse
from util import log_message, DEVICE_LIST_NAME

# ===== Helper: click menu "..." =====
def redirect_to(driver, link):
    """ Hàm này có thể sử dụng để vào link bài viết cá nhân hoặc nhóm với link rút gọn"""
    # Dù link ngắn hay dài, hãy mã hóa nó
    encoded_link = urllib.parse.quote(link)
    
    # Ép mở bằng WebView của Facebook
    # WebView sẽ tự lo việc giải mã link /share/p/ thành link bài viết thật
    deep_link = f"fb://facewebmodal/f?href={encoded_link}"
    
    driver.shell(f"am start -a android.intent.action.VIEW -d '{deep_link}'")

def _find_more_button(driver):
    # Ưu tiên content-desc trước vì "⋯" đôi khi là text rỗng
    candidates = (
        {"descriptionContains": "Tùy chọn"},
        {"descriptionContains": "More options"},
        {"descriptionContains": "Khác"},
        {"descriptionContains": "More"},
        {"text": "⋯"},
    )
    for sel in candidates:
        el = driver(**sel)
        if el.exists:
            return el
    return None

# ===== Helper: find delete option in menu =====
def _find_delete_option(driver):
    # VN/EN, có thể là "Xoá bài viết", "Xóa bài viết", "Delete post", "Move to trash" (tùy phiên bản)
    texts = (
        "Xoá",
        "Xóa",
        "Xoá bài",
        "Xóa bài",
        "Delete",
        "Remove",
        "Chuyển vào thùng rác",
        "Move to trash",
        "Trash",
    )
    for t in texts:
        el = driver(textContains=t)
        if el.exists:
            return el
    return None

def _find_confirm_delete(driver):
    # popup confirm thường có "Xoá"/"Delete"/"OK"
    for t in ("Xoá", "Xóa", "Delete", "OK", "Có", "XÓA", "XOÁ", "CÓ"):
        el = driver(text=t) if len(t) <= 3 else driver(textContains=t)
        if el.exists:
            return el
    return None

async def delete_post(driver, command_id: str, post_link: str) -> bool:
    dev = DEVICE_LIST_NAME.get(driver.serial, driver.serial)

    if not post_link:
        msg = "❌ Thiếu post_link"
        log_message(f"[{dev}] Xoá bài: {msg}", logging.WARNING)
        await pymongo_management.execute_command(command_id, msg)
        return False

    log_message(f"[{dev}] 🗑️ Xoá bài theo link: {post_link}", logging.INFO)

    # 1) Mở đúng bài (post detail)
    try:
        redirect_to(driver, post_link)  # đã có sẵn :contentReference[oaicite:1]{index=1}
        await asyncio.sleep(3)
    except Exception as e:
        msg = f"❌ Không mở được post_link: {e}"
        log_message(f"[{dev}] Xoá bài: {msg}", logging.WARNING)
        await pymongo_management.execute_command(command_id, msg)
        return False

    # 2) Mở menu ...
    more_btn = _find_more_button(driver)
    if not more_btn:
        msg = "❌ Không tìm thấy nút '...' (More options)"
        log_message(f"[{dev}] Xoá bài: {msg}", logging.WARNING)
        await pymongo_management.execute_command(command_id, msg)
        return False

    await asyncio.to_thread(more_btn.click)
    await asyncio.sleep(1)

    # 3) Tìm "Xoá"/"Delete" (có thể nằm sâu -> scroll menu vài lần)
    delete_el = None
    for _ in range(5):
        delete_el = _find_delete_option(driver)
        if delete_el:
            break
        driver.swipe_ext("up", scale=0.6)
        await asyncio.sleep(0.4)

    if not delete_el:
        msg = "❌ Không thấy lựa chọn 'Xoá/Delete' trong menu"
        log_message(f"[{dev}] Xoá bài: {msg}", logging.WARNING)
        await pymongo_management.execute_command(command_id, msg)
        # đóng menu
        driver.press("back")
        await asyncio.sleep(0.3)
        return False

    await asyncio.to_thread(delete_el.click)
    await asyncio.sleep(1)

    # 4) Confirm
    confirm = None
    for _ in range(3):
        confirm = _find_confirm_delete(driver)
        if confirm:
            break
        await asyncio.sleep(0.5)

    if confirm:
        await asyncio.to_thread(confirm.click)
        await asyncio.sleep(1)

    # 5) Báo Mongo thành công (theo style hiện tại)
    await pymongo_management.execute_command(command_id, "Đã thực hiện")
    log_message(f"[{dev}] ✅ Xoá bài xong: {post_link}", logging.INFO)

    # 6) Dọn UI
    try:
        await toolfacebook_lib.back_to_facebook(driver)  # đã có sẵn :contentReference[oaicite:2]{index=2}
    except Exception:
        pass

    return True
