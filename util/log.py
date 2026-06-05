import logging
import os

# Tạo thư mục log nếu chưa có
log_dir = "assets/logs"
os.makedirs(log_dir, exist_ok=True)

log_file = os.path.join(log_dir, "toolfacebook.log")

# Cấu hình logging chỉ một lần
logger = logging.getLogger("FacebookToolLogger")
logger.setLevel(logging.DEBUG)

# Tránh thêm nhiều handler trùng lặp
if not logger.handlers:
    # Handler ghi log vào file
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_format = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(file_format)
    logger.addHandler(file_handler)

    # Handler in log ra console (terminal)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG)
    console_format = logging.Formatter('\033[1;32m%(asctime)s\033[0m - \033[1;34m%(levelname)s\033[0m - %(message)s')
    console_handler.setFormatter(console_format)
    logger.addHandler(console_handler)

def log_message(message, level=logging.INFO):
    """Ghi log ra file và terminal với định dạng chuẩn và màu sắc."""
    logger.log(level, message)

logger.propagate = False
