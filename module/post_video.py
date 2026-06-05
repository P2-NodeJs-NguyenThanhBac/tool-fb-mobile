# post_video.py
import asyncio
from typing import List, Optional, Union
from util import log_message, DEVICE_LIST_NAME
from module.post_to_wall import post_to_wall

async def normalize_files(files: Optional[Union[str, List[str]]]) -> List[str]:
    """
    Chuẩn hoá tham số files:
    - None      -> []
    - "a.mp4"   -> ["a.mp4"]
    - ["a.mp4"] -> giữ nguyên
    """
    if files is None:
        return []
    if isinstance(files, str):
        return [files]
    if isinstance(files, (list, tuple)):
        # convert tuple -> list + ép hết về str cho chắc
        return [str(f) for f in files]
    # kiểu khác (dict, int, ...) thì bỏ qua
    return []


async def post_video(
    driver,
    command_id: str,
    user_id: str,
    content: str,
    files: Optional[Union[str, List[str]]] = None,
):
    """
    Đăng VIDEO lên tường Facebook cho tài khoản `user_id`.

    - `content`: caption / mô tả video (lấy từ blog / CRM)
    - `files`  : tên file video trên server CRM (vd: "blog-ai-future.mp4"
                 hoặc ["blog-ai-future.mp4"])

    Hiện tại:
    - Tái sử dụng logic post_to_wall (vì FB dùng chung picker Ảnh/Video)
    - Bước chọn media trong post_to_wall sẽ chọn các file mới nhất mà
      tool đã push sang /sdcard/Download trên điện thoại.

    Sau này nếu muốn tách luồng đăng video riêng (thêm tag, hashtag,
    chọn layout khác...) thì chỉ cần chỉnh trong file này.
    """
    # Chuẩn hoá danh sách file
    files_list = await normalize_files(files)

    if not files_list:
        # Về lý thuyết "post_video" luôn nên có file,
        # nhưng ta vẫn xử lý trường hợp thiếu cho an toàn.
        log_message(
            f"[{DEVICE_LIST_NAME.get(driver.serial, driver.serial)}] "
            f"post_video được gọi nhưng không có file video, "
            f"chuyển sang đăng bài text bình thường.",
        )
    else:
        log_message(
            f"[{DEVICE_LIST_NAME.get(driver.serial, driver.serial)}] "
            f"post_video: chuẩn bị đăng với {len(files_list)} file: {files_list}"
        )

    # Gọi lại hàm post_to_wall:
    #  - post_to_wall đã lo: push_file_to_device, mở FB, mở composer,
    #    gõ content, bấm nút Ảnh/Video, chọn media mới nhất, bấm Đăng.
    await post_to_wall(
        driver=driver,
        command_id=command_id,
        user_id=user_id,
        content=content,
        files=files_list,
    )
