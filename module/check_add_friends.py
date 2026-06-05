import os
import json
from datetime import datetime, timedelta
from typing import Dict, Any, List
from util import *
import asyncio 
# --- CẤU HÌNH ---
BASE_FOLDER = "check_add_friends"
DAYS_TO_EXPIRE = 7  # Số ngày giới hạn để xóa

# Code đọc data *******************************
def get_file_path(username: str) -> str:
    """Lấy đường dẫn file, tự động tạo thư mục nếu chưa có"""
    os.makedirs(BASE_FOLDER, exist_ok=True)
    return os.path.join(BASE_FOLDER, f"fb_{username}_check_add_friend.json")

def load_data(file_path: str) -> Dict[str, Any]:
    """Đọc dữ liệu từ file JSON"""
    if os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}
# ******************************************************
# Code lưu data *************************************
def save_data(file_path: str, data: Dict[str, Any]):
    """Lưu dữ liệu vào file JSON"""
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        # print(f"Đã lưu dữ liệu vào {file_path}")
    except Exception as e:
        print(f"Lỗi khi lưu file: {e}")
# ************************************************

# --- 1. CHỨC NĂNG THÊM NGƯỜI MỚI ADD ---
def add_new_request(username: str, link_fb: str):
    """
    Lưu người vừa gửi lời mời kết bạn vào file.
    Mặc định be_friend là 'False' (chưa đồng ý).
    """
    #load data đã có
    file_path = get_file_path(username)
    data = load_data(file_path)

    # Chỉ thêm nếu chưa tồn tại (hoặc cập nhật lại ngày add mới)
    current_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    data[link_fb] = {
        "link_fb": link_fb,
        "date_add": current_date,
        "be_friend": "False" # Mặc định là chưa là bạn
    }
    
    save_data(file_path, data)
    print(f"[ADD] Đã thêm {link_fb} vào danh sách theo dõi.")
# *********************************************************************


# --- 2. CHỨC NĂNG CẬP NHẬT TRẠNG THÁI (KHI HỌ ĐỒNG Ý) ---
def mark_as_friend(username: str, link_fb: str):
    """
    Gọi hàm này khi tool check thấy 2 người đã là bạn bè.
    """
    file_path = get_file_path(username)
    data = load_data(file_path)
    
    if link_fb in data:
        data[link_fb]["be_friend"] = "True"
        # Cập nhật lại ngày thành bạn bè nếu cần
        # data[link_fb]["date_add"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S") 
        save_data(file_path, data)
        print(f"[UPDATE] Đã cập nhật {link_fb} thành bạn bè.")
    else:
        print(f"Không tìm thấy {link_fb} trong data.")
# ******************************************************************


# --- 3. CHỨC NĂNG QUAN TRỌNG: KIỂM TRA file  VÀ Thu hồi lời mời kết bạn sau 7 ngày  ---
async def clean_expired_requests_fb(driver, username: str):
    """
    Duyệt toàn bộ list, nếu:
    - be_friend == "False"
    - VÀ (Ngày hiện tại - date_add) > 7 ngày
    => Xóa khỏi file JSON.
    """
    username = str(username or "").replace("_", " ").strip()
    if not username:
        log_message("Bỏ qua kiểm tra trạng thái kết bạn vì username rỗng", logging.WARNING)
        return

    file_path = get_file_path(username)
    data = load_data(file_path)
    
    current_time = datetime.now()
    keys_to_delete = []
    
    log_message(f"--- Bắt đầu quét dọn file của {username} ---")

    for link, info in data.items():
        try:
            # Nếu đã là bạn rồi thì bỏ qua, không xóa
            if info.get("be_friend") == "True":
                continue
            check_agree = await check_add_friend_fb(driver,link)
            if check_agree:
                mark_as_friend(username, link)
            else:
                date_str = info.get("date_add")
                try:
                    # Chuyển đổi string ngày tháng trong file thành object datetime
                    added_time = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
                    # Tính khoảng cách thời gian
                    delta = current_time - added_time
                    if delta.days > DAYS_TO_EXPIRE:
                        keys_to_delete.append(link)
                        print(f"-> Phát hiện hết hạn ({delta.days} ngày): {link}")
                except ValueError:
                    print(f"Lỗi định dạng ngày tháng cho link: {link}")
                    continue
        except asyncio.CancelledError:
            log_message("Tác vụ Kiểm tra trạng thái kết bạn bị hủy", logging.WARNING)
            raise
    # Thực hiện hủy kết bạn và xóa trong file json
    if keys_to_delete:
        for link in keys_to_delete:
            await un_friend(driver,link_fb=link)
            del data[link]
        save_data(file_path, data)
        print(f"[CLEANUP] Đã xóa {len(keys_to_delete)} lời mời quá hạn.")
    else:
        print("[CLEANUP] Không có lời mời nào quá hạn.")

# --- KIỂM TRA KẾT BẠN TRÊN FACEBOOK ---

async def check_add_friend_fb(driver, link_fb:str):
    """
    Kiểm tra trạng thái kết bạn, đã là bạn bè return True
    """
    try:
        redirect_to(driver, link_fb)
        await asyncio.sleep(10)
    except Exception:
        log_message("Không thể truy cập link trang cá nhân", logging.ERROR)
        return False

    #element trạng thái kết bạn trên facebook
    selector = [
        # ("xpath",'//*[@resource-id="android:id/content"]/android.widget.FrameLayout[1]'
        #                     '/android.widget.FrameLayout[1]/android.widget.FrameLayout[1]/android.view.ViewGroup[1]'
        #                     '/android.view.ViewGroup[1]/android.view.ViewGroup[1]/android.view.ViewGroup[1]'
        #                     '/androidx.recyclerview.widget.RecyclerView[1]/android.view.ViewGroup[5]/android.view.ViewGroup[1]'
        #                     '/android.view.ViewGroup[1]/android.view.ViewGroup[1]/android.widget.Button[1]'
        #                     '/android.view.ViewGroup[1]/android.view.ViewGroup[1]/android.view.ViewGroup[1]/android.view.ViewGroup[1]')
        # ,
        ("text","Bạn bè")
    ]
    if await my_find_element(driver,selector):
        log_message(f"đã là bạn")
        driver.press("back")
        return True
    else:
        log_message(f"chưa là bạn")
    driver.press("back")
    return False

# --- Hủy LỜI MỜI KẾT BẠN---
async def un_friend(driver, link_fb):
    """
    Hùy lời mời kết bạn khi đã quá 7 ngày
    """
    try:
        redirect_to(driver, link_fb)
        await asyncio.sleep(10)
    except Exception:
        log_message("Không thể truy cập link trang cá nhân", logging.ERROR)
        return False
    
    #element trạng thái kết bạn trên facebook
    selector = [
        # ("xpath",'//*[@resource-id="android:id/content"]/android.widget.FrameLayout[1]'
        #                     '/android.widget.FrameLayout[1]/android.widget.FrameLayout[1]/android.view.ViewGroup[1]'
        #                     '/android.view.ViewGroup[1]/android.view.ViewGroup[1]/android.view.ViewGroup[1]'
        #                     '/androidx.recyclerview.widget.RecyclerView[1]/android.view.ViewGroup[5]/android.view.ViewGroup[1]'
        #                     '/android.view.ViewGroup[1]/android.view.ViewGroup[1]/android.widget.Button[1]'
        #                     '/android.view.ViewGroup[1]/android.view.ViewGroup[1]/android.view.ViewGroup[1]/android.view.ViewGroup[1]')
        # ,
        ("text","Hủy yêu cầu")
    ]

    cancel_botton = await my_find_element(driver,selector)
    if cancel_botton:
        cancel_botton.click()
        await asyncio.sleep(3)
        cancel_botton2 = await my_find_element(driver,selector)
        if cancel_botton2:
            cancel_botton2.click()
            log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] hủy kết bạn thành công", logging.INFO)
            driver.press("back")
        else:
            log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] không tìm thấy nút bấm lần 2",logging.INFO)
            driver.press("back")
            return False
    else:
        log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] không tìm thấy nút bấm hủy kết bạn",logging.INFO)
        driver.press("back")
        return True
    
