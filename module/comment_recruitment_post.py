import pymongo_management
import toolfacebook_lib
from util import log_message, DEVICE_LIST_NAME
import asyncio
import random
import urllib.parse
from main_merged import disable_auto_rotation
def redirect_to(driver, link):
    """ Hàm này có thể sử dụng để vào link bài viết cá nhân hoặc nhóm với link rút gọn"""
    # Dù link ngắn hay dài, hãy mã hóa nó
    encoded_link = urllib.parse.quote(link)
    
    # Ép mở bằng WebView của Facebook
    # WebView sẽ tự lo việc giải mã link /share/p/ thành link bài viết thật
    deep_link = f"fb://facewebmodal/f?href={encoded_link}"
    
    driver.shell(f"am start -a android.intent.action.VIEW -d '{deep_link}'")
async def comment_recruitment_post(driver, user_id):
    #Lưu ý: hàm update_daily_kpi() cập nhật kpi cho tất cả user_id có trong DB
    await pymongo_management.update_daily_kpi()
    comment = await pymongo_management.get_comment(user_id)
    log_message(f"{DEVICE_LIST_NAME[driver.serial]} - {comment[0]['message']}", comment[1])
    if 'comment' in comment[0]:
        comment = comment[0]
        link = comment.get("link")
        comment = comment.get("comment", None)
        try:
            log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] đang vào link:{link}")
            redirect_to(driver, link)
        except Exception as e:
            log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] không vào được link:,{e}")
            return False
        await asyncio.sleep(random.uniform(20,30))
        await disable_auto_rotation(driver, driver.serial)
        if driver(description="Bình luận").wait(timeout =5):
            driver(description="Bình luận").click()
            await asyncio.sleep(2)
            driver.send_keys(comment, clear=True)
        if driver(description="Gửi").wait(timeout =5):
            driver(description="Gửi").click()
        await toolfacebook_lib.back_to_facebook(driver)