import random
import os, json
from datetime import datetime
from typing import Dict, Any, List
import asyncio
from .log import log_message
import logging
import textwrap
DEVICE_LIST_NAME = {}
LOG_FILE_CHECK_RAN_PATH = "zalo_run_log.json"

# Truy cập 1 trang facebook qua link
def redirect_to(driver, link):
    driver.shell(f"am start -a android.intent.action.VIEW -d '{link}'")

# Nhập text
async def type_text_input(element, text):
    """
    Gõ từng ký tự một vào element với thời gian trễ ngẫu nhiên,
    giúp mô phỏng hành vi nhập liệu của con người.
    """
    await asyncio.sleep(random.uniform(0.5, 0.8))
    for char in text:
        element.set_text(element.info['text'] + char)
        await asyncio.sleep(random.uniform(0.1, 0.3))
    log_message(f"Đã nhập: {text}")

# Tìm element thỏa mãn
async def my_find_element(d, locators, max_retries=2, nature_scroll_if_not_found=False, back_if_not_found = False):
    try:
        for _ in range(max_retries):
            for locator in locators:
                method, value = locator
                if method == "text":
                    element = d(text=value)
                elif method == "desc":
                    element = d(description=value)
                elif method == "resourceId":
                    element = d(resourceId=value)
                elif method == "className":
                    element = d(className=value)
                elif method == "xpath":
                    element = d.xpath(value)
                else:
                    log_message(f"{d.serial} - Không hỗ trợ method: {method}", logging.ERROR)
                    continue
                # if element.exists:
                found = await asyncio.to_thread(element.wait, timeout=10.0)
                if found:
                    # log_message(f"{d.serial} - Tìm thấy element với locator: {locator}")
                    return element
            if nature_scroll_if_not_found:
                await nature_scroll(d, max_roll=1, isFast=True)
            if back_if_not_found:
                await asyncio.to_thread(d.press,"back")
            await asyncio.sleep(1)
    except Exception as e:
        log_message(f"Lỗi {type(e).__name__}: {e}", logging.ERROR)
    return None

# Tìm nhiều element
def my_find_elements(d, locators):
    elements = []
    for locator in locators:
        method, value = locator
        try:
            found = []
            if method == "xpath":
                selector = d.xpath(value)
                if selector.exists:
                    found = selector.all()
            elif method == "text":
                count = d(text=value).count
                found = [d(text=value, instance=i) for i in range(count)]
            elif method == "desc":
                count = d(description=value).count
                found = [d(description=value, instance=i) for i in range(count)]
            elif method == "resourceId":
                count = d(resourceId=value).count
                found = [d(resourceId=value, instance=i) for i in range(count)]
            elif method == "className":
                count = d(className=value).count
                found = [d(className=value, instance=i) for i in range(count)]
            else:
                log_message(f"Không hỗ trợ method: {method}", logging.ERROR)
                continue

            if found:
                # log_message(f"Tìm thấy {len(found)} element với locator: {locator}")
                elements.extend(found)
            # else:
                # log_message(f"Không tìm thấy element với locator: {locator}", logging.WARNING)

        except Exception as e:
            log_message(f"Lỗi {type(e).__name__} khi xử lý locator {locator}: {e}", logging.ERROR)

    if not elements:
        log_message("Không tìm thấy element nào trong tất cả locator", logging.ERROR)
    log_message(f"Đã tìm thấy {len(elements)} element")
    return elements



async def nature_scroll(d, max_roll=1, isFast=False):
    """
    Mô phỏng thao tác cuộn bằng ngón tay cái.
    """
    size = d.window_size()
    width, height = size[0], size[1]

    start_x = width / 2
    end_x = width*3/4
    start_y = height * 0.8
    end_y = height * 0.2
    duration = 0.04 if isFast else 0.2
    sleep_time = 3 if isFast else 1

    for _ in range(max_roll):
        d.swipe(start_x, start_y, end_x, end_y, duration=duration)

        await asyncio.sleep(sleep_time)

async def scroll_up(d, max_roll=1, isFast=False):
    """
    Mô phỏng thao tác cuộn ngược.
    """
    size = d.window_size()
    width, height = size[0], size[1]

    start_x = width / 4
    end_x = width / 2
    start_y = height * 0.2
    end_y = height * 0.8
    duration = 0.04 if isFast else 0.2
    sleep = 2 if isFast else 0.5

    for _ in range(max_roll):
        d.swipe(start_x, start_y, end_x, end_y, duration=duration)

        await asyncio.sleep(sleep)
    log_message(f"Cuộn ngược {max_roll} lần")

# Kéo xuống để tìm element
async def scroll_until_element_visible(driver, locators, max_scrolls=10):    
    """
    Element khi không hiển thị trên view thường sẽ không tìm được, cần kéo xuống để load và tìm lại
    """

    for _ in range(max_scrolls):
        element = await my_find_element(driver, locators)
        if element != None:
            return element
        await nature_scroll(driver)
    log_message(f"Không tìm thấy element sau {max_scrolls} roll", logging.ERROR)
    return None

# Tìm về trang chủ
async def go_to_home_page(driver):
    """
    Trở về đầu trang để tìm các tác vụ khác
    """
    element = await my_find_element(driver, {("xpath", '//android.widget.Button[@content-desc="Đi tới trang cá nhân"]')}, 10, back_if_not_found=True)
    if element is None:
        log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Không tìm được homepage sau 10 lần thử", logging.ERROR)
        return None
    log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Trạng thái: Homepage")

def check_interruption_events(driver, restart_event, screen_standing_event, action_name) -> bool:
    """
    Kiểm tra xem có sự kiện restart hoặc đứng màn hình không.
    Trả về True nếu cần DỪNG action hiện tại.
    """
    device_id = driver.serial

    # Kiểm tra restart_event
    if restart_event.get(device_id):
        log_message(f"[{DEVICE_LIST_NAME[device_id]}] ⚠️ Phát hiện restart event, dừng action {action_name}", logging.WARNING)
        return True

    # Kiểm tra screen_standing_event
    if screen_standing_event.get(device_id):
        log_message(f"[{DEVICE_LIST_NAME[device_id]}] ⚠️ Phát hiện đứng màn hình, dừng action {action_name}", logging.WARNING)
        screen_standing_event[device_id] = False
        return True

    return False

# Hàm lấy dữ liệu 
def load_log_check_ran():
    """Đọc file log, nếu không có thì trả về list rỗng"""
    if not os.path.exists(LOG_FILE_CHECK_RAN_PATH):
        return []
    try:
        with open(LOG_FILE_CHECK_RAN_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return []

def check_zalo_ran_today(device_id):
    """
    Kiểm tra xem device_id đã chạy trong ngày hôm nay chưa.
    Trả về True nếu đã chạy, False nếu chưa.
    """
    logs = load_log_check_ran()
    today_str = datetime.now().strftime("%Y-%m-%d")
    
    for entry in logs:
        if entry.get('device_id') == device_id and entry.get('last_run_date') == today_str:
            return True
    return False


#-------------------------------------------------------------
#Hàm log thông báo cần người dùng thao tác tay
BASE_FOLDER_LOG_NOTE = "log_note_thao_tac_tay"

def get_file_path_log_note() -> str:
    """Lấy đường dẫn file, tự động tạo thư mục nếu chưa có"""
    now = datetime.now()
    date_str= now.strftime("%Y-%m-%d")
    os.makedirs(BASE_FOLDER_LOG_NOTE, exist_ok=True)
    return os.path.join(BASE_FOLDER_LOG_NOTE, f"{date_str}_log_note_thao_tac_tay.json")

def load_data(file_path: str) -> List[Dict[str, Any]]:
    """Đọc dữ liệu từ file JSON"""
    if os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def save_data(file_path: str, data: List[Dict[str, Any]]):
    """Lưu dữ liệu vào file JSON"""
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        # print(f"Đã lưu dữ liệu vào {file_path}")
    except Exception as e:
        print(f"Lỗi khi lưu file: {e}")
def log_note_acc(driver, account, note):
    """Hàm log thông báo cần người dùng thao tác tay"""
    device_id = driver.serial
    device_name = DEVICE_LIST_NAME.get(device_id, device_id)
    # Mở file, nếu chưa có thì tạo mới 
    file_path = get_file_path_log_note()
    data = load_data(file_path)
    clean_note = textwrap.dedent(note).strip() # Loại bỏ khoảng trắng thụt đầu dòng
    note_as_list = clean_note.split('\n')
    # Tạo dữ liệu json
    new_record = {
        "timestamp": datetime.now().strftime("%H:%M:%S"),
        "device_id": device_id,
        "device_name": device_name,
        "account": account,
        "note": note_as_list
    }
    data.append(new_record)
    save_data(file_path, data)
    
