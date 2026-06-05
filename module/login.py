import time
import asyncio
import logging
import random
import re
import unicodedata
import xml.etree.ElementTree as ET
import toolfacebook_lib
from util import *
import pymongo_management

ENABLE_FACEBOOK_LINK_SYNC = False  # Set True to re-enable syncing the current account profile link.

FACEBOOK_PACKAGE = "com.facebook.katana"
FACEBOOK_ACTIVITY = ".LoginActivity"
ZALO_PACKAGE = "com.zing.zalo"
ZALO_ACTIVITY = ".ui.SplashActivity"
ONEONEONE_PACKAGE = "com.cloudflare.onedotonedotonedotone"
ONEONEONE_ACTIVITY = "com.cloudflare.app.presentation.main.SplashActivity"
ONEONEONE_SWITCH_RESOURCE_ID = "com.cloudflare.onedotonedotonedotone:id/launchSwitch"
FACEBOOK_FEED_DEEPLINK = "fb://feed"
PRE_SWITCH_STOP_PACKAGES = (
    FACEBOOK_PACKAGE,
    ZALO_PACKAGE,
    ONEONEONE_PACKAGE,
)
UIAUTOMATOR_EXCLUDE_PACKAGES = (
    "com.github.uiautomator",
    "com.github.uiautomator.test",
    "com.github.uiautomator2",
)

# Xml Trình quản lý mật khẩu của Google
gg_password_locators = [
    ("resourceId","com.google.android.gms:id/title"),
    ("text", "Trình quản lý mật khẩu của Google"),
    ("text", "Chọn thông tin đăng nhập đã lưu cho Facebook")
]

# Helper cho quy trinh truoc khi chuyen tai khoan
def _device_label(driver):
    device_id = getattr(driver, "serial", "")
    try:
        return DEVICE_LIST_NAME.get(device_id, device_id)
    except Exception:
        return device_id


def _window_size(driver):
    size = driver.window_size()
    if isinstance(size, dict):
        return int(size.get("width", 0)), int(size.get("height", 0))
    return int(size[0]), int(size[1])


async def _start_app(driver, package_name, activity=None):
    try:
        if activity:
            await asyncio.to_thread(driver.app_start, package_name, activity)
        else:
            await asyncio.to_thread(driver.app_start, package_name)
    except TypeError:
        await asyncio.to_thread(driver.app_start, package_name)


async def _stop_known_apps(driver):
    for package_name in PRE_SWITCH_STOP_PACKAGES:
        try:
            await asyncio.to_thread(driver.app_stop, package_name)
            await asyncio.sleep(0.2)
        except Exception as e:
            log_message(
                f"[{_device_label(driver)}] app_stop({package_name}) loi: {e}",
                logging.WARNING,
            )


async def _close_apps_before_switch(driver):
    log_message(
        f"[{_device_label(driver)}] Pre-switch: thoat tat ca ung dung dang mo",
        logging.INFO,
    )
    app_stop_all = getattr(driver, "app_stop_all", None)
    if callable(app_stop_all):
        try:
            await asyncio.to_thread(
                app_stop_all,
                excludes=list(UIAUTOMATOR_EXCLUDE_PACKAGES),
            )
        except TypeError:
            try:
                await asyncio.to_thread(app_stop_all)
            except Exception as e:
                log_message(
                    f"[{_device_label(driver)}] app_stop_all loi: {e}",
                    logging.WARNING,
                )
        except Exception as e:
            log_message(
                f"[{_device_label(driver)}] app_stop_all loi: {e}",
                logging.WARNING,
            )

    await _stop_known_apps(driver)

    try:
        await asyncio.to_thread(driver.press, "home")
    except Exception:
        pass
    await asyncio.sleep(1.0)


async def _mark_all_facebook_accounts_offline(driver):
    device_id = getattr(driver, "serial", None)
    if not device_id:
        return

    try:
        set_offline(device_id)
    except Exception:
        pass

    try:
        result = await pymongo_management.update_statusFB(
            statusFB="Offline",
            device_id=device_id,
        )
        log_message(
            f"[{_device_label(driver)}] Pre-switch: set tat ca account FB Offline result={result}",
            logging.INFO,
        )
    except Exception as e:
        log_message(
            f"[{_device_label(driver)}] Pre-switch: set account FB Offline loi: {e}",
            logging.WARNING,
        )


async def _browse_zalo_randomly(driver, min_seconds=20, max_seconds=60):
    duration = random.randint(min_seconds, max_seconds)
    log_message(
        f"[{_device_label(driver)}] Pre-switch: mo Zalo va luot random {duration}s",
        logging.INFO,
    )

    try:
        await _start_app(driver, ZALO_PACKAGE, ZALO_ACTIVITY)
        await asyncio.sleep(random.uniform(4.0, 6.0))

        end_at = time.monotonic() + duration
        while time.monotonic() < end_at:
            width, height = _window_size(driver)
            if width <= 0 or height <= 0:
                await asyncio.sleep(2.0)
                continue

            x = int(width * random.uniform(0.42, 0.58))
            x2 = max(1, min(width - 1, x + random.randint(-35, 35)))
            if random.random() < 0.85:
                start_y = int(height * random.uniform(0.72, 0.86))
                end_y = int(height * random.uniform(0.20, 0.38))
            else:
                start_y = int(height * random.uniform(0.25, 0.40))
                end_y = int(height * random.uniform(0.65, 0.82))

            await asyncio.to_thread(
                driver.swipe,
                x,
                start_y,
                x2,
                end_y,
                random.uniform(0.18, 0.55),
            )

            remaining = end_at - time.monotonic()
            if remaining <= 0:
                break
            await asyncio.sleep(min(remaining, random.uniform(2.0, 5.0)))

    except Exception as e:
        log_message(
            f"[{_device_label(driver)}] Pre-switch: luot Zalo loi: {e}",
            logging.WARNING,
        )
    finally:
        try:
            await asyncio.to_thread(driver.press, "home")
        except Exception:
            pass
        try:
            await asyncio.to_thread(driver.app_stop, ZALO_PACKAGE)
        except Exception:
            pass
        await asyncio.sleep(1.0)


async def _find_1111_switch(driver, timeout=8.0):
    end_at = time.monotonic() + timeout
    while time.monotonic() < end_at:
        try:
            switch = driver(resourceId=ONEONEONE_SWITCH_RESOURCE_ID)
            if switch.exists:
                return switch
        except Exception:
            pass
        await asyncio.sleep(0.5)
    return None


async def _accept_1111_dialog_if_present(driver):
    for text in ("OK", "Ok", "Allow", "ALLOW"):
        try:
            button = driver(text=text)
            if button.exists:
                await asyncio.to_thread(button.click)
                await asyncio.sleep(1.5)
                return True
        except Exception:
            pass
    return False


async def _set_1111_vpn_state(driver, desired_on):
    switch = await _find_1111_switch(driver)
    if not switch:
        return False

    try:
        current_on = bool(switch.info.get("checked"))
    except Exception:
        current_on = False

    if current_on != desired_on:
        await asyncio.to_thread(switch.click)
        await asyncio.sleep(1.0)
        if desired_on:
            await _accept_1111_dialog_if_present(driver)
        await asyncio.sleep(1.5)

    switch = await _find_1111_switch(driver, timeout=3.0)
    if not switch:
        return False

    try:
        return bool(switch.info.get("checked")) == desired_on
    except Exception:
        return True


async def _toggle_1111_vpn_before_switch(driver, cycles=1):
    log_message(
        f"[{_device_label(driver)}] Pre-switch: bat/tat 1.1.1.1 {cycles} lan va giu bat",
        logging.INFO,
    )
    try:
        await _start_app(driver, ONEONEONE_PACKAGE, ONEONEONE_ACTIVITY)
        await asyncio.sleep(3.0)

        for i in range(cycles):
            await _set_1111_vpn_state(driver, False)
            await asyncio.sleep(random.uniform(1.0, 2.0))
            ok_on = await _set_1111_vpn_state(driver, True)
            log_message(
                f"[{_device_label(driver)}] Pre-switch: 1.1.1.1 cycle {i + 1}/{cycles} on={ok_on}",
                logging.INFO,
            )
            await asyncio.sleep(random.uniform(2.0, 3.0))

        await _set_1111_vpn_state(driver, True)
        await asyncio.sleep(1.0)
    except Exception as e:
        log_message(
            f"[{_device_label(driver)}] Pre-switch: toggle 1.1.1.1 loi: {e}",
            logging.WARNING,
        )


def _xpath_literal(value):
    value = str(value or "")
    if '"' not in value:
        return f'"{value}"'
    if "'" not in value:
        return f"'{value}'"
    parts = value.split('"')
    return "concat(" + ', \'"\', '.join(f'"{part}"' for part in parts) + ")"


def _account_name_variants(name):
    raw = str(name or "").strip()
    if not raw:
        return []

    variants = []
    for item in (raw, raw.replace("_", " "), " ".join(raw.split())):
        item = item.strip()
        if item and item not in variants:
            variants.append(item)
    return variants


def _normalize_account_name_for_match(value):
    value = str(value or "").replace("_", " ").strip().lower()
    if not value:
        return ""
    value = unicodedata.normalize("NFD", value)
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    value = re.sub(r"[^0-9a-zA-Z]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _account_name_tokens(value):
    normalized = _normalize_account_name_for_match(value)
    return [part for part in normalized.split(" ") if part]


def _normalized_contains_name(candidate, target):
    candidate_norm = _normalize_account_name_for_match(candidate)
    target_norm = _normalize_account_name_for_match(target)
    if not candidate_norm or not target_norm:
        return False

    start = candidate_norm.find(target_norm)
    while start >= 0:
        before_ok = start == 0 or not candidate_norm[start - 1].isalnum()
        end = start + len(target_norm)
        after_ok = end == len(candidate_norm) or not candidate_norm[end].isalnum()
        if before_ok and after_ok:
            return True
        start = candidate_norm.find(target_norm, start + 1)
    return False


def _names_match_fuzzy(candidate, target):
    if _normalized_contains_name(candidate, target):
        return True

    candidate_tokens = _account_name_tokens(candidate)
    target_tokens = _account_name_tokens(target)
    if len(candidate_tokens) >= 2 and len(candidate_tokens) == len(target_tokens):
        return sorted(candidate_tokens) == sorted(target_tokens)

    return False


def _parse_bounds(bounds):
    match = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", str(bounds or ""))
    if not match:
        return None
    left, top, right, bottom = map(int, match.groups())
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def _is_login_picker_non_account_label(value):
    normalized = _normalize_account_name_for_match(value)
    if not normalized:
        return True
    blocked = (
        "facebook from meta",
        "logo meta",
        "cai dat",
        "dung trang ca nhan khac",
        "tao tai khoan moi",
    )
    return any(item in normalized for item in blocked)


async def _click_facebook_account_candidate_from_dump(driver, account_name):
    try:
        xml = await asyncio.to_thread(driver.dump_hierarchy)
        root = ET.fromstring(xml)
    except Exception as e:
        log_message(
            f"[{_device_label(driver)}] Dump login picker loi khi tim '{account_name}': {e}",
            logging.WARNING,
        )
        return None

    matches = []
    for node in root.iter("node"):
        if str(node.attrib.get("clickable", "")).lower() != "true":
            continue

        text = node.attrib.get("text") or ""
        desc = node.attrib.get("content-desc") or ""
        candidate_text = desc or text
        if _is_login_picker_non_account_label(candidate_text):
            continue
        if not _names_match_fuzzy(candidate_text, account_name):
            continue

        bounds = _parse_bounds(node.attrib.get("bounds"))
        if bounds:
            matches.append((bounds, candidate_text))

    if not matches:
        return None

    bounds, candidate_text = sorted(matches, key=lambda item: (item[0][1], item[0][0]))[0]
    left, top, right, bottom = bounds
    try:
        await asyncio.to_thread(driver.click, int((left + right) / 2), int((top + bottom) / 2))
        await asyncio.sleep(4.0)
        log_message(
            f"[{_device_label(driver)}] Da chon account Facebook tren login picker: target='{account_name}', ui='{candidate_text}'",
            logging.INFO,
        )
        return str(candidate_text).split(",")[0].strip() or account_name
    except Exception as e:
        log_message(
            f"[{_device_label(driver)}] Click account picker theo dump '{account_name}' loi: {e}",
            logging.WARNING,
        )
        return None


async def is_facebook_account_picker_visible(driver):
    markers = [
        '//*[contains(@content-desc, "Dùng trang cá nhân khác")]',
        '//*[contains(@content-desc, "Tạo tài khoản mới")]',
        '//*[contains(@content-desc, "Facebook from Meta")]',
    ]
    found = 0
    for xpath in markers:
        try:
            if driver.xpath(xpath).exists:
                found += 1
        except Exception:
            pass
    if found >= 2:
        return True

    try:
        xml = await asyncio.to_thread(driver.dump_hierarchy)
        root = ET.fromstring(xml)
        values = []
        for node in root.iter("node"):
            values.append(node.attrib.get("text") or "")
            values.append(node.attrib.get("content-desc") or "")
        haystack = _normalize_account_name_for_match(" ".join(values))
        normalized_markers = (
            "dung trang ca nhan khac",
            "tao tai khoan moi",
            "facebook from meta",
        )
        return sum(1 for marker in normalized_markers if marker in haystack) >= 2
    except Exception:
        return False


async def _light_scroll_facebook_account_picker(driver, direction="up"):
    try:
        width, height = _window_size(driver)
        x = int(width * 0.5)
        if direction == "down":
            start_y = int(height * 0.42)
            end_y = int(height * 0.72)
        else:
            start_y = int(height * 0.72)
            end_y = int(height * 0.42)
        await asyncio.to_thread(driver.swipe, x, start_y, x, end_y, 0.25)
        await asyncio.sleep(1.0)
    except Exception as e:
        log_message(
            f"[{_device_label(driver)}] Login picker scroll loi: {e}",
            logging.WARNING,
        )


async def _reset_facebook_account_picker_to_top(driver, max_scrolls=4):
    for _ in range(max_scrolls):
        await _light_scroll_facebook_account_picker(driver, direction="down")


async def _click_facebook_account_candidate(driver, account_name):
    for name in _account_name_variants(account_name):
        name_literal = _xpath_literal(name)
        xpaths = [
            (
                f'//android.view.ViewGroup[contains(@content-desc, {name_literal}) '
                f'and @clickable="true"]'
            ),
            (
                f'//*[@text={name_literal}]/ancestor::android.view.ViewGroup'
                f'[@clickable="true"][1]'
            ),
        ]

        for xpath in xpaths:
            try:
                selector = driver.xpath(xpath)
                if selector.exists:
                    selector.click()
                    await asyncio.sleep(4.0)
                    log_message(
                        f"[{_device_label(driver)}] Da chon account Facebook tren login picker: {name}",
                        logging.INFO,
                    )
                    return name
            except Exception as e:
                log_message(
                    f"[{_device_label(driver)}] Click account picker '{name}' loi: {e}",
                    logging.WARNING,
                )
    return await _click_facebook_account_candidate_from_dump(driver, account_name)


async def select_facebook_account_from_picker(driver, account_names, max_scrolls=4):
    """
    Return:
    - selected account name string: picker visible and account clicked
    - False: picker visible but no requested account was found after scrolling
    - None: current screen is not the Facebook account picker
    """
    if isinstance(account_names, str):
        account_names = [account_names]

    account_names = [
        str(name or "").strip()
        for name in (account_names or [])
        if str(name or "").strip()
    ]
    if not account_names:
        return False if await is_facebook_account_picker_visible(driver) else None

    if not await is_facebook_account_picker_visible(driver):
        return None

    for name_index, account_name in enumerate(account_names):
        if name_index > 0:
            await _reset_facebook_account_picker_to_top(driver, max_scrolls=max_scrolls)

        for attempt in range(max_scrolls + 1):
            selected = await _click_facebook_account_candidate(driver, account_name)
            if selected:
                return selected

            if attempt < max_scrolls:
                await _light_scroll_facebook_account_picker(driver, direction="up")

    return False


async def _quick_find_element(driver, locators, timeout=1.0):
    for method, value in locators:
        try:
            if method == "xpath":
                element = driver.xpath(value)
            elif method == "text":
                element = driver(text=value)
            elif method == "desc":
                element = driver(description=value)
            elif method == "resourceId":
                element = driver(resourceId=value)
            elif method == "className":
                element = driver(className=value)
            else:
                continue

            if await asyncio.to_thread(element.wait, timeout=timeout):
                return element
        except Exception:
            continue
    return None


async def _find_facebook_password_input(driver, timeout=1.0):
    password_input = await _quick_find_element(
        driver,
        [
            ("xpath", '//android.widget.EditText[@password="true"]'),
            ("xpath", '//android.widget.EditText[contains(@content-desc, "Mật khẩu")]'),
            ("xpath", '//android.widget.EditText[contains(@content-desc, "mat khau")]'),
        ],
        timeout=timeout,
    )
    if password_input:
        return password_input

    try:
        fields = my_find_elements(driver, {("className", "android.widget.EditText")})
        if len(fields) == 1:
            return fields[0]
    except Exception:
        pass
    return None


async def _find_facebook_login_button(driver, timeout=1.0):
    return await _quick_find_element(
        driver,
        [
            ("xpath", '//android.widget.Button[@content-desc="Đăng nhập"]'),
            ("xpath", '//*[contains(@content-desc, "Đăng nhập") and @clickable="true"]'),
            ("xpath", '//*[@text="Đăng nhập"]'),
        ],
        timeout=timeout,
    )


async def submit_facebook_password_if_prompted(driver, password, account_name="", timeout=6.0):
    """
    Return:
    - True: password prompt was visible and password was submitted
    - False: password prompt was visible but could not be handled
    - None: current screen is not the password prompt
    """
    end_time = time.time() + max(1.0, float(timeout or 6.0))
    password_input = None
    login_button = None

    while time.time() < end_time:
        password_input = await _find_facebook_password_input(driver, timeout=0.6)
        login_button = await _find_facebook_login_button(driver, timeout=0.6)
        if password_input or login_button:
            break
        await asyncio.sleep(0.3)

    if not password_input and not login_button:
        return None

    password = str(password or "")
    if not password:
        log_message(
            f"[{_device_label(driver)}] Facebook password prompt hien nhung base khong co password cho account {account_name or 'unknown'}",
            logging.ERROR,
        )
        return False

    if not password_input:
        password_input = await _find_facebook_password_input(driver, timeout=1.0)
    if not password_input:
        log_message(
            f"[{_device_label(driver)}] Khong tim duoc o nhap mat khau Facebook cho account {account_name or 'unknown'}",
            logging.ERROR,
        )
        return False

    try:
        await asyncio.to_thread(password_input.click)
    except Exception:
        pass
    await asyncio.sleep(0.3)

    try:
        await asyncio.to_thread(password_input.set_text, password)
    except Exception:
        try:
            await asyncio.to_thread(driver.send_keys, password, clear=True)
        except Exception as e:
            log_message(
                f"[{_device_label(driver)}] Nhap mat khau Facebook loi cho account {account_name or 'unknown'}: {e}",
                logging.ERROR,
            )
            return False

    await asyncio.sleep(0.5)
    login_button = await _find_facebook_login_button(driver, timeout=2.0)
    if not login_button:
        log_message(
            f"[{_device_label(driver)}] Da nhap mat khau nhung khong tim duoc nut Dang nhap",
            logging.ERROR,
        )
        return False

    try:
        await asyncio.to_thread(login_button.click)
        log_message(
            f"[{_device_label(driver)}] Da nhap mat khau va bam Dang nhap cho account {account_name or 'unknown'}",
            logging.INFO,
        )
        await asyncio.sleep(6)
        return True
    except Exception as e:
        log_message(
            f"[{_device_label(driver)}] Bam Dang nhap sau khi nhap mat khau loi: {e}",
            logging.ERROR,
        )
        return False


async def prepare_environment_before_swap_account(driver):
    await _close_apps_before_switch(driver)
    await _mark_all_facebook_accounts_offline(driver)
    await _browse_zalo_randomly(driver, min_seconds=20, max_seconds=60)
    await _toggle_1111_vpn_before_switch(driver, cycles=1)

    try:
        await _start_app(driver, FACEBOOK_PACKAGE, FACEBOOK_ACTIVITY)
        await asyncio.sleep(random.uniform(4.0, 6.0))
    except Exception as e:
        log_message(
            f"[{_device_label(driver)}] Pre-switch: mo Facebook loi: {e}",
            logging.WARNING,
        )


# Hàm đăng xuất
async def log_out(driver):
    """
    Đăng xuất tài khoản khỏi thiết bị
    """

    # await go_to_home_page(driver)

    log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Đăng xuất")
    menu = await my_find_element(driver, {("xpath", '//*[contains(@content-desc, "Menu")]')})
    try:
        menu.click()
    except Exception:
        log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Không tìm được nút menu", logging.ERROR)
        return
    # Đợi chuyển sang tab menu
    log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Trạng thái: Menu")
    await asyncio.sleep(2)
    safe_flag = 5
    log_out = await my_find_element(driver, {("xpath", '//android.widget.Button[@content-desc="Đăng xuất"]')}, safe_flag, True)
    if log_out == None:
        log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Không tìm được nút đăng xuất sau {safe_flag} lần thử",logging.ERROR)
        # await go_to_home_page(driver)
        return
    log_out.click()
    log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Đang đăng xuất")

    # Xác nhận lưu tài khoản(nếu có)
    save = await my_find_element(driver, {("text", "LƯU")}, 2)
    if save:
        save.click()
        log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Đã lưu tài khoản")

    # Xác nhận đăng xuất
    xac_nhan = await my_find_element(driver, {("text", "ĐĂNG XUẤT")}, 2)   
    if xac_nhan == None:
        log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Không tìm thấy box xác nhận đăng xuất", logging.ERROR)
        return
    else:
        xac_nhan.click()
    # Đợi load trang chọn tài khoản
    log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Đăng xuất thành công")
    await pymongo_management.update_statusFB(statusFB="Offline", device_id=driver.serial)
    return True
# Đăng nhập lần đầu
async def login_facebook(driver, acc):
    """
    Đăng nhập và lưu tài khoản vào ứng dụng Facebook trên Android
    
    """
    # Thao tác đăng nhập
    account = acc['account']
    password = acc['password']

    # Tìm nút chuyển trang cá nhân
    while swap := await my_find_element(driver, {("text", "Dùng trang cá nhân khác"), ("text", "Đăng nhập bằng tài khoản khác")}):
        swap.click()
    await asyncio.sleep(6)
    # Tắt box quản lý mật khẩu của google
    gg_password = await my_find_element(driver, {("text", "Trình quản lý mật khẩu của Google")})
    # cập nhật locator ngày 6/11/2025
    gg_password1 = await my_find_element(driver, {("text", "Chọn thông tin đăng nhập đã lưu cho Facebook")})

    if gg_password or gg_password1:
        log_message("Tắt trình quản lý mật khẩu")
        await asyncio.to_thread(driver.press, "back")
    await asyncio.sleep(6)
    # Tìm nút đăng nhập, nếu thấy thì mới có thể tìm được ô nhập text
    login_button = await my_find_element(driver, {("xpath", '//android.widget.Button[@content-desc="Đăng nhập"]')})
    if login_button:
        # Tìm tất cả ô nhập text có thể tương tác
        input_fields = my_find_elements(driver, {("className", 'android.widget.EditText')})
        try:
            input_fields[0].set_text(account)  # Nhập số điện thoại
            input_fields[1].set_text(password)  # Nhập mật khẩu
        except Exception:
            log_message("Không tìm được ô text", logging.ERROR)
            return False
        
        login_button.click()
        log_message("Đang đăng nhập")
        await asyncio.sleep(10)
    else:
        log_message("Không tìm được nút login", logging.ERROR)
        return False
    # Kiểm tra có đăng nhập thành công không
    cant_login = await my_find_element(driver, {("text", "Không thể đăng nhập")})
    if cant_login:
        log_message("Đăng nhập thất bại do lỗi không thể đăng nhập", logging.ERROR)
        await asyncio.to_thread(driver.press, "back")
        return False
    # Kiểm tra có yêu cầu lưu tài khoản không
    save = await my_find_element(driver, {("text", "Lưu")})
    try:
        save.click()
        log_message("Lưu tài khoản")
        await asyncio.sleep(3)
        # Kiểm tra có yêu cầu lưu mật khẩu vào trình quản lý mật khẩu không
        gg_save = await my_find_element(driver, {("text", "Trình quản lý mật khẩu của Google")})
        if gg_save:
            tiep = await my_find_element(driver, {("text", "Tiếp tục")})
            tiep.click()
            log_message("Đã lưu mk vào tk gg")
            await asyncio.sleep(6)
    except Exception:
        log_message("Không thấy box lưu tài khoản", logging.WARNING)
    
    # Kiểm tra có yêu cầu quyền gì không
    while skip := await my_find_element(driver, {("text", "Bỏ qua")}):
        skip.click()
        log_message("Bỏ qua")
        await asyncio.sleep(3)
        check_skip = await my_find_element(driver, {("text", "BỎ QUA")})
        if check_skip != None:
            check_skip.click()
            await asyncio.sleep(3)

    # Đợi load trang chủ
    await asyncio.sleep(20)
    log_message("Đăng nhập thành công")
    # await pymongo_management.update_statusFB(account, "Online")
    await pymongo_management.update_statusFB(
        account=account,
        statusFB="Online",
        device_id=driver.serial,
    )
    return True

async def wait_menu_transition(driver, timeout=5.0, poll=0.3):
    end_time = time.time() + timeout
    while time.time() < end_time:
        switcher = await my_find_element(
            driver,
            {("xpath", '//*[contains(@content-desc, "Hãy mở công cụ chuyển trang cá nhân")]')},
            max_retries=1,
            back_if_not_found=False
        )
        if switcher:
            return True
        await asyncio.sleep(poll)
    return False

async def find_switcher_entry(driver):
    switch_acc = await my_find_element(
        driver,
        {("xpath", '//*[contains(@content-desc, "Hãy mở công cụ chuyển trang cá nhân")]')},
        max_retries=2,
        back_if_not_found=False
    )
    if not switch_acc:
        switch_acc = await my_find_element(
            driver,
            {("xpath", '//androidx.viewpager.widget.ViewPager/android.widget.FrameLayout[1]/android.widget.FrameLayout[1]/android.widget.FrameLayout[1]/android.view.ViewGroup[1]/android.view.ViewGroup[1]/android.view.ViewGroup[1]/androidx.recyclerview.widget.RecyclerView[1]/android.view.ViewGroup[1]/android.view.ViewGroup[1]')},
            max_retries=3,
            back_if_not_found=False
        )
    return switch_acc


async def open_menu_and_find_switcher(driver, wait_timeout=5.0):
    menu = await my_find_element(
        driver,
        {("xpath", '//*[contains(@content-desc, "Menu")]')},
        max_retries=3,
        back_if_not_found=False
    )
    if not menu:
        return None

    try:
        menu.click()
    except Exception:
        return None

    await asyncio.sleep(2)
    await wait_menu_transition(driver, timeout=wait_timeout)
    return await find_switcher_entry(driver)

async def fallback_to_profile_then_open_switcher(driver):
    ok = await go_to_profile_area_for_switch(driver)
    if not ok:
        return None

    await asyncio.sleep(2)

    # sau khi vào trang cá nhân, bấm lại menu như flow gốc
    switch_acc = await open_menu_and_find_switcher(driver, wait_timeout=5.0)
    return switch_acc


async def open_facebook_feed_deeplink(driver):
    try:
        log_message(
            f"[{_device_label(driver)}] Switch fallback: open {FACEBOOK_FEED_DEEPLINK}",
            logging.INFO,
        )
        await asyncio.to_thread(
            driver.shell,
            f"am start -a android.intent.action.VIEW -d {FACEBOOK_FEED_DEEPLINK}",
        )
        await asyncio.sleep(4.0)
        return True
    except Exception as e:
        log_message(
            f"[{_device_label(driver)}] Switch fallback: open {FACEBOOK_FEED_DEEPLINK} loi: {e}",
            logging.WARNING,
        )
        return False


async def fallback_feed_then_open_switcher(driver):
    ok = await open_facebook_feed_deeplink(driver)
    if not ok:
        return None

    switch_acc = await open_menu_and_find_switcher(driver, wait_timeout=5.0)
    if switch_acc:
        return switch_acc

    log_message(
        f"[{_device_label(driver)}] Switch fallback: da mo feed nhung chua thay switcher, thu qua trang ca nhan",
        logging.WARNING,
    )
    return await fallback_to_profile_then_open_switcher(driver)

async def check_logged_in(driver):
    """
    Kiểm tra trạng thái đăng nhập của tài khoản trên thiết bị
    
    """
    # Mở ứng dụng Facebook
    # driver.app_start("com.facebook.katana")
    # home = await my_find_element(driver, {("xpath", '//android.widget.Button[@content-desc="Đi tới trang cá nhân"]')}, 10, back_if_not_found=False)
    home = await my_find_element(driver, {("xpath", '//*[contains(@content-desc,"Trang chủ") and contains(@content-desc,"Tab")]')}, 2, back_if_not_found=False)
    home_1 = await my_find_element(driver, {("xpath", '//android.widget.FrameLayout[1]/android.widget.LinearLayout[1]/android.widget.FrameLayout[1]/android.widget.FrameLayout[1]/android.widget.LinearLayout[1]/android.widget.FrameLayout[1]/android.widget.LinearLayout[1]/android.widget.LinearLayout[1]/android.widget.FrameLayout[1]/android.widget.LinearLayout[1]/android.widget.LinearLayout[1]/android.view.View[1]')}, 2, back_if_not_found=False)
    home_2 = await my_find_element(driver, {("xpath", '//android.widget.FrameLayout[3]/android.widget.LinearLayout[1]/android.widget.FrameLayout[1]/android.widget.FrameLayout[1]/android.widget.LinearLayout[1]/android.widget.FrameLayout[1]/android.widget.LinearLayout[1]/android.widget.LinearLayout[1]/android.widget.FrameLayout[2]/android.widget.LinearLayout[1]/android.widget.FrameLayout[1]')}, 2, back_if_not_found=False)
    if home or home_1 or home_2:
        return True
    else:
        return False

async def wait_logged_in(driver, timeout=12.0, poll=0.5):
    end_time = time.time() + timeout
    while time.time() < end_time:
        if await check_logged_in(driver):
            return True
        await asyncio.sleep(poll)
    return False


async def go_to_profile_area_for_switch(driver):
    # Ưu tiên 1: Đi tới trang cá nhân
    goto_profile = await my_find_element(
        driver,
        {("xpath", '//*[contains(@content-desc, "Đi tới trang cá nhân")]')},
        max_retries=2,
        back_if_not_found=False
    )
    if goto_profile:
        try:
            goto_profile.click()
            await asyncio.sleep(2)
            return True
        except Exception:
            pass

    # Ưu tiên 2: Tab Trang cá nhân
    profile_tab = await my_find_element(
        driver,
        {("xpath", '//*[contains(@content-desc, "Trang cá nhân") and contains(@content-desc, "Tab")]')},
        max_retries=2,
        back_if_not_found=False
    )
    if profile_tab:
        try:
            profile_tab.click()
            await asyncio.sleep(2)
            return True
        except Exception:
            pass

    return False

async def check_facebook_link(driver):
    if not ENABLE_FACEBOOK_LINK_SYNC:
        return
    if await pymongo_management.check_facebook_link(driver.serial):
        return
    log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Cập nhật link facebook")
    # (await go_to_home_page(driver)).click()

    # Bấm vào menu
    menu = await my_find_element(driver, {("xpath", '//*[contains(@content-desc, "Menu")]')})
    try:
        menu.click()
    except Exception:
        log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Không tìm được nút menu", logging.ERROR)
        return
    # Đợi chuyển sang tab menu
    log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Trạng thái: Menu")
    await asyncio.sleep(2)

    # bấm vào trang cá nhân ở menu
    try:
        user = await my_find_element(driver, {("xpath", '//androidx.recyclerview.widget.RecyclerView/android.view.ViewGroup[1]/android.view.ViewGroup[1]')}, 2, back_if_not_found=False)
        user.click()
    except Exception as e:
        log_message(f"máy {DEVICE_LIST_NAME[driver.serial]}: lỗi khi bấm vào trang cá nhân {type(e).__name__}\n {e}", logging.ERROR)
        return False
    # # Bấm vào đi tới trang cá nhân ở trang chủ
    # try:
    #     user = await my_find_element(driver, {("xpath", '//*[@resource-id="android:id/list"]/android.view.ViewGroup[1]/android.view.ViewGroup[1]/android.view.ViewGroup[1]')}, 2, back_if_not_found=False)
    #     user.click()
    # except Exception as e:
    #     log_message(f"máy {DEVICE_LIST_NAME[driver.serial]}: lỗi khi bấm vào trang cá nhân {type(e).__name__}\n {e}", logging.ERROR)
    #     return False
    try:
        driver(description="Xem cài đặt khác của trang cá nhân").click()
    except Exception as e:
        log_message(f"máy {DEVICE_LIST_NAME[driver.serial]}: lỗi khi bấm Xem cài đặt khác của trang cá nhân {type(e).__name__}\n {e}", logging.ERROR)
        return False
    await asyncio.sleep(3)
    await asyncio.to_thread(driver.swipe_ext, "up", scale=0.8)
    
    await toolfacebook_lib.click_template(driver, "copy_profile_link")
    link = await toolfacebook_lib.get_clipboard_content(driver, "com.facebook.katana")
    await pymongo_management.update_facebook_link(driver.serial, link)
    log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Đã cập nhật link facebook: {link}")

    # bấm back 2 lần để sau đó đi tới menu 
    await asyncio.to_thread(driver.press, "back")
    await asyncio.to_thread(driver.press, "back")

# Hàm đăng nhập vào tài khoản đã lưu
async def swap_account(driver, acc):
    
    """
    Đăng nhập vào tài khoản facebook đã lưu sẵn
    
    """
    # Mở ứng dụng Facebook
    # driver.app_start("com.facebook.katana")

    # Lấy thông tin tài khoản
    name = acc['name']
    username = acc['account']
    password = acc['password']

    # Đăng xuất nếu đã đăng nhập
    if await check_logged_in(driver): # kiểm tra đã đăng nhập 
        try:
            await log_out(driver) 
        except Exception as e:
            log_message(f"DEVICE_LIST_NAME[driver.serial] lỗi đăng xuất", logging.ERROR)
            return False
    await asyncio.to_thread(driver.app_start, "com.facebook.katana")
    # Tắt box quản lý mật khẩu của google
    gg_password = await my_find_element(driver, {("text", "Trình quản lý mật khẩu của Google")},1)
    # cập nhật locator ngày 6/11/2025
    gg_password1 = await my_find_element(driver, {("text", "Chọn thông tin đăng nhập đã lưu cho Facebook")},1)

    if gg_password or gg_password1:
        log_message("Tắt trình quản lý mật khẩu")
        await asyncio.to_thread(driver.press, "back")
        await asyncio.sleep(3)
    # Đăng nhập
    log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Bắt đầu đăng nhập vào tài khoản {name}")
    account = await my_find_element(driver, {("xpath", f'//android.view.View[@content-desc="{name}"]')}, 2, nature_scroll_if_not_found=True)
    if account == None:
        log_message(f"{DEVICE_LIST_NAME[driver.serial]}không tìm được nút đăng nhập vào tài khoản {name}")
        login_button = await my_find_element(driver, {("xpath", '//*[@text="Đăng nhập"]')},1)
        login_button1 = await my_find_element(driver, {("xpath", '//*[@text="Dùng trang cá nhân khác"]')},1)
        if (not login_button and login_button1) or (login_button and login_button1):
            login_button1.click()
            await asyncio.sleep(3)
            login_button = await my_find_element(driver, {("xpath", '//*[@text="Đăng nhập"]')},1)
            await asyncio.sleep(3)
        if login_button:
            # Tắt box quản lý mật khẩu của google
            gg_password = await my_find_element(driver, {("text", "Trình quản lý mật khẩu của Google")},1)
            # cập nhật locator ngày 6/11/2025
            gg_password1 = await my_find_element(driver, {("text", "Chọn thông tin đăng nhập đã lưu cho Facebook")},1)

            if gg_password or gg_password1:
                log_message("Tắt trình quản lý mật khẩu")
                await asyncio.to_thread(driver.press, "back")
                await asyncio.sleep(3)
        # Tìm tất cả ô nhập text có thể tương tác
            input_fields = my_find_elements(driver, {("className", 'android.widget.EditText')})
            try:
                input_fields[0].set_text(username)  # Nhập số điện thoại
                input_fields[1].set_text(password)  # Nhập mật khẩu
            except Exception:
                log_message("Không tìm được ô text", logging.ERROR)
                return False
            login_button.click()
            log_message("Đang đăng nhập")
            await asyncio.sleep(10)
        else:
            log_message("Không tìm được nút login", logging.ERROR)
            await asyncio.to_thread(driver.press, "back")
            return False

    else:
        log_message(f"{DEVICE_LIST_NAME[driver.serial]}đã tìm được nút đăng nhập vào tài khoản {name}")
        try:
            account.click()
        except Exception:
            log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Không thể đăng nhập", logging.ERROR)
            return False
        
    # Tắt box quản lý mật khẩu của google
    gg_password = await my_find_element(driver, {("text", "Trình quản lý mật khẩu của Google")},1)
    # cập nhật locator ngày 6/11/2025
    gg_password1 = await my_find_element(driver, {("text", "Chọn thông tin đăng nhập đã lưu cho Facebook")},1)

    if gg_password or gg_password1:
        log_message("Tắt trình quản lý mật khẩu")
        await asyncio.to_thread(driver.press, "back")
        await asyncio.sleep(3)
    # Tìm xem có bắt nhập mật khẩu lại không
    login = await my_find_element(driver, {("xpath", '//android.widget.Button[@content-desc="Đăng nhập"]')},1)
    if login != None:
        await asyncio.sleep(3)
        await asyncio.to_thread(driver.send_keys, password)
        login.click()

    # Kiểm tra có yêu cầu lưu tài khoản không
    save = await my_find_element(driver, {("text", "Lưu")},1)
    try:
        save.click()
        log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Đã lưu tài khoản")
        await asyncio.sleep(3)
    except Exception:
        pass

    # Kiểm tra có yêu cầu quyền gì không
    while True:
        skip = await my_find_element(driver, {("text", "Bỏ qua")}, 1)
        if skip is not None:
            skip.click()
        else:
            break
    
    # Đợi vào màn hình chính
    if await check_logged_in(driver):
        log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Đăng nhập thành công vào tài khoản {name}")
        await pymongo_management.update_statusFB(
            username=name,
            account=username,
            statusFB="Online",
            device_id=driver.serial,
        )
        try:
            set_online_account(driver.serial, account=username, username=name)
        except Exception:
            pass
        await check_facebook_link(driver)
        return True
    else:
        log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Đăng nhập thất bại vào tài khoản {name}", logging.ERROR)
        await pymongo_management.update_statusFB(
            username=name,
            account=username,
            statusFB="Crash",
            device_id=driver.serial,
        )
        await asyncio.to_thread(driver.press, "back")
        await asyncio.to_thread(driver.press, "back")
        return False
    

# async def swap_account_new(driver, acc):
#     """
#     Đăng nhập vào tài khoản facebook đã lưu sẵn
    
#     """
#     # Mở ứng dụng Facebook
#     # driver.app_start("com.facebook.katana")

#     # Lấy thông tin tài khoản
#     name = acc['name']
#     username = acc['account']

#     # bấm vào menu
#     menu = await my_find_element(driver, {("xpath", '//*[contains(@content-desc, "Menu")]')})
#     try:
#         menu.click()
#     except Exception:
#         log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Không tìm được nút menu", logging.ERROR)
#         return
#     await asyncio.sleep(2)
#     # bấm vào chuyển tài khoản 
#     switch_acc = await my_find_element(driver, {("xpath", '//*[contains(@content-desc, "Hãy mở công cụ chuyển trang cá nhân")]')}, 3)
#     if not switch_acc:
#         switch_acc = await my_find_element(driver, {("xpath", '//androidx.viewpager.widget.ViewPager/android.widget.FrameLayout[1]/android.widget.FrameLayout[1]/android.widget.FrameLayout[1]/android.view.ViewGroup[1]/android.view.ViewGroup[1]/android.view.ViewGroup[1]/androidx.recyclerview.widget.RecyclerView[1]/android.view.ViewGroup[1]/android.view.ViewGroup[1]')},3)
#     try:
#         switch_acc.click()
#     except Exception:
#         log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Không tìm được nút chuyển trang cá nhân", logging.ERROR)
#         return
#     await asyncio.sleep(2)
#     # bấm vào tài khoản khác
#     other_acc_locator = [
#         ("xpath", '//*[contains(@text, "Tài khoản khác")]'),
#         ("xpath", '//*[@content-desc="Xem tất cả"]'),
#         ("xpath", '//*[@text="Xem tất cả"]'),
#         ("text","Xem tất cả")
#     ]
#     other_acc = await my_find_element(driver, other_acc_locator,3)
#     try:
#         other_acc.click()
#     except Exception:
#         log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Không tìm được nút tài khoản khác", logging.ERROR)
#         await asyncio.to_thread(driver.press, "back")
#         return
#     await asyncio.sleep(2)
#     # chọn tài khoản đăng nhập
#     log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Bắt đầu đăng nhập vào tài khoản {name}")
#     account = await my_find_element(driver, {("xpath", f'//*[@text="{name}"]')},3)
#     if account == None:
#         log_message(f"{DEVICE_LIST_NAME[driver.serial]}không tìm được nút đăng nhập vào tài khoản {name}")
#         await asyncio.to_thread(driver.press, "back")
#         await asyncio.to_thread(driver.press, "back")
#         return False
#     else:
#         log_message(f"{DEVICE_LIST_NAME[driver.serial]}đã tìm được nút đăng nhập vào tài khoản {name}")
#         try:
#             account.click()
#         except Exception:
#             log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Không thể đăng nhập", logging.ERROR)
#             return False
#     await asyncio.sleep(2)    
#     # Đợi vào màn hình chính
#     if await check_logged_in(driver):
#         log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Đăng nhập thành công vào tài khoản {name}",3)
#         await pymongo_management.update_statusFB(driver.serial, "Offline")
#         await pymongo_management.update_statusFB(username, "Online", device_id=driver.serial)
#         await check_facebook_link(driver)
#         return True
#     else:
#         log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Đăng nhập thất bại vào tài khoản {name}", logging.ERROR)
#         if not await my_find_element(driver, {("xpath", f'//*[@text="{name}"]')},3):
#             await pymongo_management.update_statusFB(driver.serial, "Offline")
#             await pymongo_management.update_statusFB(username, "Crash", device_id=driver.serial)
#             note = """Phase Facebook: Có thể gặp lỗi trong quá trình tự động đăng nhập tài khoản :
#                     Thực hiện các bước sau:
#                     - Đăng nhập vào tài khoản gặp lỗi
#                     - Kiểm tra tài khoản mật khẩu đã đúng chưa
#                     - Sửa DB nếu tk+mk không khớp """
#             try:
#                 log_note_acc(driver, username, note)
#             except Exception as e:
#                 log_message("Lỗi khi lưu file log note")
#                 pass
#         await asyncio.to_thread(driver.press, "back")
#         await asyncio.to_thread(driver.press, "back")
#         return False    

async def find_other_account_button(driver, total_timeout=4.0, poll=0.35):
    """
    Tìm nút 'Tài khoản khác' hoặc 'Xem tất cả' cho nhiều kiểu UI Facebook.
    Ưu tiên node Button vì thường clickable ổn hơn node text con.
    """
    xpaths = [
        '//android.widget.Button[contains(@content-desc, "Tài khoản khác")]',
        '//android.widget.Button[contains(@content-desc, "Xem tất cả")]',
        '//*[contains(@content-desc, "Tài khoản khác")]',
        '//*[contains(@content-desc, "Xem tất cả")]',
        '//*[@text="Tài khoản khác"]',
        '//*[@text="Xem tất cả"]',
    ]

    end_time = time.time() + total_timeout

    while time.time() < end_time:
        for xp in xpaths:
            try:
                el = driver.xpath(xp)
                found = await asyncio.to_thread(el.wait, timeout=0.4)
                if found:
                    return el
            except Exception:
                pass
        await asyncio.sleep(poll)

    return None

# async def swap_account_new(driver, acc):
#     """
#     Đăng nhập vào tài khoản facebook đã lưu sẵn
#     """
#     # Lấy thông tin tài khoản
#     name = acc['name']
#     username = acc['account']

#     # ===== BƯỚC MỚI: kéo từ trên xuống để UI load đủ =====
#     try:
#         size = driver.window_size()
#         if isinstance(size, dict):
#             w = int(size.get("width", 0))
#             h = int(size.get("height", 0))
#         else:
#             w, h = size
        
#         x = int(w * 0.5)
#         start_y = int(h * 0.52)
#         end_y = int(h * 0.82)
        
#         await asyncio.sleep(1.0)
#         await asyncio.to_thread(driver.swipe, x, start_y, x, end_y, 0.45)
#         log_message(
#             f"[{DEVICE_LIST_NAME[driver.serial]}] Đã vuốt từ trên xuống để load UI trước khi đổi tài khoản",
#             logging.INFO
#         )
#         await asyncio.sleep(5)
#     except Exception as e:
#         log_message(
#             f"[{DEVICE_LIST_NAME[driver.serial]}] Lỗi khi vuốt load UI trước đổi tài khoản: {e}",
#             logging.WARNING
#         )

#     # bấm vào menu
#     menu = await my_find_element(
#         driver,
#         {("xpath", '//*[contains(@content-desc, "Menu")]')},
#         max_retries=3
#     )
#     if not menu:
#         log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Không tìm được nút menu", logging.ERROR)
#         return False

#     try:
#         menu.click()
#     except Exception:
#         log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Không click được nút menu", logging.ERROR)
#         return False

#     await asyncio.sleep(2)
    
#     changed = await wait_menu_transition(driver, timeout=5.0)
#     if not changed:
#         log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Bấm Menu nhưng chưa thấy switcher, sẽ thử fallback", logging.WARNING)

#     switch_acc = await my_find_element(
#         driver,
#         {("xpath", '//*[contains(@content-desc, "Hãy mở công cụ chuyển trang cá nhân")]')},
#         max_retries=2,
#         back_if_not_found=False
#     )
    
#     if not switch_acc:
#         switch_acc = await my_find_element(
#             driver,
#             {("xpath", '//androidx.viewpager.widget.ViewPager/android.widget.FrameLayout[1]/android.widget.FrameLayout[1]/android.widget.FrameLayout[1]/android.view.ViewGroup[1]/android.view.ViewGroup[1]/android.view.ViewGroup[1]/androidx.recyclerview.widget.RecyclerView[1]/android.view.ViewGroup[1]/android.view.ViewGroup[1]')},
#             max_retries=3
#         )

#     if not switch_acc:
#         log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Không thấy switcher trong Menu, thử fallback sang trang cá nhân", logging.WARNING)

#         ok = await go_to_profile_area_for_switch(driver)
#         if ok:
#             switch_acc = await my_find_element(
#                 driver,
#                 {("xpath", '//*[contains(@content-desc, "Hãy mở công cụ chuyển trang cá nhân")]')},
#                 max_retries=3,
#                 back_if_not_found=False
#             )

#             if not switch_acc:
#                 switch_acc = await my_find_element(
#                     driver,
#                     {("xpath", '//androidx.viewpager.widget.ViewPager/android.widget.FrameLayout[1]/android.widget.FrameLayout[1]/android.widget.FrameLayout[1]/android.view.ViewGroup[1]/android.view.ViewGroup[1]/android.view.ViewGroup[1]/androidx.recyclerview.widget.RecyclerView[1]/android.view.ViewGroup[1]/android.view.ViewGroup[1]')},
#                     max_retries=3
#                 )

#     if not switch_acc:
#         log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Đã fallback sang trang cá nhân nhưng vẫn không thấy công cụ chuyển trang cá nhân", logging.ERROR)
#         return False

#     try:
#         switch_acc.click()
#     except Exception:
#         log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Không click được nút chuyển trang cá nhân", logging.ERROR)
#         return False

#     await asyncio.sleep(2)

#     # bấm vào tài khoản khác
#     # other_acc_locator = {
#     #     ("xpath", '//*[contains(@text, "Tài khoản khác")]'),
#     #     ("xpath", '//*[@content-desc="Xem tất cả"]'),
#     #     ("xpath", '//*[@text="Xem tất cả"]'),
#     #     ("text", "Xem tất cả"),
#     # }
#     # other_acc = await my_find_element(driver, other_acc_locator, max_retries=3)

#     # bấm vào tài khoản khác / xem tất cả
#     other_acc = await find_other_account_button(driver, total_timeout=4.0, poll=0.35)

#     if not other_acc:
#         log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Không tìm được nút tài khoản khác / xem tất cả", logging.ERROR)
#         await asyncio.to_thread(driver.press, "back")
#         return False

#     try:
#         other_acc.click()
#     except Exception as e:
#         log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Không click được nút tài khoản khác / xem tất cả: {e}", logging.ERROR)
#         await asyncio.to_thread(driver.press, "back")
#         return False

#     await asyncio.sleep(2)

#     # chọn tài khoản đăng nhập
#     # log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Bắt đầu đăng nhập vào tài khoản {name}", logging.INFO)
#     # account = await my_find_element(
#     #     driver,
#     #     {("xpath", f'//*[@text="{name}"]')},
#     #     max_retries=3
#     # )

#     log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Bắt đầu đăng nhập vào tài khoản {name}", logging.INFO)

#     account = await my_find_element(
#         driver,
#         [
#             ("xpath", f'//android.view.ViewGroup[contains(@content-desc, "{name}")]'),
#             ("xpath", f'//*[@text="{name}"]/ancestor::android.view.ViewGroup[@clickable="true"][1]'),
#             ("xpath", f'//*[contains(@content-desc, "{name},")]'),
#             ("xpath", f'//*[@text="{name}"]'),
#         ],
#         max_retries=2
#     )

#     if account is None:
#         log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Không tìm được nút đăng nhập vào tài khoản {name}", logging.ERROR)
#         await asyncio.to_thread(driver.press, "back")
#         await asyncio.to_thread(driver.press, "back")
#         return False
#     else:
#         log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Đã tìm được nút đăng nhập vào tài khoản {name}", logging.INFO)

#     try:
#         account.click()
#     except Exception:
#         log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Không thể đăng nhập", logging.ERROR)
#         return False

#     await asyncio.sleep(3)

#     # Đợi vào màn hình chính
#     if await wait_logged_in(driver, timeout=12.0):
#         log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Đăng nhập thành công vào tài khoản {name}", logging.INFO)
#         await pymongo_management.update_statusFB(driver.serial, "Offline")
#         await pymongo_management.update_statusFB(username, "Online", device_id=driver.serial)
#         await check_facebook_link(driver)
#         return True
#     else:
#         log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Đăng nhập thất bại vào tài khoản {name}", logging.ERROR)
#         if not await my_find_element(driver, {("xpath", f'//*[@text="{name}"]')}, max_retries=3):
#             await pymongo_management.update_statusFB(driver.serial, "Offline")
#             await pymongo_management.update_statusFB(username, "Crash", device_id=driver.serial)
#             note = """Phase Facebook: Có thể gặp lỗi trong quá trình tự động đăng nhập tài khoản :
#                     Thực hiện các bước sau:
#                     - Đăng nhập vào tài khoản gặp lỗi
#                     - Kiểm tra tài khoản mật khẩu đã đúng chưa
#                     - Sửa DB nếu tk+mk không khớp """
#             try:
#                 log_note_acc(driver, username, note)
#             except Exception:
#                 log_message("Lỗi khi lưu file log note", logging.WARNING)

#         await asyncio.to_thread(driver.press, "back")
#         await asyncio.to_thread(driver.press, "back")
#         return False

async def _finish_facebook_account_login_after_click(driver, name, username, password=None):
    password_result = await submit_facebook_password_if_prompted(
        driver,
        password,
        account_name=name,
        timeout=6.0,
    )
    if password_result is False:
        await pymongo_management.update_statusFB(statusFB="Offline", device_id=driver.serial)
        await pymongo_management.update_statusFB(
            username=name,
            account=username,
            statusFB="Crash",
            device_id=driver.serial,
        )
        return False

    if await wait_logged_in(driver, timeout=12.0):
        log_message(
            f"[{DEVICE_LIST_NAME[driver.serial]}] Dang nhap thanh cong vao tai khoan {name}",
            logging.INFO,
        )
        await pymongo_management.update_statusFB(statusFB="Offline", device_id=driver.serial)
        await pymongo_management.update_statusFB(
            username=name,
            account=username,
            statusFB="Online",
            device_id=driver.serial,
        )
        try:
            set_online_account(driver.serial, account=username, username=name)
        except Exception:
            pass
        await check_facebook_link(driver)
        return True

    log_message(
        f"[{DEVICE_LIST_NAME[driver.serial]}] Dang nhap that bai vao tai khoan {name}",
        logging.ERROR,
    )
    await pymongo_management.update_statusFB(statusFB="Offline", device_id=driver.serial)
    await pymongo_management.update_statusFB(
        username=name,
        account=username,
        statusFB="Crash",
        device_id=driver.serial,
    )
    return False


async def swap_account_new(driver, acc):
    name = acc['name']
    username = acc['account']
    password = acc.get('password') or ""
    await prepare_environment_before_swap_account(driver)

    # Có thể giữ swipe nếu bạn muốn test tiếp
    try:
        size = driver.window_size()
        if isinstance(size, dict):
            w = int(size.get("width", 0))
            h = int(size.get("height", 0))
        else:
            w, h = size

        x = int(w * 0.5)
        start_y = int(h * 0.52)
        end_y = int(h * 0.82)

        await asyncio.sleep(1.0)
        await asyncio.to_thread(driver.swipe, x, start_y, x, end_y, 0.45)
        log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Đã vuốt từ trên xuống để load UI trước khi đổi tài khoản", logging.INFO)
        await asyncio.sleep(3)
    except Exception as e:
        log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Lỗi khi vuốt load UI trước đổi tài khoản: {e}", logging.WARNING)

    # Nhánh chính: từ màn hiện tại bấm Menu -> tìm nút vào giao diện chuyển tài khoản
    switch_acc = await open_menu_and_find_switcher(driver, wait_timeout=5.0)

    # Nhánh fallback: vào trang cá nhân -> bấm lại Menu -> tìm nút vào giao diện chuyển tài khoản
    if not switch_acc:
        log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Không thấy switcher từ menu hiện tại, thử fallback qua trang cá nhân rồi mở lại Menu", logging.WARNING)
        switch_acc = await fallback_to_profile_then_open_switcher(driver)

    if not switch_acc:
        log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Khong thay switcher sau 2 nhanh, thu mo Facebook feed deeplink", logging.WARNING)
        switch_acc = await fallback_feed_then_open_switcher(driver)

    if not switch_acc:
        log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Không tìm được nút vào giao diện chuyển tài khoản sau cả 3 nhánh", logging.ERROR)
        return False

    try:
        switch_acc.click()
    except Exception:
        log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Không click được nút chuyển trang cá nhân", logging.ERROR)
        return False

    await asyncio.sleep(2)

    other_acc = await find_other_account_button(driver, total_timeout=4.0, poll=0.35)
    if not other_acc:
        log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Không tìm được nút tài khoản khác / xem tất cả", logging.ERROR)
        await asyncio.to_thread(driver.press, "back")
        return False

    try:
        other_acc.click()
    except Exception as e:
        log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Không click được nút tài khoản khác / xem tất cả: {e}", logging.ERROR)
        await asyncio.to_thread(driver.press, "back")
        return False

    await asyncio.sleep(2)

    log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Bắt đầu đăng nhập vào tài khoản {name}", logging.INFO)

    selected_from_picker = await select_facebook_account_from_picker(driver, [name], max_scrolls=4)
    if selected_from_picker:
        return await _finish_facebook_account_login_after_click(driver, name, username, password=password)

    account = await my_find_element(
        driver,
        [
            ("xpath", f'//android.view.ViewGroup[contains(@content-desc, "{name}")]'),
            ("xpath", f'//*[@text="{name}"]/ancestor::android.view.ViewGroup[@clickable="true"][1]'),
            ("xpath", f'//*[contains(@content-desc, "{name},")]'),
            ("xpath", f'//*[@text="{name}"]'),
        ],
        max_retries=2
    )

    if account is None:
        log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Không tìm được nút đăng nhập vào tài khoản {name}", logging.ERROR)
        await asyncio.to_thread(driver.press, "back")
        await asyncio.to_thread(driver.press, "back")
        return False

    try:
        account.click()
    except Exception:
        log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Không thể đăng nhập", logging.ERROR)
        return False

    await submit_facebook_password_if_prompted(
        driver,
        password,
        account_name=name,
        timeout=6.0,
    )

    if await wait_logged_in(driver, timeout=12.0):
        log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Đăng nhập thành công vào tài khoản {name}", logging.INFO)
        await pymongo_management.update_statusFB(statusFB="Offline", device_id=driver.serial)
        await pymongo_management.update_statusFB(
            username=name,
            account=username,
            statusFB="Online",
            device_id=driver.serial,
        )
        try:
            set_online_account(driver.serial, account=username, username=name)
        except Exception:
            pass
        await check_facebook_link(driver)
        return True
    else:
        log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Đăng nhập thất bại vào tài khoản {name}", logging.ERROR)
        if not await my_find_element(driver, {("xpath", f'//*[@text="{name}"]')}, max_retries=3):
            await pymongo_management.update_statusFB(statusFB="Offline", device_id=driver.serial)
            await pymongo_management.update_statusFB(
                username=name,
                account=username,
                statusFB="Crash",
                device_id=driver.serial,
            )
            note = """Phase Facebook: Có thể gặp lỗi trong quá trình tự động đăng nhập tài khoản :
                    Thực hiện các bước sau:
                    - Đăng nhập vào tài khoản gặp lỗi
                    - Kiểm tra tài khoản mật khẩu đã đúng chưa
                    - Sửa DB nếu tk+mk không khớp """
            try:
                log_note_acc(driver, username, note)
            except Exception:
                log_message("Lỗi khi lưu file log note", logging.WARNING)

        await asyncio.to_thread(driver.press, "back")
        await asyncio.to_thread(driver.press, "back")
        return False

 
async def first_login_account(driver, acc):
    """
    Đăng nhập vào tài khoản đầu danh sách trong DB nếu chưa đăng nhập
    B1) Bấm Dùng trang cá nhân khác
    //*[@text="Dùng trang cá nhân khác"]
    B2) Tắt trình quản lý mật khẩu 

    B3) Đăng nhập tài khoản 
    
    """
    # Mở ứng dụng Facebook
    # driver.app_start("com.facebook.katana")

    # Lấy thông tin tài khoản
    name = acc['name']
    username = acc['account']
    password = acc['password']

    # Bấm vào dùng trang cá nhân khác
    other_acc =  await my_find_element(driver,{("xpath",'//*[@text="Dùng trang cá nhân khác"]')},2)
    if other_acc :
        other_acc.click()
    else:
        log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Lỗi khi đăng nhập lần đầu", logging.ERROR)
        return False
    
    # tắt trình quản lý mật khẩu

    if await my_find_element(driver, gg_password_locators,1):
        driver.press("back")


    # Đăng nhập tài khoản

    # Tìm tất cả ô nhập text có thể tương tác

    input_fields = my_find_elements(driver, {("className", 'android.widget.EditText')})
    try:
        input_fields[0].set_text(username)  # Nhập số điện thoại
        input_fields[1].set_text(password)  # Nhập mật khẩu
    except Exception:
        log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Không tìm được ô text", logging.ERROR)
        return False
    login_button = await my_find_element(driver,{("xpath", '//android.widget.Button[@content-desc="Đăng nhập"]')},1 )

    if login_button:
        login_button.click()
    else:
        log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Không tìm được nút đăng nhập")
        return False
    
    await asyncio.sleep(2)    
    # Đợi vào màn hình chính
    if await wait_logged_in(driver, timeout=12.0):
        log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Đăng nhập thành công vào tài khoản {name}",3)
        await pymongo_management.update_statusFB(statusFB="Offline", device_id=driver.serial)
        await pymongo_management.update_statusFB(
            username=name,
            account=username,
            statusFB="Online",
            device_id=driver.serial,
        )
        await check_facebook_link(driver)
        return True
    else:
        log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Đăng nhập thất bại vào tài khoản {name}", logging.ERROR)
        if not await my_find_element(driver, {("xpath", f'//*[@text="{name}"]')},3):
            await pymongo_management.update_statusFB(statusFB="Offline", device_id=driver.serial)
            await pymongo_management.update_statusFB(
                username=name,
                account=username,
                statusFB="Crash",
                device_id=driver.serial,
            )
            note = """Phase Facebook: Có thể gặp lỗi trong quá trình tự động đăng nhập tài khoản :
                    Thực hiện các bước sau:
                    - Đăng nhập vào tài khoản gặp lỗi
                    - Kiểm tra tài khoản mật khẩu đã đúng chưa
                    - Sửa DB nếu tk+mk không khớp """
            try:
                log_note_acc(driver, username, note)
            except Exception as e:
                log_message("Lỗi khi lưu file log note")
                pass
        await asyncio.to_thread(driver.press, "back")
        await asyncio.to_thread(driver.press, "back")
        return False
