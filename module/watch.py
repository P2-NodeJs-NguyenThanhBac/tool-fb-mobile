import asyncio
import logging
import random
from util import *
# from main import screen_standing_event
from main_merged import disable_auto_rotation

# Xem story, còn một role mới cập nhật là ghi chú cũng sẽ lẫn vào story, cần kiểm tra để bỏ qua
async def watch_story(driver, action_name: str, duration=random.uniform(60, 90), screen_standing_event=None, restart_event=None):
    """
    Story mặc định ở trên đầu trang
    """
    log_message("Bắt đầu watch story")
    # Về đầu trang
    await go_to_home_page(driver)
    await asyncio.sleep(random.uniform(5,7))
    # Tìm story
    story_item = await my_find_element(driver, {("xpath", '//android.widget.Button[contains(@content-desc, "Tin của ")]')})
    if story_item == None:
        log_message("Không thể tìm được story", logging.ERROR)
        return
    story_item.click()
    log_message("Xem story", logging.INFO)

    # Kiểm tra có hiện box xác nhận không
    ok = await my_find_element(driver, {("text", "OK")})
    if ok:
        ok.click()
        await asyncio.sleep(random.uniform(3,4))

    # Kiểm tra interruption events 
    if check_interruption_events(driver, restart_event, screen_standing_event, action_name):
        return  
        
    await asyncio.sleep(duration)
    # react, gửi tin nhắn,... sẽ mở rộng sau
    # do somthing stupid

    log_message("Xem story chán rồi, té thôi", logging.INFO)

    # Thoát trang story
    driver.press("back")
    log_message("Đã thoát trang story")

# Xem reels
async def watch_reels(driver, action_name: str, duration=random.randint(600, 900), screen_standing_event=None, restart_event=None):#(10->15'):
# async def watch_reels(driver, action_name: str, duration=random.randint(120, 120), screen_standing_event=None, restart_event=None):#(2700,3600)):
    """
    Lướt để tìm reels rồi bấm xem các video liên quan đến tuyển dụng
    """
    log_message("Bắt đầu watch reels tuyển dụng")
    # Về đầu trang
    await go_to_home_page(driver)
    await asyncio.sleep(random.uniform(5,7))
    
    # Danh sách từ khóa tuyển dụng
    recruitment_keywords = [
        "tuyển dụng",
        "việc làm", 
        "tìm việc",
        "nhân sự",
        "tuyển nhân viên", 
        "tuyển sinh",
        "cơ hội nghề nghiệp"
    ]
    
    reel_selectors = [
            # Samsung selectors 
            ("description", "Reels, Tab 3/6"),
            ("descriptionContains", "Reels, Tab"),
            ("description", "Reels, Tab 2/6"),                                         
            
            # Xiaomi selectors  
            ("xpath", '//android.widget.Button[contains(@content-desc, "Xem thước phim")]'),
            
            # Khác 
            ("textContains", "Reels"),                                                 
            ("descriptionContains", "reel"),                                               
        ]
    # Tìm Reels
    reel_item = None
    log_message("Đang tìm nút Reels...")
        
    # Thử từng selector
    for i, selector in enumerate(reel_selectors):
        try:
            if selector[0] == "description":
                reel_item = driver(description=selector[1])
                if reel_item.exists:
                    break
                        
            elif selector[0] == "descriptionContains":
                reel_item = driver(descriptionContains=selector[1])
                if reel_item.exists:
                    break
                        
            elif selector[0] == "xpath":
                reel_item = driver.xpath(selector[1])
                if reel_item.exists:
                    break
                        
            elif selector[0] == "textContains":
                reel_item = driver(textContains=selector[1])
                if reel_item.exists:
                    break
        except Exception as e:
            continue
            
    if reel_item is None or (hasattr(reel_item, 'exists') and not reel_item.exists):
        reel_item = await scroll_until_element_visible(driver, {("xpath", '//android.widget.Button[contains(@content-desc, "Xem thước phim")]')})
        if reel_item is None:
            log_message("Không tìm thấy nút Reels", logging.ERROR)
            screen_standing_event[driver.serial] = False
            return

        
    reel_item.click()
    log_message("Đã vào phần Reels")
    await asyncio.sleep(random.uniform(3,5))
    found_search = True
    # Tìm và bấm nút tìm kiếm trong reels
    try:
        search_button = driver(description="Tìm kiếm")
        if search_button.exists:
            search_button.click()
            log_message("Đã bấm nút tìm kiếm trong Reels")
            await asyncio.sleep(random.uniform(2,4))
            
            # Tìm ô search để nhập text
            search_selectors = [
                ("className", "android.widget.EditText"),
                ("xpath", "//android.widget.EditText"),
                ("textContains", "Tìm kiếm"),
                ("descriptionContains", "search"),
                ("descriptionContains", "tìm kiếm")
            ]
            
            search_box = None
            for selector_type, selector_value in search_selectors:
                try:
                    if selector_type == "className":
                        search_box = driver(className=selector_value)
                    elif selector_type == "xpath":
                        search_box = driver.xpath(selector_value)
                    elif selector_type == "textContains":
                        search_box = driver(textContains=selector_value)
                    elif selector_type == "descriptionContains":
                        search_box = driver(descriptionContains=selector_value)
                    
                    if search_box and search_box.exists:
                        break
                except:
                    continue
            
            if search_box and search_box.exists:
                # Chọn từ khóa tuyển dụng ngẫu nhiên
                keyword = random.choice(recruitment_keywords)
                log_message(f"Nhập từ khóa tìm kiếm: {keyword}")
                
                # Bấm vào ô search và nhập text
                search_box.click()
                await asyncio.sleep(random.uniform(1,2))
                
                # Xóa text cũ nếu có
                search_box.clear_text()
                await asyncio.sleep(random.uniform(0.5,1))
                
                # Nhập từ khóa
                search_box.set_text(keyword)
                await asyncio.sleep(random.uniform(1,2))
                
                # Bấm enter hoặc tìm nút search
                driver.press("enter")
                await asyncio.sleep(random.uniform(3,5))
                log_message(f"Đã tìm kiếm với từ khóa: {keyword}")
            else:
                found_search = False
                log_message("Không tìm thấy ô tìm kiếm, xem reels thông thường")
                
        else:
            log_message("Không tìm thấy nút tìm kiếm, xem reels thông thường")
    except Exception as e:
        log_message(f"Lỗi khi tìm kiếm: {e}", logging.WARNING)
        log_message("Tiếp tục xem reels thông thường")
    
    # Xem các video reels
    log_message("Bắt đầu lướt reels tuyển dụng")
    # time = random.uniform(10,30)
    time = random.uniform(10,20) # Giảm thời gian xem mỗi video
    await disable_auto_rotation(driver, driver.serial)
    # Kiểm tra interruption events 
    if check_interruption_events(driver, restart_event, screen_standing_event, action_name):
        return 
        
    await asyncio.sleep(time)
    # CLick vào video đầu tiên nếu có thể
    try:
        first_video = driver.xpath('//androidx.recyclerview.widget.StaggeredGridLayoutManager/android.view.ViewGroup[3]')
        if first_video.exists:
            first_video.click()
            log_message("Đã bấm vào video đầu tiên")
            await asyncio.sleep(random.uniform(5,7))
    except:
        pass
    while time < duration:
        try:
            # Kiểm tra interruption events 
            if check_interruption_events(driver, restart_event, screen_standing_event, action_name):
                return 

            await nature_scroll(driver, isFast=True)
            log_message("Video tiếp theo")
            # i = random.uniform(10,30)
            i = random.uniform(10,20) # Giảm thời gian xem mỗi video
            time += i
            await asyncio.sleep(i)
        except asyncio.CancelledError:
            log_message("Tác vụ xem reels bị hủy", logging.WARNING)
            raise
    
    # Thoát về màn hình chính bằng cách back 4 lần
    if found_search:
        driver.press("back")
        driver.press("back")
        driver.press("back")
        driver.press("back")
    else:
        go_to_home_page(driver)
    log_message("Đã thoát về màn hình chính")
