import pymongo_management
import toolfacebook_lib
import xml.etree.ElementTree as ET
from util import log_message, DEVICE_LIST_NAME
import asyncio
import logging
from main_merged import disable_auto_rotation
import re


DEFAULT_GROUP_ANSWER = "Vâng okey ạ!"

def _facebook_group_url(group_link: str) -> str:
    group_link = (group_link or "").strip()
    if group_link.startswith(("http://", "https://")):
        return group_link
    return "https://facebook.com/" + group_link.lstrip("/")


QUESTION_ANSWER_RULES = {
    # thêm các mapping riêng ở đây khi cần
    "mời 20-50 bạn bè": "Vâng okey ạ!",
    "không được pr": "Vâng okey ạ!",
    "số điện thoại": "0966597777",
    "sđt": "0966597777",
    "hr": "hr"
}

QUESTION_IGNORE_TEXTS = {
    "câu hỏi dành cho người tham gia",
    "viết câu trả lời...",
    "gửi",
    "thoát",
    "trả lời câu hỏi",
    "thoát mà không trả lời?",
    "bạn sắp được đăng bài trong nhóm này",
}

RADIO_OPTION_KEYWORDS = [
    "đăng tin",
    "đăng tin tuyển dụng",
    "ok",
    "okay",
    "oke",
    "sẵn sàng",
    "đồng ý",
    "vâng",
    "rồi",
    "đã làm",
    "có",
    "đăng bài"
    "chờ",
    "đảm bảo",
    "cam kết",
    "miền bắc", "miền nam", "miền trung",
    "Nhà tuyển dụng", "Ứng viên", "Tôi là ứng viên", "Tôi là nhà tuyển dụng", "Hà Nội", "HN"
]

CHECKBOX_OPTION_POSITIVE_KEYWORDS = [
    "đồng ý",
    "ok",
    "okay",
    "oke",
    "vâng",
    "sẵn sàng",
    "xác nhận",
    "đã đọc",
    "cam kết",
    "rồi",
    "đã làm",
    "đăng tin tuyển dụng",
    "đăng tin",
    "có",
    "đăng bài"
    "chờ",
    "đảm bảo",
    "cam kết",
    "miền bắc", "miền nam", "miền trung",
    "Nhà tuyển dụng", "Ứng viên", "Tôi là ứng viên", "Tôi là nhà tuyển dụng", "Hà Nội", "HN"
]

CHECKBOX_OPTION_NEGATIVE_KEYWORDS = [
    "thực hiện sau",
    "để sau",
    "không",
    "chưa",
    "bỏ qua",
    "từ chối",
    "chờ"
]

def extract_radio_options_from_root(root):
    options = []

    for node in root.iter():
        cls = node.attrib.get("class", "")
        bounds = node.attrib.get("bounds", "")
        desc = (node.attrib.get("content-desc", "") or "").strip()
        text = (node.attrib.get("text", "") or "").strip()

        if cls != "android.widget.RadioButton":
            continue

        label = desc or text
        if not label or not bounds:
            continue

        checked = str(node.attrib.get("checked", "false")).lower() == "true"

        options.append({
            "label": label,
            "bounds": bounds,
            "checked": checked,
        })

    return options

def extract_question_checkbox_options_from_root(root):
    """
    Lấy các checkbox/compoundbutton thuộc phần trả lời câu hỏi,
    loại trừ checkbox 'Tôi đồng ý với các quy tắc nhóm'.
    """
    options = []

    for node in root.iter():
        cls = node.attrib.get("class", "")
        bounds = node.attrib.get("bounds", "")
        desc = (node.attrib.get("content-desc", "") or "").strip()
        text = (node.attrib.get("text", "") or "").strip()

        if cls not in ("android.widget.CompoundButton", "android.widget.CheckBox"):
            continue

        label = desc or text
        if not label or not bounds:
            continue

        norm = normalize_text(label)

        # loại trừ checkbox quy tắc nhóm
        if "tôi đồng ý với các quy tắc nhóm" in norm:
            continue

        checked = str(node.attrib.get("checked", "false")).lower() == "true"

        options.append({
            "label": label,
            "bounds": bounds,
            "checked": checked,
            "class": cls,
        })

    return options

def should_choose_radio_label(label: str) -> bool:
    norm = normalize_text(label)
    return any(k in norm for k in RADIO_OPTION_KEYWORDS)

def should_choose_checkbox_label(label: str) -> bool:
    norm = normalize_text(label)

    if any(k in norm for k in CHECKBOX_OPTION_NEGATIVE_KEYWORDS):
        return False

    if any(k in norm for k in CHECKBOX_OPTION_POSITIVE_KEYWORDS):
        return True

    return False

async def choose_preferred_radio_options(driver, root, selected_radio_bounds):
    """
    Chọn radio theo keyword ưu tiên như:
    'đăng tin', 'ok', 'sẵn sàng'
    """
    changed = False
    radio_options = extract_radio_options_from_root(root)

    for opt in radio_options:
        bounds = opt["bounds"]
        label = opt["label"]
        checked = opt["checked"]

        if checked:
            selected_radio_bounds.add(bounds)
            log_message(
                f"[{DEVICE_LIST_NAME[driver.serial]}] Radio đã được chọn sẵn: '{label}'",
                logging.INFO,
            )
            continue

        if bounds in selected_radio_bounds:
            continue

        if not should_choose_radio_label(label):
            continue

        center = _center_from_bounds(bounds)
        if not center:
            continue

        try:
            await asyncio.to_thread(driver.click, center[0], center[1])
            await asyncio.sleep(0.6)
            selected_radio_bounds.add(bounds)
            changed = True

            log_message(
                f"[{DEVICE_LIST_NAME[driver.serial]}] Đã chọn radio: '{label}'",
                logging.INFO,
            )
        except Exception as e:
            log_message(
                f"[{DEVICE_LIST_NAME[driver.serial]}] Lỗi khi chọn radio '{label}': {e}",
                logging.WARNING,
            )

    return changed

async def choose_preferred_checkbox_options(driver, root, selected_checkbox_bounds):
    """
    Xử lý các checkbox thuộc câu hỏi nhiều lựa chọn.
    Ví dụ ở window_dump9.xm:
    - 'Tôi đồng ý'  -> nên chọn
    - 'Tôi sẽ thực hiện sau' -> không chọn
    """
    changed = False
    checkbox_options = extract_question_checkbox_options_from_root(root)

    for opt in checkbox_options:
        bounds = opt["bounds"]
        label = opt["label"]
        checked = opt["checked"]

        if checked:
            selected_checkbox_bounds.add(bounds)
            log_message(
                f"[{DEVICE_LIST_NAME[driver.serial]}] Checkbox câu hỏi đã được chọn sẵn: '{label}'",
                logging.INFO,
            )
            continue

        if bounds in selected_checkbox_bounds:
            continue

        if not should_choose_checkbox_label(label):
            continue

        point = _right_side_click_point(bounds) or _center_from_bounds(bounds)
        if not point:
            continue

        try:
            await asyncio.to_thread(driver.click, point[0], point[1])
            await asyncio.sleep(0.8)

            checked_ok = await wait_checkbox_checked_by_bounds(driver, bounds, timeout=3.0, poll=0.4)

            if checked_ok:
                log_message(
                    f"[{DEVICE_LIST_NAME[driver.serial]}] Đã chọn checkbox câu hỏi: '{label}'",
                    logging.INFO,
                )
            else:
                log_message(
                    f"[{DEVICE_LIST_NAME[driver.serial]}] Đã click checkbox câu hỏi nhưng chưa verify checked=true: '{label}'",
                    logging.WARNING,
                )

            selected_checkbox_bounds.add(bounds)
            changed = True

        except Exception as e:
            log_message(
                f"[{DEVICE_LIST_NAME[driver.serial]}] Lỗi khi chọn checkbox câu hỏi '{label}': {e}",
                logging.WARNING,
            )

    return changed

def extract_group_rules_checkbox_from_root(root):
    candidates = []

    for node in root.iter():
        cls = node.attrib.get("class", "")
        bounds = node.attrib.get("bounds", "")
        desc = (node.attrib.get("content-desc", "") or "").strip()
        text = (node.attrib.get("text", "") or "").strip()

        label = desc or text
        norm = normalize_text(label)

        if "tôi đồng ý với các quy tắc nhóm" not in norm:
            continue

        if cls not in ("android.widget.CompoundButton", "android.widget.CheckBox"):
            continue

        checked = str(node.attrib.get("checked", "false")).lower() == "true"

        candidates.append({
            "bounds": bounds,
            "checked": checked,
            "label": label,
            "class": cls,
        })

    if not candidates:
        return None

    # Ưu tiên node đã checked=true
    for item in candidates:
        if item["checked"]:
            return item

    # Nếu chưa checked thì lấy candidate đầu tiên
    return candidates[0]


async def wait_group_rules_checked(driver, timeout=4.0, poll=0.4):
    waited = 0.0
    while waited < timeout:
        try:
            xml_now = driver.dump_hierarchy()
            root_now = ET.fromstring(xml_now)
            info_now = extract_group_rules_checkbox_from_root(root_now)
            if info_now and info_now["checked"]:
                return True
        except Exception:
            pass

        await asyncio.sleep(poll)
        waited += poll

    return False

async def wait_checkbox_checked_by_bounds(driver, bounds_text: str, timeout=4.0, poll=0.4):
    waited = 0.0
    while waited < timeout:
        try:
            xml_now = driver.dump_hierarchy()
            root_now = ET.fromstring(xml_now)

            for node in root_now.iter():
                cls = node.attrib.get("class", "")
                bounds = node.attrib.get("bounds", "")
                if cls not in ("android.widget.CompoundButton", "android.widget.CheckBox"):
                    continue
                if bounds != bounds_text:
                    continue

                checked = str(node.attrib.get("checked", "false")).lower() == "true"
                if checked:
                    return True
        except Exception:
            pass

        await asyncio.sleep(poll)
        waited += poll

    return False

def _right_side_click_point(bounds_text: str):
    rect = _bounds_rect(bounds_text)
    if not rect:
        return None
    left, top, right, bottom = rect
    x = max(left + 10, right - 45)   # ưu tiên click gần ô vuông bên phải
    y = (top + bottom) // 2
    return x, y


async def ensure_group_rules_checkbox_checked(driver, root, min_settle_after_click=3.0):
    """
    Nếu thấy ô 'Tôi đồng ý với các quy tắc nhóm':
    - checked rồi => bỏ qua
    - chưa checked => click 1 lần, chờ UI cập nhật, poll lại
    """
    info = extract_group_rules_checkbox_from_root(root)
    if not info:
        return False

    if info["checked"]:
        log_message(
            f"[{DEVICE_LIST_NAME[driver.serial]}] Checkbox 'Tôi đồng ý với các quy tắc nhóm' đã bật sẵn",
            logging.INFO,
        )
        return True

    point = _right_side_click_point(info["bounds"]) or _center_from_bounds(info["bounds"])
    if not point:
        return False

    try:
        log_message(
            f"[{DEVICE_LIST_NAME[driver.serial]}] Checkbox quy tắc nhóm đang chưa bật, click tại {point}",
            logging.INFO,
        )

        await asyncio.to_thread(driver.click, point[0], point[1])

        # chờ UI cập nhật đủ lâu, tránh đọc XML quá sớm
        await asyncio.sleep(min_settle_after_click)

        checked_ok = await wait_group_rules_checked(driver, timeout=4.0, poll=0.4)
        if checked_ok:
            log_message(
                f"[{DEVICE_LIST_NAME[driver.serial]}] Đã tick 'Tôi đồng ý với các quy tắc nhóm'",
                logging.INFO,
            )
            return True

        log_message(
            f"[{DEVICE_LIST_NAME[driver.serial]}] Đã click checkbox nhưng sau khi chờ vẫn chưa thấy checked=true",
            logging.WARNING,
        )
        return False

    except Exception as e:
        log_message(
            f"[{DEVICE_LIST_NAME[driver.serial]}] Lỗi khi tick checkbox quy tắc nhóm: {e}",
            logging.WARNING,
        )
        return False

def find_edittext_by_bounds(driver, bounds_text: str):
    try:
        xp = f'//*[@class="android.widget.EditText" and @bounds="{bounds_text}"]'
        el = driver.xpath(xp)
        if el.exists:
            return el
    except Exception:
        pass
    return None


async def fill_answer_into_input(driver, bounds_text: str, answer_text: str):
    """
    Điền answer_text vào đúng EditText theo bounds.
    Ưu tiên set_text trên element thật, fallback mới dùng send_keys toàn cục.
    """
    el = find_edittext_by_bounds(driver, bounds_text)
    center = _center_from_bounds(bounds_text)

    # Cách 1: element-level click + set_text
    if el is not None:
        try:
            await asyncio.to_thread(el.click)
            await asyncio.sleep(0.25)
            await asyncio.to_thread(el.set_text, answer_text)
            await asyncio.sleep(0.35)
            return True
        except Exception:
            pass

    # Cách 2: click tọa độ + send_keys fallback
    if center:
        try:
            await asyncio.to_thread(driver.click, center[0], center[1])
            await asyncio.sleep(0.25)
            await asyncio.to_thread(driver.send_keys, answer_text, clear=True)
            await asyncio.sleep(0.35)
            return True
        except Exception:
            pass

    return False

def normalize_text(text: str) -> str:
    text = (text or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text


def pick_answer_for_question(question: str) -> str:
    q = normalize_text(question)
    for key, answer in QUESTION_ANSWER_RULES.items():
        if normalize_text(key) in q:
            return answer
    return DEFAULT_GROUP_ANSWER


def _bounds_rect(bounds_text: str):
    nums = list(map(int, re.findall(r"\d+", bounds_text or "")))
    if len(nums) < 4:
        return None
    left, top, right, bottom = nums[:4]
    return left, top, right, bottom


def _center_from_bounds(bounds_text: str):
    rect = _bounds_rect(bounds_text)
    if not rect:
        return None
    left, top, right, bottom = rect
    return (left + right) // 2, (top + bottom) // 2

def is_input_filled_in_xml(root, bounds_text: str, expected_text: str = "") -> bool:
    for node in root.iter():
        if node.attrib.get("class", "") != "android.widget.EditText":
            continue
        if node.attrib.get("bounds", "") != bounds_text:
            continue

        text = (node.attrib.get("text", "") or "").strip()

        # Chỉ cần ô không còn rỗng là xem như đã có dữ liệu
        if text and text != "Viết câu trả lời...":
            return True

        if expected_text and normalize_text(expected_text) in normalize_text(text):
            return True

    return False

def extract_question_input_pairs_from_root(root):
    """
    Trích các cặp:
    - question: text gần nhất phía trên ô EditText
    - bounds: bounds của EditText

    Với UI kiểu mới, placeholder 'Viết câu trả lời...' có thể là TextView anh em,
    không nằm trực tiếp trong EditText.
    """
    text_nodes = []
    input_nodes = []

    for node in root.iter():
        visible = node.attrib.get("visible-to-user", "true").lower() != "false"
        if not visible:
            continue

        cls = node.attrib.get("class", "")
        text = (node.attrib.get("text", "") or "").strip()
        bounds = node.attrib.get("bounds", "")

        rect = _bounds_rect(bounds)
        if not rect:
            continue

        # Lấy tất cả EditText visible làm input
        if cls == "android.widget.EditText":
            input_nodes.append({
                "bounds": bounds,
                "rect": rect,
                "text": text,
            })
            continue

        # Lấy text node để ghép câu hỏi
        if text:
            norm = normalize_text(text)
            if norm not in QUESTION_IGNORE_TEXTS:
                text_nodes.append({
                    "text": text,
                    "rect": rect,
                })

    text_nodes.sort(key=lambda x: (x["rect"][1], x["rect"][0]))
    input_nodes.sort(key=lambda x: (x["rect"][1], x["rect"][0]))

    pairs = []
    for inp in input_nodes:
        left_i, top_i, right_i, bottom_i = inp["rect"]
        candidates = []

        for txt in text_nodes:
            left_t, top_t, right_t, bottom_t = txt["rect"]

            # text phải nằm phía trên input
            if bottom_t <= top_i:
                vertical_gap = top_i - bottom_t
                horizontal_gap = abs(left_i - left_t)
                candidates.append((vertical_gap, horizontal_gap, txt["text"]))

        question = ""
        if candidates:
            candidates.sort(key=lambda x: (x[0], x[1]))
            question = candidates[0][2]

        pairs.append({
            "question": question,
            "bounds": inp["bounds"],
        })

    return pairs


async def is_question_form_screen(driver):
    return (
        driver(textContains="Câu hỏi dành cho người tham gia").exists
        or driver(descriptionContains="Câu hỏi dành cho người tham gia").exists
        or driver(className="android.widget.EditText", text="Viết câu trả lời...").exists
        or driver(className="android.widget.TextView", text="Viết câu trả lời...").exists
        or driver(className="android.widget.EditText", descriptionContains="Viết câu trả lời").exists
        or driver(className="android.widget.CompoundButton").exists
        or driver(className="android.widget.CheckBox").exists
        or driver(textContains="Bạn có thể chọn nhiều đáp án").exists
        or driver(descriptionContains="Bạn có thể chọn nhiều đáp án").exists
    )

async def wait_and_click_submit_button(driver, wait_timeout=4.0, poll=0.5, settle_before_click=2.0):
    """
    Chờ nút Gửi xuất hiện và sáng ổn định rồi click.
    """
    waited = 0.0
    btn = None

    while waited < wait_timeout:
        btn = find_submit_button(driver)
        if btn.exists:
            try:
                info = btn.info or {}
            except Exception:
                info = {}

            log_message(
                f"[{DEVICE_LIST_NAME[driver.serial]}] Check nút Gửi: "
                f"enabled={info.get('enabled')} clickable={info.get('clickable')} "
                f"class={info.get('className') or info.get('class')} "
                f"text={info.get('text')} desc={info.get('contentDescription')} "
                f"bounds={info.get('bounds')}",
                logging.INFO,
            )

            # nhiều máy enabled không trả chuẩn, nên chỉ cần thấy button clickable là cho qua
            if info.get("enabled", True):
                await asyncio.sleep(settle_before_click)
                if safe_click(driver, btn):
                    log_message(
                        f"[{DEVICE_LIST_NAME[driver.serial]}] Đã bấm nút Gửi sau khi chờ sáng ổn định",
                        logging.INFO,
                    )
                    return True

        await asyncio.sleep(poll)
        waited += poll

    log_message(
        f"[{DEVICE_LIST_NAME[driver.serial]}] Hết thời gian chờ nhưng chưa bấm được nút Gửi",
        logging.WARNING,
    )
    return False

async def fill_all_question_answers_and_submit(driver, max_total_swipes=2):
    answered_any = False
    filled_bounds = set()
    selected_radio_bounds = set()
    selected_checkbox_bounds = set()
    rules_checked_confirmed = False
    no_new_after_swipe_rounds = 0

    for swipe_idx in range(max_total_swipes + 1):
        try:
            xml_dump = driver.dump_hierarchy()
            root = ET.fromstring(xml_dump)
        except Exception as e:
            log_message(
                f"[{DEVICE_LIST_NAME[driver.serial]}] Lỗi dump XML màn câu hỏi: {e}",
                logging.WARNING,
            )
            return False

        pairs = extract_question_input_pairs_from_root(root)

        new_items = []
        for item in pairs:
            bounds = item.get("bounds", "")
            if bounds and bounds not in filled_bounds:
                new_items.append(item)

        log_message(
            f"[{DEVICE_LIST_NAME[driver.serial]}] Lượt {swipe_idx + 1}: thấy {len(pairs)} ô, mới {len(new_items)} ô",
            logging.INFO,
        )

        for item in new_items:
            question = item.get("question", "")
            bounds = item.get("bounds", "")
            answer_text = pick_answer_for_question(question)

            ok_fill = await fill_answer_into_input(driver, bounds, answer_text)
            if not ok_fill:
                log_message(
                    f"[{DEVICE_LIST_NAME[driver.serial]}] Không điền được ô bounds={bounds} | question='{question}'",
                    logging.WARNING,
                )
                continue

            try:
                xml_after = driver.dump_hierarchy()
                root_after = ET.fromstring(xml_after)

                if is_input_filled_in_xml(root_after, bounds, answer_text):
                    answered_any = True
                    filled_bounds.add(bounds)
                    log_message(
                        f"[{DEVICE_LIST_NAME[driver.serial]}] Đã điền câu hỏi: '{question}' -> '{answer_text}'",
                        logging.INFO,
                    )
                else:
                    answered_any = True
                    filled_bounds.add(bounds)
                    log_message(
                        f"[{DEVICE_LIST_NAME[driver.serial]}] Đã thử điền nhưng chưa verify được ở bounds={bounds} | question='{question}'",
                        logging.WARNING,
                    )
            except Exception as e:
                answered_any = True
                filled_bounds.add(bounds)
                log_message(
                    f"[{DEVICE_LIST_NAME[driver.serial]}] Đã điền (không verify được XML) | question='{question}' | err={e}",
                    logging.INFO,
                )
        # xử lý radio trên màn hiện tại
        try:
            changed_radio = await choose_preferred_radio_options(driver, root, selected_radio_bounds)
            if changed_radio:
                await asyncio.sleep(0.5)
        except Exception as e:
            log_message(
                f"[{DEVICE_LIST_NAME[driver.serial]}] Lỗi xử lý radio option: {e}",
                logging.WARNING,
            )

        # xử lý checkbox answer trên màn hiện tại
        try:
            changed_checkbox = await choose_preferred_checkbox_options(driver, root, selected_checkbox_bounds)
            if changed_checkbox:
                answered_any = True
                await asyncio.sleep(0.5)
        except Exception as e:
            log_message(
                f"[{DEVICE_LIST_NAME[driver.serial]}] Lỗi xử lý checkbox answer: {e}",
                logging.WARNING,
            )

        # xử lý checkbox quy tắc nhóm nếu đang nhìn thấy
        if not rules_checked_confirmed:
            try:
                rules_checked_confirmed = await ensure_group_rules_checkbox_checked(
                    driver,
                    root,
                    min_settle_after_click=3.0,
                )
            except Exception as e:
                log_message(
                    f"[{DEVICE_LIST_NAME[driver.serial]}] Lỗi xử lý checkbox sau swipe: {e}",
                    logging.WARNING,
                )
        # đóng bàn phím
        # try:
        #     await asyncio.to_thread(driver.press, "back")
        #     await asyncio.sleep(0.4)
        # except Exception:
        #     pass

        # sau khi điền xong các box đang thấy -> luôn vuốt tiếp để check còn box không
        if swipe_idx < max_total_swipes:
            found_new_in_two_swipes = False

            for sub_swipe in range(2):
                try:
                    size = driver.window_size()
                    if isinstance(size, dict):
                        w = int(size.get("width", 720))
                        h = int(size.get("height", 1600))
                    else:
                        w, h = size

                    x = int(w * 0.5)
                    start_y = int(h * 0.68)
                    end_y = int(h * 0.32)

                    log_message(
                        f"[{DEVICE_LIST_NAME[driver.serial]}] Swipe kiểm tra box lần {sub_swipe + 1}/2 từ ({x},{start_y}) -> ({x},{end_y})",
                        logging.INFO,
                    )

                    await asyncio.to_thread(driver.swipe, x, start_y, x, end_y, 0.25)
                    await asyncio.sleep(1.0)
                    
                except Exception as e:
                    log_message(
                        f"[{DEVICE_LIST_NAME[driver.serial]}] Lỗi vuốt kiểm tra thêm ô câu hỏi: {e}",
                        logging.WARNING,
                    )

                try:
                    xml_after_swipe = driver.dump_hierarchy()
                    root_after_swipe = ET.fromstring(xml_after_swipe)
                    pairs_after_swipe = extract_question_input_pairs_from_root(root_after_swipe)

                    # nếu sau khi swipe xuất hiện radio hoặc checkbox thì xử lý luôn
                    try:
                        changed_radio_after_swipe = await choose_preferred_radio_options(
                            driver,
                            root_after_swipe,
                            selected_radio_bounds,
                        )
                        if changed_radio_after_swipe:
                            await asyncio.sleep(0.5)
                    except Exception as e:
                        log_message(
                            f"[{DEVICE_LIST_NAME[driver.serial]}] Lỗi xử lý radio sau swipe: {e}",
                            logging.WARNING,
                        )

                    try:
                        changed_checkbox_after_swipe = await choose_preferred_checkbox_options(
                            driver,
                            root_after_swipe,
                            selected_checkbox_bounds,
                        )
                        if changed_checkbox_after_swipe:
                            answered_any = True
                            await asyncio.sleep(0.5)
                    except Exception as e:
                        log_message(
                            f"[{DEVICE_LIST_NAME[driver.serial]}] Lỗi xử lý checkbox answer sau swipe: {e}",
                            logging.WARNING,
                        )

                    if not rules_checked_confirmed:
                        try:
                            rules_checked_confirmed = await ensure_group_rules_checkbox_checked(
                                driver,
                                root_after_swipe,
                                min_settle_after_click=3.0,
                            )
                        except Exception as e:
                            log_message(
                                f"[{DEVICE_LIST_NAME[driver.serial]}] Lỗi xử lý checkbox sau swipe: {e}",
                                logging.WARNING,
                            )

                    unseen = [
                        it for it in pairs_after_swipe
                        if it.get("bounds", "") and it.get("bounds", "") not in filled_bounds
                    ]

                    log_message(
                        f"[{DEVICE_LIST_NAME[driver.serial]}] Sau swipe {sub_swipe + 1}/2: còn {len(unseen)} ô chưa điền",
                        logging.INFO,
                    )

                    if unseen:
                        found_new_in_two_swipes = True
                        break
                except Exception:
                    pass

            if found_new_in_two_swipes:
                no_new_after_swipe_rounds = 0
                continue
            else:
                no_new_after_swipe_rounds += 1

        if no_new_after_swipe_rounds >= 1:
            log_message(
                f"[{DEVICE_LIST_NAME[driver.serial]}] Đã vuốt kiểm tra 2 lần và không còn box mới, chuyển sang chờ nút Gửi sáng rồi bấm",
                logging.INFO,
            )
            break

    if not answered_any:
        log_message(
            f"[{DEVICE_LIST_NAME[driver.serial]}] Không điền được ô câu hỏi nào",
            logging.WARNING,
        )
        return False

    # đóng bàn phím thêm lần nữa trước khi bấm Gửi
    # try:
    #     await asyncio.to_thread(driver.press, "back")
    #     await asyncio.sleep(0.6)
    # except Exception:
    #     pass

    # check lại lần cuối checkbox quy tắc nhóm trước khi bấm Gửi
    if not rules_checked_confirmed:
        try:
            xml_final = driver.dump_hierarchy()
            root_final = ET.fromstring(xml_final)
            rules_checked_confirmed = await ensure_group_rules_checkbox_checked(
                driver,
                root_final,
                min_settle_after_click=3.0,
            )
        except Exception as e:
            log_message(
                f"[{DEVICE_LIST_NAME[driver.serial]}] Lỗi check cuối checkbox quy tắc nhóm: {e}",
                logging.WARNING,
            )

    # chờ nút Gửi sáng ổn định rồi click
    clicked_submit = await wait_and_click_submit_button(
        driver,
        wait_timeout=4.0,
        poll=0.5,
        settle_before_click=2.0,
    )

    if clicked_submit:
        await asyncio.sleep(1.5)
        return True

    log_message(
        f"[{DEVICE_LIST_NAME[driver.serial]}] Không click được nút Gửi sau khi trả lời xong toàn bộ câu hỏi",
        logging.WARNING,
    )
    return False

async def handle_exit_question_popup(driver):
    """
    Nếu hiện popup 'Thoát mà không trả lời?' thì bấm 'Thoát'
    """
    title_exists = (
        driver(textContains="Thoát mà không trả lời").exists
        or driver(descriptionContains="Thoát mà không trả lời").exists
    )
    if not title_exists:
        return False

    exit_btn = None
    for sel in (
        {"text": "Thoát"},
        {"description": "Thoát"},
        {"textContains": "Thoát"},
        {"descriptionContains": "Thoát"},
    ):
        el = driver(**sel)
        if el.exists:
            exit_btn = el
            break

    if not exit_btn:
        return False

    if safe_click(driver, exit_btn):
        await asyncio.sleep(1.0)
        log_message(
            f"[{DEVICE_LIST_NAME[driver.serial]}] Đã bấm Thoát ở popup câu hỏi nhóm",
            logging.INFO,
        )
        return True

    return False

class JoinResult:
    def __init__(self, status, reason=None):
        self.status = status
        self.reason = reason
def is_question_required_pending(driver):
    """
    Các marker cho trạng thái:
    - cần trả lời câu hỏi trước khi gửi yêu cầu tham gia
    - không được coi là 'đã tham gia' thật
    """
    selectors = [
        {"textContains": "Trả lời câu hỏi"},
        {"descriptionContains": "Trả lời câu hỏi"},
        {"textContains": "Câu hỏi dành cho người tham gia"},
        {"descriptionContains": "Câu hỏi dành cho người tham gia"},
        {"textContains": "Answer questions"},
        {"descriptionContains": "Answer questions"},
    ]

    for sel in selectors:
        try:
            el = driver(**sel)
            if el.exists:
                return True
        except Exception:
            pass
    return False


def is_join_request_pending(driver):
    """
    Markers for a submitted join request waiting for admin approval.
    This is different from a questionnaire screen: the join request has been sent,
    but the account is not a member yet, so posting cannot continue.
    """
    selectors = [
        {"textContains": "Yêu cầu xem xét lại của bạn vẫn đang chờ phê duyệt"},
        {"descriptionContains": "Yêu cầu xem xét lại của bạn vẫn đang chờ phê duyệt"},
        {"textContains": "xem xét lại của bạn vẫn đang chờ phê duyệt"},
        {"descriptionContains": "xem xét lại của bạn vẫn đang chờ phê duyệt"},
        {"textContains": "đang chờ phê duyệt"},
        {"descriptionContains": "đang chờ phê duyệt"},
        {"textContains": "Đang chờ phê duyệt"},
        {"descriptionContains": "Đang chờ phê duyệt"},
        {"textContains": "Chờ phê duyệt"},
        {"descriptionContains": "Chờ phê duyệt"},
        {"textContains": "đang chờ duyệt"},
        {"descriptionContains": "đang chờ duyệt"},
        {"textContains": "Chờ duyệt"},
        {"descriptionContains": "Chờ duyệt"},
        {"textContains": "Yêu cầu đang chờ"},
        {"descriptionContains": "Yêu cầu đang chờ"},
        {"textContains": "Đã gửi yêu cầu"},
        {"descriptionContains": "Đã gửi yêu cầu"},
        {"textContains": "Hủy yêu cầu"},
        {"descriptionContains": "Hủy yêu cầu"},
        {"textContains": "Pending approval"},
        {"descriptionContains": "Pending approval"},
        {"textContains": "Cancel request"},
        {"descriptionContains": "Cancel request"},
    ]

    for sel in selectors:
        try:
            el = driver(**sel)
            if el.exists:
                return True
        except Exception:
            pass
    return False


def find_joined_label(driver):
    """
    Chỉ coi là joined thật nếu KHÔNG có marker của trạng thái
    'nhóm yêu cầu trả lời câu hỏi / đang chờ phê duyệt'.
    """
    if is_question_required_pending(driver) or is_join_request_pending(driver):
        return driver(text="__never_match__")

    candidates = (
        {"textContains": "Đã tham gia"},
        {"descriptionContains": "Đã tham gia"},
        {"textContains": "đã tham gia"},
        {"descriptionContains": "đã tham gia"},
        {"textContains": "Đã là thành viên"},
        {"descriptionContains": "Đã là thành viên"},
    )
    for sel in candidates:
        el = driver(**sel)
        if el.exists:
            return el
    return driver(text="__never_match__")


def _selector_exists(driver, selectors):
    for sel in selectors:
        try:
            if driver(**sel).exists:
                return True
        except Exception:
            pass
    return False


def find_join_group_button(driver):
    selectors = [
        {"textContains": "Tham gia nh\u00f3m"},
        {"descriptionContains": "Tham gia nh\u00f3m"},
        {"textContains": "Join group"},
        {"descriptionContains": "Join group"},
    ]
    for sel in selectors:
        try:
            el = driver(**sel)
            if el.exists:
                return el
        except Exception:
            pass
    return driver(text="__never_match__")


def is_member_group_home_screen(driver):
    """
    Facebook sometimes hides the explicit joined label on group home. In that
    layout, member-only controls are still visible.
    """
    if is_question_required_pending(driver) or is_join_request_pending(driver):
        return False

    member_tool_selectors = [
        {"textContains": "C\u00f4ng c\u1ee5 kh\u00e1c cho th\u00e0nh vi\u00ean"},
        {"descriptionContains": "C\u00f4ng c\u1ee5 kh\u00e1c cho th\u00e0nh vi\u00ean"},
        {"textContains": "Member tools"},
        {"descriptionContains": "Member tools"},
    ]
    if _selector_exists(driver, member_tool_selectors):
        return True

    composer_selectors = [
        {"textContains": "B\u1ea1n vi\u1ebft g\u00ec \u0111i"},
        {"descriptionContains": "B\u1ea1n vi\u1ebft g\u00ec \u0111i"},
        {"textContains": "Write something"},
        {"descriptionContains": "Write something"},
    ]
    invite_selectors = [
        {"text": "M\u1eddi"},
        {"description": "M\u1eddi"},
        {"textContains": "M\u1eddi"},
        {"descriptionContains": "M\u1eddi"},
        {"text": "Invite"},
        {"description": "Invite"},
        {"textContains": "Invite"},
        {"descriptionContains": "Invite"},
    ]
    return _selector_exists(driver, composer_selectors) and _selector_exists(driver, invite_selectors)


def get_group_membership_state(driver):
    """
    Trả về một trong:
    - question_required
    - pending_approval
    - joined
    - not_joined
    - unknown
    """
    if is_question_required_pending(driver):
        return "question_required"

    if is_join_request_pending(driver):
        return "pending_approval"

    if find_joined_label(driver).exists:
        return "joined"

    if find_join_group_button(driver).exists:
        return "not_joined"

    if is_member_group_home_screen(driver):
        return "joined"

    return "unknown"

def _dummy_not_found(driver):
    return driver(text="__never_match__")

def find_first_existing(driver, selectors):
    """
    selectors: list[dict]
    Trả về phần tử đầu tiên tồn tại, nếu không có trả về dummy element.
    """
    for sel in selectors:
        try:
            el = driver(**sel)
            if el.exists:
                return el
        except Exception:
            pass
    return _dummy_not_found(driver)

def find_next_button(driver):
    # XML bạn gửi cho thấy nút chính nằm ở content-desc là "Tiếp"
    selectors = [
        {"description": "Tiếp"},
        {"descriptionContains": "Tiếp"},
        {"text": "Tiếp"},
        {"textContains": "Tiếp"},
    ]
    return find_first_existing(driver, selectors)

def find_skip_button(driver):
    # XML bạn gửi cho thấy nút chính nằm ở content-desc là "Bỏ qua"
    selectors = [
        {"description": "Bỏ qua"},
        {"descriptionContains": "Bỏ qua"},
        {"text": "Bỏ qua"},
        {"textContains": "Bỏ qua"},
    ]
    return find_first_existing(driver, selectors)

def find_group_rules_checkbox(driver):
    selectors = [
        {
            "className": "android.widget.CompoundButton",
            "description": "Tôi đồng ý với các quy tắc nhóm",
        },
        {
            "className": "android.widget.CompoundButton",
            "descriptionContains": "Tôi đồng ý với các quy tắc nhóm",
        },
        {
            "className": "android.widget.CompoundButton",
            "text": "Tôi đồng ý với các quy tắc nhóm",
        },
        {
            "className": "android.widget.CompoundButton",
            "textContains": "Tôi đồng ý với các quy tắc nhóm",
        },
    ]
    return find_first_existing(driver, selectors)

def find_submit_button(driver):
    selectors = [
        {"className": "android.widget.Button", "description": "Gửi"},
        {"className": "android.widget.Button", "text": "Gửi"},
        {"description": "Gửi"},
        {"text": "Gửi"},
        {"descriptionContains": "Gửi"},
        {"textContains": "Gửi"},
        {"text": "GUI"},
        {"description": "GUI"},
    ]
    return find_first_existing(driver, selectors)

def safe_click(driver, el):
    try:
        el.click()
        return True
    except Exception:
        try:
            info = el.info
            bounds = info.get("bounds")
            if bounds:
                left = bounds["left"]
                top = bounds["top"]
                right = bounds["right"]
                bottom = bounds["bottom"]
                driver.click((left + right) // 2, (top + bottom) // 2)
                return True
        except Exception:
            pass
    return False

def find_exit_without_answer_popup(driver):
    selectors = [
        {"text": "Thoát mà không trả lời?"},
        {"textContains": "Thoát mà không trả lời"},
        {"description": "Thoát mà không trả lời?"},
        {"descriptionContains": "Thoát mà không trả lời"},
    ]
    return find_first_existing(driver, selectors)

def find_answer_questions_button(driver):
    selectors = [
        {"text": "TRẢ LỜI CÂU HỎI"},
        {"textContains": "TRẢ LỜI CÂU HỎI"},
        {"text": "Trả lời câu hỏi"},
        {"textContains": "Trả lời câu hỏi"},
        {"description": "TRẢ LỜI CÂU HỎI"},
        {"descriptionContains": "TRẢ LỜI CÂU HỎI"},
    ]
    return find_first_existing(driver, selectors)

def find_exit_button(driver):
    selectors = [
        {"text": "THOÁT"},
        {"textContains": "THOÁT"},
        {"text": "Thoát"},
        {"textContains": "Thoát"},
        {"description": "THOÁT"},
        {"descriptionContains": "THOÁT"},
    ]
    return find_first_existing(driver, selectors)


# async def handle_group_rules_and_submit(driver, sleep_each=2.0):
#     """
#     Xử lý màn:
#     - tick 'Tôi đồng ý với các quy tắc nhóm' nếu chưa tick
#     - bấm 'Gửi'
#     """
#     handled = False

#     try:
#         checkbox = find_group_rules_checkbox(driver)
#         if checkbox.exists:
#             checked = False
#             try:
#                 info = checkbox.info
#                 checked = bool(info.get("checked", False))
#             except Exception:
#                 try:
#                     checked = str(checkbox.attrib.get("checked", "false")).lower() == "true"
#                 except Exception:
#                     checked = False

#             if not checked:
#                 try:
#                     checkbox.click()
#                 except Exception:
#                     try:
#                         bounds = checkbox.info.get("bounds")
#                         if bounds:
#                             left = bounds["left"]
#                             top = bounds["top"]
#                             right = bounds["right"]
#                             bottom = bounds["bottom"]
#                             driver.click((left + right) // 2, (top + bottom) // 2)
#                     except Exception:
#                         pass

#                 log_message(
#                     f"{DEVICE_LIST_NAME[driver.serial]} - Đã tick 'Tôi đồng ý với các quy tắc nhóm'",
#                     logging.INFO,
#                 )
#                 handled = True
#                 await asyncio.sleep(sleep_each)
#             else:
#                 log_message(
#                     f"{DEVICE_LIST_NAME[driver.serial]} - Checkbox quy tắc nhóm đã được tick sẵn",
#                     logging.INFO,
#                 )

#         submit_btn = find_submit_button(driver)
#         if submit_btn.exists:
#             try:
#                 submit_btn.click()
#             except Exception:
#                 try:
#                     info = submit_btn.info
#                     bounds = info.get("bounds")
#                     if bounds:
#                         left = bounds["left"]
#                         top = bounds["top"]
#                         right = bounds["right"]
#                         bottom = bounds["bottom"]
#                         driver.click((left + right) // 2, (top + bottom) // 2)
#                 except Exception:
#                     raise

#             log_message(
#                 f"{DEVICE_LIST_NAME[driver.serial]} - Đã bấm nút 'Gửi'",
#                 logging.INFO,
#             )
#             handled = True
#             await asyncio.sleep(sleep_each)

#     except Exception as e:
#         log_message(
#             f"{DEVICE_LIST_NAME[driver.serial]} - Lỗi khi xử lý màn hình quy tắc nhóm / gửi: {e}",
#             logging.WARNING,
#         )

#     return handled

async def handle_group_rules_and_submit(driver, sleep_each=2.0):
    handled = False

    try:
        checkbox = find_group_rules_checkbox(driver)
        if checkbox.exists:
            checked = False
            try:
                checked = bool(checkbox.info.get("checked", False))
            except Exception:
                checked = False

            if not checked:
                if safe_click(driver, checkbox):
                    log_message(
                        f"{DEVICE_LIST_NAME[driver.serial]} - Đã tick 'Tôi đồng ý với các quy tắc nhóm'",
                        logging.INFO,
                    )
                    handled = True
                    await asyncio.sleep(sleep_each)

        submit_btn = find_submit_button(driver)
        if submit_btn.exists:
            if safe_click(driver, submit_btn):
                log_message(
                    f"{DEVICE_LIST_NAME[driver.serial]} - Đã bấm nút 'Gửi'",
                    logging.INFO,
                )
                handled = True
                await asyncio.sleep(sleep_each)

    except Exception as e:
        log_message(
            f"{DEVICE_LIST_NAME[driver.serial]} - Lỗi handle_group_rules_and_submit: {e}",
            logging.WARNING,
        )

    return handled

async def handle_post_join_intro_flow(driver, max_steps=8, sleep_each=2.0):
    """
    Sau khi bấm 'Tham gia nhóm', nếu FB hiện onboarding:
    - còn 'Tiếp' thì bấm
    - không còn 'Tiếp' mà còn 'Bỏ qua' thì bấm
    - lặp đến khi không còn cả 2 nút
    """
    clicked_any = False

    for step in range(max_steps):
        next_btn = find_next_button(driver)
        if next_btn.exists:
            try:
                next_btn.click()
                clicked_any = True
                log_message(
                    f"{DEVICE_LIST_NAME[driver.serial]} - Đã bấm nút 'Tiếp' (bước {step + 1})",
                    logging.INFO,
                )
                await asyncio.sleep(sleep_each)
                continue
            except Exception as e:
                log_message(
                    f"{DEVICE_LIST_NAME[driver.serial]} - Lỗi khi bấm 'Tiếp': {e}",
                    logging.WARNING,
                )

        skip_btn = find_skip_button(driver)
        if skip_btn.exists:
            try:
                skip_btn.click()
                clicked_any = True
                log_message(
                    f"{DEVICE_LIST_NAME[driver.serial]} - Đã bấm nút 'Bỏ qua' (bước {step + 1})",
                    logging.INFO,
                )
                await asyncio.sleep(sleep_each)
                continue
            except Exception as e:
                log_message(
                    f"{DEVICE_LIST_NAME[driver.serial]} - Lỗi khi bấm 'Bỏ qua': {e}",
                    logging.WARNING,
                )

        # Không còn cả 2 nút thì thoát
        break

    return clicked_any

async def reload_group_and_check_joined(driver, group_link, wait_after_reload=4.0):
    toolfacebook_lib.redirect_to(driver, _facebook_group_url(group_link))
    await asyncio.sleep(wait_after_reload)
    await disable_auto_rotation(driver, driver.serial)
    return get_group_membership_state(driver)

async def pull_to_refresh_current_group_page(driver, settle_after_swipe=4.0):
    """
    Vuốt từ trên xuống để refresh ngay tại trang nhóm hiện tại.
    """
    try:
        size = driver.window_size()
        if isinstance(size, dict):
            w = int(size.get("width", 720))
            h = int(size.get("height", 1600))
        else:
            w, h = size

        x = int(w * 0.5)
        start_y = int(h * 0.22)   # gần phía trên
        end_y = int(h * 0.72)     # kéo xuống sâu để refresh

        log_message(
            f"[{DEVICE_LIST_NAME[driver.serial]}] Vuốt từ trên xuống để refresh trang nhóm tại chỗ: ({x},{start_y}) -> ({x},{end_y})",
            logging.INFO,
        )

        await asyncio.to_thread(driver.swipe, x, start_y, x, end_y, 0.35)
        await asyncio.sleep(settle_after_swipe)
        return True

    except Exception as e:
        log_message(
            f"[{DEVICE_LIST_NAME[driver.serial]}] Lỗi khi refresh trang nhóm bằng vuốt xuống: {e}",
            logging.WARNING,
        )
        return False

# async def reload_group_after_question_submit(driver, group_link, wait_before_reload=15.0, retry_if_unknown=True):
#     """
#     Chỉ dùng cho case:
#     - đã trả lời câu hỏi
#     - đã bấm Gửi

#     Đợi lâu hơn để UI của Facebook cập nhật trạng thái nhóm.
#     """
#     log_message(
#         f"[{DEVICE_LIST_NAME[driver.serial]}] Đã gửi câu trả lời, chờ {wait_before_reload}s trước khi reload nhóm",
#         logging.INFO,
#     )
#     await asyncio.sleep(wait_before_reload)

#     toolfacebook_lib.redirect_to(driver, "https://facebook.com/" + group_link)
#     await asyncio.sleep(4.0)
#     await disable_auto_rotation(driver, driver.serial)

#     state = get_group_membership_state(driver)
#     log_message(
#         f"[{DEVICE_LIST_NAME[driver.serial]}] State sau reload lần 1 (sau trả lời câu hỏi): {state}",
#         logging.INFO,
#     )

#     if state != "unknown":
#         return state

#     if retry_if_unknown:
#         log_message(
#             f"[{DEVICE_LIST_NAME[driver.serial]}] State vẫn unknown, chờ thêm 6s rồi reload lại lần 2",
#             logging.INFO,
#         )
#         await asyncio.sleep(15.0)

#         toolfacebook_lib.redirect_to(driver, "https://facebook.com/" + group_link)
#         await asyncio.sleep(4.0)
#         await disable_auto_rotation(driver, driver.serial)

#         state = get_group_membership_state(driver)
#         log_message(
#             f"[{DEVICE_LIST_NAME[driver.serial]}] State sau reload lần 2 (sau trả lời câu hỏi): {state}",
#             logging.INFO,
#         )

#     return state

async def reload_group_after_question_submit(driver, group_link, wait_before_reload=15.0, retry_if_unknown=True):
    """
    Chỉ dùng cho case:
    - đã trả lời câu hỏi
    - đã bấm Gửi

    Đợi lâu hơn để UI của Facebook cập nhật trạng thái nhóm.
    """
    log_message(
        f"[{DEVICE_LIST_NAME[driver.serial]}] Đã gửi câu trả lời, chờ {wait_before_reload}s trước khi reload nhóm",
        logging.INFO,
    )
    await asyncio.sleep(wait_before_reload)

    # Lần 1: mở lại bằng link như cũ
    toolfacebook_lib.redirect_to(driver, _facebook_group_url(group_link))
    await asyncio.sleep(4.0)
    await disable_auto_rotation(driver, driver.serial)

    state = get_group_membership_state(driver)
    log_message(
        f"[{DEVICE_LIST_NAME[driver.serial]}] State sau reload lần 1 (sau trả lời câu hỏi): {state}",
        logging.INFO,
    )

    if state != "unknown":
        return state

    if retry_if_unknown:
        log_message(
            f"[{DEVICE_LIST_NAME[driver.serial]}] State vẫn unknown, chờ thêm 15s rồi refresh tại chỗ bằng vuốt xuống",
            logging.INFO,
        )
        await asyncio.sleep(15.0)

        refreshed = await pull_to_refresh_current_group_page(driver, settle_after_swipe=4.0)
        if not refreshed:
            log_message(
                f"[{DEVICE_LIST_NAME[driver.serial]}] Refresh bằng vuốt xuống thất bại, fallback mở lại bằng link nhóm",
                logging.WARNING,
            )
            toolfacebook_lib.redirect_to(driver, _facebook_group_url(group_link))
            await asyncio.sleep(4.0)

        await disable_auto_rotation(driver, driver.serial)

        state = get_group_membership_state(driver)
        log_message(
            f"[{DEVICE_LIST_NAME[driver.serial]}] State sau reload lần 2 (refresh tại chỗ sau trả lời câu hỏi): {state}",
            logging.INFO,
        )

    return state

async def mark_joined_success(driver, command_id, user_id, group_link, back_to_facebook=True):
    if user_id:
        try:
            result = await pymongo_management.update_joined_accounts(user_id, group_link)
            log_message(f"{DEVICE_LIST_NAME[driver.serial]} - {result[0]['message']}", result[1])
        except Exception as e:
            log_message(
                f"{DEVICE_LIST_NAME[driver.serial]} - Đã join nhóm nhưng cập nhật Mongo Joined_Accounts lỗi: {type(e).__name__}: {e}",
                logging.WARNING,
            )

    if command_id:
        try:
            await pymongo_management.execute_command(command_id, "Đã thực hiện")
        except Exception as e:
            log_message(
                f"{DEVICE_LIST_NAME[driver.serial]} - Đã join nhóm nhưng cập nhật trạng thái command lỗi: {type(e).__name__}: {e}",
                logging.WARNING,
            )

    if back_to_facebook:
        try:
            await toolfacebook_lib.back_to_facebook(driver)
        except Exception as e:
            log_message(
                f"{DEVICE_LIST_NAME[driver.serial]} - Đã join nhóm nhưng quay về Facebook lỗi: {type(e).__name__}: {e}",
                logging.WARNING,
            )

    return JoinResult("joined")

async def join_group(driver, command_id=None, user_id=None, group_link=None, back_to_facebook=True):
    toolfacebook_lib.redirect_to(driver, _facebook_group_url(group_link))
    await asyncio.sleep(2.0)
    await disable_auto_rotation(driver, driver.serial)

    log_message(
        f"{DEVICE_LIST_NAME[driver.serial]} - Đã mở trang nhóm {group_link}",
        logging.INFO,
    )

    if command_id:
        await pymongo_management.execute_command(command_id, "Bắt đầu thực hiện")

    log_message(
        f"[DEBUG] join_group START | user_id={user_id} | group_link={group_link}",
        logging.INFO,
    )

    initial_state = get_group_membership_state(driver)

    if initial_state == "question_required":
        log_message(
            f"{DEVICE_LIST_NAME[driver.serial]} - Nhóm đang ở trạng thái yêu cầu trả lời câu hỏi / chờ phê duyệt",
            logging.INFO,
        )

        if command_id:
            await pymongo_management.execute_command(command_id, "Nhóm yêu cầu trả lời câu hỏi")

        if back_to_facebook:
            await toolfacebook_lib.back_to_facebook(driver)

        return JoinResult("question_required", reason="Nhóm yêu cầu trả lời câu hỏi")

    if initial_state == "joined":
        return await mark_joined_success(
            driver=driver,
            command_id=command_id,
            user_id=user_id,
            group_link=group_link,
            back_to_facebook=back_to_facebook,
        )

    join_button_clicked = False
    join_button = find_join_group_button(driver)

    if join_button.exists:
        join_button.click()
        join_button_clicked = True

        log_message(
            f"{DEVICE_LIST_NAME[driver.serial]} - Đã bấm 'Tham gia nhóm', bắt đầu xử lý màn hình tiếp theo nếu có",
            logging.INFO,
        )

        await asyncio.sleep(5.0)

        # Case A: form câu hỏi hiện ngay sau khi bấm join
        if await is_question_form_screen(driver):
            ok_answer = await fill_all_question_answers_and_submit(driver, max_total_swipes=2)

            if ok_answer:
                state_after_submit = await reload_group_after_question_submit(
                    driver,
                    group_link,
                    wait_before_reload=15.0,
                    retry_if_unknown=True,
                )

                if state_after_submit == "joined":
                    return await mark_joined_success(
                        driver=driver,
                        command_id=command_id,
                        user_id=user_id,
                        group_link=group_link,
                        back_to_facebook=back_to_facebook,
                    )

                if user_id:
                    result = await pymongo_management.update_temp_joined_accounts(user_id, group_link)
                    log_message(f"{DEVICE_LIST_NAME[driver.serial]} - {result[0]['message']}", result[1])

                if command_id:
                    if state_after_submit == "question_required":
                        await pymongo_management.execute_command(command_id, "Nhóm yêu cầu trả lời câu hỏi")
                    elif state_after_submit == "pending_approval":
                        await pymongo_management.execute_command(command_id, "Đã thực hiện")
                    else:
                        await pymongo_management.execute_command(command_id, "Nhóm yêu cầu trả lời câu hỏi")

                if back_to_facebook:
                    await toolfacebook_lib.back_to_facebook(driver)

                if state_after_submit == "question_required":
                    return JoinResult("question_required", reason="Nhóm yêu cầu trả lời câu hỏi")

                return JoinResult("pending_approval", reason="Waiting for admin approval")

            try:
                await asyncio.to_thread(driver.press, "back")
                await asyncio.sleep(1.0)
            except Exception:
                pass

            exited = await handle_exit_question_popup(driver)
            if exited:
                if command_id:
                    await pymongo_management.execute_command(command_id, "Nhóm yêu cầu trả lời câu hỏi")

                if back_to_facebook:
                    await toolfacebook_lib.back_to_facebook(driver)

                return JoinResult("question_required", reason="Nhóm yêu cầu trả lời câu hỏi")

        # Giữ nguyên luồng Tiếp / Bỏ qua
        await handle_post_join_intro_flow(driver, max_steps=8, sleep_each=2.0)

        # Case B: có máy hiện form câu hỏi sau onboarding
        if await is_question_form_screen(driver):
            ok_answer = await fill_all_question_answers_and_submit(driver, max_total_swipes=2)

            if ok_answer:
                state_after_submit = await reload_group_after_question_submit(
                    driver,
                    group_link,
                    wait_before_reload=15.0,
                    retry_if_unknown=True,
                )

                if state_after_submit == "joined":
                    return await mark_joined_success(
                        driver=driver,
                        command_id=command_id,
                        user_id=user_id,
                        group_link=group_link,
                        back_to_facebook=back_to_facebook,
                    )

                if user_id:
                    result = await pymongo_management.update_temp_joined_accounts(user_id, group_link)
                    log_message(f"{DEVICE_LIST_NAME[driver.serial]} - {result[0]['message']}", result[1])

                if command_id:
                    if state_after_submit == "question_required":
                        await pymongo_management.execute_command(command_id, "Nhóm yêu cầu trả lời câu hỏi")
                    elif state_after_submit == "pending_approval":
                        await pymongo_management.execute_command(command_id, "Đã thực hiện")
                    else:
                        await pymongo_management.execute_command(command_id, "Nhóm yêu cầu trả lời câu hỏi")

                if back_to_facebook:
                    await toolfacebook_lib.back_to_facebook(driver)

                if state_after_submit == "question_required":
                    return JoinResult("question_required", reason="Nhóm yêu cầu trả lời câu hỏi")

                return JoinResult("pending_approval", reason="Waiting for admin approval")

            try:
                await asyncio.to_thread(driver.press, "back")
                await asyncio.sleep(1.0)
            except Exception:
                pass

            exited = await handle_exit_question_popup(driver)
            if exited:
                if command_id:
                    await pymongo_management.execute_command(command_id, "Nhóm yêu cầu trả lời câu hỏi")

                if back_to_facebook:
                    await toolfacebook_lib.back_to_facebook(driver)

                return JoinResult("question_required", reason="Nhóm yêu cầu trả lời câu hỏi")

        # Xử lý màn tick quy tắc nhóm + Gửi
        handled_rules = await handle_group_rules_and_submit(driver, sleep_each=2.0)
        if handled_rules:
            await asyncio.sleep(3.0)

        # Reload kiểm tra đã join chưa
        reloaded_state = await reload_group_and_check_joined(driver, group_link, wait_after_reload=4.0)

        if reloaded_state == "joined":
            return await mark_joined_success(
                driver=driver,
                command_id=command_id,
                user_id=user_id,
                group_link=group_link,
                back_to_facebook=back_to_facebook,
            )

        if reloaded_state == "question_required":
            log_message(
                f"{DEVICE_LIST_NAME[driver.serial]} - Reload xong phát hiện trạng thái: Nhóm yêu cầu trả lời câu hỏi",
                logging.INFO,
            )

            if command_id:
                await pymongo_management.execute_command(command_id, "Nhóm yêu cầu trả lời câu hỏi")

            if back_to_facebook:
                await toolfacebook_lib.back_to_facebook(driver)

            return JoinResult("question_required", reason="Nhóm yêu cầu trả lời câu hỏi")

        if reloaded_state == "pending_approval":
            if user_id:
                result = await pymongo_management.update_temp_joined_accounts(user_id, group_link)
                log_message(f"{DEVICE_LIST_NAME[driver.serial]} - {result[0]['message']}", result[1])

            if command_id:
                await pymongo_management.execute_command(command_id, "Đã thực hiện")

            if back_to_facebook:
                await toolfacebook_lib.back_to_facebook(driver)

            return JoinResult("pending_approval", reason="Waiting for admin approval")

        # fallback check tại chỗ
        await asyncio.sleep(2.0)
        current_state = get_group_membership_state(driver)

        if current_state == "joined":
            return await mark_joined_success(
                driver=driver,
                command_id=command_id,
                user_id=user_id,
                group_link=group_link,
                back_to_facebook=back_to_facebook,
            )

        if current_state == "question_required":
            if command_id:
                await pymongo_management.execute_command(command_id, "Nhóm yêu cầu trả lời câu hỏi")

            if back_to_facebook:
                await toolfacebook_lib.back_to_facebook(driver)

            return JoinResult("question_required", reason="Nhóm yêu cầu trả lời câu hỏi")

        if current_state == "pending_approval":
            if user_id:
                result = await pymongo_management.update_temp_joined_accounts(user_id, group_link)
                log_message(f"{DEVICE_LIST_NAME[driver.serial]} - {result[0]['message']}", result[1])

            if command_id:
                await pymongo_management.execute_command(command_id, "Đã thực hiện")

            if back_to_facebook:
                await toolfacebook_lib.back_to_facebook(driver)

            return JoinResult("pending_approval", reason="Waiting for admin approval")

        # Nếu back ra mà hiện popup câu hỏi thì bấm Thoát
        await asyncio.to_thread(driver.press, "back")
        await asyncio.sleep(1.5)

        exited = await handle_exit_question_popup(driver)
        if exited:
            if command_id:
                await pymongo_management.execute_command(command_id, "Nhóm yêu cầu trả lời câu hỏi")

            if back_to_facebook:
                await toolfacebook_lib.back_to_facebook(driver)

            return JoinResult("question_required", reason="Nhóm yêu cầu trả lời câu hỏi")

        final_state_after_click = await reload_group_and_check_joined(driver, group_link, wait_after_reload=4.0)
        if final_state_after_click == "joined":
            return await mark_joined_success(
                driver=driver,
                command_id=command_id,
                user_id=user_id,
                group_link=group_link,
                back_to_facebook=back_to_facebook,
            )

        if final_state_after_click == "question_required":
            if command_id:
                await pymongo_management.execute_command(command_id, "Nhóm yêu cầu trả lời câu hỏi")

            if back_to_facebook:
                await toolfacebook_lib.back_to_facebook(driver)

            return JoinResult("question_required", reason="Nhóm yêu cầu trả lời câu hỏi")

        if final_state_after_click == "pending_approval":
            if user_id:
                result = await pymongo_management.update_temp_joined_accounts(user_id, group_link)
                log_message(f"{DEVICE_LIST_NAME[driver.serial]} - {result[0]['message']}", result[1])

            if command_id:
                await pymongo_management.execute_command(command_id, "Đã thực hiện")

            if back_to_facebook:
                await toolfacebook_lib.back_to_facebook(driver)

            return JoinResult("pending_approval", reason="Waiting for admin approval")

    # Không còn nút join mà cũng chưa hiện joined => thường là đang chờ duyệt
    if not join_button_clicked:
        final_state = get_group_membership_state(driver)

        if final_state == "question_required":
            log_message(
                f"{DEVICE_LIST_NAME[driver.serial]} - Nhóm đang ở trạng thái yêu cầu trả lời câu hỏi / chờ phê duyệt",
                logging.INFO,
            )

            if command_id:
                await pymongo_management.execute_command(command_id, "Nhóm yêu cầu trả lời câu hỏi")

            if back_to_facebook:
                await toolfacebook_lib.back_to_facebook(driver)

            return JoinResult("question_required", reason="Nhóm yêu cầu trả lời câu hỏi")

        if final_state == "pending_approval":
            if user_id:
                result = await pymongo_management.update_temp_joined_accounts(user_id, group_link)
                log_message(f"{DEVICE_LIST_NAME[driver.serial]} - {result[0]['message']}", result[1])

            if command_id:
                await pymongo_management.execute_command(command_id, "Đã thực hiện")

            if back_to_facebook:
                await toolfacebook_lib.back_to_facebook(driver)

            return JoinResult("pending_approval", reason="Waiting for admin approval")

        if final_state == "joined":
            return await mark_joined_success(
                driver=driver,
                command_id=command_id,
                user_id=user_id,
                group_link=group_link,
                back_to_facebook=back_to_facebook,
            )

        log_message(
            f"{DEVICE_LIST_NAME[driver.serial]} - Nhóm đang chờ duyệt hoặc không còn nút tham gia: {group_link}",
            logging.INFO,
        )

        if command_id:
            await pymongo_management.execute_command(command_id, "Đã thực hiện")

        if back_to_facebook:
            await toolfacebook_lib.back_to_facebook(driver)

        return JoinResult("pending_approval", reason="Waiting for admin approval")

    if command_id:
        await pymongo_management.execute_command(command_id, "Nhóm yêu cầu trả lời câu hỏi")

    if back_to_facebook:
        await toolfacebook_lib.back_to_facebook(driver)

    return JoinResult("failed", reason="Could not complete join flow")
    
async def check_unapproved_groups(driver, user_id):
    unapproved_groups = await pymongo_management.get_unapproved_groups(user_id)
    for group in unapproved_groups:
        await join_group(driver, "", user_id, group['Link'], False)
        
async def left_group(driver, group_link=None, user_id=None, back_to_facebook=True):
    toolfacebook_lib.redirect_to(driver, _facebook_group_url(group_link))
    await asyncio.sleep(10)
    await disable_auto_rotation(driver, driver.serial)
    log_message(f"[DEBUG] left_group START | user_id={user_id} | group_link={group_link}", logging.INFO)

    joined_group = find_joined_label(driver)
    if not joined_group.exists:
        return

    joined_btn = driver(textContains="tham gia")
    if not joined_btn.exists:
        joined_btn = driver(descriptionContains="tham gia")
    
    if joined_btn.exists:
        joined_btn.click()
        await asyncio.sleep(2)
        leave_option = driver(textStartsWith="Rời nhóm")
        if not leave_option.exists:
            leave_option = driver(textStartsWith="Leave group")
        if leave_option.exists:
            leave_option.click()
            await asyncio.sleep(3)
            confirm_btn = driver(text="RỜI NHÓM")
            if not confirm_btn.exists:
                # ID thường gặp của nút OK/Confirm bên phải trong Android
                confirm_btn = driver(resourceId="com.facebook.katana:id/button1")
            if confirm_btn.exists:
                confirm_btn.click()
                await asyncio.sleep(3)
                log_message(f"{DEVICE_LIST_NAME[driver.serial]} - Đã rời khỏi nhóm: {group_link}", logging.INFO)
                if user_id:
                    result = await pymongo_management.update_left_accounts(user_id, group_link)
                    log_message(f"{DEVICE_LIST_NAME[driver.serial]} - {result[0]['message']}", result[1])
                if back_to_facebook:
                    await toolfacebook_lib.back_to_facebook(driver)
