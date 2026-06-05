import asyncio
import logging
import json
import random
from util import *
import toolfacebook_lib
from main_merged import disable_auto_rotation
from module.join_group import join_group
EMOTION = [
    "Thích",
    "Yêu thích",
    "Thương thương"
]

COMMENTS = [
    # Nhóm quan tâm, hỏi thông tin
    "Còn tuyển không ạ? 👨‍💼",
    "Vị trí này còn không ạ?",
    "Mình có thể ứng tuyển được không?",
    "Mình quan tâm position này ạ 👍",

    # Nhóm thể hiện hứng thú
    "Công việc hay quá! 😍",
    "Phù hợp với mình ghê! 😊",
    "Mình đang tìm việc như này!",
    "Cơ hội tốt quá! 🎯",
    "Công ty có vẻ ổn nhỉ! 😎",
    "Môi trường làm việc tuyệt! 💼",
    "Thử apply xem sao! 🚀",
    "Đúng ngành mình rồi!",
    "Thanks for sharing! 🙏",
    "Cảm ơn info hay! ✨",

    # Nhóm tích cực, professional
    "Cảm ơn bạn đã share!",
    "Thông tin hữu ích quá! 👌",
    "Note lại để apply sau! 📝",
    "Công ty uy tín nhỉ! 🏢",
    "Mong được cơ hội thử! 🤝",
    "Hy vọng sẽ có cơ hội! 🤞",
    "Up cho mọi người cùng biết! ⬆️",
    "Good luck cho ai apply! 🍀"
]

#Thả cảm xúc vào bài viết (Phẫn nộ sẽ đổi thành Buồn, "đấy là tính năng")
async def like_post(driver, emotion="like"):
    """
    Tìm nút like phía dưới, scroll vào màn hình, nhấn like.\n
    Nhấn giữ để hiện bảng emote, kéo thả vào emote tương ứng:
    'Thích', 'Yêu thích', 'Thương Thương', 'Haha', 'Wow', 'Buồn', 'Phẫn nộ'
    """
    log_message("Bắt đầu like post")
    # Tìm nút like
    like_button = await scroll_until_element_visible(driver, {("xpath", '//android.widget.Button[contains(@content-desc, "Nút Thích.")]')})
    # Đọc bài viết 1 tí
    # await asyncio.sleep(random.uniform(5,15))
    await asyncio.sleep(random.uniform(5,10)) # giảm thời gian chờ

    if like_button == None:
        log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Không thể tìm được nút like", logging.ERROR)
        return
    if emotion == "like":
        like_button.click()
        log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Đã thả cảm xúc Thích")
        return

    # Chờ menu cảm xúc xuất hiện
    like_button.long_click()
    await asyncio.sleep(random.uniform(1,2))
    
    # Tìm và chọn cảm xúc mong muốn
    emotion_element = await my_find_element(driver, {("xpath", f'//com.facebook.feedback.sharedcomponents.reactions.dock.RopeStyleUFIDockView[@content-desc="{emotion}"]')})
    try:
        emotion_element.click()
        await asyncio.sleep(random.uniform(2,3))
        log_message(f"Đã thả cảm xúc {emotion}")
        return
    except Exception:
        log_message(f"Không tìm được emotion: {emotion}", logging.ERROR)
        return

# Bình luận vào bài viết
async def comment_post(driver, text):
    """
    Tìm nút comment phía dưới, nhấn vào và comment đoạn comment cho trước"""
    log_message("Bắt đầu comment post")

    # Thoát giao diện comment
    async def exit():
        exit = await my_find_element(driver, {("xpath", '//android.widget.Button[contains(@content-desc, "Đóng")]')})
        try:
            exit.click()
            log_message("Đã thoát giao diện comment")
        except Exception:
            log_message("Không tìm được nút thoát", logging.ERROR)
            await go_to_home_page(driver)
            return

    # Tìm nút comment
    comment_button = await scroll_until_element_visible(driver, {("xpath", '//android.widget.Button[contains(@content-desc, "Bình luận")]')})
    # Đọc bài viết một tí
    # await asyncio.sleep(random.uniform(5,15))
    await asyncio.sleep(random.uniform(5,10)) # giảm thời gian chờ
    try:
        comment_button.click()
        await asyncio.sleep(random.uniform(2,5))
    except Exception:
        log_message("Không thể tìm được nút comment", logging.ERROR)
        return

    # Nhập comment
    binhluan = await my_find_element(driver, {("xpath", '//android.widget.AutoCompleteTextView')})
    try:
        # Nhập comment, thay thế bằng hàm input text nếu bị ban, và sửa được hàm input text
        await asyncio.sleep(random.uniform(2,5))
        binhluan.set_text(text)
        await asyncio.sleep(random.uniform(2,5))
    except Exception:
        log_message("Không tìm được ô nhập comment", logging.ERROR)
        await exit()
        return

    # Gửi comment
    send_comment = await my_find_element(driver, {("xpath", '//android.widget.Button[contains(@content-desc, "Gửi")]')})
    try:
        send_comment.click()
        await asyncio.sleep(random.uniform(3,5))
        log_message("Đã comment")
    except Exception:
        log_message("Không tìm được nút gửi", logging.ERROR)
    await exit()
    return

def load_groups(file_path: str = "nhom_tuyen_dung.json"):
    """Đọc dữ liệu nhóm từ file JSON đã lưu."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        log_message(f"Không tìm thấy file '{file_path}'. Hãy chạy get_groups_data_and_save trước.", logging.WARNING)
    except Exception as e:
        log_message(f"Lỗi khi đọc file '{file_path}': {e}", logging.ERROR)
    return None


def get_random_group(file_path: str = "nhom_tuyen_dung.json", only_link: bool = True):
    """Lấy ngẫu nhiên một nhóm từ file JSON đã lưu."""
    data = load_groups(file_path)
    if not data:
        return None

    groups = data.get("groups", [])
    if not groups:
        log_message("Danh sách nhóm rỗng.", logging.WARNING)
        return None

    g = random.choice(groups)
    return g.get("link") if only_link else g


def _normalize_group_link(raw: str | None) -> str:
    value = str(raw or "").strip()
    if not value:
        return ""

    value = value.replace("m.facebook.com/", "facebook.com/")
    if "facebook.com/" in value:
        value = value.split("facebook.com/", 1)[1]

    return value.lstrip("/")


def _resolve_group_targets(group: dict | None) -> tuple[str, str]:
    group = group or {}
    browse_link = str(group.get("link") or "").strip()
    group_link = _normalize_group_link(group.get("Link") or browse_link)

    if not browse_link and group_link:
        browse_link = "https://facebook.com/" + group_link

    return browse_link, group_link

# lướt facebook
async def surf_fb(driver, action_name: str, screen_standing_event=None, restart_event=None):
    log_message("Lướt tin nhóm tuyển dụng")
    group = get_random_group(only_link=False)
    if not group:
        log_message("Không lấy được nhóm để lướt", logging.WARNING)
        await go_to_home_page(driver)
        return

    link, group_link = _resolve_group_targets(group)
    if not link or not group_link:
        log_message(f"Nhóm có dữ liệu link không hợp lệ: {group}", logging.WARNING)
        await go_to_home_page(driver)
        return

    current_account = (get_current_account(driver.serial) or "").strip()
    if not current_account:
        log_message(
            f"[{DEVICE_LIST_NAME.get(driver.serial, driver.serial)}] Không lấy được current_account khi lướt nhóm, vẫn tiếp tục nhưng không cập nhật Joined_Accounts",
            logging.WARNING,
        )

    logging.info(
        f"Đi đến nhóm: {link} | group_link={group_link} | current_account={current_account or 'missing'}"
    )
    try:
        if check_interruption_events(driver, restart_event, screen_standing_event, action_name):
            return

        join_result = await join_group(
            driver,
            command_id=None,
            user_id=current_account or None,
            group_link=group_link,
            back_to_facebook=False,
        )
        log_message(
            f"[{DEVICE_LIST_NAME.get(driver.serial, driver.serial)}] Lướt nhóm: join_result.status={join_result.status} | reason={join_result.reason or ''}",
            logging.INFO,
        )

        toolfacebook_lib.redirect_to(driver, link)
        await asyncio.sleep(random.uniform(5,8))
        await disable_auto_rotation(driver, driver.serial)

        await asyncio.sleep(random.uniform(5,8))
        scroll_count = random.randint(50, 100)
        # scroll_count = random.randint(20, 50) # Giảm số lần scroll để giảm thời gian chạy 
        # Kiểm tra interruption events 
        if check_interruption_events(driver, restart_event, screen_standing_event, action_name):
            return 
        while scroll_count > 0:
            # Kiểm tra interruption events 
            try:
                if check_interruption_events(driver, restart_event, screen_standing_event, action_name):
                    return 
                count = random.randint(1,2)
                await nature_scroll(driver, max_roll=count, isFast=random.choice([True,False]))
                # await asyncio.sleep(random.uniform(1,10))
                await asyncio.sleep(random.uniform(2,5))  # Giảm thời gian chờ giữa các lần scroll
                if scroll_count % 50 == 0:
                    await comment_post(driver, text=random.choice(COMMENTS))
                    await asyncio.sleep(random.uniform(3,5))
                    exit= await my_find_element(driver,{("xpath",'//*[@text="THOÁT"]')},1)
                    if exit:
                        exit.click()
                        driver.press("back")
                if scroll_count % 11 == 0:
                    await like_post(driver, random.choice(EMOTION))
                    await asyncio.sleep(random.uniform(3,5))                                                                                                           
                scroll_count -= 1
            except asyncio.CancelledError:
                log_message("Tác vụ Lướt nhóm tuyển dụng bị hủy", logging.WARNING)
                raise
        await asyncio.sleep(random.uniform(2,5))
        log_message("Đã hoàn thành lướt nhóm tuyển dụng")
    except Exception as e:    
        log_message(f"Error {e}", logging.ERROR)

    await go_to_home_page(driver)
