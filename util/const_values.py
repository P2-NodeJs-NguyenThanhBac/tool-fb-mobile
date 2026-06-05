WINDOW_ADB_PATH = "platform-tools/adb"
LINUX_ADB_PATH = "adb"
ZALO_BASE_PATH = "D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/"

def DEVICE_STATUS_PATH(device_id: str) -> str:
    return f"{ZALO_BASE_PATH}device_status_{device_id}.json"

def ZALO_DATA_LOGIN_PATH(phone: str) -> str:
    return f'{ZALO_BASE_PATH}Zalo_data_login_path_{phone}.json'

def ZALO_IMAGE_PATH(image_number: int) -> str:
    return f'{ZALO_BASE_PATH}image{image_number}.jpg'
