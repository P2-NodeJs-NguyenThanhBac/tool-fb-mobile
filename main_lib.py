from util import *
import subprocess
import json
import os
import glob

async def check_termux(driver):
    device_id = driver.serial
    if os.name == 'nt':
        result = subprocess.run([WINDOW_ADB_PATH, "-s", device_id, "shell", "pm", "list", "packages"], capture_output=True, text=True)
    else:
        result = subprocess.run([LINUX_ADB_PATH, "-s", device_id, "shell", "pm", "list", "packages"], capture_output=True, text=True)
    if "com.termux" in result.stdout:
        return True
    else:
        return False
async def install_termux(driver):
    device_id = driver.serial
    if os.name == 'nt':
        subprocess.run([WINDOW_ADB_PATH, "-s", device_id, "install-multiple", "arm64-v8a/base.apk", "arm64-v8a/split_config.arm64_v8a.apk", "arm64-v8a/split_config.vi.apk", "arm64-v8a/split_config.xxhdpi.apk"])
    else:
        subprocess.run([LINUX_ADB_PATH, "-s", device_id, "install-multiple", "arm64-v8a/base.apk", "arm64-v8a/split_config.arm64_v8a.apk", "arm64-v8a/split_config.vi.apk", "arm64-v8a/split_config.xxhdpi.apk"])
    if not await check_termux(driver):
        if os.name == 'nt':
            subprocess.run([WINDOW_ADB_PATH, "-s", device_id, "install-multiple", "armeabi-v7a/base.apk", "armeabi-v7a/split_config.armeabi_v7a.apk", "armeabi-v7a/split_config.vi.apk", "armeabi-v7a/split_config.xhdpi.apk"])
        else:
            subprocess.run([LINUX_ADB_PATH, "-s", device_id, "install-multiple", "armeabi-v7a/base.apk", "armeabi-v7a/split_config.armeabi_v7a.apk", "armeabi-v7a/split_config.vi.apk", "armeabi-v7a/split_config.xhdpi.apk"])
        if not await check_termux(driver):
            log_message(f"[{DEVICE_LIST_NAME[device_id]}] Lỗi cài đặt Termux", logging.ERROR)
            return False
        
async def check_termux_api_installed(driver):
    device_id = driver.serial
    log_message(f"[{DEVICE_LIST_NAME[device_id]}] Kiểm tra cài đặt Termux", logging.INFO)
    if not await check_termux(driver):
        log_message(f"[{DEVICE_LIST_NAME[device_id]}] Cài đặt Termux", logging.INFO)
        if not await install_termux(driver):
            return False
        driver.app_start("com.termux")
        await asyncio.sleep(10)
        driver(resourceId="com.android.permissioncontroller:id/permission_allow_button").click()
        await asyncio.sleep(10)
    driver.app_start("com.termux")
    driver.send_keys("pkg install termux-api -y\n")
    return True

async def reset_active():
    # Đường dẫn tới thư mục chứa các file JSON
    folder_path = ZALO_BASE_PATH

    # Tìm tất cả các file có dạng device_status_*.json
    json_files = glob.glob(os.path.join(folder_path, "device_status_*.json"))

    for file_path in json_files:
        device_id = os.path.basename(file_path).replace("device_status_", "").replace(".json", "")
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # Nếu có trường 'active', cập nhật thành False
            if 'active' in data:
                data['active'] = False

                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=4, ensure_ascii=False)
        except Exception as e:
            log_message(f"[{DEVICE_LIST_NAME[device_id]}] Lỗi khi đặt lại trạng thái {file_path}: {e}", logging.ERROR)