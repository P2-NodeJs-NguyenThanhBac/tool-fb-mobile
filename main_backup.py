from fb_task_patched import *
# from sending_message_and_adding_friend import DeviceHandler, NUM_PHONE
from Zalo_Phase import DeviceHandler
from PIL import Image,ImageChops, ImageStat
import uiautomator2 as u2
import asyncio
import logging
import time
import json
import hashlib
from typing import Optional, Callable
import sys
sys.stdout.reconfigure(encoding='utf-8')
import threading

from module import *
from util import *
import main_lib
import pymongo_management
from rabbitmq_device_worker_merged import start_device_rabbit_consumer
from urgent_queue import get_next_task_if_due

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
TERMUX_PKG = "com.termux"
TARGET_PACKAGES = {ZALO_PKG, FACEBOOK_PKG}
TERMUX_GRACE_SECONDS = 40.0
OTHER_APP_GRACE_SECONDS = 1.5

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

    try:
        log_message(f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] Kiểm tra bật 1.1.1.1 (một lần)", logging.INFO)

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

        _VPN_CHECKED.add(device_id)
        log_message(f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] One-time check 1.1.1.1 OK", logging.INFO)

    except Exception as e:
        log_message(f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] VPN one-time check lỗi: {e}", logging.WARNING)

async def global_urgent_scheduler():
    """
    Scheduler global:
      - Mỗi 60s kiểm tra urgent_queue.json
      - Nếu đã đủ 30' kể từ job trước (do urgent_queue.py kiểm soát),
        sẽ lấy 1 job ra
      - Quyết định job đó thuộc device nào, rồi đẩy vào queue + set event
        để fb_task.urgent_worker() xử lý như cũ.
    """
    while True:
        try:
            job = get_next_task_if_due()
            if job:
                # 1) Tìm device target
                device_ids = job.get("device_ids")
                target_device_id = None

                # Ensure device_ids là list
                if isinstance(device_ids, str):
                    device_ids = [device_ids]
                if isinstance(device_ids, list):
                    # Ưu tiên device đang online trong DEVICE_LIST
                    for did in DEVICE_LIST:
                        if did in device_ids:
                            target_device_id = did
                            break

                # Nếu chưa có device từ device_ids -> map theo user_id (CRM)
                if not target_device_id:
                    uid = str(job.get("user_id") or "").strip()
                    if uid:
                        target_device_id = USER_DEVICE_MAP.get(uid)

                if not target_device_id:
                    log_message(f"[SCHED] ❌ Không xác định được device cho job {job.get('id')}", logging.WARNING)
                else:
                    # 2) Bơm vào queue urgent của device đó
                    event, queue = get_urgent_objects(target_device_id)
                    await queue.put(job)
                    event.set()
                    log_message(
                        f"[SCHED] ➡️ Dispatch job id={job.get('id')} action={job.get('action')} "
                        f"tới device {DEVICE_LIST_NAME.get(target_device_id, target_device_id)}",
                        logging.INFO,
                    )
        except Exception as e:
            log_message(f"[SCHED] Lỗi trong global_urgent_scheduler: {e}", logging.ERROR)

        # Check mỗi 60s, việc delay 30' đã được urgent_queue.py xử lý
        await asyncio.sleep(10)
        
async def disable_auto_rotation(driver, device_id: str):
    """
    Thực sự tắt chế độ tự động xoay màn hình của hệ thống Android
    """
    
    def parse_shell_response(result):
        """Helper function để xử lý ShellResponse object từ UIAutomator2"""
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
        
        # Kiểm tra trạng thái hiện tại trước
        try:
            current_result = driver.shell("settings get system accelerometer_rotation")
            current_value = parse_shell_response(current_result)
            log_message(f"[{DEVICE_LIST_NAME[device_id]}] Trạng thái auto-rotation hiện tại: {current_value}", logging.INFO)
        except Exception as e:
            log_message(f"[{DEVICE_LIST_NAME[device_id]}] Không thể kiểm tra trạng thái hiện tại: {e}", logging.INFO)
            current_value = "unknown"
        
        # Chỉ tắt auto-rotation nếu đang bật
        try:
            if current_value != "0":
                await asyncio.to_thread(driver.shell, "settings put system accelerometer_rotation 0")
                await asyncio.sleep(0.5)
                log_message(f"[{DEVICE_LIST_NAME[device_id]}] Đã gửi lệnh tắt auto-rotation", logging.INFO)
            else:
                log_message(f"[{DEVICE_LIST_NAME[device_id]}] Auto-rotation đã tắt sẵn, bỏ qua", logging.INFO)
            
        except Exception as e:
            log_message(f"[{DEVICE_LIST_NAME[device_id]}] Lỗi tắt auto-rotation qua settings: {e}", logging.INFO)
        
        # Kiểm tra trạng thái sau khi tắt
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
async def mute_device_volume(driver, device_id: str):
    """
    Sử dụng ADB Keyevent để tắt âm lượng.
    """
    try:
        log_message(f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] 🔇 Đang tắt âm lượng...", logging.INFO)

        # Keycode 164 = KEYCODE_VOLUME_MUTE
        await asyncio.to_thread(driver.shell, "input keyevent 164")

        log_message(f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] ✅ Đã tắt âm lượng hoàn toàn.", logging.INFO)

    except Exception as e:
        log_message(f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] ⚠️ Lỗi khi tắt âm lượng: {e}", logging.WARNING)
# class InactivityWatchdog:
#     def __init__(
#         self,
#         driver,
#         device_id: str,
#         idle_seconds: int = 60,
#         on_resume: Optional[Callable[[str], "asyncio.Future"]] = None,
#         on_restart: Optional[Callable[[], "asyncio.Future"]] = None,
#         phase_provider: Optional[Callable[[], str]] = None,
#         check_frozen: bool = True, # Kiểm tra trạng thái màn hình bị đứng
#         frozen_threshold: int = 1, # Số lần kiểm tra liên tiếp màn hình đứng mới coi là bị đứng
#         ui_check_interval: int = 40 # Thời gian kiểm tra UI (giây)
#     ):
#         self.driver = driver
#         self.device_id = device_id
#         self.idle_seconds = int(idle_seconds)
#         self.on_resume = on_resume
#         self.on_restart = on_restart
#         self.phase_provider = phase_provider or (lambda: "facebook")
#         self._task: Optional[asyncio.Task] = None
#         self._stop = asyncio.Event()

#         self._last_seen_target = time.time()
#         self._home_since: Optional[float] = None
#         self.check_frozen = check_frozen          # Có kiểm tra màn hình đứng không
#         self.frozen_threshold = frozen_threshold # số lần đứng liên tiếp để coi là bị đứng
#         self._last_ui_hash = None # Lưu hash của UI gần nhất
#         self._same_ui_count = 0 # Đếm số lần UI không đổi liên tiếp
#         self.ui_check_interval = ui_check_interval # Khoảng thời gian kiểm tra UI
#         self._last_ui_check = 0 # Thời gian lần cuối kiểm tra UI

#     def log(self, msg: str, level=logging.INFO):
#         try:
#             log_message(f"[{self.device_id}][Watchdog] {msg}", level)
#         except Exception:
#             print(f"[{self.device_id}][Watchdog] {msg}")

#     async def start(self):
#         if self._task is None:
#             self._stop.clear()
#             self._task = asyncio.create_task(self._run())

#     async def stop(self):
#         if self._task:
#             self._stop.set()
#             try:
#                 await self._task
#             except Exception:
#                 pass
#             self._task = None

#     async def _run(self):
#         while not self._stop.is_set():
#             # Đọc app hiện tại
#             try:
#                 info = await asyncio.to_thread(self.driver.app_current)
#                 pkg = (info or {}).get("package", "") or ""
#             except Exception:
#                 pkg = ""

#             now = time.time()

#             # Cập nhật lần cuối thấy target
#             if _is_target_pkg(pkg):
#                 self._last_seen_target = now
#                 self._home_since = None  # reset
#                 # 🔍 Kiểm tra "màn hình bị đứng"
#                 if self.check_frozen and now - self._last_ui_check >= self.ui_check_interval:
#                     self._last_ui_check = now  # ✅ reset mốc thời gian check UI
#                     try:
#                         xml = await asyncio.to_thread(self.driver.dump_hierarchy)
#                         ui_hash = hashlib.sha256(xml.encode('utf-8')).hexdigest()

#                         if self._last_ui_hash == ui_hash:
#                             self._same_ui_count += 1
#                         else:
#                             self._same_ui_count = 0
#                             self._last_ui_hash = ui_hash

#                         if self._same_ui_count >= self.frozen_threshold:
#                             self.log(f"[{DEVICE_LIST_NAME[self.device_id]}] ⚠️ UI không thay đổi {self.frozen_threshold} lần → đặt trạng thái đứng màn hình ", logging.WARNING)
#                             screen_standing_event[self.device_id] = True
#                             break
#                     except Exception as e:
#                         self.log(f"Lỗi kiểm tra đứng màn hình: {e}", logging.DEBUG)
#             # elif _is_home_pkg(pkg):
#             else:
#                 # if self._home_since is None:
#                 #     self._home_since = now
#                 phase = self.phase_provider()
#                 self.on_resume(phase)
#             # else:
#             #     # ở app khác, không reset _home_since
#             #     pass

#             # # Nếu ở HOME quá 12s -> resume app theo phase
#             # if self._home_since and now - self._home_since >= 1:
#             #     self._home_since = None  # tránh spam
#             #     if self.on_resume:
#             #         phase = self.phase_provider()
#             #         self.log(f"[{DEVICE_LIST_NAME[self.device_id]}]HOME >=1s → mở lại app theo phase='{phase}'")
#             #         try:
#             #             self.on_resume(phase)
#             #             asyncio.create_task(disable_auto_rotation(self.driver, self.device_id))
#             #         except Exception as e:
#             #             self.log(f"[{DEVICE_LIST_NAME[self.device_id]}]Lỗi on_resume: {e}", logging.WARNING)


#             await asyncio.sleep(1.0)

class InactivityWatchdog(threading.Thread):
    def __init__(
        self,
        driver,
        device_id: str,
        idle_seconds: int = 60,
        on_resume: Optional[Callable[[str], None]] = None,
        phase_provider: Optional[Callable[[], str]] = None,
        check_frozen: bool = True,
        frozen_threshold: int = 1,
        ui_check_interval: int = 80
    ):
        super().__init__()
        self.daemon = True

        self.driver = driver
        self.device_id = device_id
        self.idle_seconds = int(idle_seconds)
        self.on_resume = on_resume
        self.phase_provider = phase_provider or (lambda: "facebook")

        self._stop_event = threading.Event()

        # Theo dõi thời gian khi app đi ra ngoài Facebook/Zalo
        self._home_since: Optional[float] = None
        self._last_away_pkg: Optional[str] = None

        # Frozen check
        self.check_frozen = check_frozen
        self.frozen_threshold = frozen_threshold
        self._last_img = None
        self._same_ui_count = 0
        self.ui_check_interval = ui_check_interval
        self._last_ui_check = 0

    def stop(self):
        self._stop_event.set()

    def _device_name(self):
        return DEVICE_LIST_NAME.get(self.device_id, self.device_id)

    def _reset_away_timer(self):
        self._home_since = None
        self._last_away_pkg = None

    def _reset_frozen_state(self):
        self._same_ui_count = 0
        self._last_img = None

    def _resume_current_phase(self, reason: str = ""):
        if not self.on_resume:
            return

        try:
            phase = self.phase_provider()
            log_message(
                f"[{self._device_name()}] Watchdog resume app theo phase='{phase}'"
                + (f" | lý do: {reason}" if reason else ""),
                logging.WARNING
            )
            self.on_resume(phase)
            try:
                                res = self.driver.shell("settings get system accelerometer_rotation")
                                cur = getattr(res, "output", None) or getattr(res, "text", None) or getattr(res, "stdout", None) or str(res)
                                if str(cur).strip() != "0":
                                    self.driver.shell("settings put system accelerometer_rotation 0")
            except Exception:
                pass
        except Exception as e:
            log_message(
                f"[{self._device_name()}] Lỗi resume watchdog: {e}",
                logging.WARNING
            )

    def _get_screen_image(self):
        """
        Chụp màn hình, crop bớt phần trên, resize nhỏ, grayscale để so sánh nhanh.
        """
        try:
            img = self.driver.screenshot()
            w, h = img.size

            # Cắt 10% phía trên để giảm nhiễu status bar / đồng hồ / icon mạng
            top_crop = int(h * 0.1)
            img = img.crop((0, top_crop, w, h))

            # Resize nhỏ để so sánh nhanh
            img = img.resize((100, 100), Image.Resampling.BOX)

            # Chuyển grayscale
            return img.convert("L")
        except Exception:
            return None

    def _is_image_frozen(self, img1, img2, tolerance_percent=8.0):
        """
        True nếu 2 ảnh gần như giống nhau.
        tolerance_percent = % khác biệt cho phép.
        """
        if img1 is None or img2 is None:
            return False

        try:
            diff = ImageChops.difference(img1, img2)
            hist = diff.histogram()

            # Bỏ qua nhiễu rất nhỏ
            diff_pixels = sum(hist[10:])
            total_pixels = 100 * 100
            diff_percent = (diff_pixels / total_pixels) * 100

            return diff_percent < tolerance_percent
        except Exception:
            return False

    def _handle_target_packages(self, now: float, pkg: str):
        """
        Khi đang ở Facebook/Zalo:
        - reset timer "đi ra ngoài"
        - check frozen theo chu kỳ
        """
        self._reset_away_timer()

        if not self.check_frozen:
            return

        if now - self._last_ui_check < self.ui_check_interval:
            return

        self._last_ui_check = now
        current_img = self._get_screen_image()

        if self._last_img is not None and current_img is not None:
            is_frozen = self._is_image_frozen(
                self._last_img,
                current_img,
                tolerance_percent=8.0
            )

            if is_frozen:
                self._same_ui_count += 1
            else:
                self._same_ui_count = 0

        # luôn cập nhật ảnh mới nhất
        self._last_img = current_img

        if self._same_ui_count >= self.frozen_threshold:
            log_message(
                f"[{self._device_name()}] ⚠️ Màn hình đứng yên (Facebook/Zalo) -> Set cờ reset",
                logging.WARNING
            )
            screen_standing_event[self.device_id] = True
            self._same_ui_count = 0
            self._last_img = None

    def _handle_termux(self, now: float):
        """
        Cho Termux thời gian grace dài hơn để chạy lệnh.
        """
        # Sang app khác rồi thì không nên tiếp tục so frozen của Facebook/Zalo
        self._reset_frozen_state()

        if self._last_away_pkg != TERMUX_PKG:
            self._home_since = now
            self._last_away_pkg = TERMUX_PKG
            log_message(
                f"[{self._device_name()}] Watchdog phát hiện đang ở Termux -> bắt đầu grace {TERMUX_GRACE_SECONDS}s",
                logging.INFO
            )
            return

        if self._home_since is not None and (now - self._home_since >= TERMUX_GRACE_SECONDS):
            self._reset_away_timer()
            self._resume_current_phase(reason=f"ở Termux quá {TERMUX_GRACE_SECONDS}s")

    def _handle_other_packages(self, now: float, pkg: str):
        """
        Các app khác ngoài Facebook/Zalo/Termux:
        resume nhanh hơn.
        """
        self._reset_frozen_state()

        if self._last_away_pkg != pkg:
            self._home_since = now
            self._last_away_pkg = pkg
            return

        if self._home_since is not None and (now - self._home_since >= OTHER_APP_GRACE_SECONDS):
            self._reset_away_timer()
            self._resume_current_phase(reason=f"ra app khác '{pkg}' quá {OTHER_APP_GRACE_SECONDS}s")

    def run(self):
        log_message(
            f"[{self._device_name()}] Watchdog Thread bắt đầu chạy song song",
            logging.INFO
        )

        while not self._stop_event.is_set():
            try:
                try:
                    info = self.driver.app_current()
                    pkg = (info or {}).get("package", "") or ""
                except Exception:
                    pkg = ""

                now = time.time()

                # 1) Đang ở app chính
                if pkg in TARGET_PACKAGES:
                    self._handle_target_packages(now, pkg)

                # 2) Đang ở Termux
                elif pkg == TERMUX_PKG:
                    self._handle_termux(now)

                # 3) Đang ở app khác
                else:
                    self._handle_other_packages(now, pkg)

            except Exception as e:
                log_message(
                    f"[{self._device_name()}] Watchdog error: {e}",
                    logging.WARNING
                )

            time.sleep(0.5)

# ======================= LUỒNG THIẾT BỊ =======================
class RestartThisDevice(Exception):
    pass

async def device_once(device_id: str):
    """
    Chạy một vòng đầy đủ cho MỘT thiết bị:
      - Kết nối
      - Tắt auto-rotation để tránh xoay màn hình
      - Bật VPN 1.1.1.1 nếu cần (1 lần)
      - Watchdog chạy nền
      - Pha 'zalo' (một vòng) -> Pha 'facebook'
    """
    # # # ===== PHA Cào zalo =====
    # while not check_zalo_ran_today(device_id):
    #     log_message(f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] đang cào zalo", logging.INFO)
    #     await asyncio.sleep(60)
    restart_event[device_id] = False
    screen_standing_event[device_id] = False
    # Kết nối thiết bị
    driver = await asyncio.to_thread(u2.connect_usb, device_id)
    handler = DeviceHandler(driver, device_id)
    await handler.connect()

    # Bật 1.1.1.1 (một lần)
    await ensure_1111_vpn_on_once(driver, device_id)

    # Kiểm tra cài đặt Termux và termux-api
    result = await main_lib.check_termux_api_installed(driver)
    if not result:
        return
    # Tắt chế độ tự động xoay màn hình
    await disable_auto_rotation(driver, device_id)

    #Tắt âm lượng 
    await mute_device_volume(driver, device_id)
    # Trạng thái pha hiện tại để watchdog biết cần resume app nào khi về HOME
    current_phase = {"value": "zalo"}

    # Cờ yêu cầu restart từ watchdog
    restart_event_flag = asyncio.Event()

    def _on_resume(phase: str):
        # Đưa việc mở app sang luồng riêng để không chặn Watchdog
        target = ZALO_PKG if phase == "zalo" else FACEBOOK_PKG
        # Dùng create_task + to_thread để lệnh này chạy ngầm hoàn toàn
        driver.app_start( target)

    async def _on_restart():
        # Dọn app để về trạng thái sạch rồi bật cờ restart
        try:
            await asyncio.to_thread(driver.app_stop, ZALO_PKG) # dừng ứng dụng, trạng thái sẽ là không hoạt động -> android cho về home page
        except Exception:
            pass
        try:
            await asyncio.to_thread(driver.app_stop, FACEBOOK_PKG)
        except Exception:
            pass
        restart_event_flag.set()




    # Watchdog
    watchdog = InactivityWatchdog(
        driver=driver,
        device_id=device_id,
        idle_seconds=60,
        on_resume=_on_resume,
        phase_provider=lambda: current_phase["value"],
    )
    # await watchdog.start()
    watchdog.start()
    await pymongo_management.update_device_status(device_id, True)  # Cập nhật thiết bị thành online
    try:
        # ===== PHA FACEBOOK =====
        current_phase["value"] = "facebook"
        # Chạy flow Facebook như thường lệ
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

        # ===== PHA ZALO =====
        await asyncio.to_thread(driver.app_stop, FACEBOOK_PKG)
        current_phase["value"] = "zalo"
        logging.info("[%s] BẮT ĐẦU pha Zalo", DEVICE_LIST_NAME.get(device_id, device_id))

        await asyncio.to_thread(driver.app_start, ZALO_PKG)
        await asyncio.sleep(2.0)

        logging.info("[%s] GỌI handler.run(Zalo)", DEVICE_LIST_NAME.get(device_id, device_id))
        await asyncio.to_thread(handler.run, screen_standing_event)  # khi code zalo la dong bo 
        # await handler.run(screen_standing_event)                   # khi code zalo la bat dong bo
        logging.info("[%s] KẾT THÚC pha Zalo", DEVICE_LIST_NAME.get(device_id, device_id))

        if restart_event_flag.is_set():
            raise RestartThisDevice("RESTART_THIS_DEVICE (sau pha Zalo)")
        


    finally:
        watchdog.stop()
        try:
            await asyncio.to_thread(watchdog.join, 2.0)
        except Exception:
            pass

async def check_driver(driver):
    try:
        _ = driver.info
        return True
    except Exception:
        return False
    
async def device_supervisor(device_id: str):
    """
    Giám sát riêng từng thiết bị:
      - Giữ nguyên flow chạy của main.py.
      - Nhận urgent task qua RabbitMQ, nhưng KHÔNG execute trong consumer.
      - Consumer chỉ bơm job vào queue RAM của đúng device; fb_task_patched sẽ xử lý.
    """
    driver = None
    rabbit_task = None

    while True:
        try:
            driver = await asyncio.to_thread(u2.connect_usb, device_id)
            break
        except Exception:
            await asyncio.sleep(2.0)

    crm_device_id = driver.serial

    # Lấy danh sách user_id CRM cho thiết bị này (để log/đối chiếu vận hành)
    user_ids = DEVICE_USERS.get(crm_device_id, [])
    if not user_ids:
        log_message(
            f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] ❌ Không tìm thấy user_id nào tương ứng trong JSON — dùng default rỗng",
            logging.ERROR,
        )

    # Urgent queue dùng chung với fb_task_patched (key theo serial thật của máy)
    event, queue = get_urgent_objects(crm_device_id)
    _ = (event, queue)  # giữ khởi tạo sẵn queue/event cho device

    rabbit_task = asyncio.create_task(
        start_device_rabbit_consumer(machine_code=crm_device_id),
        name=f"rabbitmq_{crm_device_id}",
    )
    logging.info("[MAIN] scheduled RabbitMQ consumer for %s", crm_device_id)
    await asyncio.sleep(0)

    task = None
    temp_alive = True
    device_status_path = DEVICE_STATUS_PATH(device_id)
    await main_lib.reset_active()  # Đặt lại tất cả device về inactive khi khởi động

    try:
        while True:
            try:
                # Nếu consumer RabbitMQ bị chết ngoài ý muốn thì dựng lại
                if rabbit_task is None or rabbit_task.done():
                    exc = None
                    try:
                        exc = rabbit_task.exception() if rabbit_task else None
                    except Exception:
                        exc = None
                    log_message(
                        f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] ♻️ RabbitMQ consumer đã dừng{' do lỗi: ' + str(exc) if exc else ''} -> khởi động lại",
                        logging.WARNING,
                    )
                    rabbit_task = asyncio.create_task(
                        start_device_rabbit_consumer(machine_code=crm_device_id),
                        name=f"rabbitmq_{crm_device_id}",
                    )

                # Vòng lặp chờ, liên tục kiểm tra file status trước khi làm bất cứ điều gì
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
                        if device_id not in _STATUS_FILE_CHECK:
                            log_message(f"[{DEVICE_LIST_NAME[device_id]}] ✅ Không tìm thấy file status, tiếp tục chạy.", logging.WARNING)
                            _STATUS_FILE_CHECK.add(device_id)
                    except Exception as e:
                        log_message(f"[{DEVICE_LIST_NAME[device_id]}] ❌ Lỗi: {e}, tiếp tục chạy.", logging.WARNING)

                    if is_paused:
                        if task is not None and not task.done():
                            task.cancel()
                            log_message(
                                f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] ⏸️ Phát hiện tạm dừng từ file status, hủy task đang chạy.",
                                logging.WARNING
                            )
                            try:
                                await task
                            except Exception:
                                pass

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
            except asyncio.CancelledError:
                raise
            except Exception as e:
                if await check_driver(driver):
                    log_message(f"[{DEVICE_LIST_NAME[device_id]}] Lỗi không mong muốn: {e}. Sẽ thử chạy lại sau.", logging.ERROR)
                await asyncio.sleep(5.0)
                continue
    finally:
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except Exception:
                pass

        if rabbit_task is not None and not rabbit_task.done():
            rabbit_task.cancel()
            try:
                await rabbit_task
            except Exception:
                pass

# async def run_all_devices():
#     """Chạy tất cả device với Task Manager và WebSocket"""
#     # Khởi động supervisor cho mỗi device
#     tasks = [asyncio.create_task(device_supervisor(did)) for did in DEVICE_LIST]
#     await asyncio.gather(*tasks)

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

        # Xử lý thiết bị mới
        for device_id in added:
            log_message(f"📱 Thiết bị [{device_id}] đã được kết nối và sẵn sàng")
            DEVICE_LIST.append(device_id)
            task = asyncio.create_task(device_supervisor(device_id))
            active_tasks[device_id] = task
            try:
                # Thêm timeout 5 giây. Nếu quá 5s không lấy được tên thì bỏ qua.
                device_name = await asyncio.wait_for(pymongo_management.get_device_name_by_id(device_id), timeout=5.0)
                DEVICE_LIST_NAME[device_id] = f"Máy {device_name}"
                log_message(f"📱 Thiết bị [{device_id}] có tên là: Máy {device_name}")
            except asyncio.TimeoutError:
                log_message(f"⚠️ Quá thời gian lấy tên thiết bị [{device_id}] -> Dùng ID mặc định", logging.WARNING)
                DEVICE_LIST_NAME[device_id] = f"Máy {device_id}"

        # Xử lý thiết bị bị ngắt kết nối
        for device_id in removed:
            log_message(f"❌ Thiết bị [{device_id}] đã bị ngắt kết nối")
            DEVICE_LIST.remove(device_id)
            DEVICE_LIST_NAME.pop(device_id, None)
            if device_id in active_tasks:
                active_tasks[device_id].cancel()
                del active_tasks[device_id]
            await pymongo_management.update_device_status(device_id, False)

        # In log tổng quan để dễ theo dõi
        # if added or removed:
        log_message(f"📋 Danh sách thiết bị hiện tại: {DEVICE_LIST_NAME}")

        await asyncio.sleep(10)  # Quét lại mỗi 10 giây




async def main():
    # Chạy song song:
    #   - run_all_devices(): quản lý device + supervisor
    #   - global_urgent_scheduler(): bơm job từ file vào từng device
    await asyncio.gather(
        run_all_devices(),
        global_urgent_scheduler(),
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
