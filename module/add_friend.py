import logging
from util import *
from .check_add_friends import add_new_request
import requests

API_LINK = "https://api.timviec365.vn/api/crm/customer/getNTDByEmpIdToGetPhoneNumber"

# Gọi API lấy tên ntd, kết bạn
async def add_friend(driver, crm_id:str, user_name):
    """
    Lấy ntd, kiểm tra xem có thể kết bạn không, nếu không thì tìm ntd khác
    """
    crm_id = str(crm_id or "").strip()
    user_name = str(user_name or "").replace("_", " ").strip() or crm_id
    if not crm_id.isdigit():
        log_message(f"crm_id không hợp lệ, bỏ qua tác vụ kết bạn: {crm_id}", logging.WARNING)
        return

    log_message("Bắt đầu kết bạn")
    # Gọi API lấy ntd
    payload = {
        "emp_ids": [int(crm_id)],
        "size": 1,
        "key": "1697a131cb22ea0ab9510d379a8151f1",
        "getFbLink": True
    }
    fb_link = None
    try:
        response = requests.post(API_LINK, json=payload, timeout=20)
        response.raise_for_status()
        data = response.json()
        log_message("Lấy được response")
        api_data = data.get("data") or {}
        entries = api_data.get(crm_id) or api_data.get(int(crm_id)) or []
        if entries and isinstance(entries, list):
            fb_link = (entries[0] or {}).get("link_user_post")
        if not fb_link:
            log_message(f"Không lấy được link Facebook từ API cho crm_id={crm_id}", logging.WARNING)
            return
    except Exception as e:
        log_message(f"Có lỗi trong khi gọi API kết bạn: {type(e).__name__} | {e}", logging.ERROR)
        return
    # Truy cập link trang cá nhân
    try:
        redirect_to(driver, fb_link)
    except Exception:
        log_message("Không thể truy cập link trang cá nhân", logging.ERROR)
        return
    await asyncio.sleep(random.uniform(20,30))

    try:
        # Tìm nút kết bạn
        if add_button := await my_find_element(driver, {('xpath', '//android.widget.Button[@content-desc="Thêm bạn bè"]')}):
            add_button.click()
            log_message("Đã gửi lời mời kết bạn")
            add_new_request(user_name, fb_link)
        elif setting := await my_find_element(driver, {('xpath', '//android.widget.Button[contains(@content-desc,"Xem cài đặt khác của trang cá nhân")]')}):
            log_message("Không tìm được nút kết bạn", logging.WARNING)
            setting.click()
            log_message("Mở menu để tìm nút kết bạn")
            if add_button := await my_find_element(driver, {('xpath', '//android.widget.Button[@content-desc="Thêm bạn bè"]')}):
                add_button.click()
                log_message("Đã gửi lời mời kết bạn")
                add_new_request(user_name, fb_link)
            else:
                log_message("Không tìm được nút kết bạn", logging.ERROR)
        else:
            log_message("Không thể kết bạn", logging.ERROR)
    except asyncio.CancelledError:
        log_message("Tác vụ Kết bạn bị hủy", logging.WARNING)
        raise
    await asyncio.sleep(random.uniform(3,6))
    # Về home
    await go_to_home_page(driver)
    return

