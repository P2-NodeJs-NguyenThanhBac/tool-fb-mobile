import os
import re
import json
import random
import logging
import asyncio
import unicodedata
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional
from util.my_utils import my_find_element, my_find_elements, check_interruption_events
from util import log_message
import time
from main_merged import disable_auto_rotation
from util import *

# Optional imports from surf_fb for interactions
try:
    from module.surf_fb import like_post, comment_post, EMOTION, COMMENTS
    HAS_SURF_FB = True
except Exception:
    HAS_SURF_FB = False

LAST_PROFILE_FRIEND_COUNT: dict[str, int] = {}


# ---------------------- Name helpers ----------------------

def vn_remove_tone(text: str) -> str:
    text = str(text or "")
    text = text.replace("đ", "d").replace("Đ", "D")
    return "".join(
        char for char in unicodedata.normalize("NFD", text)
        if unicodedata.category(char) != "Mn"
    )


def normalize_person_name_for_compare(text: str) -> str:
    text = (text or "").strip().lower()
    text = re.sub(r"[_\-.]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    try:
        text = vn_remove_tone(text)
    except Exception:
        pass
    return text


def get_allowed_account_names_for_device(device_id: str) -> List[str]:
    """
    Lấy danh sách account.name từ base của đúng device.
    """
    names: List[str] = []

    try:
        device = load_device_account(device_id)
        if device and isinstance(device.get("accounts"), list):
            for acc in device["accounts"]:
                name = (acc.get("name") or "").strip()
                if name:
                    names.append(name)
    except Exception:
        pass

    seen = set()
    result = []
    for n in names:
        key = normalize_person_name_for_compare(n)
        if key and key not in seen:
            seen.add(key)
            result.append(n)
    return result


def name_match_score(candidate_text: str, allowed_names: List[str]) -> tuple[int, Optional[str]]:
    """
    Tính điểm match giữa text đang thấy trên màn hình và danh sách account.name trong base.
    Trả về:
      - bonus score
      - matched base name tốt nhất
    """
    cand_norm = normalize_person_name_for_compare(candidate_text)
    if not cand_norm:
        return 0, None

    cand_tokens = set(cand_norm.split())
    cand_list = cand_norm.split()

    best_score = 0
    best_name = None

    for base_name in allowed_names:
        base_norm = normalize_person_name_for_compare(base_name)
        if not base_norm:
            continue

        if cand_norm == base_norm:
            return 1000, base_name

        base_tokens = set(base_norm.split())
        base_list = base_norm.split()
        inter = cand_tokens & base_tokens

        if not inter:
            continue

        overlap_ratio = len(inter) / max(len(base_tokens), 1)
        score = 0

        if overlap_ratio >= 1.0:
            score += 700
        elif overlap_ratio >= 0.8:
            score += 450
        elif overlap_ratio >= 0.6:
            score += 250
        elif overlap_ratio >= 0.5:
            score += 120

        if cand_list and base_list:
            if cand_list[0] == base_list[0]:
                score += 30
            if cand_list[-1] == base_list[-1]:
                score += 30

        if len(cand_list) >= 3 and len(base_list) >= 3:
            score += 20

        if score > best_score:
            best_score = score
            best_name = base_name

    return best_score, best_name


def has_vietnamese_surname(text: str) -> bool:
    text_lower = normalize_person_name_for_compare(text)
    surnames = [
        'bui', 'nguyen', 'tran', 'le', 'pham', 'hoang',
        'phan', 'vu', 'dang', 'vo', 'do', 'ho', 'duong'
    ]
    return any(s in text_lower.split()[:1] for s in surnames)


def _parse_friend_count_text(value: str) -> Optional[int]:
    raw = str(value or "").strip()
    if not raw:
        return None

    normalized = normalize_person_name_for_compare(raw)
    if "nguoi ban" not in normalized:
        return None
    if "ban chung" in normalized or "mutual" in normalized:
        return None

    match = re.search(
        r"(\d[\d\s.,]*)\s*(nghin|ngan|k|trieu|m)?\s*nguoi\s+ban\b",
        normalized,
    )
    if not match:
        return None

    number_text = re.sub(r"\s+", "", match.group(1) or "")
    unit = (match.group(2) or "").strip()
    if not number_text:
        return None

    try:
        if unit in {"nghin", "ngan", "k", "trieu", "m"}:
            multiplier = 1000000 if unit in {"trieu", "m"} else 1000
            if "," in number_text or "." in number_text:
                number = float(number_text.replace(",", "."))
            else:
                number = float(number_text)
            return int(round(number * multiplier))

        digits = re.sub(r"\D", "", number_text)
        if not digits:
            return None
        return int(digits)
    except (TypeError, ValueError):
        return None


def extract_number_of_friends_from_xml(xml_dump: str) -> Optional[int]:
    try:
        root = ET.fromstring(xml_dump or "")
    except ET.ParseError:
        return None

    for node in root.iter():
        for attr_name in ("content-desc", "text"):
            count = _parse_friend_count_text(node.attrib.get(attr_name, ""))
            if count is not None:
                return count
    return None


def get_last_profile_friend_count(device_id: str) -> Optional[int]:
    return LAST_PROFILE_FRIEND_COUNT.get(str(device_id or "").strip())


# ---------------------- Persistence helpers ----------------------

async def get_facebook_account_name(driver) -> str:
    """
    Lấy tên account Facebook từ profile.
    Ưu tiên mạnh các tên có trong base của đúng device.
    """
    print("🎯 Lấy tên Facebook từ profile - bản ưu tiên account.name trong base...")

    try:
        device_id = driver.serial
        allowed_names = get_allowed_account_names_for_device(device_id)

        log_message(
            f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] allowed account names = {allowed_names}",
            logging.INFO,
        )

        driver.press("home")
        driver.app_start("com.facebook.katana")
        await asyncio.sleep(3)

        try:
            profile_locators = [
                ('Đi tới trang cá nhân', 'xpath', '//*[@content-desc="Đi tới trang cá nhân"]'),
                ('Tab Trang cá nhân', 'xpath', '//*[contains(@content-desc,"Trang cá nhân, Tab")]'),
                ('Ảnh đại diện của bạn', 'xpath', '//*[@content-desc="ảnh đại diện của bạn"]'),
            ]

            user = None
            matched_locator = None
            matched_label = None

            for label, by, value in profile_locators:
                try:
                    el = await my_find_element(
                        driver,
                        {(by, value)},
                        2,
                        back_if_not_found=False
                    )
                    if el:
                        user = el
                        matched_locator = value
                        matched_label = label
                        break
                except Exception:
                    continue

            if not user:
                log_message(
                    f"máy {DEVICE_LIST_NAME[driver.serial]}: không tìm thấy nút vào trang cá nhân",
                    logging.ERROR
                )
                return "unknown_user"

            user.click()
            log_message(
                f"máy {DEVICE_LIST_NAME[driver.serial]}: click '{matched_label}' | locator: {matched_locator}",
                logging.INFO
            )

        except Exception as e:
            log_message(
                f"máy {DEVICE_LIST_NAME[driver.serial]}: lỗi khi bấm vào trang cá nhân {type(e).__name__}\n{e}",
                logging.ERROR
            )
            return "unknown_user"

        await asyncio.sleep(4)

        print("🔍 Phân tích trang profile...")
        xml_dump = driver.dump_hierarchy()
        if len(xml_dump) < 500:
            await asyncio.sleep(1.5)
            xml_dump = driver.dump_hierarchy()

        with open("profile_debug.xml", "w", encoding="utf-8") as f:
            f.write(xml_dump)

        friend_count = extract_number_of_friends_from_xml(xml_dump)
        if friend_count is not None:
            LAST_PROFILE_FRIEND_COUNT[device_id] = friend_count
            log_message(
                f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] number_of_friends={friend_count}",
                logging.INFO,
            )
        else:
            LAST_PROFILE_FRIEND_COUNT.pop(device_id, None)

        root = ET.fromstring(xml_dump)

        size = driver.window_size()
        screen_w, screen_h = size[0], size[1]

        def parse_bounds(bounds: str):
            m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds or "")
            if not m:
                return 9999, 9999, 9999, 9999
            x1, y1, x2, y2 = map(int, m.groups())
            return x1, y1, x2, y2

        def clean_name(text: str) -> str:
            text = text.strip()
            text = ''.join(c if c.isalnum() or c == ' ' else '' for c in text)
            text = re.sub(r"\s+", "_", text).strip("_")
            return text

        def looks_like_person_name(text: str) -> bool:
            if not text:
                return False

            text = text.strip()
            text_lower = text.lower()
            words = text.split()

            blacklist_phrases = [
                'đang nghĩ về', 'thinking about', "what's on your mind",
                'bài viết', 'posts', 'ảnh', 'photos', 'bạn bè', 'friends',
                'theo dõi', 'follow', 'tin nhắn', 'message', 'giới thiệu', 'about',
                'xem thêm', 'see more', 'chỉnh sửa', 'edit', 'cài đặt', 'settings',
                'hoạt động', 'activity', 'video', 'story', 'reels', 'live',
                'người bạn', 'mutual friends', 'bạn chung', 'common friends',
                'thêm story', 'add story', 'tạo', 'create', 'chia sẻ', 'share',
                'thông tin cá nhân', 'xem tất cả', 'tất cả',
                '1 giờ', '2 giờ', '3 giờ', '4 giờ',
                'thông báo của chrome', 'vpn đang bật', 'chuông im lặng',
                'notification', 'chrome'
            ]

            if any(phrase in text_lower for phrase in blacklist_phrases):
                return False

            location_blacklist_exact = {
                "ha tinh", "hà tĩnh",
                "hanoi", "ha noi", "hà nội",
                "da nang", "đà nẵng",
                "sai gon", "sài gòn",
                "ho chi minh", "hồ chí minh",
                "can tho", "cần thơ",
                "hai phong", "hải phòng",
                "quang ninh", "quảng ninh",
                "thanh hoa", "thanh hóa",
                "nghe an", "nghệ an",
                "bac ninh", "bắc ninh",
                "dong nai", "đồng nai",
                "binh duong", "bình dương",
                "vung tau", "vũng tàu",
            }
            if text_lower in location_blacklist_exact:
                return False

            if text.isdigit():
                return False

            if not (2 <= len(text) <= 40):
                return False

            if not any(c.isalpha() for c in text):
                return False

            if len(words) > 5:
                return False

            if len(text) >= 8 and text.upper() == text and any(c.isalpha() for c in text):
                return False

            return True

        def score_candidate(
            text: str,
            class_name: str,
            resource_id: str,
            bounds: str,
            screen_h: Optional[int] = None
        ) -> int:
            x1, y1, x2, y2 = parse_bounds(bounds)
            words = text.split()
            text_lower = text.lower()
            score = 0

            # format tên người
            if len(words) >= 2:
                proper = all((w and (not w[0].isalpha() or w[0].isupper())) for w in words)
                score += 100 if proper else 60
            elif len(words) == 1:
                score += 70 if text and text[0].isupper() else 30

            # bonus cho 3-4 từ
            if 3 <= len(words) <= 4:
                score += 35
            elif len(words) == 2:
                score += 10

            # độ dài hợp lý
            if 3 <= len(text) <= 25:
                score += 15

            # class
            if "TextView" in class_name or "View" in class_name:
                score += 10

            # phạt mạnh button ngắn
            if "Button" in class_name:
                score -= 50
                if len(words) <= 2:
                    score -= 40

            # ưu tiên nửa trên màn hình
            if screen_h:
                upper_third = int(screen_h * 0.36)
                upper_half = int(screen_h * 0.5)
                lower_bad = int(screen_h * 0.7)

                if y1 <= upper_third:
                    score += 150
                elif y1 <= upper_half:
                    score += 90
                elif y1 >= lower_bad:
                    score -= 140

            # giữ rule cũ
            if 350 <= y1 <= 620:
                score += 120
            elif 620 < y1 <= 850:
                score += 30
            elif y1 > 1000:
                score -= 120

            # gần trung tâm ngang
            center_x = (x1 + x2) // 2
            if 160 <= center_x <= 560:
                score += 20

            # độ rộng hợp lý
            width = x2 - x1
            if 180 <= width <= 420:
                score += 15

            # bonus họ Việt mạnh hơn
            vietnamese_name_patterns = [
                'bùi', 'nguyễn', 'trần', 'lê', 'phạm', 'hoàng',
                'phan', 'vũ', 'đặng', 'võ', 'đỗ', 'hồ', 'dương'
            ]
            if any(p in text_lower for p in vietnamese_name_patterns):
                score += 35

            bad_patterns = ['tab', 'button', '...', ',']
            if any(p in text_lower for p in bad_patterns):
                score -= 20

            return score

        candidates = []

        for node in root.iter():
            class_name = node.attrib.get("class", "")
            bounds = node.attrib.get("bounds", "")
            resource_id = node.attrib.get("resource-id", "")

            raw_text = node.attrib.get("text", "").strip()
            content_desc = node.attrib.get("content-desc", "").strip()

            candidate_texts = []
            if raw_text:
                candidate_texts.append(raw_text)
            if content_desc and content_desc != raw_text:
                candidate_texts.append(content_desc)

            x1, y1, x2, y2 = parse_bounds(bounds)

            # loại status bar / system text quá sát mép trên
            if y1 < 80:
                continue

            for cand_text in candidate_texts:
                if not looks_like_person_name(cand_text):
                    continue

                base_score = score_candidate(
                    cand_text,
                    class_name,
                    resource_id,
                    bounds,
                    screen_h=screen_h
                )
                match_bonus, matched_base_name = name_match_score(cand_text, allowed_names)
                final_score = base_score + match_bonus

                candidates.append({
                    "text": cand_text,
                    "score": final_score,
                    "base_score": base_score,
                    "match_bonus": match_bonus,
                    "matched_base_name": matched_base_name,
                    "x1": x1,
                    "y1": y1,
                    "x2": x2,
                    "y2": y2,
                    "bounds": bounds,
                    "class": class_name,
                    "resource_id": resource_id
                })

        if candidates:
            ranked = sorted(candidates, key=lambda x: (-x["score"], x["y1"]))

            print("\nTOP 5 candidates:")
            for c in ranked[:5]:
                print({
                    "text": c["text"],
                    "score": c["score"],
                    "base_score": c.get("base_score"),
                    "match_bonus": c.get("match_bonus"),
                    "matched_base_name": c.get("matched_base_name"),
                    "y1": c["y1"],
                    "bounds": c["bounds"],
                    "class": c["class"],
                    "resource_id": c["resource_id"],
                })

            matched_candidates = [c for c in ranked if c.get("match_bonus", 0) >= 200]

            if matched_candidates:
                best = matched_candidates[0]
                log_message(
                    f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] ưu tiên candidate match base: "
                    f"{best['text']} -> {best.get('matched_base_name')}",
                    logging.INFO,
                )
            else:
                upper_header_candidates = [c for c in candidates if c["y1"] <= int(screen_h * 0.36)]
                upper_candidates = [c for c in candidates if c["y1"] <= int(screen_h * 0.5)]

                if upper_header_candidates:
                    upper_header_candidates.sort(key=lambda x: (-x["score"], x["y1"]))
                    best = upper_header_candidates[0]
                elif upper_candidates:
                    upper_candidates.sort(key=lambda x: (-x["score"], x["y1"]))
                    best = upper_candidates[0]
                else:
                    best = ranked[0]

                # nếu điểm sít sao, ưu tiên tên có họ Việt
                if len(ranked) >= 2:
                    first = best
                    second = ranked[1]
                    if abs(first["score"] - second["score"]) <= 30:
                        if not has_vietnamese_surname(first["text"]) and has_vietnamese_surname(second["text"]):
                            best = second

            print("\n📌 Candidate tốt nhất:")
            print(best)

            result = clean_name(best["text"])

            driver.press("back")
            await asyncio.sleep(2)

            if result and len(result) >= 2:
                print(f"\n✅ THÀNH CÔNG: {result}")
                return result

        driver.press("back")
        await asyncio.sleep(2)
        return "unknown_user"

    except Exception as e:
        print(f"❌ Lỗi: {e}")
        try:
            driver.press("back")
            await asyncio.sleep(2)
        except Exception:
            pass
        return "unknown_user"


async def get_facebook_username(driver, device_id: str) -> str:
    """Trả về tên user thực tế từ device_id + account_name."""
    account_name = await get_facebook_account_name(driver)
    return f"{device_id}_{account_name}"


def load_friends_data(username: str) -> Dict[str, bool]:
    base_folder = "base_banbe"
    if not os.path.exists(base_folder):
        os.makedirs(base_folder)

    filename = os.path.join(base_folder, f"{username}_banbe.json")
    if os.path.exists(filename):
        try:
            with open(filename, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_friends_data(username: str, friends_data: Dict[str, bool]) -> bool:
    base_folder = "base_banbe"
    if not os.path.exists(base_folder):
        os.makedirs(base_folder)

    filename = os.path.join(base_folder, f"{username}_banbe.json")
    try:
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(friends_data, f, ensure_ascii=False, indent=2)
        log_message(f"✅ Đã lưu dữ liệu {len(friends_data)} bạn bè vào {filename}")
        return True
    except Exception as e:
        log_message(f"❌ Lỗi khi lưu file: {e}", logging.ERROR)
        return False


# ---------------------- Screen parsing ----------------------
def extract_friends_from_screen(driver) -> List[str]:
    friends_found: List[str] = []
    try:
        xml_dump = driver.dump_hierarchy()
        root = ET.fromstring(xml_dump)

        potential_names = []
        for node in root.iter():
            class_name = node.attrib.get("class", "")
            text = node.attrib.get("text", "").strip()
            resource_id = node.attrib.get("resource-id", "")
            if text and len(text) > 1:
                excluded_keywords = [
                    "bạn bè", "friends", "tab", "tìm kiếm", "search", "menu",
                    "thêm", "gợi ý", "xem", "tìm", "online", "active", "home",
                    "bạn chung", "thêm bạn", "chấp nhận", "từ chối", "tin nhắn",
                    "theo dõi", "đã theo dõi", "bỏ theo dõi", "kết bạn", "đã kết bạn",
                    "phút", "giờ", "ngày", "tuần", "tháng", "năm", "trước", "giây",
                    "hoạt động", "trực tuyến", "offline", "đang hoạt động",
                ]
                text_lower = text.lower()
                if not any(keyword in text_lower for keyword in excluded_keywords):
                    if (
                        2 <= len(text) <= 50
                        and any(c.isalpha() for c in text)
                        and not text.isdigit()
                        and not all(c in "!@#$%^&*()_+-=[]{}|;:,.<>?" for c in text)
                    ):
                        potential_names.append(
                            {
                                "text": text,
                                "class": class_name,
                                "resource_id": resource_id,
                                "bounds": node.attrib.get("bounds", ""),
                            }
                        )

        for item in potential_names:
            text = item["text"]
            words = text.split()
            if len(words) >= 2:
                is_name_format = all(w[0].isupper() if w and w[0].isalpha() else True for w in words)
                if is_name_format:
                    friends_found.append(text)
                    continue
            if len(words) == 1 and len(text) >= 3 and text[0].isupper():
                friends_found.append(text)

        if not friends_found:
            for node in root.iter():
                class_name = node.attrib.get("class", "")
                bounds = node.attrib.get("bounds", "")
                if bounds and "ViewGroup" in class_name:
                    try:
                        m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds)
                        if m:
                            left, top, right, bottom = map(int, m.groups())
                            width = right - left
                            height = bottom - top
                            if 200 <= width <= 500 and 40 <= height <= 150:
                                for child in node.iter():
                                    child_text = child.attrib.get("text", "").strip()
                                    child_class = child.attrib.get("class", "")
                                    if (
                                        "TextView" in child_class
                                        and child_text
                                        and 3 <= len(child_text) <= 50
                                        and any(c.isalpha() for c in child_text)
                                    ):
                                        excluded_in_card = [
                                            "bạn chung",
                                            "thêm bạn",
                                            "tin nhắn",
                                            "theo dõi",
                                            "phút",
                                            "giờ",
                                            "hoạt động",
                                        ]
                                        if not any(
                                            kw in child_text.lower() for kw in excluded_in_card
                                        ):
                                            friends_found.append(child_text)
                                            break
                    except Exception:
                        continue

        unique = sorted(set(friends_found))
        return unique
    except Exception as e:
        log_message(f"❌ Lỗi khi trích xuất tên bạn bè: {e}", logging.ERROR)
        return []


# ---------------------- Interactions on profile ----------------------
async def surf_friend_profile_with_interactions_v2(driver, scroll_count: Optional[int] = None):
    if scroll_count is None:
        scroll_count = random.randint(10, 20)
    log_message(f"📜 Lướt tường với {scroll_count} lần và tương tác")
    try:
        for i in range(scroll_count):
            driver.swipe_ext("up", scale=random.uniform(0.5, 0.7))
            await asyncio.sleep(random.uniform(1.5, 3))

            if HAS_SURF_FB:
                if (i + 1) % 11 == 0:
                    try:
                        emotion = random.choice(EMOTION)
                        await like_post(driver, emotion)
                        await asyncio.sleep(random.uniform(2, 3))
                    except Exception:
                        pass
    except Exception as e:
        log_message(f"❌ Lỗi khi lướt tường: {e}", logging.ERROR)


async def _search_and_click_friend(driver, friend_name: str):
    friend_locators = [
        ("text", friend_name),
        ("xpath", f"//*[@text='{friend_name}']"),
        ("xpath", f"//*[contains(@text, '{friend_name}')]"),
    ]

    friend_el = await my_find_element(driver, friend_locators)
    if friend_el:
        friend_el.click()
        return True

    log_message(f"🔍 Không thấy {friend_name} trên màn hình, bắt đầu lướt tìm kiếm...")
    found = False

    for _ in range(7):
        friend_el = await my_find_element(driver, friend_locators)
        if friend_el:
            friend_el.click()
            found = True
            break
        driver.swipe_ext("up", scale=random.uniform(0.6, 0.8))
        await asyncio.sleep(random.uniform(1, 1.5))

    if not found:
        for _ in range(8):
            friend_el = await my_find_element(driver, friend_locators)
            if friend_el:
                friend_el.click()
                found = True
                break
            driver.swipe_ext("down", scale=random.uniform(0.6, 0.8))
            await asyncio.sleep(random.uniform(1, 1.5))
    return found


async def visit_friend_profile_v2(driver, friend_name: str, device_id: str, username: Optional[str] = None, friends_data: Optional[Dict[str, bool]] = None) -> bool:
    try:
        log_message(f"👀 Đang thăm tường: {friend_name}")
        if not await _search_and_click_friend(driver, friend_name):
            log_message(f"❌ Không tìm thấy {friend_name}", logging.WARNING)
            return False

        await asyncio.sleep(random.uniform(3, 5))

        if username and friends_data is not None:
            friends_data[friend_name] = True
            save_friends_data(username, friends_data)

        scroll_times = random.randint(10, 20)
        try:
            await surf_friend_profile_with_interactions_v2(driver, scroll_times)
        except Exception as e:
            log_message(f"⚠️ Lỗi khi chạy tương tác: {e}")
            for _ in range(scroll_times):
                driver.swipe_ext("up", scale=random.uniform(0.5, 0.7))
                await asyncio.sleep(random.uniform(1, 2))

        driver.press("back")
        await asyncio.sleep(random.uniform(2, 3))
        log_message(f"✅ Đã thăm xong: {friend_name}")
        return True
    except Exception as e:
        log_message(f"❌ Lỗi khi thăm tường {friend_name}: {e}", logging.ERROR)
        return False


def _get_friends_tab_locators() -> List[tuple[str, str]]:
    return [
        ("desc", "Bạn bè, Tab 2/6"),
        ("desc", "Friends, Tab 2/6"),
        ("desc", "Bạn bè, Tab 3/6"),
        ("desc", "Friends, Tab 3/6"),
        ("text", "Bạn bè"),
        ("text", "Friends"),
        ("desc", "Bạn bè"),
        ("desc", "Friends"),
        ("resourceId", "com.facebook.katana:id/bookmarks_tab"),
        ("xpath", "//*[contains(@content-desc, 'Bạn bè') and contains(@content-desc, 'Tab')]"),
        ("xpath", "//*[contains(@content-desc, 'Friends') and contains(@content-desc, 'Tab')]"),
        ("xpath", "//android.widget.FrameLayout[1]/android.widget.LinearLayout[1]/android.widget.FrameLayout[1]/android.widget.FrameLayout[1]/android.widget.LinearLayout[1]/android.widget.FrameLayout[1]/android.widget.LinearLayout[1]/android.widget.LinearLayout[1]/android.widget.FrameLayout[2]/android.widget.LinearLayout[1]/android.widget.FrameLayout[3]"),
    ]


_FRIENDS_TAB_XPATHS = (
    '//*[contains(@content-desc, "Bạn bè") and contains(@content-desc, "Tab")]',
    '//*[contains(@content-desc, "Friends") and contains(@content-desc, "Tab")]',
    '//*[contains(@content-desc, "Bạn bè")]',
    '//*[contains(@content-desc, "Friends")]',
    '//*[contains(@text, "Bạn bè") and contains(@text, "Tab")]',
    '//*[contains(@text, "Friends") and contains(@text, "Tab")]',
    '//*[contains(@text, "Bạn bè")]',
    '//*[contains(@text, "Friends")]',
)


def _contains_friends_marker(text: str) -> bool:
    raw_text = (text or "").strip()
    if "Bạn bè" in raw_text or "Friends" in raw_text:
        return True

    normalized = normalize_person_name_for_compare(raw_text)
    if not normalized:
        return False
    return "ban be" in normalized or "friends" in normalized


def _is_friends_tab_label(text: str) -> bool:
    raw_text = (text or "").strip()
    if "Bạn bè, Tab" in raw_text or "Friends, Tab" in raw_text:
        return True

    normalized = normalize_person_name_for_compare(raw_text)
    if not normalized:
        return False
    return "tab" in normalized and ("ban be" in normalized or "friends" in normalized)


def _is_friends_tab_candidate(
    label: str,
    class_name: str = "",
    clickable: bool = False,
    top: Optional[int] = None,
) -> bool:
    if _is_friends_tab_label(label):
        return True

    if not _contains_friends_marker(label):
        return False

    normalized_class = (class_name or "").lower()
    is_nav_like_class = (
        "android.view.view" in normalized_class
        or "android.widget.button" in normalized_class
    )

    if clickable and is_nav_like_class and top is not None and top <= 320:
        return True

    return False


def _extract_friends_tab_from_xml(xml_dump: str) -> Optional[Dict[str, object]]:
    try:
        root = ET.fromstring(xml_dump)
    except ET.ParseError as e:
        log_message(f"❌ Lỗi parse XML tab bạn bè: {e}", logging.WARNING)
        return None

    candidates: List[Dict[str, object]] = []

    for node in root.iter():
        class_name = node.attrib.get("class", "")
        text = (node.attrib.get("text") or "").strip()
        content_desc = (node.attrib.get("content-desc") or "").strip()
        label = content_desc or text
        bounds = _parse_node_bounds(node.attrib.get("bounds", ""))
        if not bounds:
            continue

        left, top, right, bottom = bounds
        if right <= left or bottom <= top:
            continue
        clickable = node.attrib.get("clickable", "").lower() == "true"

        if not _is_friends_tab_candidate(label, class_name=class_name, clickable=clickable, top=top):
            continue

        candidates.append(
            {
                "label": label,
                "class_name": class_name,
                "bounds": bounds,
                "x": (left + right) // 2,
                "y": (top + bottom) // 2,
                "selected": node.attrib.get("selected", "").lower() == "true",
                "strong_match": _is_friends_tab_label(label),
            }
        )

    if not candidates:
        return None

    candidates.sort(
        key=lambda item: (
            0 if item.get("selected") else 1,
            0 if item.get("strong_match") else 1,
            int(item["y"]),
            int(item["x"]),
        )
    )
    return candidates[0]


def _find_friends_tab(driver) -> Optional[Dict[str, object]]:
    candidates: List[Dict[str, object]] = []
    seen = set()

    for xpath in _FRIENDS_TAB_XPATHS:
        try:
            selector = driver.xpath(xpath)
            if not selector.exists:
                continue

            for element in selector.all():
                try:
                    info = getattr(element, "info", {}) or {}
                except Exception:
                    info = {}

                text = str(info.get("text") or "").strip()
                content_desc = str(
                    info.get("contentDescription") or info.get("content-desc") or ""
                ).strip()
                label = content_desc or text
                class_name = str(info.get("className") or info.get("class") or "")
                clickable = bool(info.get("clickable", False))
                x, y = _extract_click_point_from_info(info)
                top = y if isinstance(y, int) else None

                if not _is_friends_tab_candidate(
                    label,
                    class_name=class_name,
                    clickable=clickable,
                    top=top,
                ):
                    continue

                key = (label, x, y)
                if key in seen:
                    continue
                seen.add(key)

                candidates.append(
                    {
                        "label": label,
                        "element": element,
                        "x": x,
                        "y": y,
                        "class_name": class_name,
                        "selected": bool(info.get("selected", False)),
                        "strong_match": _is_friends_tab_label(label),
                    }
                )
        except Exception:
            continue

    if candidates:
        candidates.sort(
            key=lambda item: (
                0 if item.get("selected") else 1,
                0 if item.get("strong_match") else 1,
                int(item["y"]) if isinstance(item.get("y"), int) else 999999,
                int(item["x"]) if isinstance(item.get("x"), int) else 999999,
            )
        )
        return candidates[0]

    try:
        xml_dump = driver.dump_hierarchy()
    except Exception:
        return None
    return _extract_friends_tab_from_xml(xml_dump)


async def _click_friends_tab(driver, tab_info: Dict[str, object]) -> bool:
    element = tab_info.get("element")
    x = tab_info.get("x")
    y = tab_info.get("y")
    label = str(tab_info.get("label", "")).strip()

    if element is not None:
        try:
            await asyncio.to_thread(element.click)
            return True
        except Exception as e:
            log_message(f"⚠️ Click trực tiếp tab bạn bè '{label}' lỗi: {e}", logging.WARNING)

    if not isinstance(x, int) or not isinstance(y, int):
        log_message(f"❌ Không lấy được tọa độ tab bạn bè: {label}", logging.WARNING)
        return False

    try:
        await asyncio.to_thread(driver.click, x, y)
        return True
    except Exception as e:
        log_message(f"❌ Lỗi click tab bạn bè '{label}': {e}", logging.WARNING)
        return False


async def _open_facebook_friends_tab(driver, action_name: str, screen_standing_event=None, restart_event=None):
    recovery_steps = (
        "current_screen",
        "app_start",
        "back",
        "home_and_app_start",
    )

    for step in recovery_steps:
        if check_interruption_events(driver, restart_event, screen_standing_event, action_name):
            return False

        tab_info = _find_friends_tab(driver)
        if tab_info:
            if not await _click_friends_tab(driver, tab_info):
                return False

            await asyncio.sleep(3)
            await disable_auto_rotation(driver, driver.serial)
            return True

        if step == "app_start":
            log_message("⚠️ Không thấy tab bạn bè ở màn hiện tại, mở lại Facebook", logging.INFO)
            driver.app_start("com.facebook.katana")
            await asyncio.sleep(3)
        elif step == "back":
            log_message("⚠️ Không thấy tab bạn bè, thử back về màn chính Facebook", logging.INFO)
            driver.press("back")
            await asyncio.sleep(2)
        elif step == "home_and_app_start":
            log_message("⚠️ Vẫn chưa thấy tab bạn bè, thử về home rồi mở lại Facebook", logging.INFO)
            driver.press("home")
            await asyncio.sleep(1)
            driver.app_start("com.facebook.katana")
            await asyncio.sleep(3)

            tab_info = _find_friends_tab(driver)
            if tab_info:
                if not await _click_friends_tab(driver, tab_info):
                    return False

                await asyncio.sleep(3)
                await disable_auto_rotation(driver, driver.serial)
                return True

    log_message("❌ Không tìm thấy tab bạn bè", logging.ERROR)
    return False


_ACCEPT_FRIEND_REQUEST_KEYWORDS = (
    "xac nhan loi moi ket ban",
    "chap nhan loi moi ket ban",
    "confirm friend request",
    "accept friend request",
)

_ACCEPT_FRIEND_REQUEST_LABELS = (
    "Xác nhận lời mời kết bạn",
    "Chấp nhận lời mời kết bạn",
    "Confirm friend request",
    "Accept friend request",
)

_ACCEPT_FRIEND_REQUEST_XPATHS = (
    '//android.widget.Button[contains(@text, "Xác nhận lời mời kết bạn")]',
    '//android.widget.Button[contains(@content-desc, "Xác nhận lời mời kết bạn")]',
    '//android.widget.Button[contains(@text, "Chấp nhận lời mời kết bạn")]',
    '//android.widget.Button[contains(@content-desc, "Chấp nhận lời mời kết bạn")]',
    '//android.widget.Button[contains(@text, "Confirm friend request")]',
    '//android.widget.Button[contains(@content-desc, "Confirm friend request")]',
    '//android.widget.Button[contains(@text, "Accept friend request")]',
    '//android.widget.Button[contains(@content-desc, "Accept friend request")]',
)


def _parse_node_bounds(bounds: str) -> Optional[tuple[int, int, int, int]]:
    match = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds or "")
    if not match:
        return None
    return tuple(map(int, match.groups()))


def _is_accept_friend_request_button_label(text: str) -> bool:
    raw_text = (text or "").strip()
    if any(label in raw_text for label in _ACCEPT_FRIEND_REQUEST_LABELS):
        return True

    normalized = normalize_person_name_for_compare(text)
    if not normalized:
        return False
    return any(keyword in normalized for keyword in _ACCEPT_FRIEND_REQUEST_KEYWORDS)


def _extract_click_point_from_info(info: Dict[str, object]) -> tuple[Optional[int], Optional[int]]:
    bounds = info.get("bounds") or {}

    if isinstance(bounds, dict):
        left = int(bounds.get("left", 0))
        top = int(bounds.get("top", 0))
        right = int(bounds.get("right", 0))
        bottom = int(bounds.get("bottom", 0))
        if right > left and bottom > top:
            return (left + right) // 2, (top + bottom) // 2

    if isinstance(bounds, str):
        parsed = _parse_node_bounds(bounds)
        if parsed:
            left, top, right, bottom = parsed
            if right > left and bottom > top:
                return (left + right) // 2, (top + bottom) // 2

    return None, None


def _find_visible_accept_friend_request_buttons(driver) -> List[Dict[str, object]]:
    buttons: List[Dict[str, object]] = []
    seen = set()

    for xpath in _ACCEPT_FRIEND_REQUEST_XPATHS:
        try:
            selector = driver.xpath(xpath)
            if not selector.exists:
                continue

            for element in selector.all():
                try:
                    info = getattr(element, "info", {}) or {}
                except Exception:
                    info = {}

                text = str(info.get("text") or "").strip()
                content_desc = str(
                    info.get("contentDescription") or info.get("content-desc") or ""
                ).strip()
                label = content_desc or text
                x, y = _extract_click_point_from_info(info)

                key = (label, x, y)
                if key in seen:
                    continue
                seen.add(key)

                buttons.append(
                    {
                        "label": label,
                        "element": element,
                        "x": x,
                        "y": y,
                    }
                )
        except Exception:
            continue

    if buttons:
        buttons.sort(
            key=lambda item: (
                int(item["y"]) if isinstance(item.get("y"), int) else 999999,
                int(item["x"]) if isinstance(item.get("x"), int) else 999999,
            )
        )
        return buttons

    try:
        xml_dump = driver.dump_hierarchy()
    except Exception:
        return []
    return _extract_visible_accept_friend_request_buttons(xml_dump)


def _extract_visible_accept_friend_request_buttons(xml_dump: str) -> List[Dict[str, object]]:
    try:
        root = ET.fromstring(xml_dump)
    except ET.ParseError as e:
        log_message(f"❌ Lỗi parse XML lời mời kết bạn: {e}", logging.WARNING)
        return []

    buttons: List[Dict[str, object]] = []
    seen = set()

    for node in root.iter():
        if node.attrib.get("class", "") != "android.widget.Button":
            continue
        if node.attrib.get("clickable", "").lower() != "true":
            continue
        if node.attrib.get("enabled", "").lower() != "true":
            continue

        text = (node.attrib.get("text") or "").strip()
        content_desc = (node.attrib.get("content-desc") or "").strip()
        if not (
            _is_accept_friend_request_button_label(text)
            or _is_accept_friend_request_button_label(content_desc)
        ):
            continue

        bounds = _parse_node_bounds(node.attrib.get("bounds", ""))
        if not bounds:
            continue

        left, top, right, bottom = bounds
        if right <= left or bottom <= top:
            continue

        label = content_desc or text
        key = (label, bounds)
        if key in seen:
            continue
        seen.add(key)

        buttons.append(
            {
                "label": label,
                "bounds": bounds,
                "x": (left + right) // 2,
                "y": (top + bottom) // 2,
            }
        )

    buttons.sort(key=lambda item: (int(item["y"]), int(item["x"])))
    return buttons


async def _click_accept_friend_request_button(driver, button: Dict[str, object]) -> bool:
    element = button.get("element")
    x = button.get("x")
    y = button.get("y")
    label = str(button.get("label", "")).strip()

    if element is not None:
        try:
            await asyncio.to_thread(element.click)
            return True
        except Exception as e:
            log_message(f"⚠️ Click trực tiếp nút xác nhận '{label}' lỗi: {e}", logging.WARNING)

    if not isinstance(x, int) or not isinstance(y, int):
        log_message(f"❌ Không lấy được tọa độ nút xác nhận: {label}", logging.WARNING)
        return False

    try:
        await asyncio.to_thread(driver.click, x, y)
        return True
    except Exception as e:
        log_message(f"❌ Lỗi click nút xác nhận '{label}': {e}", logging.WARNING)
        return False


async def _accept_friend_requests_in_current_friends_tab(
    driver,
    username: str,
    action_name: str,
    screen_standing_event=None,
    restart_event=None,
    max_time: Optional[int] = None,
) -> bool:
    if max_time is None:
        max_time = random.randint(1800, 2400)

    start_time = time.time()
    accepted_count = 0
    empty_scroll_rounds = 0
    last_clicked_target = None
    repeated_same_target = 0

    log_message(
        f"⏱️ Timeout đồng ý kết bạn cho lượt này: {max_time}s",
        logging.INFO,
    )

    try:
        while True:
            if check_interruption_events(driver, restart_event, screen_standing_event, action_name):
                return False

            if max_time < time.time() - start_time:
                note = (
                    "Phase Facebook: Có thể gặp lỗi trong quá trình tự động đồng ý kết bạn:\n"
                    "Thực hiện các bước sau:\n"
                    "- Đăng nhập vào tài khoản gặp lỗi\n"
                    "- Bấm vào tab bạn bè\n"
                    "- Xác nhận lời mời kết bạn đầu tiên\n"
                    "- Nếu bạn đó đã đạt giới hạn kết bạn thì xóa đi, nếu không thì tool chạy bình thường"
                )
                try:
                    log_note_acc(driver, username, note)
                except Exception:
                    log_message("Lỗi khi lưu file log note")
                break

            visible_buttons = _find_visible_accept_friend_request_buttons(driver)

            if visible_buttons:
                empty_scroll_rounds = 0
                target = visible_buttons[0]
                target_label = str(target.get("label", "")).strip()
                target_key = (target_label, target.get("x"), target.get("y"))

                if target_key == last_clicked_target:
                    repeated_same_target += 1
                else:
                    last_clicked_target = target_key
                    repeated_same_target = 0

                if repeated_same_target >= 3:
                    log_message(
                        f"⚠️ Nút '{target_label}' vẫn còn sau nhiều lần click, thử cuộn để tải lại danh sách",
                        logging.WARNING,
                    )
                    driver.swipe_ext("up", scale=0.3)
                    await asyncio.sleep(1.5)
                    last_clicked_target = None
                    repeated_same_target = 0
                    continue

                if not await _click_accept_friend_request_button(driver, target):
                    return False

                accepted_count += 1
                log_message(f"✅ Đã xác nhận lời mời kết bạn: {target_label}", logging.INFO)
                await asyncio.sleep(1.5)
                continue

            last_clicked_target = None
            repeated_same_target = 0
            empty_scroll_rounds += 1

            if empty_scroll_rounds >= 3:
                break

            driver.swipe_ext("up", scale=0.55)
            await asyncio.sleep(1.5)
    except asyncio.CancelledError:
        log_message("Tác vụ đồng ý lời mời kết bạn bị hủy", logging.WARNING)
        raise
    except Exception as e:
        log_message(f"❌ Lỗi bấm chấp nhận bạn bè: {e}", logging.ERROR)
        return False

    log_message(f"✅ Đã xử lý {accepted_count} lời mời kết bạn", logging.INFO)
    return True


async def accept_facebook_friend_requests(driver, device_id: str, action_name: str, screen_standing_event=None, restart_event=None) -> bool:
    username = f"{device_id}_unknown_user"

    try:
        opened = await _open_facebook_friends_tab(
            driver,
            action_name=action_name,
            screen_standing_event=screen_standing_event,
            restart_event=restart_event,
        )
        if not opened:
            return False

        return await _accept_friend_requests_in_current_friends_tab(
            driver,
            username=username,
            action_name=action_name,
            screen_standing_event=screen_standing_event,
            restart_event=restart_event,
        )
    except Exception as e:
        log_message(f"❌ Lỗi tổng quát khi đồng ý lời mời kết bạn: {e}", logging.ERROR)
        await go_to_home_page(driver)
        return False


async def load_facebook_friends_list_advanced(driver, device_id: str, action_name: str, visit_friends: bool = True, accept_requests: bool = True, screen_standing_event=None, restart_event=None) -> bool:
    """Thu thập danh sách bạn bè và thăm ngẫu nhiên, đánh dấu ngay."""
    username = await get_facebook_username(driver, device_id)
    friends_data = load_friends_data(username)

    try:
        opened = await _open_facebook_friends_tab(
            driver,
            action_name=action_name,
            screen_standing_event=screen_standing_event,
            restart_event=restart_event,
        )
        if not opened:
            return False

        if accept_requests:
            await _accept_friend_requests_in_current_friends_tab(
                driver,
                username=username,
                action_name=f"{action_name} - đồng ý lời mời",
                screen_standing_event=screen_standing_event,
                restart_event=restart_event,
            )
            reopened = await _open_facebook_friends_tab(
                driver,
                action_name=action_name,
                screen_standing_event=screen_standing_event,
                restart_event=restart_event,
            )
            if not reopened:
                return False

        friends_list_locators = [
            ("xpath", "//androidx.viewpager.widget.ViewPager/android.widget.FrameLayout[1]"
             "/android.widget.FrameLayout[1]/android.widget.FrameLayout[1]"
             "/android.view.ViewGroup[1]/android.widget.LinearLayout[1]"
             "/android.widget.FrameLayout[1]/android.widget.FrameLayout[1]"
             "/android.view.ViewGroup[1]/android.view.ViewGroup[1]/android.view.ViewGroup[1]"
             "/androidx.recyclerview.widget.RecyclerView[1]/android.view.ViewGroup[1]"
             "/android.view.ViewGroup[1]/androidx.recyclerview.widget.RecyclerView[1]"
             "/android.widget.Button[4]"),
            ("xpath", "//androidx.viewpager.widget.ViewPager/android.widget.FrameLayout[1]"
             "/android.widget.FrameLayout[1]/android.widget.FrameLayout[1]"
             "/android.view.ViewGroup[1]/android.widget.LinearLayout[1]"
             "/android.widget.FrameLayout[1]/android.widget.FrameLayout[1]"
             "/android.view.ViewGroup[1]/android.view.ViewGroup[1]/android.view.ViewGroup[1]"
             "/androidx.recyclerview.widget.RecyclerView[1]/android.view.ViewGroup[1]"
             "/android.view.ViewGroup[1]/androidx.recyclerview.widget.RecyclerView[1]"
             "/android.widget.Button[3]"),
            ("xpath", "//androidx.viewpager.widget.ViewPager/android.widget.FrameLayout[1]"
             "/android.widget.FrameLayout[1]/android.widget.FrameLayout[1]"
             "/android.view.ViewGroup[1]/android.widget.LinearLayout[1]"
             "/android.widget.FrameLayout[1]/android.widget.FrameLayout[1]"
             "/android.view.ViewGroup[1]/android.view.ViewGroup[1]/android.view.ViewGroup[1]"
             "/androidx.recyclerview.widget.RecyclerView[1]/android.view.ViewGroup[1]"
             "/android.view.ViewGroup[1]/androidx.recyclerview.widget.RecyclerView[1]"
             "/android.widget.Button[2]"),
            ("xpath", "//*[@resource-id='android:id/list']"
             "/android.view.ViewGroup[1]/android.view.ViewGroup[1]"
             "/androidx.recyclerview.widget.RecyclerView[1]"
             "/android.widget.Button[4]"),
            ("xpath", "//*[@resource-id='android:id/list']"
             "/android.view.ViewGroup[1]/android.view.ViewGroup[1]"
             "/androidx.recyclerview.widget.RecyclerView[1]"
             "/android.widget.Button[3]"),
            ("xpath", "//*[@resource-id='android:id/list']"
             "/android.view.ViewGroup[1]/android.view.ViewGroup[1]"
             "/androidx.recyclerview.widget.RecyclerView[1]"
             "/android.widget.Button[2]"),
            ("text", "Bạn bè"),
            ("text", "All friends"),
            ("text", "Tất cả bạn bè"),
            ("text", "Danh sách bạn bè"),
            ("desc", "Bạn bè"),
            ("desc", "All friends"),
            ("desc", "Tất cả bạn bè"),
            ("xpath", "//android.widget.Button[contains(@text, 'bạn')]"),
            ("xpath", "//android.widget.Button[contains(@text, 'friend')]"),
            ("xpath", "//android.widget.Button[contains(@content-desc, 'bạn')]"),
            ("xpath", "//android.widget.Button[contains(@content-desc, 'friend')]")
        ]
        btn = await my_find_element(driver, friends_list_locators, 1)
        if not btn:
            log_message("❌ Không tìm thấy nút danh sách bạn bè", logging.ERROR)
            return False
        btn.click()

        if check_interruption_events(driver, restart_event, screen_standing_event, action_name):
            return
        await asyncio.sleep(3)

        all_friends = set()
        no_new = 0
        for _ in range(50):
            try:
                if check_interruption_events(driver, restart_event, screen_standing_event, action_name):
                    return
                names = extract_friends_from_screen(driver)
                before = len(all_friends)
                all_friends.update(names)
                after = len(all_friends)
                if after == before:
                    no_new += 1
                else:
                    no_new = 0
                if no_new >= 3:
                    break
                driver.swipe_ext("up", scale=random.uniform(0.6, 0.8))
                await asyncio.sleep(random.uniform(1.5, 2.5))
            except asyncio.CancelledError:
                log_message("Tác vụ xem tường bạn bè bị hủy", logging.WARNING)
                raise

        for name in all_friends:
            friends_data.setdefault(name, False)
        save_friends_data(username, friends_data)

        if not visit_friends or not friends_data:
            return True

        unvisited = [n for n, v in friends_data.items() if not v]
        if not unvisited:
            for k in list(friends_data.keys()):
                friends_data[k] = False
            unvisited = list(friends_data.keys())

        visible = []
        for _ in range(min(5, max(1, len(unvisited) // 2 + 1))):
            if check_interruption_events(driver, restart_event, screen_standing_event, action_name):
                return
            onscreen = extract_friends_from_screen(driver)
            for n in onscreen:
                if n in unvisited and n not in visible:
                    visible.append(n)
            if len(visible) >= 8:
                break
            driver.swipe_ext("up", scale=random.uniform(0.4, 0.6))
            await asyncio.sleep(1)

        to_visit: List[str] = []
        if visible:
            to_visit.extend(random.sample(visible, min(6, len(visible))))
        remaining = [n for n in unvisited if n not in to_visit]
        if remaining and len(to_visit) < 10:
            to_visit.extend(random.sample(remaining, min(4, len(remaining), 10 - len(to_visit))))
        if not to_visit:
            count = random.randint(min(3, len(unvisited)), min(8, len(unvisited)))
            to_visit = random.sample(unvisited, count)

        success = 0
        for idx, name in enumerate(to_visit, 1):
            if check_interruption_events(driver, restart_event, screen_standing_event, action_name):
                return
            log_message(f"🔄 Thăm bạn {idx}/{len(to_visit)}: {name}")
            try:
                try:
                    if check_interruption_events(driver, restart_event, screen_standing_event, action_name):
                        return
                    result = await visit_friend_profile_v2(driver, name, device_id, username, friends_data)
                    if result:
                        success += 1
                        save_friends_data(username, friends_data)
                except asyncio.CancelledError:
                    log_message("Tác vụ xem tường bạn bè bị hủy", logging.WARNING)
                    raise
            except Exception as e:
                log_message(f"❌ Lỗi khi thăm {name}: {e}", logging.ERROR)
            await asyncio.sleep(random.uniform(3, 6))

        log_message(f"📈 Kết quả: Thăm thành công {success}/{len(to_visit)} bạn")
        return True
    except Exception as e:
        log_message(f"❌ Lỗi tổng quát: {e}", logging.ERROR)
        await go_to_home_page(driver)
        return False


__all__ = [
    "get_facebook_account_name",
    "get_facebook_username",
    "extract_number_of_friends_from_xml",
    "get_last_profile_friend_count",
    "load_friends_data",
    "save_friends_data",
    "extract_friends_from_screen",
    "surf_friend_profile_with_interactions_v2",
    "visit_friend_profile_v2",
    "accept_facebook_friend_requests",
    "load_facebook_friends_list_advanced",
]
