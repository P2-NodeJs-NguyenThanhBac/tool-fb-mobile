import uiautomator2 as u2
import asyncio
import os
import json
import logging
from util import *
from module import *
from collections import deque
from util.crm_bridge import crm_bridge


USER_ACCOUNT_FILE = "user_account.json"
DEVICES_FOLDER = "devices"

# Xử lý khi tài khoản không đăng nhập được, đánh dấu tài khoản bị ban, trả về danh sách tài khoản ở thiết bị theo cấu trúc file gốc
# Thay đổi cấu trúc file gốc, bỏ phần thiết bị ra khỏi file, thay vào đó là chia tài khoản cho các thiết bị, mỗi thiết bị 3 tài khoản
# Kiểm tra thiết bị nào chưa đăng nhập đủ tài khoản thì chia thêm tài khoản cho thiết bị đó

# Luồng xử lý:
'''
đọc file user_account.json lấy danh sách tài khoản, chỉnh sửa cấu trúc để xử lý trong task

'''

# Chỉ tạo thư mục khi chưa tồn tại
def ensure_devices_folder():
    if not os.path.exists(DEVICES_FOLDER):
        os.makedirs(DEVICES_FOLDER)

# Lấy danh sách tài khoản từ file json
def load_account_list():
    if not os.path.exists(USER_ACCOUNT_FILE):
        return []
    try:
        with open(USER_ACCOUNT_FILE, "r", encoding="utf-8") as f:
            if os.stat(USER_ACCOUNT_FILE).st_size == 0:
                return []            
            data = json.load(f)
    except json.JSONDecodeError:
        return []
    for acc in data:
        acc['name'] = None
        acc['status'] = True
    return data

# Tạo cấu trúc file thiết bị
def create_device_structure(device_id):
    return {
        "device_id": device_id,
        "current_account": None,
        "accounts": []
    }

# Lưu dữ liệu thiết bị vào file
def save_device(device):
    file_path = os.path.join(DEVICES_FOLDER, f"device_{device['device_id']}.json")
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(device, f, indent=4, ensure_ascii=False)
    log_message(f"Đã tạo file {file_path} cho thiết bị {device['device_id']}")

# Lưu lại danh sách tài khoản
def save_account_list(account_list):
    with open(USER_ACCOUNT_FILE, "w", encoding="utf-8") as f:
        json.dump(account_list, f, indent=4, ensure_ascii=False)
    log_message("Đã lưu lại danh sách tài khoản chưa xử lý")

# Lấy tên tài khoản nếu tên được lưu là null
async def get_account_name(driver, account):
    if not account['name']:
        personal_page = await go_to_home_page(driver)
        try:
            personal_page.click()
            log_message(f"Tới trang cá nhân để lấy tên cho tk {account['account']}")
            await asyncio.sleep(3)
            text_views = my_find_elements(driver, {("className", "android.view.View")})
            try:
                if ("..." or "?") in text_views[0].get_text():
                    account['name'] = text_views[1].get_text()
                else:
                    account['name'] = text_views[0].get_text()
                log_message(f"Lấy được tên tài khoản: {account['name']}")
            except Exception as err:
                log_message(f"Lỗi khi lấy text_view: {err}", logging.ERROR)
        except Exception as e:
            log_message(f"Lỗi khi lấy tên tài khoản: {e}", logging.ERROR)
        # back về trang chủ
        driver.press("back")
        await asyncio.sleep(3)

# Các task xử lý riêng biệt cho từng thiết bị
async def handle_device(device_id, account_list, lock):
    device = create_device_structure(device_id)
    try:
        driver = u2.connect(device_id)
        driver.app_start("com.facebook.katana", ".LoginActivity")
        log_message(f"Kết nốt với thiết bị {device_id}")
    except Exception as e:
        log_message(f"Lỗi kết nối thiết bị {device_id}, nguyên nhân: {e}", logging.ERROR)
        return
    await asyncio.sleep(6)
    while True:
        if (count := len(device['accounts'])) == 3:
            log_message(f"Thiết bị {device_id} đã đủ tài khoản, kết thúc task")
            break
        log_message(f"Thiết bị {device_id} đã có {count} tài khoản")
        # Truy cập vào account_list để lấy tk
        async with lock:
            if not account_list:
                log_message("Hết tài khoản để đăng nhập, kết thúc task")
                break
            account = account_list.popleft()
            if not account['status']:
                log_message("Tài khoản không đăng nhập được")
                account_list.append(account)
                break
            log_message(f"Xử lý tài khoản {account['account']} trên thiết bị {device_id}")
        # Kiểm tra tài khoản đã đăng nhập trên thiết bị chưa
        flag = True
        for acc in device['accounts']:
            if acc['account'] == account['account'] and acc['password'] == account['password']:
                flag = False
                break
        if not flag:
            log_message(f"Tài khoản {account['account']} đã đăng nhập trên thiết bị {device_id}, bỏ qua")
            break
        # Kiểm tra thiết bị đang ở giao diện nào(home_page hay login_activity)
        if my_find_element(driver, {("xpath", '//android.widget.Button[@content-desc="Đi tới trang cá nhân"]')}):
            log_message("Đang ở trang chủ")
            await log_out(driver)
        # ERROR: Chưa kiểm soát các trường hợp ở màn hình khác
        log_message(f"Đăng nhập vào tài khoản {account['account']} trên thiết bị {device_id}")
        if await login_facebook(driver, account):
            # Lấy tên cho tài khoản
            await get_account_name(driver, account)
            device['current_account'] = account['name']
            
            # Đồng bộ với Folder 1: Đăng ký bot
            crm_bridge.connect()
            crm_bridge.register_device(account['account'])
            
            device['accounts'].append(account)
        else:
            log_message("login thất bại", logging.ERROR)
            account['status'] = False
            async with lock:
                account_list.append(account)
    save_device(device) 

# Hàm xử lý luồng chính
async def main():
    ensure_devices_folder()
    account_list = deque(load_account_list())
    lock = asyncio.Lock()
    task = [handle_device(device_id, account_list, lock) for device_id in DEVICE_LIST]
    await asyncio.gather(*task)
    save_account_list(list(account_list))


if __name__ == "__main__":
    asyncio.run(main())
    