import asyncio
import re
import cv2
import subprocess
import easyocr
import aiohttp
import os
import time
import numpy as np
from urllib.parse import urlparse, unquote
from util import log_message
import logging
from util import go_to_home_page
from util.const_values import *
import xml.etree.ElementTree as ET
from module.fb_group_post_status_by_text import detect_group_post_status_by_text
# from main_merged import disable_auto_rotation

CLIPBOARD_DIR = "clipboard"
_OCR_INSTANCE = None

# def _get_ocr():
#     global _OCR_INSTANCE
#     if _OCR_INSTANCE is None:
#         _OCR_INSTANCE = LatestPostStatusOCR(languages=("vi","en"), gpu=False)
#     return _OCR_INSTANCE

def _private_api_base() -> str:
    # Ưu tiên env giống private_api_client_patched.py
    # private_api_client đang dùng env PRIVATE_API_BASE :contentReference[oaicite:3]{index=3}
    return (os.environ.get("PRIVATE_API_BASE", PRIVATE_API_BASE_DEFAULT) or PRIVATE_API_BASE_DEFAULT).rstrip("/")

def _parse_bounds_str(bounds: str):
    # "[0,294][1080,334]" -> (l,t,r,b)
    m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds or "")
    if not m:
        return None
    l, t, r, b = map(int, m.groups())
    return l, t, r, b


def _iter_nodes_from_xml(xml: str):
    """
    Yield dict node info:
      {"text","desc","cls","rid","clickable","bounds":(l,t,r,b)}
    """
    root = ET.fromstring(xml)
    for node in root.iter():
        b = _parse_bounds_str(node.attrib.get("bounds", ""))
        if not b:
            continue
        yield {
            "text": (node.attrib.get("text") or "").strip(),
            "desc": (node.attrib.get("content-desc") or "").strip(),
            "cls": (node.attrib.get("class") or "").strip(),
            "rid": (node.attrib.get("resource-id") or "").strip(),
            "clickable": (node.attrib.get("clickable") or "").strip().lower() == "true",
            "bounds": b,
        }

async def _click_you_tab_from_xml(driver):
    """
    Tìm và click tab 'Bạn' bằng dump_hierarchy.
    Ưu tiên node có text/desc = 'Bạn'.
    """
    try:
        xml = driver.dump_hierarchy()
    except Exception as e:
        log_message(
            f"{[driver.serial]} - dump_hierarchy lỗi khi tìm tab 'Bạn': {e}",
            logging.WARNING,
        )
        return False

    best = None  # (cx, cy, meta)

    for meta in _iter_nodes_from_xml(xml):
        txt = (meta.get("text") or "").strip().lower()
        desc = (meta.get("desc") or "").strip().lower()
        cls = (meta.get("cls") or "").strip()

        if txt != "bạn" and desc != "bạn":
            continue

        # ưu tiên button
        if cls not in ("android.widget.Button", "android.view.ViewGroup"):
            continue

        l, t, r, b = meta["bounds"]
        cx = (l + r) // 2
        cy = (t + b) // 2

        # tab strip thường nằm nửa trên màn hình
        w, h = driver.window_size()
        if cy < int(h * 0.35) or cy > int(h * 0.70):
            continue

        best = (cx, cy, meta)
        break

    if not best:
        log_message(
            f"{[driver.serial]} - Không tìm thấy tab 'Bạn' bằng XML",
            logging.INFO,
        )
        return False

    cx, cy, meta = best
    log_message(
        f"{[driver.serial]} - Click tab 'Bạn' bằng XML tại ({cx}, {cy}), meta={meta}",
        logging.INFO,
    )

    try:
        driver.click(cx, cy)
        await asyncio.sleep(1.2)
        return True
    except Exception as e:
        log_message(
            f"{[driver.serial]} - Click tab 'Bạn' bằng XML lỗi: {e}",
            logging.WARNING,
        )
        return False

# Truy cập 1 trang facebook qua link
def redirect_to(driver, link):
    link = str(link or "").strip()
    if not link:
        return None

    safe_link = link.replace("'", "%27")
    is_facebook_link = "facebook.com" in safe_link.lower() or safe_link.lower().startswith("fb://")

    if is_facebook_link:
        primary_cmd = (
            "am start -a android.intent.action.VIEW "
            f"-d '{safe_link}' "
            "-n com.facebook.katana/com.facebook.deeplinking.aliasactivity.FacebookLoggedInUsersDeeplinkAliasActivity"
        )
        fallback_cmd = f"am start -a android.intent.action.VIEW -d '{safe_link}' -p com.facebook.katana"
        try:
            result = driver.shell(primary_cmd)
            result_text = str(getattr(result, "output", result) or "")
            lowered = result_text.lower()
            if "error:" not in lowered and "unable to resolve" not in lowered and "does not exist" not in lowered:
                return result
            log_message(
                f"{getattr(driver, 'serial', 'unknown')} - Facebook logged-in deeplink failed, fallback package intent: {result_text}",
                logging.WARNING,
            )
        except Exception as e:
            log_message(
                f"{getattr(driver, 'serial', 'unknown')} - Facebook logged-in deeplink error, fallback package intent: {e}",
                logging.WARNING,
            )
        return driver.shell(fallback_cmd)

    return driver.shell(f"am start -a android.intent.action.VIEW -d '{safe_link}'")

# Trở về trang chủ của facebook
async def back_to_facebook(driver):
    return await go_to_home_page(driver)
        
# Ấn vào ảnh mẫu trên màn hình
async def click_template(driver, template, threshold = 0.8, scale_start = 50, scale_end = 150, scale_step = 10):
    await asyncio.sleep(1)
    screen = await asyncio.to_thread(driver.screenshot, format='opencv')
    template = await asyncio.to_thread(cv2.imread, f"Templates/{template}.png")

    for scale in range(scale_start, scale_end, scale_step):
        # Resize template
        resized = cv2.resize(template, None, fx=scale/100, fy=scale/100, interpolation=cv2.INTER_AREA)
        if resized.shape[0] > screen.shape[0] or resized.shape[1] > screen.shape[1]:
            break

        # Template matching
        result = cv2.matchTemplate(screen, resized, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)

        # Kiểm tra độ khớp
        if max_val > threshold:
            top_left = max_loc
            h, w, _ = resized.shape
            await asyncio.to_thread(driver.click, top_left[0] + w // 2, top_left[1] + h // 2)
            return True
    return False

# Trích xuất số từ chuỗi
def parse_number(s):
    # Tìm tất cả chuỗi gồm các chữ số liền nhau
    numbers = re.findall(r'\d+', s)
    # Chuyển thành mảng số nguyên
    return [int(num) for num in numbers]

# Tải file từ server api
def _looks_like_url(value: str | None) -> bool:
    if not value:
        return False
    lowered = str(value).strip().lower()
    return lowered.startswith("http://") or lowered.startswith("https://")


def _basename_from_media_ref(media_ref: str | None) -> str:
    raw = str(media_ref or "").strip()
    if _looks_like_url(raw):
        parsed = urlparse(raw)
        candidate = os.path.basename(unquote(parsed.path))
        if candidate:
            return candidate
    return os.path.basename(raw or "file")


def make_unique_filename(original_name: str, *, ts_ms: int | None = None) -> str:
    base = _basename_from_media_ref(original_name)
    stem, ext = os.path.splitext(base)
    # sanitize nhẹ để tránh ký tự lạ
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("_") or "file"

    ts_ms = ts_ms or int(time.time() * 1000)
    # thêm 1 chút “ns tail” để tránh trùng nếu 2 file đến cùng ms
    tail = int(time.time_ns() % 1000)
    # cắt ngắn stem nếu cần (tránh tên quá dài)
    if len(stem) > 60:
        stem = stem[:60]
    return f"{stem}_{ts_ms}_{tail}{ext}"

async def _try_click_ui_selector(driver, sel: dict, label: str) -> bool:
    try:
        el = driver(**sel)
        exists = el.exists
        log_message(
            f"{[driver.serial]} - check {label} selector={sel} exists={exists}",
            logging.INFO,
        )
        if not exists:
            return False

        info = getattr(el, "info", {}) or {}
        log_message(
            f"{[driver.serial]} - MATCH {label} selector={sel} "
            f"class={info.get('className')} text={info.get('text')} "
            f"desc={info.get('contentDescription')} clickable={info.get('clickable')} "
            f"enabled={info.get('enabled')} bounds={info.get('bounds')}",
            logging.INFO,
        )

        try:
            if info.get("clickable"):
                await asyncio.to_thread(el.click)
            else:
                b = info.get("bounds") or {}
                if not b:
                    return False
                x = (int(b["left"]) + int(b["right"])) // 2
                y = (int(b["top"]) + int(b["bottom"])) // 2
                await asyncio.to_thread(driver.click, x, y)
        except Exception:
            b = info.get("bounds") or {}
            if not b:
                return False
            x = (int(b["left"]) + int(b["right"])) // 2
            y = (int(b["top"]) + int(b["bottom"])) // 2
            await asyncio.to_thread(driver.click, x, y)

        await asyncio.sleep(0.8)
        return True
    except Exception as e:
        log_message(
            f"{[driver.serial]} - Lỗi click {label} selector={sel}: {e}",
            logging.WARNING,
        )
        return False

async def _try_click_xpath(driver, xp: str, label: str) -> bool:
    try:
        el = driver.xpath(xp)
        exists = el.exists
        log_message(
            f"{[driver.serial]} - check {label} xpath={xp} exists={exists}",
            logging.INFO,
        )
        if not exists:
            return False

        await asyncio.to_thread(el.click)
        await asyncio.sleep(0.7)
        return True
    except Exception as e:
        log_message(
            f"{[driver.serial]} - Lỗi click {label} xpath={xp}: {e}",
            logging.WARNING,
        )
        return False

async def click_copy_link_from_sheet(driver) -> bool:
    """
    Click mục 'Sao chép liên kết' trong bottom sheet/menu bài viết.
    KHÔNG dùng tọa độ.
    """

    # ===== Ưu tiên 1: outer button theo content-desc =====
    ui_candidates = [
        ("copy_link_vi_desc_contains", {"descriptionContains": "Sao chép liên kết"}),
        ("copy_link_vi_desc_exact", {"description": "Sao chép liên kết"}),
        ("copy_link_vi_desc_exact_space", {"description": "Sao chép liên kết "}),
        ("copy_link_en_desc_contains", {"descriptionContains": "Copy link"}),
        ("copy_link_en_desc_exact", {"description": "Copy link"}),
    ]

    for label, sel in ui_candidates:
        ok = await _try_click_ui_selector(driver, sel, label)
        if ok:
            return True

    # ===== Ưu tiên 2: xpath đúng outer button =====
    xpath_candidates = [
        (
            "xpath_button_desc_vi",
            '//*[@class="android.widget.Button" and contains(@content-desc,"Sao chép liên kết")]'
        ),
        (
            "xpath_button_desc_en",
            '//*[@class="android.widget.Button" and contains(@content-desc,"Copy link")]'
        ),

        # fallback nếu text nằm trong chính button
        (
            "xpath_button_text_vi",
            '//*[@class="android.widget.Button" and contains(@text,"Sao chép liên kết")]'
        ),
        (
            "xpath_button_text_en",
            '//*[@class="android.widget.Button" and contains(@text,"Copy link")]'
        ),

        # fallback: button có child chứa text/desc đó
        (
            "xpath_button_has_child_vi",
            '//*[@class="android.widget.Button"][.//*[contains(@text,"Sao chép liên kết") or contains(@content-desc,"Sao chép liên kết")]]'
        ),
        (
            "xpath_button_has_child_en",
            '//*[@class="android.widget.Button"][.//*[contains(@text,"Copy link") or contains(@content-desc,"Copy link")]]'
        ),
    ]

    for label, xp in xpath_candidates:
        ok = await _try_click_xpath(driver, xp, label)
        if ok:
            return True

    log_message(
        f"{[driver.serial]} - Không tìm thấy mục 'Sao chép liên kết' trong sheet/menu",
        logging.WARNING,
    )
    return False



# async def download_file_from_server(file_name: str, save_as: str | None = None):
#     url = "https://socket.hungha365.com:4000/uploads/" + file_name
#     save_as = save_as or file_name

#     # đảm bảo folder Files tồn tại
#     await asyncio.to_thread(os.makedirs, "Files", exist_ok=True)
#     out_path = os.path.join("Files", save_as)

#     try:
#         async with aiohttp.ClientSession() as session:
#             async with session.get(url) as response:
#                 if response.status == 200:
#                     content = await response.read()
#                     await asyncio.to_thread(_write_file, out_path, content)
#                     return save_as  # <- trả về tên file local đã lưu
#                 else:
#                     error_text = await response.text()
#                     raise RuntimeError(f"Download fail {response.status}: {error_text}")
#     except Exception as e:
#         raise RuntimeError(f"Download error: {e}")

PRIVATE_API_BASE_DEFAULT = "http://192.168.1.35:8000"

def _private_api_base() -> str:
    # Ưu tiên env giống private_api_client_patched.py
    # private_api_client đang dùng env PRIVATE_API_BASE :contentReference[oaicite:3]{index=3}
    return (os.environ.get("PRIVATE_API_BASE", PRIVATE_API_BASE_DEFAULT) or PRIVATE_API_BASE_DEFAULT).rstrip("/")

def _resolve_media_download_url(file_ref: str) -> str:
    ref = str(file_ref or "").strip()
    if not ref:
        raise RuntimeError("Empty media reference")
    if _looks_like_url(ref):
        return ref
    base = _private_api_base()
    if ref.startswith("/uploads/"):
        return f"{base}{ref}"
    if ref.startswith("uploads/"):
        return f"{base}/{ref}"
    return f"{base}/uploads/{ref}"


async def download_file_from_server(file_name: str, save_as: str | None = None):
    """
    Download media ref về local folder Files/.
    `file_name` có thể là:
    - tên file đã upload trên media server (vd: media-uuid.jpg)
    - URL đầy đủ tới file media (vd: https://media.example.com/uploads/media-uuid.jpg)
    """
    url = _resolve_media_download_url(file_name)
    save_as = save_as or _basename_from_media_ref(file_name)

    # đảm bảo folder Files tồn tại
    await asyncio.to_thread(os.makedirs, "Files", exist_ok=True)
    out_path = os.path.join("Files", save_as)

    try:
        timeout = aiohttp.ClientTimeout(total=60)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as response:
                if response.status == 200:
                    content = await response.read()
                    await asyncio.to_thread(_write_file, out_path, content)
                    return save_as
                else:
                    error_text = await response.text()
                    raise RuntimeError(f"Download fail {response.status} from {url}: {error_text[:200]}")
    except Exception as e:
        raise RuntimeError(f"Download error from {url}: {e}")

def _write_file(filepath, content):
    """Helper function for writing file synchronously"""
    with open(filepath, "wb") as f:
        f.write(content)

# Xóa file đã tải về
async def delete_local_file(file_name: str):
    path = os.path.join("Files", file_name)
    try:
        await asyncio.to_thread(os.remove, path)
        return "✅ Đã xóa file: " + file_name
    except FileNotFoundError:
        return "⚠️ File không tồn tại: " + file_name
    except Exception as e:
        return f"⚠️ Không thể xóa {file_name}: {e}"
    
# Gửi file đến thiết bị
async def push_file_to_device(device_id, file_name, remote_path="/sdcard/Download/"):
    unique_name = make_unique_filename(file_name)  # <- tên sẽ thành name_ts.ext
    adb = WINDOW_ADB_PATH if os.name == "nt" else LINUX_ADB_PATH

    try:
        # tải từ server nhưng lưu local bằng unique_name
        saved_local_name = await download_file_from_server(file_name, save_as=unique_name)
        local_path = os.path.join("Files", saved_local_name)
        remote_full = remote_path + saved_local_name

        await asyncio.to_thread(
            subprocess.run,
            [adb, "-s", device_id, "push", local_path, remote_full],
            check=True
        )

        # trigger MediaScanner để file hiện trong gallery picker
        await asyncio.to_thread(
            subprocess.run,
            [
                adb, "-s", device_id,
                "shell", "am", "broadcast",
                "-a", "android.intent.action.MEDIA_SCANNER_SCAN_FILE",
                "-d", f"file://{remote_full}"
            ],
            check=True
        )

        return saved_local_name  # <- trả về tên file đã push lên Android

    except subprocess.CalledProcessError:
        log_message(f"{device_id} - ⚠️ Không thể đẩy file: {remote_path + unique_name}", logging.WARNING)
        return None
    finally:
        # xóa đúng file unique local
        await delete_local_file(unique_name)

# Xóa file trên thiết bị
async def delete_file(device_id, file_name, remote_path="/sdcard/Download/"):
    try:
        if os.name == 'nt':
            await asyncio.to_thread(subprocess.run, [WINDOW_ADB_PATH, "-s", device_id, "shell", f"rm '{remote_path + file_name}'"], check=True)
        else:
            await asyncio.to_thread(subprocess.run, [LINUX_ADB_PATH, "-s", device_id, "shell", f"rm '{remote_path + file_name}'"], check=True)
        print(f"{device_id} - ✅ Đã xóa file: {remote_path + file_name}")
    except subprocess.CalledProcessError:
        print(f"{device_id} - ⚠️ File không tồn tại hoặc không thể xóa: {remote_path + file_name}")

async def refresh_group_page_after_post(driver, sleep_after: float = 3.0):
    """
    Vuốt từ trên xuống nhẹ ở màn group sau khi vừa đăng bài
    để UI/load state của group ổn định hơn trước khi bấm nút 3 chấm.
    """
    try:
        size = driver.window_size()
        if isinstance(size, dict):
            w = int(size.get("width", 0))
            h = int(size.get("height", 0))
        else:
            w, h = size

        x = int(w * 0.5)
        start_y = int(h * 0.22)
        end_y = int(h * 0.72)

        log_message(
            f"{[driver.serial]} - Refresh màn group sau đăng bài bằng vuốt xuống tại x={x}, y={start_y}->{end_y}",
            logging.INFO,
        )

        await asyncio.to_thread(driver.swipe, x, start_y, x, end_y, 0.5)
        await asyncio.sleep(sleep_after)
        return True
    except Exception as e:
        log_message(
            f"{[driver.serial]} - Lỗi refresh màn group sau đăng bài: {e}",
            logging.WARNING,
        )
        return False

async def wait_until_app_foreground(driver, expected_pkg: str, timeout: float = 12.0):
    end = time.time() + timeout
    last_info = None

    while time.time() < end:
        try:
            info = await asyncio.to_thread(driver.app_current)
            last_info = info
            pkg = (info or {}).get("package")
            log_message(
                f"{getattr(driver, 'serial', 'unknown')} - app_current={info}",
                logging.INFO,
            )
            if pkg == expected_pkg:
                return True
        except Exception as e:
            log_message(
                f"{getattr(driver, 'serial', 'unknown')} - app_current lỗi: {e}",
                logging.WARNING,
            )
        await asyncio.sleep(0.5)

    log_message(
        f"{getattr(driver, 'serial', 'unknown')} - Không thấy app foreground={expected_pkg}, last_info={last_info}",
        logging.WARNING,
    )
    return False


async def _click_if_exists(driver, sel: dict, label: str) -> bool:
    try:
        el = driver(**sel)
        if not el.exists:
            return False

        info = getattr(el, "info", {}) or {}
        log_message(
            f"{[driver.serial]} - Termux popup MATCH {label}: "
            f"text={info.get('text')} desc={info.get('contentDescription')} "
            f"bounds={info.get('bounds')}",
            logging.INFO,
        )

        try:
            await asyncio.to_thread(el.click)
        except Exception:
            b = info.get("bounds") or {}
            if not b:
                return False
            x = (int(b["left"]) + int(b["right"])) // 2
            y = (int(b["top"]) + int(b["bottom"])) // 2
            await asyncio.to_thread(driver.click, x, y)

        await asyncio.sleep(1.0)
        return True
    except Exception as e:
        log_message(
            f"{[driver.serial]} - Termux popup click lỗi {label}: {e}",
            logging.WARNING,
        )
        return False

async def open_member_tools_menu(driver) -> bool:
    """
    Mở menu từ nút 3 chấm góc trên phải của group:
    content-desc thường là 'Công cụ khác cho thành viên'
    """
    candidates = (
        {"description": "Công cụ khác cho thành viên"},
        {"descriptionContains": "Công cụ khác cho thành viên"},
        {"descriptionContains": "Công cụ khác"},
        {"descriptionContains": "thành viên"},
    )

    for sel in candidates:
        try:
            el = driver(**sel)
            exists = el.exists
            log_message(
                f"{[driver.serial]} - check member-tools selector={sel} exists={exists}",
                logging.INFO,
            )
            if not exists:
                continue

            info = getattr(el, "info", {}) or {}
            log_message(
                f"{[driver.serial]} - MATCH member-tools selector={sel} "
                f"text={info.get('text')} desc={info.get('contentDescription')} "
                f"clickable={info.get('clickable')} enabled={info.get('enabled')} "
                f"bounds={info.get('bounds')}",
                logging.INFO,
            )

            try:
                await asyncio.to_thread(el.click)
            except Exception:
                b = info.get("bounds") or {}
                if not b:
                    continue
                x = (int(b["left"]) + int(b["right"])) // 2
                y = (int(b["top"]) + int(b["bottom"])) // 2
                await asyncio.to_thread(driver.click, x, y)

            await asyncio.sleep(1.0)
            return True

        except Exception as e:
            log_message(
                f"{[driver.serial]} - Lỗi open_member_tools_menu selector={sel}: {e}",
                logging.WARNING,
            )

    return False

async def open_manage_content_from_menu(driver) -> bool:
    """
    Sau khi mở menu 3 chấm của group, click vào 'Quản lý nội dung'
    để vào màn quản lý bài viết/nội dung của bạn.
    """
    candidates = (
        {"description": "Quản lý nội dung"},
        {"descriptionContains": "Quản lý nội dung"},
        {"text": "Quản lý nội dung"},
        {"textContains": "Quản lý nội dung"},
        {"descriptionContains": "Manage content"},
        {"textContains": "Manage content"},
    )

    for sel in candidates:
        try:
            el = driver(**sel)
            exists = el.exists
            log_message(
                f"{[driver.serial]} - check manage-content selector={sel} exists={exists}",
                logging.INFO,
            )
            if not exists:
                continue

            info = getattr(el, "info", {}) or {}
            log_message(
                f"{[driver.serial]} - MATCH manage-content selector={sel} "
                f"text={info.get('text')} desc={info.get('contentDescription')} "
                f"clickable={info.get('clickable')} enabled={info.get('enabled')} "
                f"bounds={info.get('bounds')}",
                logging.INFO,
            )

            try:
                await asyncio.to_thread(el.click)
            except Exception:
                b = info.get("bounds") or {}
                if not b:
                    continue
                x = (int(b["left"]) + int(b["right"])) // 2
                y = (int(b["top"]) + int(b["bottom"])) // 2
                await asyncio.to_thread(driver.click, x, y)

            await asyncio.sleep(2.0)
            return True

        except Exception as e:
            log_message(
                f"{[driver.serial]} - Lỗi open_manage_content_from_menu selector={sel}: {e}",
                logging.WARNING,
            )

    return False

async def open_manage_group_posts_via_member_tools(driver, timeout: float = 8.0) -> bool:
    """
    Luồng mới ưu tiên:
    - từ màn group
    - bấm 'Công cụ khác cho thành viên'
    - bấm 'Quản lý nội dung'
    """
    end = time.time() + timeout
    round_idx = 0

    while time.time() < end:
        round_idx += 1
        log_message(
            f"{[driver.serial]} - [open_manage_via_member_tools] round={round_idx}",
            logging.INFO,
        )

        opened_menu = await open_member_tools_menu(driver)
        if opened_menu:
            opened_manage = await open_manage_content_from_menu(driver)
            if opened_manage:
                log_message(
                    f"{[driver.serial]} - Đã mở 'Quản lý nội dung' bằng luồng mới",
                    logging.INFO,
                )
                await _refresh_manage_group_posts_page(driver, sleep_after=6.0)
                return True

        await asyncio.sleep(0.8)

    log_message(
        f"{[driver.serial]} - open_manage_group_posts_via_member_tools timeout",
        logging.WARNING,
    )
    return False

async def _dismiss_termux_popups(driver):
    popup_candidates = [
        ("allow_vi", {"text": "Cho phép"}),
        ("allow_en", {"text": "Allow"}),
        ("ok_vi", {"text": "OK"}),
        ("ok_en", {"text": "Ok"}),
        ("continue_vi", {"textContains": "Tiếp tục"}),
        ("continue_en", {"textContains": "Continue"}),
        ("while_using_vi", {"textContains": "Trong khi dùng ứng dụng"}),
        ("while_using_en", {"textContains": "While using the app"}),
    ]

    clicked_any = False
    for label, sel in popup_candidates:
        ok = await _click_if_exists(driver, sel, label)
        if ok:
            clicked_any = True

    return clicked_any


async def _focus_termux_prompt(driver):
    try:
        size = driver.window_size()
        if isinstance(size, dict):
            w = int(size.get("width", 0))
            h = int(size.get("height", 0))
        else:
            w, h = size

        # Click vào vùng prompt shell phía dưới màn hình
        points = [
            (int(w * 0.50), int(h * 0.82)),
            (int(w * 0.50), int(h * 0.74)),
            (int(w * 0.18), int(h * 0.82)),
        ]

        for idx, (x, y) in enumerate(points, start=1):
            await asyncio.to_thread(driver.click, x, y)
            log_message(
                f"{[driver.serial]} - Focus Termux prompt click #{idx} tại ({x}, {y})",
                logging.INFO,
            )
            await asyncio.sleep(0.35)

        # gửi 1 newline trước để đánh thức prompt
        try:
            await asyncio.to_thread(driver.send_keys, "\n")
            log_message(f"{[driver.serial]} - Đã gửi newline để wake prompt", logging.INFO)
        except Exception as e:
            log_message(f"{[driver.serial]} - send newline wake prompt lỗi: {e}", logging.WARNING)

        await asyncio.sleep(0.5)
        return True
    except Exception as e:
        log_message(
            f"{[driver.serial]} - Focus Termux prompt lỗi: {e}",
            logging.WARNING,
        )
        return False

# Lấy nội dung clipboard
async def get_clipboard_content(driver, app, device_id: str | None = None):
    device_id = device_id or getattr(driver, "serial", None) or "unknown_device"

    ts = int(time.time() * 1000)
    remote_txt = f"/sdcard/Download/clipboard_{device_id}_{ts}.txt"
    remote_probe = f"/sdcard/Download/clipboard_probe_{device_id}_{ts}.txt"

    await asyncio.to_thread(os.makedirs, CLIPBOARD_DIR, exist_ok=True)
    local_txt = os.path.join(CLIPBOARD_DIR, f"clipboard_{device_id}_{ts}.txt")

    log_message(
        f"{device_id} - Bắt đầu lấy clipboard. remote_txt={remote_txt}, local_txt={local_txt}",
        logging.INFO,
    )

    # Chờ Facebook ghi clipboard xong
    await asyncio.sleep(1.2)

    # Nếu cần disable rotation thì làm TRƯỚC khi mở Termux
    try:
        from main_merged import disable_auto_rotation
        await disable_auto_rotation(driver, driver.serial)
    except Exception as e:
        log_message(f"{device_id} - disable_auto_rotation lỗi: {e}", logging.WARNING)

    adb = WINDOW_ADB_PATH if os.name == "nt" else LINUX_ADB_PATH

    async def _remote_cat(path: str):
        return await asyncio.to_thread(
            subprocess.run,
            [adb, "-s", device_id, "shell", "cat", path],
            capture_output=True,
            text=True
        )

    async def _probe_ok():
        proc = await _remote_cat(remote_probe)
        log_message(
            f"{device_id} - probe check returncode={proc.returncode}, stdout={proc.stdout!r}, stderr={proc.stderr!r}",
            logging.INFO,
        )
        return proc.returncode == 0 and "TERMUX_OK" in (proc.stdout or "")

    cmd = (
        f"rm -f {remote_txt} {remote_probe}; "
        f"echo TERMUX_OK > {remote_probe}; "
        f"termux-clipboard-get > {remote_txt}"
    )

    probe_ok = False

    # Retry 3 lần vì lỗi chính là focus/inject vào prompt
    for attempt in range(1, 4):
        log_message(f"{device_id} - Termux inject attempt={attempt}", logging.INFO)

        try:
            driver.app_start("com.termux")
        except Exception as e:
            log_message(f"{device_id} - app_start(com.termux) lỗi: {e}", logging.WARNING)
            await asyncio.sleep(1.5)
            continue

        ok_fg = await wait_until_app_foreground(driver, "com.termux", timeout=12.0)
        if not ok_fg:
            log_message(f"{device_id} - Termux không lên foreground ở attempt={attempt}", logging.WARNING)
            await asyncio.sleep(1.0)
            continue

        # tăng thời gian chờ như bạn nghi ngờ
        await asyncio.sleep(5.0 if attempt == 1 else 2.5)

        # xử lý popup nếu có
        await _dismiss_termux_popups(driver)
        await asyncio.sleep(0.8)

        # ép focus vào shell prompt
        await _focus_termux_prompt(driver)

        log_message(f"{device_id} - Gửi lệnh Termux: {cmd}", logging.INFO)

        try:
            await asyncio.to_thread(driver.send_keys, cmd + "\n")
            log_message(f"{device_id} - Đã send_keys command vào Termux kèm newline", logging.INFO)
        except Exception as e:
            log_message(f"{device_id} - send_keys command lỗi: {e}", logging.WARNING)
            await asyncio.sleep(1.0)
            continue

        # poll ngắn sau mỗi attempt
        for i in range(4):
            await asyncio.sleep(1.0)
            if await _probe_ok():
                probe_ok = True
                break

        if probe_ok:
            break

        log_message(
            f"{device_id} - attempt={attempt} chưa tạo được probe, sẽ thử focus + inject lại",
            logging.WARNING,
        )

    if not probe_ok:
        log_message(
            f"{device_id} - Probe file không được tạo sau 3 attempt => lệnh chưa vào prompt shell của Termux",
            logging.WARNING,
        )
        try:
            driver.app_start(app)
            await asyncio.sleep(1.0)
        except Exception as e:
            log_message(f"{device_id} - app_start({app}) lỗi sau khi đọc clipboard: {e}", logging.WARNING)
        return None

    # Pull file clipboard
    proc_pull = await asyncio.to_thread(
        subprocess.run,
        [adb, "-s", device_id, "pull", remote_txt, local_txt],
        capture_output=True,
        text=True
    )
    log_message(
        f"{device_id} - ADB pull {remote_txt} -> {local_txt}: returncode={proc_pull.returncode}, stdout={proc_pull.stdout!r}, stderr={proc_pull.stderr!r}",
        logging.INFO,
    )

    def _read_text(path):
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read().strip()
        except FileNotFoundError:
            return None

    txt = await asyncio.to_thread(_read_text, local_txt)
    log_message(f"{device_id} - clipboard local text: {txt!r}", logging.INFO)

    try:
        driver.app_start(app)
        await asyncio.sleep(1.0)
    except Exception as e:
        log_message(f"{device_id} - app_start({app}) lỗi sau khi đọc clipboard: {e}", logging.WARNING)

    return txt

async def get_clipboard_content_via_termux_adb_typing(driver, app, device_id: str | None = None):
    device_id = device_id or getattr(driver, "serial", None) or "unknown_device"
    adb = WINDOW_ADB_PATH if os.name == "nt" else LINUX_ADB_PATH

    remote_dir = "/sdcard/Download"
    remote_out = f"{remote_dir}/fb.txt"
    remote_err = f"{remote_dir}/fb.err"
    remote_exit = f"{remote_dir}/fb.exit"

    await asyncio.to_thread(os.makedirs, CLIPBOARD_DIR, exist_ok=True)
    safe_device_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", device_id)
    local_out = os.path.join(CLIPBOARD_DIR, f"fb_cb_{safe_device_id}.txt")

    log_message(
        f"{device_id} - Bắt đầu đọc clipboard qua Termux bằng ADB typing",
        logging.INFO,
    )

    # cho Facebook có thời gian ghi clipboard sau khi click "Sao chép liên kết"
    await asyncio.sleep(1.5)

    async def _run(args, timeout=20):
        return await asyncio.to_thread(
            subprocess.run,
            [adb, "-s", device_id] + args,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="ignore",
        )

    async def _shell(cmd, timeout=20):
        return await _run(["shell", cmd], timeout=timeout)

    termux_command_timeout_seconds = 10
    termux_command_retries = 3
    termux_ctrl_c_count = 3

    async def _send_termux_ctrl_c(reason: str):
        for idx in range(1, termux_ctrl_c_count + 1):
            r_ctrl = await _shell("input keycombination KEYCODE_CTRL_LEFT KEYCODE_C", timeout=10)
            if r_ctrl.returncode != 0:
                r_ctrl = await _shell("input keycombination 113 31", timeout=10)
            log_message(
                f"{device_id} - TERMUX CTRL+C #{idx}/{termux_ctrl_c_count} ({reason}) "
                f"returncode={r_ctrl.returncode}, stdout={r_ctrl.stdout!r}, stderr={r_ctrl.stderr!r}",
                logging.INFO,
            )
            await asyncio.sleep(0.35)

    # Cleanup file kết quả cũ để poll không đọc nhầm lần trước. Các file này được ghi đè mỗi lần lấy link.
    await _shell(f"rm -f {remote_out} {remote_err} {remote_exit}", timeout=15)

    # mở Termux
    r_open = await _shell("am start -n com.termux/.app.TermuxActivity", timeout=15)
    log_message(
        f"{device_id} - OPEN TERMUX returncode={r_open.returncode}, stdout={r_open.stdout!r}, stderr={r_open.stderr!r}",
        logging.INFO,
    )
    await asyncio.sleep(3.0)

    # lấy kích thước màn hình
    r_size = await _shell("wm size", timeout=10)
    txt_size = (r_size.stdout or "") + "\n" + (r_size.stderr or "")
    w, h = 720, 1650
    m = re.search(r"Physical size:\s*(\d+)x(\d+)", txt_size)
    if m:
        w, h = int(m.group(1)), int(m.group(2))

    # focus prompt termux
    taps = [
        (int(w * 0.50), int(h * 0.85)),
        (int(w * 0.50), int(h * 0.76)),
        (int(w * 0.18), int(h * 0.85)),
    ]

    async def _focus_termux_shell_prompt():
        for idx, (x, y) in enumerate(taps, start=1):
            r_tap = await _shell(f"input tap {x} {y}", timeout=10)
            log_message(
                f"{device_id} - FOCUS TAP #{idx} ({x},{y}) returncode={r_tap.returncode}",
                logging.INFO,
            )
            await asyncio.sleep(0.5)

    async def _prepare_termux_prompt(attempt: int):
        await _focus_termux_shell_prompt()
        await _send_termux_ctrl_c(f"before command attempt={attempt}")
        r_wake = await _shell("input keyevent 66", timeout=10)
        log_message(
            f"{device_id} - WAKE PROMPT attempt={attempt} returncode={r_wake.returncode}, "
            f"stdout={r_wake.stdout!r}, stderr={r_wake.stderr!r}",
            logging.INFO,
        )
        await asyncio.sleep(0.6)

    # Gõ trực tiếp như luồng cũ, nhưng dùng tên file cố định ngắn và ghi đè mỗi lần.
    one_liner = (
        f"termux-clipboard-get>{remote_out} 2>{remote_err};"
        f"echo $? >{remote_exit}"
    )

    # escape cho adb input text:
    # space -> %s; một số ký tự shell cần escape để adb input text gõ đúng.
    typed_cmd = (
        one_liner
        .replace("\\", "\\\\")
        .replace(" ", "%s")
        .replace("&", "\\&")
        .replace("|", "\\|")
        .replace("<", "\\<")
        .replace(">", "\\>")
        .replace(";", "\\;")
        .replace("(", "\\(")
        .replace(")", "\\)")
        .replace('"', '\\"')
    )

    exit_ok = False
    exit_text = None

    for attempt in range(1, termux_command_retries + 1):
        await _shell(f"rm -f {remote_out} {remote_err} {remote_exit}", timeout=15)
        await _prepare_termux_prompt(attempt)

        r_type = await _run(["shell", "input", "text", typed_cmd], timeout=25)
        log_message(
            f"{device_id} - TYPE COMMAND attempt={attempt}/{termux_command_retries} {one_liner!r} "
            f"returncode={r_type.returncode}, stdout={r_type.stdout!r}, stderr={r_type.stderr!r}",
            logging.INFO,
        )
        await asyncio.sleep(0.8)

        # enter de chay
        r_enter = await _shell("input keyevent 66", timeout=10)
        log_message(
            f"{device_id} - PRESS ENTER attempt={attempt} returncode={r_enter.returncode}, "
            f"stdout={r_enter.stdout!r}, stderr={r_enter.stderr!r}",
            logging.INFO,
        )

        # poll exit
        for i in range(termux_command_timeout_seconds):
            await asyncio.sleep(1.0)
            r_exit = await _shell(f"cat {remote_exit}", timeout=10)
            log_message(
                f"{device_id} - POLL EXIT attempt={attempt} round={i+1}/{termux_command_timeout_seconds} "
                f"returncode={r_exit.returncode}, stdout={r_exit.stdout!r}, stderr={r_exit.stderr!r}",
                logging.INFO,
            )
            if r_exit.returncode == 0 and (r_exit.stdout or "").strip() != "":
                exit_ok = True
                exit_text = (r_exit.stdout or "").strip()
                break

        if exit_ok:
            break

        log_message(
            f"{device_id} - Termux command timeout sau {termux_command_timeout_seconds}s "
            f"o attempt={attempt}/{termux_command_retries}; gui CTRL+C roi retry",
            logging.WARNING,
        )
        await _send_termux_ctrl_c(f"timeout attempt={attempt}")
        await asyncio.sleep(0.8)

    if not exit_ok:
        log_message(
            f"{device_id} - Không thấy file exit của lệnh Termux",
            logging.WARNING,
        )
        try:
            driver.app_start(app)
            await asyncio.sleep(1.0)
        except Exception as e:
            log_message(f"{device_id} - app_start({app}) lỗi: {e}", logging.WARNING)
        return None

    r_out = await _shell(f"cat {remote_out}", timeout=10)
    r_err = await _shell(f"cat {remote_err}", timeout=10)

    log_message(
        f"{device_id} - TERMUX EXIT={exit_text}, OUT={r_out.stdout!r}, ERR={r_err.stdout!r}{r_err.stderr!r}",
        logging.INFO,
    )

    txt = (r_out.stdout or "").strip()

    def _save_local():
        with open(local_out, "w", encoding="utf-8", errors="ignore") as f:
            f.write(txt)

    await asyncio.to_thread(_save_local)

    try:
        driver.app_start(app)
        await asyncio.sleep(1.0)
    except Exception as e:
        log_message(f"{device_id} - app_start({app}) lỗi sau khi đọc clipboard: {e}", logging.WARNING)

    return txt or None

async def test_termux_clipboard_once(driver):
    """
    Chạy 1 lần đọc clipboard qua Termux để debug.
    Không phụ thuộc flow đăng bài.
    """
    try:
        cur = await asyncio.to_thread(driver.app_current)
        back_pkg = (cur or {}).get("package") or "com.facebook.katana"
    except Exception:
        cur = None
        back_pkg = "com.facebook.katana"

    txt = await get_clipboard_content(driver, back_pkg, device_id=driver.serial)

    return {
        "ok": txt is not None,
        "current_app": cur,
        "back_pkg": back_pkg,
        "clipboard": txt,
    }

# Lấy liên kết bài viết
async def extract_post_link(driver, post):
    for node in post.iter():
        if node.attrib.get("text") == "Chia sẻ" or node.attrib.get("content-desc") == "Chia sẻ":
            bounds = parse_number(node.attrib.get("bounds"))
            driver.click((bounds[0] + bounds[2]) // 2, (bounds[1] + bounds[3]) // 2)
    await asyncio.sleep(1)
    if driver(text="Sao chép liên kết").exists:
        driver(text="Sao chép liên kết").click()
        return await get_clipboard_content(driver, "com.facebook.katana")

    width, height = driver.window_size()

    start_x = width * 0.9   # gần mép phải
    end_x = width * 0.1     # gần mép trái
    y = height * 0.9

    driver.swipe(start_x, y, end_x, y, duration=0.2)
    await click_template(driver, "copy_link")
    return await get_clipboard_content(driver, "com.facebook.katana")


async def copy_latest_post_link_for_wall(driver):
    """
    Copy link của bài đang hiển thị trên màn hình (ưu tiên bài trên cùng).
    Không cần truyền 'post' XML node.
    """
    await asyncio.sleep(1)

    # Cách 1: click trực tiếp nút "Chia sẻ" nếu thấy (VN/EN)
    share_candidates = (
        {"text": "Chia sẻ"},
        {"description": "Chia sẻ"},
        {"text": "Share"},
        {"description": "Share"},
    )
    async def _click_share_if_visible():
        for sel in share_candidates:
            el = driver(**sel)
            if el.exists:
                try:
                    el.click()
                except Exception:
                    # đôi khi element bị che -> thử click lại
                    try:
                        el.click()
                    except Exception:
                        pass
                await asyncio.sleep(1)
                return True
        return False

    # 1) Thử click share ngay (như cũ)
    clicked = await _click_share_if_visible()

    # 2) Nếu chưa thấy share, scroll dọc để lộ hàng nút Thích/Bình luận/Chia sẻ (hướng B)
    if not clicked:
        w, h = driver.window_size()
        # Vuốt "up": kéo nội dung bài lên để lộ footer (Like/Comment/Share)
        # start_y lớn -> end_y nhỏ
        start_x = int(w * 0.5)
        start_y = int(h * 0.78)
        end_y   = int(h * 0.40)

        for _ in range(5):
            # scroll dọc nhẹ trong feed/post
            driver.swipe(start_x, start_y, start_x, end_y, duration=0.25)
            await asyncio.sleep(0.7)

            if await _click_share_if_visible():
                clicked = True
                break

    # Nếu menu share mở ra và có "Sao chép liên kết" thì click luôn
    if driver(text="Sao chép liên kết").exists:
        driver(text="Sao chép liên kết").click()
        return await get_clipboard_content(driver, "com.facebook.katana", device_id=driver.serial)

    # Fallback: dùng template copy_link giống extract_post_link()
    width, height = driver.window_size()
    driver.swipe(width * 0.9, height * 0.9, width * 0.1, height * 0.9, duration=0.2)
    await click_template(driver, "copy_link")
    await asyncio.sleep(2.0)
    return await get_clipboard_content(driver, "com.facebook.katana", device_id=driver.serial)

async def _click_manage_group_posts_from_xml(driver) -> bool:
    """
    Tìm nút 'Quản lý bài viết' trong block 'Bài viết trong nhóm'
    bằng dump_hierarchy, không dùng tọa độ cứng.
    """
    try:
        xml = driver.dump_hierarchy()
    except Exception as e:
        log_message(
            f"{[driver.serial]} - dump_hierarchy lỗi khi tìm 'Quản lý bài viết': {e}",
            logging.WARNING,
        )
        return False

    nodes = list(_iter_nodes_from_xml(xml))
    if not nodes:
        return False

    screen_w, screen_h = driver.window_size()

    section_nodes = []
    manage_btn_nodes = []

    for meta in nodes:
        txt = (meta.get("text") or "").strip().lower()
        desc = (meta.get("desc") or "").strip().lower()
        cls = (meta.get("cls") or "").strip()

        if "bài viết trong nhóm" in txt or "bài viết trong nhóm" in desc:
            section_nodes.append(meta)

        if cls == "android.widget.Button":
            if "quản lý bài viết" in txt or "quản lý bài viết" in desc:
                manage_btn_nodes.append(meta)

    # ===== PASS 1: thấy đúng button "Quản lý bài viết" thì click luôn =====
    if manage_btn_nodes:
        # ưu tiên button nằm nửa dưới màn và gần phía phải
        def _score_btn(meta):
            l, t, r, b = meta["bounds"]
            cx = (l + r) // 2
            cy = (t + b) // 2
            score = 0
            if cx >= int(screen_w * 0.55):
                score -= 100
            if cy >= int(screen_h * 0.45):
                score -= 50
            score += cy
            return score

        manage_btn_nodes.sort(key=_score_btn)
        best = manage_btn_nodes[0]
        l, t, r, b = best["bounds"]
        x = (l + r) // 2
        y = (t + b) // 2

        log_message(
            f"{[driver.serial]} - Click XML button 'Quản lý bài viết' "
            f"tại ({x}, {y}), meta={best}",
            logging.INFO,
        )
        try:
            await asyncio.to_thread(driver.click, x, y)
            await asyncio.sleep(1.2)
            return True
        except Exception as e:
            log_message(
                f"{[driver.serial]} - Click XML button 'Quản lý bài viết' lỗi: {e}",
                logging.WARNING,
            )

    # ===== PASS 2: nếu có section 'Bài viết trong nhóm' thì tìm button ở cùng hàng / cùng block =====
    for sec in section_nodes:
        sl, st, sr, sb = sec["bounds"]
        sec_cy = (st + sb) // 2

        for meta in nodes:
            cls = (meta.get("cls") or "").strip()
            if cls != "android.widget.Button":
                continue
            if not meta.get("clickable", False):
                continue

            txt = (meta.get("text") or "").strip().lower()
            desc = (meta.get("desc") or "").strip().lower()
            l, t, r, b = meta["bounds"]
            cx = (l + r) // 2
            cy = (t + b) // 2

            # button nằm bên phải section và gần cùng vùng dọc
            same_band = abs(cy - sec_cy) <= 120
            right_side = cx >= int(screen_w * 0.60)

            if same_band and right_side:
                if (
                    "quản lý" in txt or "quản lý" in desc
                    or "bài viết" in txt or "bài viết" in desc
                    or "manage" in txt or "manage" in desc
                ):
                    log_message(
                        f"{[driver.serial]} - Click button cùng block 'Bài viết trong nhóm' "
                        f"tại ({cx}, {cy}), meta={meta}",
                        logging.INFO,
                    )
                    try:
                        await asyncio.to_thread(driver.click, cx, cy)
                        await asyncio.sleep(1.2)
                        return True
                    except Exception as e:
                        log_message(
                            f"{[driver.serial]} - Click button cùng block lỗi: {e}",
                            logging.WARNING,
                        )

    log_message(
        f"{[driver.serial]} - Không tìm thấy 'Quản lý bài viết' bằng XML fallback",
        logging.INFO,
    )
    return False

async def _try_click_manage_after_you_tab(driver) -> bool:
    """
    Sau khi đã click tab 'Bạn', thử ngay các cách để bấm 'Quản lý bài viết'.
    """
    # chờ UI load phần "Bài viết trong nhóm"
    for attempt in range(3):
        await asyncio.sleep(0.8 if attempt == 0 else 1.0)

        # 1) direct selectors
        direct_candidates = (
            {"text": "Quản lý bài viết"},
            {"description": "Quản lý bài viết"},
            {"textContains": "Quản lý bài viết"},
            {"descriptionContains": "Quản lý bài viết"},
            {"textContains": "Xem bài viết"},
            {"descriptionContains": "Xem bài viết"},
            {"textContains": "Xem bài"},
            {"descriptionContains": "Xem bài"},
        )

        for sel in direct_candidates:
            try:
                el = driver(**sel)
                exists = el.exists
                log_message(
                    f"{[driver.serial]} - [after_you] check selector={sel} exists={exists}",
                    logging.INFO,
                )
                if not exists:
                    continue

                info = getattr(el, "info", {}) or {}
                log_message(
                    f"{[driver.serial]} - [after_you] MATCH selector={sel} "
                    f"text={info.get('text')} desc={info.get('contentDescription')} "
                    f"clickable={info.get('clickable')} enabled={info.get('enabled')} "
                    f"bounds={info.get('bounds')}",
                    logging.INFO,
                )

                try:
                    await asyncio.to_thread(el.click)
                except Exception:
                    b = info.get("bounds") or {}
                    if b:
                        x = (int(b["left"]) + int(b["right"])) // 2
                        y = (int(b["top"]) + int(b["bottom"])) // 2
                        await asyncio.to_thread(driver.click, x, y)

                await asyncio.sleep(1.2)
                await _refresh_manage_group_posts_page(driver, sleep_after=10.0)
                return True
            except Exception as e:
                log_message(
                    f"{[driver.serial]} - [after_you] lỗi selector={sel}: {e}",
                    logging.WARNING,
                )

        # 2) xml fallback
        try:
            clicked_manage_xml = await _click_manage_group_posts_from_xml(driver)
            if clicked_manage_xml:
                log_message(
                    f"{[driver.serial]} - [after_you] Đã click 'Quản lý bài viết' bằng XML",
                    logging.INFO,
                )
                await _refresh_manage_group_posts_page(driver, sleep_after=10.0)
                return True
        except Exception as e:
            log_message(
                f"{[driver.serial]} - [after_you] XML fallback lỗi: {e}",
                logging.WARNING,
            )

    return False

async def _refresh_manage_group_posts_page(driver, sleep_after: float = 10.0):
    """
    Sau khi vào màn 'Quản lý bài viết', vuốt nhẹ từ trên xuống để reload
    rồi chờ thêm vài giây cho danh sách bài viết load xong.
    """
    try:
        size = driver.window_size()
        if isinstance(size, dict):
            w = int(size.get("width", 0))
            h = int(size.get("height", 0))
        else:
            w, h = size

        x = int(w * 0.5)

        # kéo nhẹ từ trên xuống
        start_y = int(h * 0.28)
        end_y   = int(h * 0.72)

        log_message(
            f"{[driver.serial]} - Refresh màn 'Quản lý bài viết' bằng vuốt xuống tại x={x}, y={start_y}->{end_y}",
            logging.INFO,
        )

        await asyncio.to_thread(driver.swipe, x, start_y, x, end_y, 0.25)
        await asyncio.sleep(sleep_after)

        log_message(
            f"{[driver.serial]} - Đã refresh và chờ {sleep_after}s để load bài viết",
            logging.INFO,
        )
    except Exception as e:
        log_message(
            f"{[driver.serial]} - Lỗi refresh màn 'Quản lý bài viết': {e}",
            logging.WARNING,
        )
        # vẫn chờ để UI có thời gian load
        await asyncio.sleep(sleep_after)

async def open_manage_group_posts(driver, timeout=10.0):
    """
    Mở màn quản lý/nội dung bài viết của bạn trong group.

    Logic đúng:
    - Nếu thấy 'Quản lý bài viết' / 'Xem bài viết' thì click và return True
    - Nếu chỉ mới click tab 'Bạn' thì KHÔNG return ngay, mà tiếp tục loop để tìm 'Quản lý bài viết'
    """
    end = time.time() + timeout
    round_idx = 0

    while time.time() < end:
        round_idx += 1
        log_message(
            f"{[driver.serial]} - [open_manage_group_posts] round={round_idx}",
            logging.INFO,
        )

        # =========================
        # CASE 1: text trực tiếp
        # =========================
        direct_candidates = (
            {"text": "Quản lý bài viết"},
            {"description": "Quản lý bài viết"},
            {"textContains": "Quản lý bài viết"},
            {"descriptionContains": "Quản lý bài viết"},
            {"textContains": "Xem bài viết"},
            {"descriptionContains": "Xem bài viết"},
            {"textContains": "Xem bài"},
            {"descriptionContains": "Xem bài"},
            {"textContains": "View post"},
            {"descriptionContains": "View post"},
        )

        for sel in direct_candidates:
            try:
                el = driver(**sel)
                exists = el.exists
                log_message(
                    f"{[driver.serial]} - check direct selector={sel} exists={exists}",
                    logging.INFO,
                )
                if exists:
                    info = getattr(el, "info", {}) or {}
                    log_message(
                        f"{[driver.serial]} - MATCH direct selector={sel} "
                        f"text={info.get('text')} desc={info.get('contentDescription')} "
                        f"clickable={info.get('clickable')} enabled={info.get('enabled')} "
                        f"bounds={info.get('bounds')}",
                        logging.INFO,
                    )
                    try:
                        await asyncio.to_thread(el.click)
                    except Exception:
                        b = info.get("bounds") or {}
                        if b:
                            x = (int(b["left"]) + int(b["right"])) // 2
                            y = (int(b["top"]) + int(b["bottom"])) // 2
                            await asyncio.to_thread(driver.click, x, y)

                    await asyncio.sleep(1.2)
                    log_message(
                        f"{[driver.serial]} - Đã click màn quản lý bài viết/xem bài viết thành công",
                        logging.INFO,
                    )
                    await asyncio.sleep(5)
                    await _refresh_manage_group_posts_page(driver, sleep_after=10.0)
                    return True
            except Exception as e:
                log_message(
                    f"{[driver.serial]} - lỗi direct selector={sel}: {e}",
                    logging.WARNING,
                )

        # =========================
        # CASE 2: xpath text
        # =========================
        xpath_candidates = (
            "//*[contains(@text,'Quản lý') and contains(@text,'bài viết')]",
            "//*[contains(@text,'Xem bài viết')]",
            "//*[contains(@text,'Xem bài')]",
            "//*[contains(@content-desc,'Quản lý') and contains(@content-desc,'bài viết')]",
            "//*[contains(@content-desc,'Xem bài viết')]",
            "//*[contains(@content-desc,'Xem bài')]",
        )

        for xp in xpath_candidates:
            try:
                xel = driver.xpath(xp)
                exists = xel.exists
                log_message(
                    f"{[driver.serial]} - check xpath={xp} exists={exists}",
                    logging.INFO,
                )
                if exists:
                    await asyncio.to_thread(xel.click)
                    await asyncio.sleep(1.2)
                    log_message(
                        f"{[driver.serial]} - Đã click xpath quản lý bài viết/xem bài viết",
                        logging.INFO,
                    )
                    await _refresh_manage_group_posts_page(driver, sleep_after=10.0)
                    return True
            except Exception as e:
                log_message(
                    f"{[driver.serial]} - lỗi xpath={xp}: {e}",
                    logging.WARNING,
                )

        # =========================
        # CASE 3: tab 'Bạn'
        # =========================
        you_tab_selectors = (
            {"description": "Bạn"},
            {"text": "Bạn"},
            {"descriptionContains": "Bạn"},
            {"textContains": "Bạn"},
        )

        clicked_you = False

        for sel in you_tab_selectors:
            try:
                el = driver(**sel)
                exists = el.exists
                log_message(
                    f"{[driver.serial]} - check you-tab selector={sel} exists={exists}",
                    logging.INFO,
                )
                if not exists:
                    continue

                info = getattr(el, "info", {}) or {}
                log_message(
                    f"{[driver.serial]} - MATCH you-tab selector={sel} "
                    f"text={info.get('text')} desc={info.get('contentDescription')} "
                    f"clickable={info.get('clickable')} enabled={info.get('enabled')} "
                    f"bounds={info.get('bounds')}",
                    logging.INFO,
                )

                try:
                    await asyncio.to_thread(el.click)
                    clicked_you = True
                except Exception as e:
                    log_message(
                        f"{[driver.serial]} - Click trực tiếp tab 'Bạn' lỗi: {e}",
                        logging.WARNING,
                    )
                    b = info.get("bounds") or {}
                    if b:
                        x = (int(b["left"]) + int(b["right"])) // 2
                        y = (int(b["top"]) + int(b["bottom"])) // 2
                        await asyncio.to_thread(driver.click, x, y)
                        clicked_you = True
                        log_message(
                            f"{[driver.serial]} - Click tọa độ tab 'Bạn' tại ({x}, {y})",
                            logging.INFO,
                        )

                if clicked_you:
                    await asyncio.sleep(1.5)
                    log_message(
                        f"{[driver.serial]} - Đã click tab 'Bạn', tiếp tục tìm 'Quản lý bài viết'",
                        logging.INFO,
                    )
                    break

            except Exception as e:
                log_message(
                    f"{[driver.serial]} - lỗi case tab 'Bạn' selector={sel}: {e}",
                    logging.WARNING,
                )

        if clicked_you:
            log_message(
                f"{[driver.serial]} - Đã click tab 'Bạn', thử mở ngay 'Quản lý bài viết'",
                logging.INFO,
            )

            opened_after_you = await _try_click_manage_after_you_tab(driver)
            if opened_after_you:
                return True

            log_message(
                f"{[driver.serial]} - Sau khi click 'Bạn' vẫn chưa thấy 'Quản lý bài viết', lặp lại để thử tiếp",
                logging.INFO,
            )
            continue

        # fallback XML riêng cho tab Bạn
        try:
            clicked_xml = await _click_you_tab_from_xml(driver)
            if clicked_xml:
                await asyncio.sleep(1.5)
                log_message(
                    f"{[driver.serial]} - Đã click tab 'Bạn' bằng XML, thử mở ngay 'Quản lý bài viết'",
                    logging.INFO,
                )

                opened_after_you = await _try_click_manage_after_you_tab(driver)
                if opened_after_you:
                    return True

                log_message(
                    f"{[driver.serial]} - Click 'Bạn' bằng XML xong nhưng chưa mở được 'Quản lý bài viết', sẽ thử tiếp",
                    logging.INFO,
                )
                continue
        except Exception as e:
            log_message(
                f"{[driver.serial]} - fallback XML tab 'Bạn' lỗi: {e}",
                logging.WARNING,
            )

        # =========================
        # CASE 4: XML fallback cho block "Bài viết trong nhóm" -> "Quản lý bài viết"
        # =========================
        try:
            clicked_manage_xml = await _click_manage_group_posts_from_xml(driver)
            if clicked_manage_xml:
                log_message(
                    f"{[driver.serial]} - Đã click 'Quản lý bài viết' bằng XML fallback",
                    logging.INFO,
                )
                return True
        except Exception as e:
            log_message(
                f"{[driver.serial]} - fallback XML 'Quản lý bài viết' lỗi: {e}",
                logging.WARNING,
            )

        # =========================
        # scroll nhẹ rồi thử lại
        # =========================
        try:
            await asyncio.to_thread(driver.swipe_ext, "up", 0.35)
        except Exception:
            try:
                await asyncio.to_thread(driver.swipe, 0.5, 0.75, 0.5, 0.45)
            except Exception:
                pass

        await asyncio.sleep(0.5)

    log_message(
        f"{[driver.serial]} - open_manage_group_posts timeout sau {timeout}s",
        logging.WARNING,
    )
    return False

# async def detect_latest_group_post_status_by_ocr(driver, recent_max_minutes: int = 6):
#     """
#     1 screenshot, no scroll.
#     Return (status_str, debug_dict)
#     status_str: pending/posted/rejected/removed
#     """
#     ocr = _get_ocr()
#     res = ocr.detect_latest(driver, recent_max_minutes=recent_max_minutes)
#     return res.status, res.debug

# def _normalize_group_post_status(latest_status) -> tuple[str | None, str]:
#     """
#     Normalize status về đúng header UI:
#       - pending -> "Đang chờ"
#       - posted  -> "Đã đăng"
#       - rejected/removed/unknown -> None (không lấy link)
#     Return: (ui_header_or_none, status_key)
#     """
#     # enum -> string
#     try:
#         # nếu bạn dùng GroupPostStatus enum
#         from module.fb_group_post_status_by_time import GroupPostStatus
#         if isinstance(latest_status, GroupPostStatus):
#             latest_status = latest_status.value
#     except Exception:
#         pass

#     s = (latest_status or "").strip()
#     sl = s.lower()

#     # chấp nhận cả tiếng Việt lẫn code OCR
#     if sl in ("pending",) or "đang chờ" in sl or "dang cho" in sl:
#         return "Đang chờ", "pending"
#     if sl in ("posted",) or "đã đăng" in sl or "da dang" in sl:
#         return "Đã đăng", "posted"

#     # coi như không duyệt/đã gỡ
#     if sl in ("rejected", "removed", "unknown") or "từ chối" in sl or "go" in sl:
#         return None, sl

#     # fallback: nếu ai đó truyền sai
#     return None, sl

# async def click_overflow_of_latest_group_post_by_status(driver, status_text: str):
#     """
#     Click dấu '...' của BÀI ĐẦU TIÊN trong section có header = status_text
#     status_text: "Đã đăng" | "Đang chờ"
#     """
#     await asyncio.sleep(0.4)

#     header = driver(textContains=status_text)
#     if not header.exists:
#         # đừng raise kiểu UiObjectNotFoundException mơ hồ -> raise rõ ràng
#         raise RuntimeError(f"Không tìm thấy header trạng thái: {status_text}")

#     # chỉ scroll khi header chưa nằm ổn trên màn
#     try:
#         header.scroll.to()
#     except Exception:
#         pass
#     await asyncio.sleep(0.4)

#     bounds = (header.info or {}).get("bounds")
#     if not bounds:
#         raise RuntimeError("Không lấy được bounds của header")

#     w, h = driver.window_size()
#     header_bottom = bounds.get("bottom", 0)

#     # Ưu tiên tìm đúng nút overflow bằng content-desc (đỡ click tọa độ mò)
#     # (FB hay dùng: "Tùy chọn khác", "More options", "Menu", ...)
#     desc_candidates = (
#         "Tùy chọn", "Tuy chon", "More options", "More", "Menu", "Tùy chọn khác"
#     )
#     class_candidates = ("android.widget.Button", "android.widget.ImageView")

#     best_el = None
#     best_gap = None

#     for dc in desc_candidates:
#         try:
#             els = driver(descriptionContains=dc).all()
#         except Exception:
#             els = []
#         for el in els:
#             info = el.info or {}
#             if info.get("className") not in class_candidates:
#                 continue
#             b = (info.get("bounds") or {})
#             if not b:
#                 continue
#             cy = int((b["top"] + b["bottom"]) / 2)
#             cx = int((b["left"] + b["right"]) / 2)

#             # chỉ xét vùng ngay dưới header + bên phải (thường là nút ...)
#             if cy <= header_bottom:
#                 continue
#             if cy > header_bottom + int(h * 0.45):
#                 continue
#             if cx < int(w * 0.60):
#                 continue

#             gap = cy - header_bottom
#             if best_gap is None or gap < best_gap:
#                 best_gap = gap
#                 best_el = el

#         if best_el:
#             break

#     if best_el:
#         try:
#             best_el.click()
#             await asyncio.sleep(1.2)
#             return
#         except Exception:
#             pass

#     # Fallback cuối: click heuristic tọa độ như code cũ
#     y_header = (bounds["top"] + bounds["bottom"]) // 2
#     x = int(w * 0.93)
#     y = int(y_header + h * 0.12)
#     y = min(y, h - 10)
#     driver.click(x, y)
#     await asyncio.sleep(1.2)

# async def copy_latest_post_link_for_group(driver, latest_status):
#     """
#     Copy link bài mới nhất theo trạng thái đã xác định (OCR/UI):
#       - latest_status có thể là: "Đang chờ"/"Đã đăng" hoặc "pending"/"posted" hoặc enum.
#       - Nếu status là rejected/removed/unknown -> raise RuntimeError (caller update Mongo/CRM).
#     """
#     ui_status, status_key = _normalize_group_post_status(latest_status)

#     if ui_status not in ("Đã đăng", "Đang chờ"):
#         # status_key giúp log/Mongo rõ nguyên nhân
#         raise RuntimeError(f"Bài viết không được duyệt (status={status_key})")

#     def _looks_like_url(s: str) -> bool:
#         s = (s or "").strip()
#         return bool(s) and (
#             s.startswith("http://")
#             or s.startswith("https://")
#             or "facebook.com" in s
#             or "fb.watch" in s
#             or s.startswith("fb://")
#         )

#     async def _read_clipboard_url() -> str:
#         txt = await get_clipboard_content(driver, "com.facebook.katana", device_id=driver.serial)
#         if not _looks_like_url(txt):
#             raise RuntimeError(f"Clipboard không có link hợp lệ: {txt!r}")
#         return txt.strip()

#     # 1) Click dấu ... (retry 2 lần nếu menu chưa hiện)
#     for _ in range(2):
#         await click_overflow_of_latest_group_post_by_status(driver, ui_status)

#         # 2) Click 'Sao chép liên kết' (VN/EN)
#         if driver(text="Sao chép liên kết").exists:
#             driver(text="Sao chép liên kết").click()
#             await asyncio.sleep(0.7)
#             return await _read_clipboard_url()

#         if driver(textContains="Copy link").exists:
#             driver(textContains="Copy link").click()
#             await asyncio.sleep(0.7)
#             return await _read_clipboard_url()

#         if driver(descriptionContains="Sao chép liên kết").exists:
#             driver(descriptionContains="Sao chép liên kết").click()
#             await asyncio.sleep(0.7)
#             return await _read_clipboard_url()

#         if driver(descriptionContains="Copy link").exists:
#             driver(descriptionContains="Copy link").click()
#             await asyncio.sleep(0.7)
#             return await _read_clipboard_url()

#         await asyncio.sleep(0.6)

#     # 3) Fallback: swipe menu ngang + template (best effort)
#     w, h = driver.window_size()
#     driver.swipe(w * 0.9, h * 0.9, w * 0.1, h * 0.9, duration=0.2)
#     await click_template(driver, "copy_link")
#     await asyncio.sleep(0.7)
#     return await _read_clipboard_url()

async def detect_group_post_status_by_content_text(driver, content: str, min_score: float = 0.65):
    """
    Detect status của bài vừa đăng trong màn 'Nội dung của bạn' bằng cách match 200 ký tự đầu content (bỏ icon).
    Return (status, debug)
    status: pending/posted/rejected/removed/unknown
    """
    # đợi UI render list
    last_info = None
    for attempt in range(3):
        await asyncio.sleep(1.2 if attempt == 0 else 0.8)
        info = detect_group_post_status_by_text(
            driver,
            content,
            min_score=min_score,
            command_max_chars=200,
        )
        last_info = info

        # nếu đã match được bài -> trả luôn
        if info.get("found"):
            return info.get("status", "unknown"), info

        # nếu rơi vào case "3 section đầu empty" -> trả removed luôn
        if info.get("status") == "removed" and (info.get("debug") or {}).get("reason") == "all_three_sections_empty":
            return "removed", info
    
    if last_info and last_info.get("debug"):
        logging.info(f"[TEXT_MATCH] fail reason={(last_info['debug'] or {}).get('reason')} debug={last_info['debug']}")

    if last_info:
        reason = ((last_info.get("debug") or {}).get("reason") or "").strip()
        status = last_info.get("status", "unknown")
        if status == "removed" and reason in {"no_headers", "no_match"}:
            return "unknown", last_info
        return status, last_info
    return "unknown", {}


async def click_overflow_of_latest_post_in_status_section_xml(driver, status: str, detect_info: dict | None = None):
    """
    Click nút '...' của BÀI ĐẦU TIÊN trong section status (Đang chờ / Đã đăng)
    bằng dump_hierarchy + bounds.

    Logic:
      - lấy vùng section (y0..y1) từ detect_info.debug.sections (ưu tiên)
      - define window ngay dưới header (tránh 'Xem tất cả')
      - scan XML tìm candidate icon nhỏ ở bên phải trong window
      - ưu tiên node có desc/text giống "Tùy chọn/More options/Menu"
      - click theo center(bounds)
    """
    s = (status or "").strip().lower()
    # if s in ("pending", "đang chờ", "dang cho"):
    #     key = "pending"
    #     header_text = "Đang chờ"
    # elif s in ("posted", "đã đăng", "da dang"):
    #     key = "posted"
    #     header_text = "Đã đăng"
    if s in ("pending", "đang chờ", "dang cho", "chờ duyệt", "cho duyet"):
        key = "pending"
        header_text_candidates = ("Đang chờ", "Chờ duyệt")
    elif s in ("posted", "đã đăng", "da dang"):
        key = "posted"
        header_text_candidates = ("Đã đăng",)
    else:
        raise RuntimeError(f"Status không hỗ trợ để click ... theo section: {status!r}")

    await asyncio.sleep(0.25)
    w, h = driver.window_size()

    # 1) Lấy range section
    sec = None
    try:
        secs = (detect_info or {}).get("debug", {}).get("sections") or []
        for it in secs:
            if it.get("key") == key:
                sec = it
                break
    except Exception:
        sec = None

    # if not sec:
        # fallback: lấy header bounds trực tiếp qua UIAutomator
        # header = driver(textContains=header_text)
        # if not header.exists:
        #     raise RuntimeError(f"Không tìm thấy header: {header_text}")
        # hb = (header.info or {}).get("bounds")
        # if not hb:
        #     raise RuntimeError(f"Không lấy được bounds header: {header_text}")
    if not sec:
        # fallback: lấy header bounds trực tiếp qua UIAutomator
        header = None
        header_name = None
        for cand in header_text_candidates:
            hobj = driver(textContains=cand)
            if hobj.exists:
                header = hobj
                header_name = cand
                break
        if header is None:
            raise RuntimeError(f"Không tìm thấy header nào trong các giá trị: {header_text_candidates}")
        hb = (header.info or {}).get("bounds")
        if not hb:
            raise RuntimeError(f"Không lấy được bounds header: {header_name}")
        y0 = int(hb["bottom"])
        y1 = min(h, y0 + int(h * 0.45))
    else:
        y0 = int(sec["y0"])
        y1 = min(int(sec["y1"]), h)

    # 2) Window chỉ ngay dưới header (tránh click nhầm 'Xem tất cả' nằm trên y0)
    window_y0 = min(max(0, y0 + 4), h)
    window_y1 = min(h, y0 + max(120, int(0.45 * (y1 - y0))))
    if window_y1 <= window_y0:
        window_y1 = min(h, window_y0 + 140)

    # 3) Dump XML
    try:
        xml = driver.dump_hierarchy()
    except Exception:
        xml = ""

    if not xml:
        # fallback tọa độ
        driver.click(int(w * 0.95), int((window_y0 + window_y1) / 2))
        await asyncio.sleep(0.9)
        return

    # 4) Scan candidates
    prefer_desc = ("tùy chọn", "tuy chon", "more options", "menu", "more")
    ban_text = ("xem tất cả", "xem tat ca")  # tránh bấm nhầm
    class_ok = ("android.widget.ImageView", "android.widget.Button")

    best = None  # (score, cx, cy, (l,t,r,b), meta)

    def score_node(l, t, r, b, cx, cy, meta):
        # ưu tiên: càng ở đầu window càng tốt + càng sát phải càng tốt + càng nhỏ càng tốt
        area = (r - l) * (b - t)
        base = (cy - window_y0) + (w - cx) * 0.03 + area * 0.00002

        # thưởng nếu có desc/text gợi ý overflow
        hint = (meta.get("desc", "") + " " + meta.get("text", "")).lower()
        if any(p in hint for p in prefer_desc):
            base -= 40  # ưu tiên mạnh
        # thưởng nếu resource-id có chữ menu/overflow/options
        rid = (meta.get("rid") or "").lower()
        if any(k in rid for k in ("overflow", "options", "menu", "more")):
            base -= 20
        return base

    for meta in _iter_nodes_from_xml(xml):
        cls = meta["cls"]
        if cls not in class_ok:
            continue

        txt_low = meta["text"].lower()
        desc_low = meta["desc"].lower()

        # loại các node chữ "xem tất cả"
        if any(x in txt_low for x in ban_text) or any(x in desc_low for x in ban_text):
            continue

        l, t, r, b = meta["bounds"]
        cx = (l + r) // 2
        cy = (t + b) // 2

        # phải nằm trong window
        if not (window_y0 <= cy <= window_y1):
            continue

        # phải nằm bên phải
        if cx < int(w * 0.78):
            continue

        bw = r - l
        bh = b - t

        # overflow icon thường nhỏ (lọc bỏ view to)
        if bw > int(w * 0.22) or bh > int(h * 0.22):
            continue

        # clickable nếu có (không bắt buộc vì nhiều icon nằm trong container clickable)
        # nếu bạn thấy miss nhiều, có thể bỏ điều kiện clickable hoàn toàn.
        # if not meta["clickable"]:
        #     continue

        sc = score_node(l, t, r, b, cx, cy, meta)

        if best is None or sc < best[0]:
            best = (sc, cx, cy, (l, t, r, b), meta)

    if best:
        _, cx, cy, _, meta = best
        driver.click(int(cx), int(cy))
        await asyncio.sleep(0.9)
        return

    # 5) Fallback tọa độ: bên phải, ngay dưới header
    driver.click(int(w * 0.95), int((window_y0 + window_y1) / 2))
    await asyncio.sleep(0.9)

async def copy_post_link_for_group_by_content_text(driver, content: str, detect_debug: dict | None = None):
    def _looks_like_facebook_url(s: str) -> bool:
        s = (s or "").strip().lower()
        return bool(s) and (
            s.startswith("http://")
            or s.startswith("https://")
            or "facebook.com" in s
            or "m.facebook.com" in s
            or "fb.watch" in s
            or "groups/" in s
        )

    async def _read_clipboard_url() -> str:
        txt = await get_clipboard_content_via_termux_adb_typing(
            driver,
            "com.facebook.katana",
            device_id=driver.serial
        )
        if not _looks_like_facebook_url(txt):
            raise RuntimeError(f"Clipboard không có link Facebook hợp lệ: {txt!r}")
        return txt.strip()

    info = detect_debug
    if not info or not info.get("match_bounds"):
        status, info = await detect_group_post_status_by_content_text(driver, content)
    else:
        status = info.get("status")

    if status not in ("pending", "posted"):
        raise RuntimeError(f"Bài viết không được duyệt (status={status})")

    # ===== attempt 1 =====
    await click_overflow_of_latest_post_in_status_section_xml(driver, status, detect_info=info)

    log_message(
        f"{[driver.serial]} - Bắt đầu tìm mục 'Sao chép liên kết' trong sheet (attempt 1)",
        logging.INFO,
    )

    clicked_copy = await click_copy_link_from_sheet(driver)
    if not clicked_copy:
        raise RuntimeError("Không tìm thấy hoặc không click được mục 'Sao chép liên kết'")

    await asyncio.sleep(1.5)

    txt = await get_clipboard_content_via_termux_adb_typing(
        driver,
        "com.facebook.katana",
        device_id=driver.serial
    )

    if _looks_like_facebook_url(txt):
        return txt.strip()

    log_message(
        f"{[driver.serial]} - Clipboard lần 1 chưa phải link Facebook: {txt!r}, thử lại lần 2",
        logging.WARNING,
    )

    # ===== attempt 2 =====
    await click_overflow_of_latest_post_in_status_section_xml(driver, status, detect_info=info)

    log_message(
        f"{[driver.serial]} - Bắt đầu tìm mục 'Sao chép liên kết' trong sheet (attempt 2)",
        logging.INFO,
    )

    clicked_copy = await click_copy_link_from_sheet(driver)
    if not clicked_copy:
        raise RuntimeError("Attempt 2: Không tìm thấy hoặc không click được mục 'Sao chép liên kết'")

    await asyncio.sleep(2.0)

    txt = await get_clipboard_content_via_termux_adb_typing(
        driver,
        "com.facebook.katana",
        device_id=driver.serial
    )

    if not _looks_like_facebook_url(txt):
        raise RuntimeError(f"Clipboard không chứa link Facebook hợp lệ sau 2 lần thử: {txt!r}")

    return txt.strip()

async def click_view_post_if_present(driver):
    for t in ("Xem bài viết", "Xem bài", "View post"):
        el = driver(textContains=t)
        if el.exists:
            el.click()
            await asyncio.sleep(1.5)
            return True
    return False

async def wait_published_snackbar(driver, timeout=300, poll=2.0, on_poll=None):
    """
    Đợi Facebook hiện snackbar/toast sau khi đăng bài.
    Return True nếu thấy tín hiệu 'đã đăng' hoặc 'xem bài viết', False nếu timeout.
    """
    start = time.time()
    round_idx = 0

    success_phrases = (
        "Đã đăng",
        "Đã chia sẻ",
        "Đăng bài thành công",
        "Post published",
        "Đã tham gia",
        "đã tham gia",
        "tham gia nhóm",
        "Tham gia nhóm",
        "Your post is now",
        "vừa xong",
    )
    view_phrases = (
        "Xem bài viết",
        "Xem bài",
        "View post",
    )

    while True:
        elapsed = time.time() - start
        if elapsed >= timeout:
            logging.warning(
                f"[WAIT_POST] ⏰ Timeout {timeout}s – không thấy snackbar"
            )
            return False

        round_idx += 1
        logging.info(
            f"[WAIT_POST] 🔍 Round {round_idx} | t={elapsed:.1f}s – đang quét snackbar…"
        )

        if on_poll:
            try:
                result = on_poll()
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logging.warning(f"[WAIT_POST] on_poll lỗi: {e}")

        # --- TEXT ---
        for p in success_phrases:
            if driver(textContains=p).exists:
                logging.info(
                    f"[WAIT_POST] ✅ FOUND text='{p}' tại t={elapsed:.1f}s"
                )
                return True

        for p in view_phrases:
            if driver(textContains=p).exists:
                logging.info(
                    f"[WAIT_POST] ✅ FOUND view='{p}' tại t={elapsed:.1f}s"
                )
                return True

        # --- DESCRIPTION ---
        for p in success_phrases:
            if driver(descriptionContains=p).exists:
                logging.info(
                    f"[WAIT_POST] ✅ FOUND desc='{p}' tại t={elapsed:.1f}s"
                )
                return True

        for p in view_phrases:
            if driver(descriptionContains=p).exists:
                logging.info(
                    f"[WAIT_POST] ✅ FOUND desc-view='{p}' tại t={elapsed:.1f}s"
                )
                return True

        logging.info(
            f"[WAIT_POST] ❌ Chưa thấy snackbar, sleep {poll}s"
        )
        await asyncio.sleep(poll)

# Lấy liên kết trang cá nhân
async def extract_facebook_user_link(driver):
    button = driver(description="Xem cài đặt khác của trang cá nhân")
    await asyncio.sleep(1)
    if button.exists:
        button.click()
    await click_template(driver, "copy_profile_link")
    link = await get_clipboard_content(driver, "com.facebook.katana")
    await asyncio.sleep(1)
    driver(resourceId="com.android.systemui:id/back").click()
    await asyncio.sleep(1)
    driver(resourceId="com.android.systemui:id/back").click()
    return link

# Trích xuất thời gian từ ảnh
def extract_time_from_image(image):
    reader = easyocr.Reader(['vi'])  
    results = reader.readtext(image) 

    ocr_texts = [text for _, text, _ in results]

    time_units = ['giờ', 'phút', 'ngày', 'tháng', 'năm']
    for i, word in enumerate(ocr_texts):
        # Trường hợp 1: "23 giờ" là một phần tử duy nhất
        match = re.match(r'(\d+)\s*(giờ|phút|ngày|tháng|năm)', word.lower())
        if match:
            num, unit = match.groups()
            return f"{num} {unit}"

        # Trường hợp 2: "23", "giờ" là hai phần tử liên tiếp
        if word.lower() in time_units and i > 0:
            prev = ocr_texts[i - 1]
            if prev.isdigit():
                return f"{prev} {word.lower()}"
    return None

# Trích xuất thông tin bình luận
async def extract_comment_info(driver, comment_node, raw_comments, links):
    # Trích xuất thông tin từ các nút con
    all_nodes = driver.xpath(comment_node + '//*').all()
    comment = None
    commenter_node = None
    link_nodes = []
    for child in all_nodes:
        info = child.info
        text = info.get("text", "").strip()
        desc = info.get("contentDescription", "").strip()
        className = info.get("className", "")
        
        # Tên người bình luận
        if child.info.get("index") == 1:
            commenter_node = child
        elif "ViewGroup" in className and desc:
            comment = desc
        elif "Button" in className and text:
            if text.startswith("#"):
                continue
            link_nodes.append((child, text))
    if commenter_node is None or comment is None:
        return None
    
    if comment in raw_comments:
        return {"name": raw_comments[comment], "comment": comment}

    # Lấy bounds và cắt ảnh
    bounds_str = driver.xpath(comment_node).get().attrib.get("bounds", "")
    bounds = parse_number(bounds_str)
    screen = driver.screenshot(format='opencv')
    screen = screen[bounds[1]:bounds[3], bounds[0]:bounds[2]]

    # Trích xuất thời gian từ ảnh
    time_texts = extract_time_from_image(screen)

    # Lấy tên và link người bình luận
    if commenter_node.info.get("text", "").strip() in links:
        name = links[commenter_node.info.get("text", "").strip()]
    else:
        commenter_node.click()
        driver(text="Xem trang cá nhân").click()
        link = await extract_facebook_user_link(driver)
        await asyncio.sleep(0.5)
        driver(resourceId="com.android.systemui:id/back").click()
        name = "<a href='" + link + "'>" + commenter_node.info.get("text", "").strip() + "</a>"
        links[commenter_node.info.get("text", "").strip()] = name

    # Lấy tên và link trong comment
    for child, text in link_nodes:
        if text in links:
            link = links[text]
            comment = comment.replace(text, link)
            continue
        child.click()
        link = extract_facebook_user_link(driver)
        comment = comment.replace(text, "<a href='" + link + "'>" + text + "</a>")
        links[text] = "<a href='" + link + "'>" + text + "</a>"
        
    return {
        "name": name,
        "comment": comment,
        "time": time_texts
    }

# Kiểm tra xem màn hình có thay đổi không
def is_screen_changed(driver, threshold=0.99):
    new_screenshot = driver.screenshot(format='opencv')
    if os.path.exists("Screen Shot\\" + driver.serial + ".png"):
        old_screenshot = cv2.imread("Screen Shot\\" + driver.serial + ".png")
        diff = cv2.absdiff(old_screenshot, new_screenshot)
        score = 1 - (np.sum(diff) / (old_screenshot.shape[0] * old_screenshot.shape[1] * 255))
        cv2.imwrite("Screen Shot\\" + driver.serial + ".png", new_screenshot)
        return score < threshold
    else:
        cv2.imwrite("Screen Shot\\" + driver.serial + ".png", new_screenshot)
        return True
    
async def expand_collapse_section(driver):
    while True:
        clicked = False
        buttons = [node for node in driver.xpath("//*").all() if node.info.get("className") == "android.widget.Button"]
        for button in buttons:
            btn_text = button.info.get("text", "") or ""
            btn_description = button.info.get("contentDescription", "") or ""
            if btn_text == "Xem thêm" or "câu trả lời" in btn_description:
                button.click()
                await asyncio.sleep(1)
                clicked = True
                break
        if not clicked:
            break

async def change_comment_display_mode(driver, base_mode="Phù hợp nhất", mode="Mới nhất"):
    if base_mode == mode:
        return
    while True:
        mode_selector = driver(textContains=base_mode)
        if mode_selector.exists:
            break
        driver.swipe_ext("up", scale=0.8)
        await asyncio.sleep(1)
    mode_selector.click()
    driver(descriptionContains=mode).click()
    await asyncio.sleep(1)
