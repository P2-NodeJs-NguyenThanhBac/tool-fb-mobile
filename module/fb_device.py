import uiautomator2 as u2
import logging
from util import log_message, DEVICE_LIST_NAME


def connect_and_open_facebook(device_id: str):
    """
    Kết nối thiết bị Android qua uiautomator2 và mở app Facebook.

    Args:
        device_id: Mã thiết bị (adb serial)

    Returns:
        uiautomator2.Device hoặc None nếu lỗi
    """
    try:
        driver = u2.connect(device_id)
        log_message(f"✅ Kết nối thành công với thiết bị: {DEVICE_LIST_NAME[device_id]}")

        driver.app_start("com.facebook.katana", ".LoginActivity")
        log_message(f"🚀 Đã mở ứng dụng Facebook trên thiết bị: {DEVICE_LIST_NAME[device_id]}")

        return driver
    except Exception as e:
        log_message(f"❌{DEVICE_LIST_NAME[device_id]} Lỗi khi kết nối hoặc mở Facebook: {e}", level=logging.ERROR)
        return None
