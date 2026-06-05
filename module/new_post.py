import asyncio
import logging
from util import *

# Tạo bài post mới
async def new_post(driver, text, images={}):
    """
    Tạo bài đăng mới, bài đăng bao gồm text, ảnh(nếu có)

    image: truyền vào {"","",...} tên ảnh/video (không có đuôi định dạng)
    """

    # Hàm tìm nút hủy
    def huy():
        huy = my_find_element(driver, {("xpath", '//android.widget.Button[@content-desc="Hủy"]')})
        if huy == None:
            log_message("Không tìm được nút hủy\n Bất lực", logging.ERROR)
            return
        huy.click()
    # Hàm tìm nút back
    def back():
        back = my_find_element(driver, {("xpath", '//android.widget.ImageView[@content-desc="Quay lại"]')})
        if back == None:
            log_message("Không tìm được nút quay lại\n Bất lực", logging.ERROR)
            return
        back.click()
    # Hàm tìm nút bỏ bài viết
    def bo():
        bo = my_find_element(driver, {("xpath", '//android.widget.Button[@content-desc="Bỏ bài viết"]')})
        if bo == None:
            log_message("Không tìm được nút bỏ\n Bất lực", logging.ERROR)
            return
        bo.click()

    log_message("Bắt đầu tạo bài đăng mới")
    # Về đầu trang
    top_page = await go_to_home_page(driver)
    if top_page == None:
        log_message("Không ở trang chủ hoặc không thể về trang chủ", logging.ERROR)
        return
    await asyncio.sleep(5)

    # Tìm ô tạo bài đăng
    make_post = my_find_element(driver, {("xpath", '//android.view.ViewGroup[@content-desc="Viết bài trên Facebook"]')})
    if make_post == None:
        log_message("Không tìm được ô tạo bài đăng", logging.ERROR)
        return
    make_post.click()
    log_message("Vào giao diện tạo bài đăng")
    await asyncio.sleep(1)

    # Nếu có ảnh thì chọn ảnh theo tên
    if images:
        add_image = my_find_element(driver, {("xpath", '//android.widget.Button[@content-desc="Ảnh/video"]')})
        if add_image == None:
            log_message("Không thêm được ảnh")
            back()
            return
        add_image.click()
        log_message("Vào giao diện chọn ảnh")
        await asyncio.sleep(1)

        # Tìm nút thêm nhiều (dù chỉ có 1 ảnh cũng chọn)
        multi_choice = my_find_element(driver, {("xpath", '//android.widget.Button[@content-desc="Chọn nhiều file"]')})
        if multi_choice == None:
            log_message("Không tìm được nút multichoice", logging.ERROR)
            huy()
            back()
            return
        multi_choice.click()
        log_message("Giao diện thêm ảnh")
        await asyncio.sleep(1)

        # Vòng lặp
        first = True
        for image_path in images:
            image = my_find_element(driver, {("xpath", f'//android.widget.Button[contains(@content-desc, "{image_path}")]')})
            if image == None:
                log_message(f"Không tìm được hình ảnh: {image_path}", logging.ERROR)
                huy()
                back()
                if not first:
                    bo()
                return
            log_message(f"Tìm được ảnh: {image_path}")
            image.click()
            first = False
        log_message("Đã thêm toàn bộ ảnh")

        # Tìm nút tiếp để chuyển về nhập text
        tiep = my_find_element(driver, {("xpath", '//android.widget.Button[@content-desc="Tiếp"]')})
        if tiep == None:
            log_message("Không tìm thấy nút tiếp tục", logging.ERROR)
            huy()
            back()
            bo()
            return
        log_message("Trở về nhập text")
        tiep.click()
        await asyncio.sleep(1)

    # Nhập text
    log_message("Về giao diện nhập text")
    text_input = my_find_element(driver, {("xpath", '//android.widget.AutoCompleteTextView')})
    if text_input == None:
        log_message("Không tìm được ô nhập text", logging.ERROR)
        back()
        if images:
            bo()
        return
    await asyncio.sleep(1)
    text_input.set_text(text)
    log_message("Nhập xong text")
    await asyncio.sleep(1)

    # Đăng
    dang = my_find_element(driver, {("xpath", '//android.widget.Button[@content-desc="ĐĂNG"]')})
    if dang == None:
        log_message("Không tìm được nút đăng", logging.ERROR)
        back()
        if images:
            bo()
        return
    dang.click()
    log_message("Đăng thành công")
