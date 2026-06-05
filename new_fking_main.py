from util import *
from module import *
import uiautomator2 as u2
import asyncio
from fb_task import run_on_device_original
from uiautomator2.exceptions import ConnectError
from adbutils import AdbError

async def run_on_one_device(device_id):
    # Chạy trên 1 thiết bị

    # Kết nối thiết bị
    try:
        driver = await asyncio.to_thread(u2.connect_usb, device_id)
        log_message(f"[{DEVICE_LIST_NAME[device_id]}]🔗 Đã kết nối", logging.INFO)
    except ConnectError:
        log_message(f"[{DEVICE_LIST_NAME[device_id]}]⛓️‍💥 Thiết bị chưa được kết nối: ConnectionError", logging.ERROR)
        return
    except AdbError:
        log_message(f"[{DEVICE_LIST_NAME[device_id]}]⛓️‍💥 Ngắt kết nối thiết bị: AdbError", logging.ERROR)
        return
    except Exception as e:
        log_message(f"[{DEVICE_LIST_NAME[device_id]}]❌ Lỗi kết nối thiết bị:{type(e).__name__}: {e}", logging.ERROR)
        return
    
    # Tách 2 thread, 1 chạy tool auto, 1 nghe device_status
    # Kiểm tra device_status
    # device_status = load_device_status(device_id)

    # Chạy task nuôi
    try:
        await run_on_device_original(driver)
    except Exception as e:
        log_message(f"[{DEVICE_LIST_NAME[device_id]}]❌ Lỗi trong quá trình chạy: {type(e).__name__}: {e}", logging.ERROR)
        return

async def make_task_for_all_devices():
    # Tạo task và chia ra cho tất cả các thiết bị
    tasks = [asyncio.create_task(run_on_one_device(device_id)) for device_id in DEVICE_LIST]
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    try:
        asyncio.run(make_task_for_all_devices())
    except KeyboardInterrupt:
        log_message("[!] Dừng bằng bàn phím (KeyboardInterrupt)")
    except Exception as e:
        log_message(f"Lỗi chạy chính: {e}", logging.ERROR)