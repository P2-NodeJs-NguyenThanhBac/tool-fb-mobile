from fb_task_patched import *
# from sending_message_and_adding_friend import DeviceHandler, NUM_PHONE
from Zalo_Phase import DeviceHandler
from rabbitmq_device_worker import start_device_rabbit_consumer
import uiautomator2 as u2
import asyncio
import logging
import time
import json
import hashlib
from typing import Optional, Callable
import sys
sys.stdout.reconfigure(encoding='utf-8')

import os
import inspect

from module import *
from util import *
import main_lib
import pymongo_management
from ws_client import WSClient
from urgent_queue import get_next_task_if_due


# ======================= PRIVATE API (Postman/UI) =======================
# ĐÃ BỎ phần tự động start/open UI 192.168.1.35:8000
# Nếu sau này cần dùng private_api_poller thì server private API phải tự chạy sẵn bên ngoài.

async def private_api_poller(driver_serial: str):
    """
    Kéo job từ private API server (/api/next?device_id=<serial>) rồi bơm vào urgent queue của device.
    Job này sẽ được fb_task xử lý giống WS urgent.

    Lưu ý:
    - Hàm này vẫn được giữ nguyên để không đổi luồng chức năng.
    - Nhưng code sẽ KHÔNG còn tự bật server 192.168.1.35:8000 nữa.
    - Nếu muốn dùng lại poller này, hãy tự chạy server private API trước.
    """
    event, queue = get_urgent_objects(driver_serial)
    while True:
        try:
            job = await private_api_client_patched.fetch_next_job(driver_serial)
            if job:
                # Đánh dấu nguồn để fb_task gọi /api/done sau khi chạy xong
                job.setdefault("source", "private_api")
                await queue.put(job)
                event.set()
        except Exception as e:
            log_message(f"[private_api_poller] error: {e}", logging.WARNING)
        await asyncio.sleep(1.0)

# ======================= CẤU HÌNH =======================
DEVICE_LIST = []   # Danh sách thiết bị hiện tại
active_tasks = {}  # Lưu task của từng thiết bị
screen_standing_event = {} # Lưu sự kiện màn hình đứng của từng thiết bị
restart_event = {} # Lưu sự kiện restart của từng thiết bị
HOME_PACKAGES = {
    "com.android.launcher",
    "com.google.android.apps.nexuslauncher",
    "com.sec.android.app.launcher",
    "com.miui.home",
    "com.oppo.launcher",
    "com.bbk.launcher2",
    "com.vivo.launcher",
    "com.gogo.launcher",
    "com.huawei.android.launcher",
    "com.teslacoilsw.launcher",
    "com.perfect.asmr.tidy.free.aspgg.game.launcher"
}
ZALO_PKG = "com.zing.zalo"
FACEBOOK_PKG = "com.facebook.katana"
TARGET_PACKAGES = {ZALO_PKG, FACEBOOK_PKG}

# Một lần bật 1.1.1.1 cho mỗi device (per-process memory)
_VPN_CHECKED = set()
_STATUS_FILE_CHECK = set()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

def _build_user_device_map_from_mongo():
    result = {}
    for device in get_all_devices_from_mongo():
        device_id = str((device or {}).get("device_id") or "").strip()
        if not device_id:
            continue
        accounts = (device or {}).get("accounts") or []
        if not isinstance(accounts, list):
            continue
        for account in accounts:
            if not isinstance(account, dict):
                continue
            account_id = str(account.get("account") or "").strip()
            if account_id:
                result[account_id] = device_id
    return result


USER_DEVICE_MAP = _build_user_device_map_from_mongo()

# Tạo map ngược: device_id -> list[user_id]
DEVICE_USERS = {}
for uid, did in USER_DEVICE_MAP.items():
    if not did:
        continue
    DEVICE_USERS.setdefault(did, []).append(uid)

# ======================= HÀM HỖ TRỢ =======================
def _is_home_pkg(pkg: str) -> bool:
    return pkg in HOME_PACKAGES

def _is_target_pkg(pkg: str) -> bool:
    return pkg in TARGET_PACKAGES

async def ensure_1111_vpn_on_once(driver, device_id: str):
    """
    Bật app 1.1.1.1 và gạt switch nếu chưa bật — chỉ 1 lần cho mỗi device_id.
    Khoan fail nếu không có app.
    """
    if device_id in _VPN_CHECKED:
        return
    _VPN_CHECKED.add(device_id)
    try:
        log_message(f"[{DEVICE_LIST_NAME[device_id]}] Kiểm tra bật 1.1.1.1 (một lần)", logging.INFO)
        await asyncio.to_thread(driver.app_start, "com.cloudflare.onedotonedotonedotone")
        await asyncio.sleep(3.0)

        sw = driver(resourceId="com.cloudflare.onedotonedotonedotone:id/launchSwitch")
        if sw.exists:
            try:
                checked = bool(sw.info.get("checked"))
            except Exception:
                checked = False
            if not checked:
                sw.click()
                await asyncio.sleep(1.5)

        log_message(f"[{DEVICE_LIST_NAME[device_id]}] One-time check 1.1.1.1 OK", logging.INFO)
    except Exception as e:
        log_message(f"[{DEVICE_LIST_NAME[device_id]}] VPN one-time check lỗi: {e}", logging.WARNING)

async def disable_auto_rotation(driver, device_id: str):
    """
    Thực sự tắt chế độ tự động xoay màn hình của hệ thống Android
    """
    def parse_shell_response(result):
        if hasattr(result, 'output'):
            return result.output.strip()
        elif hasattr(result, 'text'):
            return result.text.strip()
        elif hasattr(result, 'stdout'):
            return result.stdout.strip()
        else:
            return str(result).strip()

    try:
        log_message(f"[{DEVICE_LIST_NAME[device_id]}] Tắt chế độ tự động xoay màn hình hệ thống", logging.INFO)

        try:
            current_result = driver.shell("settings get system accelerometer_rotation")
            current_value = parse_shell_response(current_result)
            log_message(f"[{DEVICE_LIST_NAME[device_id]}] Trạng thái auto-rotation hiện tại: {current_value}", logging.INFO)
        except Exception as e:
            log_message(f"[{DEVICE_LIST_NAME[device_id]}] Không thể kiểm tra trạng thái hiện tại: {e}", logging.INFO)

        try:
            if current_value != "0":
                await asyncio.to_thread(driver.shell, "settings put system accelerometer_rotation 0")
                await asyncio.sleep(0.5)
                log_message(f"[{DEVICE_LIST_NAME[device_id]}] Đã gửi lệnh tắt auto-rotation", logging.INFO)
            else:
                log_message(f"[{DEVICE_LIST_NAME[device_id]}] Auto-rotation đã tắt sẵn, bỏ qua", logging.INFO)
        except Exception as e:
            log_message(f"[{DEVICE_LIST_NAME[device_id]}] Lỗi tắt auto-rotation qua settings: {e}", logging.INFO)

        try:
            result = await asyncio.to_thread(driver.shell, "settings get system accelerometer_rotation")
            final_value = parse_shell_response(result)

            if final_value == "0":
                status = "TẮT ✅"
                log_message(f"[{DEVICE_LIST_NAME[device_id]}] Auto-rotation đã được TẮT thành công!", logging.INFO)
            elif final_value == "1":
                status = "BẬT ❌"
                log_message(f"[{DEVICE_LIST_NAME[device_id]}] Auto-rotation vẫn còn BẬT - có thể cần retry", logging.WARNING)
            else:
                status = f"KHÔNG XÁC ĐỊNH ({final_value})"
                log_message(f"[{DEVICE_LIST_NAME[device_id]}] Trạng thái auto-rotation không xác định: {final_value}", logging.WARNING)

            log_message(f"[{DEVICE_LIST_NAME[device_id]}] Trạng thái auto-rotation cuối: {status}", logging.INFO)

        except Exception as e:
            log_message(f"[{DEVICE_LIST_NAME[device_id]}] Không thể kiểm tra trạng thái cuối: {e}", logging.INFO)

        log_message(f"[{DEVICE_LIST_NAME[device_id]}] Hoàn thành disable auto-rotation", logging.INFO)

    except Exception as e:
        log_message(f"[{DEVICE_LIST_NAME[device_id]}] Lỗi khi tắt auto-rotation: {e}", logging.WARNING)

class InactivityWatchdog:
    def __init__(
        self,
        driver,
        device_id: str,
        idle_seconds: int = 60,
        on_resume: Optional[Callable[[str], "asyncio.Future"]] = None,
        on_restart: Optional[Callable[[], "asyncio.Future"]] = None,
        phase_provider: Optional[Callable[[], str]] = None,
        check_frozen: bool = True,
        frozen_threshold: int = 1,
        ui_check_interval: int = 40
    ):
        self.driver = driver
        self.device_id = device_id
        self.idle_seconds = int(idle_seconds)
        self.on_resume = on_resume
        self.on_restart = on_restart
        self.phase_provider = phase_provider or (lambda: "facebook")
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()

        self._last_seen_target = time.time()
        self._home_since: Optional[float] = None
        self.check_frozen = check_frozen
        self.frozen_threshold = frozen_threshold
        self._last_ui_hash = None
        self._same_ui_count = 0
        self.ui_check_interval = ui_check_interval
        self._last_ui_check = 0

    def log(self, msg: str, level=logging.INFO):
        try:
            log_message(f"[{self.device_id}][Watchdog] {msg}", level)
        except Exception:
            print(f"[{self.device_id}][Watchdog] {msg}")

    async def start(self):
        if self._task is None:
            self._stop.clear()
            self._task = asyncio.create_task(self._run())

    async def stop(self):
        if self._task:
            self._stop.set()
            try:
                await self._task
            except Exception:
                pass
            self._task = None

    async def _run(self):
        while not self._stop.is_set():
            try:
                info = await asyncio.to_thread(self.driver.app_current)
                pkg = (info or {}).get("package", "") or ""
            except Exception:
                pkg = ""

            now = time.time()

            if _is_target_pkg(pkg):
                self._last_seen_target = now
                self._home_since = None
                if self.check_frozen and now - self._last_ui_check >= self.ui_check_interval:
                    self._last_ui_check = now
                    try:
                        xml = await asyncio.to_thread(self.driver.dump_hierarchy)
                        ui_hash = hashlib.sha256(xml.encode('utf-8')).hexdigest()

                        if self._last_ui_hash == ui_hash:
                            self._same_ui_count += 1
                        else:
                            self._same_ui_count = 0
                            self._last_ui_hash = ui_hash

                        if self._same_ui_count >= self.frozen_threshold:
                            self.log(f"[{DEVICE_LIST_NAME[self.device_id]}] ⚠️ UI không thay đổi {self.frozen_threshold} lần → đặt trạng thái đứng màn hình ", logging.WARNING)
                            screen_standing_event[self.device_id] = True
                            break
                    except Exception as e:
                        self.log(f"Lỗi kiểm tra đứng màn hình: {e}", logging.DEBUG)
            else:
                if self._home_since is None:
                    self._home_since = now

            if self._home_since and now - self._home_since >= 12:
                self._home_since = None
                if self.on_resume:
                    phase = self.phase_provider()
                    self.log(f"[{DEVICE_LIST_NAME[self.device_id]}]HOME >12s → mở lại app theo phase='{phase}'")
                    try:
                        await self.on_resume(phase)
                    except Exception as e:
                        self.log(f"[{DEVICE_LIST_NAME[self.device_id]}]Lỗi on_resume: {e}", logging.WARNING)

            await asyncio.sleep(3.0)

# ======================= LUỒNG THIẾT BỊ =======================
class RestartThisDevice(Exception):
    pass

async def device_once(device_id: str):
    restart_event[device_id] = False
    screen_standing_event[device_id] = False

    driver = await asyncio.to_thread(u2.connect_usb, device_id)
    handler = DeviceHandler(driver, device_id)
    await handler.connect()

    await ensure_1111_vpn_on_once(driver, device_id)

    result = await main_lib.check_termux_api_installed(driver)
    if not result:
        return

    await disable_auto_rotation(driver, device_id)
    current_phase = {"value": "zalo"}
    restart_event_flag = asyncio.Event()

    async def _on_resume(phase: str):
        if phase == "zalo":
            await asyncio.to_thread(driver.app_start, ZALO_PKG)
        else:
            await asyncio.to_thread(driver.app_start, FACEBOOK_PKG)
        await asyncio.sleep(2.0)

    async def _on_restart():
        try:
            await asyncio.to_thread(driver.app_stop, ZALO_PKG)
        except Exception:
            pass
        try:
            await asyncio.to_thread(driver.app_stop, FACEBOOK_PKG)
        except Exception:
            pass
        restart_event_flag.set()

    watchdog = InactivityWatchdog(
        driver=driver,
        device_id=device_id,
        idle_seconds=60,
        on_resume=_on_resume,
        on_restart=_on_restart,
        phase_provider=lambda: current_phase["value"],
    )
    await watchdog.start()
    await pymongo_management.update_device_status(device_id, True)

    try:
        current_phase["value"] = "facebook"
        await run_on_device_original(driver, screen_standing_event, restart_event)
        logging.info(
            "[%s] Sau pha Facebook: restart_flag=%s",
            DEVICE_LIST_NAME.get(device_id, device_id),
            restart_event_flag.is_set()
        )

        if restart_event_flag.is_set():
            logging.warning(
                "[%s] Watchdog yêu cầu restart sau Facebook -> bỏ qua Zalo",
                DEVICE_LIST_NAME.get(device_id, device_id),
            )
            raise RestartThisDevice("RESTART_THIS_DEVICE (sau pha Facebook)")

        await asyncio.to_thread(driver.app_stop, FACEBOOK_PKG)
        current_phase["value"] = "zalo"
        logging.info("[%s] BẮT ĐẦU pha Zalo", DEVICE_LIST_NAME.get(device_id, device_id))

        await asyncio.to_thread(driver.app_start, ZALO_PKG)
        await asyncio.sleep(2.0)

        logging.info("[%s] GỌI handler.run(Zalo)", DEVICE_LIST_NAME.get(device_id, device_id))
        await asyncio.to_thread(handler.run, screen_standing_event)
        logging.info("[%s] KẾT THÚC pha Zalo", DEVICE_LIST_NAME.get(device_id, device_id))

        if restart_event_flag.is_set():
            raise RestartThisDevice("RESTART_THIS_DEVICE (sau pha Zalo)")

    finally:
        await watchdog.stop()

async def check_driver(driver):
    try:
        _ = driver.info
        return True
    except Exception:
        return False

async def device_supervisor(device_id: str):
    while True:
        try:
            driver = await asyncio.to_thread(u2.connect_usb, device_id)
            break
        except:
            pass

    crm_device_id = driver.serial
    user_ids = DEVICE_USERS.get(crm_device_id, [])
    if not user_ids:
        log_message(
            f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] ❌ Không tìm thấy user_id nào tương ứng trong JSON — dùng default rỗng",
            logging.ERROR
        )

    event, queue = get_urgent_objects(device_id)

    # Nếu muốn dùng lại private API poller thì tự bỏ comment dòng dưới,
    # nhưng nhớ server private API phải tự chạy trước:
    # asyncio.create_task(private_api_poller(crm_device_id), name=f"private_api_{crm_device_id}")

    asyncio.create_task(
        start_device_rabbit_consumer(driver, crm_device_id),
        name=f"rabbitmq_{crm_device_id}",
    )
    logging.info("[MAIN] scheduled WS task for %s", crm_device_id)
    logging.info("[MAIN] WSClient comes from %s", inspect.getmodule(WSClient).__file__)
    await asyncio.sleep(0)

    task = None
    temp_alive = True
    device_status_path = DEVICE_STATUS_PATH(device_id)
    await main_lib.reset_active()

    while True:
        try:
            while True:
                still_alive = await check_driver(driver)
                if not temp_alive and still_alive:
                    log_message(f"[{DEVICE_LIST_NAME[device_id]}] ✅ Kết nối thiết bị đã được khôi phục.", logging.INFO)
                    await pymongo_management.update_device_status(device_id, True)
                temp_alive = still_alive

                if not still_alive:
                    if task is not None and not task.done():
                        task.cancel()
                        log_message(f"[{DEVICE_LIST_NAME[device_id]}] ❌ Mất kết nối thiết bị, hủy task đang chạy.", logging.WARNING)
                        await pymongo_management.update_device_status(device_id, False)
                    await asyncio.sleep(5.0)
                    driver = await asyncio.to_thread(u2.connect_usb, device_id)
                    continue

                is_paused = False
                try:
                    with open(device_status_path, 'r', encoding='utf-8') as f:
                        device_status = json.load(f)
                    if device_status.get('active', False):
                        is_paused = True
                    _STATUS_FILE_CHECK.discard(device_id)
                except FileNotFoundError:
                    if not device_id in _STATUS_FILE_CHECK:
                        log_message(f"[{DEVICE_LIST_NAME[device_id]}] ✅ Không tìm thấy file status, tiếp tục chạy.", logging.WARNING)
                        _STATUS_FILE_CHECK.add(device_id)
                except Exception as e:
                    log_message(f"[{DEVICE_LIST_NAME[device_id]}] ❌ Lỗi: {e}, tiếp tục chạy.", logging.WARNING)

                if is_paused and task is not None:
                    if not task.done():
                        task.cancel()
                        log_message(f"[{DEVICE_LIST_NAME[device_id]}] ⏸️ Phát hiện tạm dừng từ file status, hủy task đang chạy.", logging.WARNING)
                    await asyncio.sleep(2)
                    continue
                else:
                    break

            if task is None or task.done():
                task = asyncio.create_task(device_once(device_id))
            await asyncio.sleep(2.0)

        except RestartThisDevice:
            log_message(f"[{DEVICE_LIST_NAME[device_id]}] ↻ Watchdog yêu cầu RESTART — khởi động lại quy trình cho máy này.", logging.WARNING)
            restart_event[device_id] = False
            await asyncio.sleep(2.0)
            continue
        except Exception as e:
            if await check_driver(driver):
                log_message(f"[{DEVICE_LIST_NAME[device_id]}] Lỗi không mong muốn: {e}. Sẽ thử chạy lại sau.", logging.ERROR)
            await asyncio.sleep(5.0)
            continue

async def run_all_devices():
    """
    Theo dõi thay đổi thiết bị ADB — thêm/bớt task tương ứng.
    Cập nhật thiết bị tự động
    """
    global DEVICE_LIST, active_tasks

    while True:
        new_list = scan_connected_devices()
        added = [d for d in new_list if d not in DEVICE_LIST]
        removed = [d for d in DEVICE_LIST if d not in new_list]

        for device_id in added:
            log_message(f"📱 Thiết bị [{device_id}] đã được kết nối và sẵn sàng")
            DEVICE_LIST.append(device_id)
            task = asyncio.create_task(device_supervisor(device_id))
            active_tasks[device_id] = task
            try:
                device_name = await pymongo_management.get_device_name_by_id(device_id)
                DEVICE_LIST_NAME[device_id] = f"Máy {device_name}"
                log_message(f"📱 Thiết bị [{device_id}] có tên là: Máy {device_name}")
            except Exception as e:
                log_message(f"⚠️ Lỗi khi lấy tên thiết bị [{device_id}]: {e}", logging.WARNING)

        for device_id in removed:
            log_message(f"❌ Thiết bị [{device_id}] đã bị ngắt kết nối")
            DEVICE_LIST.remove(device_id)
            DEVICE_LIST_NAME.pop(device_id, None)
            if device_id in active_tasks:
                active_tasks[device_id].cancel()
                del active_tasks[device_id]
            await pymongo_management.update_device_status(device_id, False)

        log_message(f"📋 Danh sách thiết bị hiện tại: {DEVICE_LIST_NAME}")
        await asyncio.sleep(10)

async def main():
    # ĐÃ BỎ:
    # start_private_api_ui()

    await asyncio.gather(
        run_all_devices(),
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("[!] Dừng bằng bàn phím (KeyboardInterrupt)")
        asyncio.run(pymongo_management.update_device_status(None, False))
    except Exception as e:
        log_message(f"Lỗi chạy chính: {e}", logging.ERROR)
        asyncio.run(pymongo_management.update_device_status(None, False))
