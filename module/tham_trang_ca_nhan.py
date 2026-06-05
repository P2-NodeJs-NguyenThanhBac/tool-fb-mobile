import asyncio
import logging
import random
from util import *

EMOTION = [
    "Thích",
    "Yêu thích",
    "Thương thương",
    "Haha",
    "Wow",
    "Buồn",
]

#Thả cảm xúc vào bài viết (Phẫn nộ sẽ đổi thành Buồn, "đấy là tính năng")
async def like_post(driver, emotion="like"):
    """
    Tìm nút like phía dưới, scroll vào màn hình, nhấn like.\n
    Nhấn giữ để hiện bảng emote, kéo thả vào emote tương ứng:
    'Thích', 'Yêu thích', 'Thương Thương', 'Haha', 'Wow', 'Buồn', 'Phẫn nộ'
    """
    log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Bắt đầu like post")
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

# lướt facebook
async def tham_trang_ca_nhan(driver, action_name: str,screen_standing_event=None, restart_event=None):
    profile_locators = [
        ("desc", "Đi tới trang cá nhân"),
        ("desc", "Go to profile"),
        ("desc", "Your profile"),
        ("desc", "Trang cá nhân của bạn"),
        ("text", "Đi tới trang cá nhân"),
        ("text", "Go to profile"),
    ]
   
    profile_element = await my_find_element(driver, profile_locators)
    if not profile_element:
        print("❌ Không tìm thấy nút 'Đi tới trang cá nhân'")
        return "unknown_user"
    profile_element.click()
    print("✅ Đã click vào trang cá nhân")
    await asyncio.sleep(4)
    try: 
        await asyncio.sleep(random.uniform(5,8))
        # scroll_count = random.randint(5, 20)
        scroll_count = random.randint(5, 15)  # Giảm số lần scroll để giảm thời gian chạy
        # Kiểm tra interruption events 
        if check_interruption_events(driver, restart_event, screen_standing_event, action_name):
            return 
        while scroll_count > 0:
            try:
                # Kiểm tra interruption events 
                if check_interruption_events(driver, restart_event, screen_standing_event, action_name):
                    return 
                count = random.randint(1,2)
                await nature_scroll(driver, max_roll=count, isFast=random.choice([True,False]))
                await asyncio.sleep(random.uniform(1,10))
                if scroll_count % 7 == 0:
                    # Kiểm tra interruption events 
                    if check_interruption_events(driver, restart_event, screen_standing_event, action_name):
                        return  
                    await like_post(driver, random.choice(EMOTION))
                    await asyncio.sleep(random.uniform(3,5))                                                                                                           
                scroll_count -= 1
            except asyncio.CancelledError:
                log_message("Tác vụ Lướt trang cá nhân bị hủy", logging.WARNING)
                raise
        await asyncio.sleep(random.uniform(2,5))
        log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Đã hoàn thành thăm trang cá nhân")
    except Exception as e:    
        log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Error {e}", logging.ERROR)
    await go_to_home_page(driver)
