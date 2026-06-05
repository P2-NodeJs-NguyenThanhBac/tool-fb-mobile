import eventlet
eventlet.monkey_patch()
import sys
from flask_cors import CORS
from flask import Flask, request, jsonify
import uiautomator2 as u2
from threading import Lock
import base64
import psutil
from datetime import datetime, date, timedelta
from flask_socketio import SocketIO, emit, join_room, leave_room
import time
import os
import re
import json
from PIL import Image, ImageFilter
import io


app = Flask(__name__)
CORS(app)

socketio = SocketIO(app, async_mode='eventlet',
                    max_http_buffer_size=1024 * 1024 * 1024, cors_allowed_origins='*')

dict_status_zalo = {}
dict_status_update_pvp = {}
dict_phone_device = {}
dict_id_chat = {}
dict_device_and_phone = {}
dict_process_id = {}
dict_queue_device = {}
dict_new_friend = {}
last_time = {}
max_message_per_day = 300
num_message = 0
num_add_friend = 0
image_number = 300
max_add_friend_per_day = 30
device_connect = {}
now_phone_zalo = {}
driver = {}
list_socket_call = []

LOG_FILE = "sent_log.txt"
IMAGE_FILE_DATA_PATH = "D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/Zalo_image_file_data"
AVA_NUMBER_PATH = "D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/Zalo_image_file_data/ava.txt"
MES_NUMBER_PATH = "D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/Zalo_image_file_data/mes.txt"
DATA_CHAT_BOX_NUMBER_PATH = "D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/Zalo_image_file_data/data_chat_box.txt"
NUM_PHONE_ZALO_FILE = "D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/num_phone_zalo.txt"
LOG_FILE_CHECK_RAN_PATH = "zalo_run_log.json"
file_lock = Lock()

# đảm bảo biến môi trường cho Python I/O
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

# với Python 3.7+:
try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    # fallback cho các Python cũ hơn
    import io
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(
        sys.stderr.buffer, encoding='utf-8', errors='replace')

dict_device_and_phone = {
    "R5CW71JT8GT": ["0867956826"],
    "R8YY70F5MKN": [],  # A01
    "TSPNH6GYZLPJBY6X": ['0971335869', '0978025150', '0941295235'],  # A03
    "9PAM7DIFW87DOBEU": ['0869773496', '0395459520', '0988003410'],  # A09
    "EQLNQ8O7EQCQPFXG": [],  # A10
    "YH9TSS7XCMPFZHNR": [],  # A11
    "2926294610DA007N": [],
    "7DXCUKKB6DVWDAQO": ['0385765903', '0963904347', '0969106521'],  # A02
    "7HYP4T4XTS4DXKCY": ['0968082574', '0964876703', '0978722184'],  # A04
    "CQIZKJ8P59AY7DHI": ['0963101851', '0971207216', '0969824937'],  # A08
    "CEIN4X45I7ZHFEFU": ['0973418952', '0971412658', '0961991742'],  # A06
    "8HMN4T9575HAQWLN": ['0978585641', '0982305784', '0973106450'],  # A07
    "UWJJOJLB85SO7LIZ": ['0865772900', '0852039719', '0963563276'],  # A05
    "MJZDFY896TMJBUPN": ['0367614180', '0975629854', '0966049501'],  # A12
    "EY5H9DJNIVNFH6OR": [],  # A13
    "F6NZ5LRKWWGACYQ8": ['0913591672', '0359615945', '0972381905'],  # A14
    "QK8TEMKZMBYHPV6P": ['0984485936', '0968902871', '0985771347'],  # A15
    "IJP78949G69DKNHM": ['0987608429', '0337416995', '0356468640'],  # A16
    "EM4DYTEITCCYJNFU": ['0966117160', '0961963671', '0966578630'],  # A17
    "PN59BMHYPFXCPN8T": ['0966338017', '0383757614', '0982470403'],  # A18
    "Z5LVOF4PRGXGTS9H": ['0988658315', '0338734680', '0967791241'],  # A19
    "EIFYAALRK7U4MRZ9": [],  # A20
    "R8YY70F81TV": [],  # A21
    "69QGMN8PXWDYPNIF": [],  # A22
    "IZDEGA8TFYXWRK9X": [],  # A23
    "R8YY70HCNRX": [],  # A24
    "R83Y50JZK6A": [],  # A25
    "R8YY70F5VSE": [] #A26
}

id_port = {
    "22773024": [{"A02": "7DXCUKKB6DVWDAQO"}, {"A06": "CEIN4X45I7ZHFEFU"}, {"A05": "UWJJOJLB85SO7LIZ"}, {"A03": "TSPNH6GYZLPJBY6X"}, {"A04": "7HYP4T4XTS4DXKCY"}, {"A01": "R8YY70F5MKN"}, {"A22": "69QGMN8PXWDYPNIF"}, {"A23": "IZDEGA8TFYXWRK9X"}],  # Sếp
    "22614471": [{"A02": "7DXCUKKB6DVWDAQO"}, {"A06": "CEIN4X45I7ZHFEFU"}, {"A05": "UWJJOJLB85SO7LIZ"}, {"A03": "TSPNH6GYZLPJBY6X"}, {"A04": "7HYP4T4XTS4DXKCY"}, {"A01": "R8YY70F5MKN"}],  # Lê Thị Liên
    "22616467": [{"A17": "EM4DYTEITCCYJNFU"}],  # Hoàng Thị Thùy Linh
    # Chị Ngô Dung
    "22615833": [{"A09": "9PAM7DIFW87DOBEU"}, {"A06": "CEIN4X45I7ZHFEFU"}],
    # Chị Bích Ngọc
    "22814414": [{"A19": "Z5LVOF4PRGXGTS9H"}, {"A02": "7DXCUKKB6DVWDAQO"}],
    # Chị Lại Thị Nhàn
    "22789191": [{"A14": "F6NZ5LRKWWGACYQ8"}, {"A01": "R8YY70F5MKN"}],
    # Chị Thư
    "22833463": [{"A15": "QK8TEMKZMBYHPV6P"}, {"A03": "TSPNH6GYZLPJBY6X"}],
    # Chị Thùy
    "22636101": [{"A16": "IJP78949G69DKNHM"}, {"A05": "UWJJOJLB85SO7LIZ"}],
    # Huyền Trang
    "22896992": [{"A13": "EY5H9DJNIVNFH6OR"}, {"A04": "7HYP4T4XTS4DXKCY"}],
    "22911349": [{"A24": "R8YY70HCNRX"}],  # Diễm Quỳnh
    "22889226": [{"A08": "CQIZKJ8P59AY7DHI"}],  # Chị Ngọc Hà
    "22894754": [{"A07": "8HMN4T9575HAQWLN"}],  # Chị Hải Yến
    "22889521": [{"A18": "PN59BMHYPFXCPN8T"}],  # Chị Ngọc Mai
    "22735395": [{"A10": "EQLNQ8O7EQCQPFXG"}],  # Chị Tâm
    "22897894": [{"A11": "YH9TSS7XCMPFZHNR"}],  # Ngọc Anh
    "22846624": [{"A20": "EIFYAALRK7U4MRZ9"}],  # Ngọc Ánh
    "22846622": [{"A21": "R8YY70F81TV"}],  # Thu Trà
    "22891672": [{"A12": "MJZDFY896TMJBUPN"}],  # Phạm Linh Chi
    "22907106": [{"A25": "R83Y50JZK6A"}],  # Vân Anh
    # Thằng đẻ ra ứng dụng
    "22862103": [{"A08": "CQIZKJ8P59AY7DHI"}, {"A04": "7HYP4T4XTS4DXKCY"}, {"A01": "R8YY70F5MKN"}],
    # Phạm Huy dùng để test
    "22858638": [{"A02": "7DXCUKKB6DVWDAQO"}, {"A05": "UWJJOJLB85SO7LIZ"}, {"A09": "9PAM7DIFW87DOBEU"}]

}
'''
Code ngày 7/8/2025
'''
mapping = {
    "T2": 0,  # Monday
    "T3": 1,  # Tuesday
    "T4": 2,
    "T5": 3,
    "T6": 4,
    "T7": 5,
    "CN": 6   # Sunday
}

for device_id in dict_device_and_phone.keys():
    for phone_zalo in dict_device_and_phone[device_id]:
        dict_status_zalo[phone_zalo] = ""
        dict_status_update_pvp[phone_zalo] = 0
        dict_phone_device[phone_zalo] = device_id

try:
    if not os.path.exists(NUM_PHONE_ZALO_FILE):
        with open(NUM_PHONE_ZALO_FILE, "w", encoding="utf-8") as f:
            f.write(str(0))
    with open(NUM_PHONE_ZALO_FILE, "r", encoding="utf-8") as f:
        raw = f.read()
    list_phone_number_zalo = [l for l in raw.split(
        "\n") if l.strip()]
    for phone in list_phone_number_zalo:
        dict_new_friend[phone] = {}
except Exception as e:
    print("Không lấy được danh sách các số điện thoại tài khoản zalo")
    list_phone_number_zalo = []

def create_base_document_json(database_name, domain, collection_name, document):
    try:
        with open(f'{database_name}/{collection_name}.json', 'r', encoding='utf-8') as f:
            data = json.load(f)
        existing_doc = [d for d in data if d.get(domain) != document[domain]]
        existing_doc.append(document)
        with open(f'{database_name}/{collection_name}.json', 'w', encoding='utf-8') as f:
            json.dump(existing_doc, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print("Lỗi gặp phải là ", e)
        return False


def delete_base_document_json(database_name, domain, collection_name, document):
    try:
        with open(f'{database_name}/{collection_name}.json', 'r', encoding='utf-8') as f:
            data = json.load(f)
        existing_doc = [d for d in data if d.get(domain) != document[domain]]
        with open(f'{database_name}/{collection_name}.json', 'w', encoding='utf-8') as f:
            json.dump(existing_doc, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print("Lỗi gặp phải là ", e)
        return False


def update_base_document_json(database_name, domain, collection_name, document):
    try:
        with open(f'{database_name}/{collection_name}.json', 'r', encoding='utf-8') as f:
            data = json.load(f)
        for id in range(len(data)):
            if data[id][domain] == document[domain]:
                for key in document.keys():
                    data[id][key] = document[key]
                break
        with open(f'{database_name}/{collection_name}.json', 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print("Lỗi gặp phải là ", e)
        return False


def get_base_id_zalo_json(database_name, domain, collection_name, document):
    try:
        with open(f'{database_name}/{collection_name}.json', 'r', encoding='utf-8') as f:
            data = json.load(f)
        cursor = []
        for d in data:
            check_key = True
            for key in document.keys():
                if d[key] != document[key]:
                    check_key = False
                    break
            if check_key:
                cursor.append(d)
        return cursor
    except Exception as e:
        return False


def already_sent(phone_number):
    with file_lock:
        if not os.path.exists(LOG_FILE):
            return False
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            return phone_number in f.read()


def log_sent(phone_number):
    with file_lock:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(phone_number + "\n")


def already_sent_number(file_path):
    if not os.path.exists(file_path):
        return False
    else:
        return True


def read_sent_number(file_path):
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            s = f.read().strip()
    except Exception:
        return 0

    if s == "":
        with file_lock:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(str(0))
        return 0

    return int(s)


def log_sent_number(number, file_path):
    with file_lock:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(str(number))


def already_sent_phone_zalo(phone_number):
    with file_lock:
        if not os.path.exists(LOG_FILE):
            return False
        with open(NUM_PHONE_ZALO_FILE, "r", encoding="utf-8") as f:
            return phone_number in f.read()


def log_sent_phone_zalo(phone_number):
    with file_lock:
        with open(NUM_PHONE_ZALO_FILE, "a", encoding="utf-8") as f:
            f.write(phone_number + "\n")

def print_usage():
    cpu_percent = psutil.cpu_percent()
    ram_percent = psutil.virtual_memory().percent
    print(f"CPU Usage: {cpu_percent}%")
    print(f"RAM Usage: {ram_percent}%")


def switch_account(d: u2.Device, name, retire=3):

    d.app_start("com.zing.zalo", stop=True)
    # d = run_start(d)
    # d.implicitly_wait(3.0)
    eventlet.sleep(1.0)
    d(resourceId="com.zing.zalo:id/maintab_metab").click()
    eventlet.sleep(1.0)
    d.xpath(
        '//*[@resource-id="com.zing.zalo:id/zalo_action_bar"]/android.widget.LinearLayout[2]').click()

    eventlet.sleep(1.0)
    d.swipe_ext(u2.Direction.FORWARD)
    eventlet.sleep(1.0)

    d(resourceId="com.zing.zalo:id/itemSwitchAccount").click()

    eventlet.sleep(1.0)
    d.xpath(f"//android.widget.TextView[@text='{name}']").click()
    eventlet.sleep(5)
    d(resourceId="com.zing.zalo:id/btn_chat_gallery_done").click()
    eventlet.sleep(1.5)
    return d


def handle_chat_view(d: u2.Device,  num_phone_zalo):
    last_time[num_phone_zalo] = time.time()
    while True:
        time_period = time.time() - last_time[num_phone_zalo]
        if dict_status_update_pvp[num_phone_zalo] == 1:
            break

        if time_period >= 75.0:
            dict_status_update_pvp[num_phone_zalo] = 0
            name_ntd = d(
                resourceId="com.zing.zalo:id/txtTitle")
            if name_ntd.exists or d(resourceId="com.zing.zalo:id/action_bar_title").exists:
                try:
                    while not d.xpath('//*[@text="Ưu tiên"]').exists:
                        d.press("back")
                        eventlet.sleep(0.1)
                except Exception as e:
                    print(e)
                    dict_status_zalo[num_phone_zalo] = ""

        if time_period >= 120.0:
            with open(f'D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/device_status_{dict_phone_device[num_phone_zalo]}.json', 'r') as f:
                device_status = json.load(f)
            if device_status['active']:
                device_status['active'] = False
                print("Có set về false không")
                with open(f"D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/device_status_{dict_phone_device[num_phone_zalo]}.json", 'w') as f:
                    json.dump(device_status, f, indent=4)
            break
        eventlet.sleep(2)


def parse_bounds(bounds):
    """
    Hỗ trợ 2 dạng bounds thường gặp:
     - chuỗi "[x1,y1][x2,y2]"
     - list/tuple [x1, y1, x2, y2] hoặc dict {...}
    Trả về (left, top, right, bottom)
    """
    if isinstance(bounds, str):
        m = re.findall(r'\[(-?\d+),(-?\d+)\]', bounds)
        if len(m) >= 2:
            x1, y1 = map(int, m[0])
            x2, y2 = map(int, m[1])
            return x1, y1, x2, y2
    elif isinstance(bounds, (list, tuple)) and len(bounds) == 4:
        return tuple(bounds)
    elif isinstance(bounds, dict) and 'left' in bounds:
        return bounds['left'], bounds['top'], bounds['right'], bounds['bottom']
    raise ValueError("Unknown bounds format: %r" % (bounds,))


def safe_click(d, cx, cy, unwanted_resid="com.zing.zalo:id/chat_layout_group_topic"):
    """
    Click an toàn:
    - Nếu (cx,cy) nằm trong bounds của unwanted_resid -> di chuyển click ra ngoài (lên trên)
      hoặc chờ phần tử biến mất (nếu wait_for_gone_timeout > 0).
    - safe_offset: số pixel dịch ra khỏi phần tử
    - Trả về vị trí thực tế đã click (x,y)
    """
    elm = d(resourceId="com.zing.zalo:id/chatinput_text")

    if elm.exists:
        try:
            print('Có phần tử Nhắn tin')
            bounds = elm.info.get('bounds')
            top, bottom = bounds['top'], bounds['bottom']
            if top <= cy <= bottom:
                cy = top - 12
        except Exception as e:
            print("Đã xảy ra Exception", e)

    elm = d(resourceId="com.zing.zalo:id/tv_function_privacy")
    if elm.exists:
        try:
            print('Có phần tử Kết bạn')
            bounds = elm.info.get('bounds')
            top, bottom = bounds['top'], bounds['bottom']
            if top <= cy <= bottom:
                cy = bottom + 5
        except Exception as e:
            print("Đã xảy ra Exception", e)

    el = d(resourceId=unwanted_resid)
    if el.exists:
        try:
            bounds = el.info.get('bounds')
            top, bottom = bounds['top'], bounds['bottom']
            print("Có phần tử tin nhắn ghim")
        except Exception:
            print("Đã xảy ra Exception")
            # nếu không lấy được bounds, fallback: không click trực tiếp ở cx,cy
            # (ở đây chúng ta sẽ di chuyển lên 1/3 chiều cao màn hình)
            # w, h = d.window_size()
            alt_x, alt_y = cx, cy
            d.long_click(alt_x, alt_y, duration=1.0)
            return d

        # nếu toạ độ nằm trong vùng cấm
        if top <= cy <= bottom:
            new_x = cx
            new_y = bottom + 5
            d.long_click(new_x, new_y, duration=1.0)
            return d
    print(f"Clicked at ({cx}, {cy})")
    # nếu phần tử không tồn tại hoặc không chặn: click bình thường
    d.long_click(cx, cy, duration=1.0)
    return d


def safe_normal_click(d, cx, cy, unwanted_resid="com.zing.zalo:id/chat_layout_group_topic"):
    """
    Click an toàn:
    - Nếu (cx,cy) nằm trong bounds của unwanted_resid -> di chuyển click ra ngoài (lên trên)
      hoặc chờ phần tử biến mất (nếu wait_for_gone_timeout > 0).
    - safe_offset: số pixel dịch ra khỏi phần tử
    - Trả về vị trí thực tế đã click (x,y)
    """
    elm = d(resourceId="com.zing.zalo:id/chatinput_text")

    if elm.exists:
        try:
            print('Có phần tử Nhắn tin')
            bounds = elm.info.get('bounds')
            top, bottom = bounds['top'], bounds['bottom']
            if top <= cy <= bottom:
                cy = top - 5
        except Exception as e:
            print("Đã xảy ra Exception", e)

    elm = d(resourceId="com.zing.zalo:id/tv_function_privacy")
    if elm.exists:
        try:
            print('Có phần tử Kết bạn')
            bounds = elm.info.get('bounds')
            top, bottom = bounds['top'], bounds['bottom']
            if top <= cy <= bottom:
                cy = bottom + 5
        except Exception as e:
            print("Đã xảy ra Exception", e)

    el = d(resourceId=unwanted_resid)
    if el.exists:
        try:
            bounds = el.info.get('bounds')
            top, bottom = bounds['top'], bounds['bottom']
            print("Có phần tử tin nhắn ghim")
        except Exception:
            print("Đã xảy ra Exception")
            # nếu không lấy được bounds, fallback: không click trực tiếp ở cx,cy
            # (ở đây chúng ta sẽ di chuyển lên 1/3 chiều cao màn hình)
            # w, h = d.window_size()
            alt_x, alt_y = cx, cy
            d.click(alt_x, alt_y)
            return d

        # nếu toạ độ nằm trong vùng cấm
        if top <= cy <= bottom:
            new_x = cx
            new_y = bottom + 5
            d.click(new_x, new_y)
            return d
    print(f"Clicked at ({cx}, {cy})")
    # nếu phần tử không tồn tại hoặc không chặn: click bình thường
    d.click(cx, cy)
    return d


def load_data_chat_box_json(data_chat_box_path):
    data_chat_box = []

    if isinstance(data_chat_box_path, list):
        return data_chat_box_path

    if not data_chat_box_path:
        return data_chat_box
    try:
        with open(data_chat_box_path, 'r', encoding='utf-8') as f:
            data_chat_box = json.load(f)
    except Exception as e:
        print("Có lỗi xảy ra trong quá trình lấy lịch sử chat ", e)

    return data_chat_box


def dump_data_chat_box_json(data_chat_box_path, data_chat_box):
    
    if data_chat_box_path and not isinstance(data_chat_box_path, list):
        with open(data_chat_box_path, "w", encoding="utf-8") as f:
            json.dump(data_chat_box, f, ensure_ascii=False, indent=4)

    else:
        if not already_sent_number(DATA_CHAT_BOX_NUMBER_PATH):
            data_chat_box_number = 0
        else:
            data_chat_box_number = read_sent_number(
                DATA_CHAT_BOX_NUMBER_PATH)
        data_chat_box_path = f"{IMAGE_FILE_DATA_PATH}/data_chat_box_{data_chat_box_number}.json"

        with open(data_chat_box_path, "w", encoding="utf-8") as f:
            json.dump(data_chat_box, f, ensure_ascii=False, indent=4)

        log_sent_number(data_chat_box_number+1,
                        DATA_CHAT_BOX_NUMBER_PATH)

    return data_chat_box_path

def get_list_friends_u2(d: u2.Device, id_device="", max_friends: int = 150, scroll_delay: float = 1.0, retire=3, has_update=False, friend_name=[], num_phone_zalo="", list_friend_old=[]):
    """
    Lấy toàn bộ bạn bè từ tab Danh bạ trên Zalo Android bằng uiautomator2.
    - d: uiautomator2 Device
    - max_friends: giới hạn tối đa số bạn bè thu thập
    """
    friends = list_friend_old
    seen = set()
    previous_last = ""
    same_count = 0

    d.app_start("com.zing.zalo", stop=True)
    eventlet.sleep(1.0)
    # d.implicitly_wait(3.0)

    try:
        d(resourceId="com.zing.zalo:id/maintab_contact").click()
        eventlet.sleep(1.0)
    except Exception as e:
        if retire > 0:
            get_list_friends_u2(d, retire=retire-1)
        else:
            return False
    try:
        d.xpath(
            '//*[@resource-id="com.zing.zalo:id/layoutTab"]/android.widget.FrameLayout[1]').click()
        eventlet.sleep(1.0)
    except Exception:
        pass

    try:
        if has_update:
            if d(resourceId="com.zing.zalo:id/header_page_new_friend").exists:
                d(resourceId="com.zing.zalo:id/header_page_new_friend").click()
            else:
                return friends
            eventlet.sleep(1.0)
    except Exception as e:
        pass

        # 2) Lặp scroll & thu thập
    while len(friends) < max_friends:
        # 2.1 lấy tất cả item bạn bè đang hiển thị
        items = d.xpath(
            "//android.widget.FrameLayout"
            "[@resource-id='com.zing.zalo:id/cel_contact_tab_contact_cell']"
        ).all()

        num_item = len(items)
        for id in range(num_item):
            raw = items[id].text or ""
            name = raw.split("\n", 1)[0].strip()
            if not name or name in seen or name in friend_name or "Tài khoản" in name or "Zalo" in raw:
                continue

            seen.add(name)
            try:

                items[id].click()
                eventlet.sleep(1.0)

                try:
                    btn = d(
                        resourceId="com.zing.zalo:id/btn_send_message")
                    if btn.exists:
                        btn.click()
                        eventlet.sleep(0.5)
                except Exception:
                    pass

                if d(textContains="Đóng").exists:
                    d(textContains="Đóng").click()
                    eventlet.sleep(0.5)
                if d(resourceId="com.zing.zalo:id/txtTitle").exists:
                    d(resourceId="com.zing.zalo:id/txtTitle").click()
                elif d(resourceId="com.zing.zalo:id/action_bar_title").exists:
                    d(resourceId="com.zing.zalo:id/action_bar_title").click()
                eventlet.sleep(1.0)
                avatar_b64 = ""
                try:
                    iv = d(resourceId="com.zing.zalo:id/rounded_avatar_frame")
                    '''
                    img = iv.screenshot()  # trả về PIL.Image
                    buf = io.BytesIO()
                    img.save(buf, format="PNG")
                    avatar_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
                    '''
                    img = iv.screenshot()
                    max_w, max_h = 200, 200
                    img.thumbnail((max_w, max_h),
                                    resample=Image.BILINEAR)
                    buf = io.BytesIO()
                    img.save(buf, format="JPEG", optimize=True, quality=75)
                    avatar_b64 = base64.b64encode(
                        buf.getvalue()).decode("ascii")
                    if not already_sent_number(AVA_NUMBER_PATH):
                        ava_number = 0
                    else:
                        ava_number = read_sent_number(
                            AVA_NUMBER_PATH)
                    with open(f"{IMAGE_FILE_DATA_PATH}/ava_{ava_number}.txt", "w", encoding="utf-8") as f:
                        f.write(avatar_b64)

                    log_sent_number(ava_number+1, AVA_NUMBER_PATH)

                    avatar_b64 = f"{IMAGE_FILE_DATA_PATH}/ava_{ava_number}.txt"
                except Exception as e:
                    print("Lỗi gặp phải là ", e)

                bd = ""
                d.xpath(
                    '//*[@resource-id="com.zing.zalo:id/zalo_action_bar"]/android.widget.LinearLayout[1]/android.widget.FrameLayout[3]').click()
                eventlet.sleep(0.5)
                d(resourceId="com.zing.zalo:id/setting_text_primary",
                    text="Thông tin").click()
                eventlet.sleep(0.5)
                try:
                    dob = d(resourceId="com.zing.zalo:id/tv_dob")
                    bd = dob.get_text()
                    eventlet.sleep(0.5)
                except Exception as e:
                    print("Không lấy được ngày tháng năm sinh ", name)
                d.press('back')
                eventlet.sleep(0.5)
                d.press('back')
                eventlet.sleep(0.5)

                friends.append({
                    "name": name,
                    "ava": avatar_b64,
                    "day_of_birth": bd
                })

                data_update = {"list_friend": friends,
                                "num_phone_zalo": num_phone_zalo}
                update_base_document_json(
                    "D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "num_phone_zalo", f"Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}", data_update)

                d.press('back')
                eventlet.sleep(1.0)

                d.press('back')
                eventlet.sleep(1.0)

            except Exception as e:
                print("Lỗi trong quá trình lấy danh sách bạn bè", e)
                while not d(resourceId="com.zing.zalo:id/maintab_contact").exists:
                    d.press('back')
                    eventlet.sleep(1.0)

            if id < num_item - 1:
                items = d.xpath(
                    "//*[@resource-id='com.zing.zalo:id/cel_contact_tab_contact_cell']").all()

        if len(friends) >= max_friends:
            break

        # Kiểm tra có còn dữ liệu mới không
        if items:
            last_raw = items[-1].text or ""
            if last_raw == previous_last:
                same_count += 1
                if same_count >= 2:
                    break
            else:
                same_count = 0
                previous_last = last_raw

        # 2.2 scroll lên để load thêm
        w, h = d.window_size()
        d.swipe(w//2, int(h*0.8), w//2, int(h*0.2), duration=0.8)
        eventlet.sleep(scroll_delay)

    return friends


def get_list_groups_u2(d: u2.Device, list_mems={}, check_mems={}, max_groups: int = 50, scroll_delay: float = 1.0, retire=3):
    """
    Lấy toàn bộ nhóm từ tab Danh bạ trên Zalo Android bằng uiautomator2.
    - d: uiautomator2 Device
    - max_groups: giới hạn tối đa số nhóm thu thập
    """
    groups = []
    seen = set()
    previous_last = ""
    same_count = 0
    d.app_start("com.zing.zalo", stop=True)
    # d.implicitly_wait(3.0)

    # 1) Chuyển sang tab Danh bạ
    try:
        d(resourceId="com.zing.zalo:id/maintab_contact").click()
        eventlet.sleep(1.0)
    except Exception as e:
        if retire > 0:
            get_list_groups_u2(d, retire=retire-1)
        else:
            return False

    # 2) Chuyển sang phần Nhóm
    try:
        d(resourceId="com.zing.zalo:id/tv_groups").click()
        eventlet.sleep(1)
    except Exception as e:
        if retire > 0:
            get_list_groups_u2(d, retire=retire-1)
        else:
            return False

    # 3) Lặp scroll & thu thập
    while len(groups) < max_groups:
        # lấy tất cả item nhóm đang hiển thị
        items = d.xpath(
            "//android.widget.FrameLayout"
            "[@resource-id='com.zing.zalo:id/cel_contact_tab_group_cell']"
        ).all()
        if len(items) == 0:
            break

        for it in items:
            raw = it.text or ""
            # Tách thành các dòng không rỗng
            lines = [line.strip() for line in raw.split("\n") if line.strip()]
            name = lines[0] if len(lines) > 0 else ""
            time_str = lines[1] if len(lines) > 1 else ""
            message = "\n".join(lines[2:]) if len(lines) > 2 else ""

            if not name or name in seen:
                continue

            seen.add(name)
            if name in list_mems.keys():
                list_mem = list_mems[name]
            else:
                list_mem = []

            if name in check_mems.keys():
                check_mem = check_mems[name]
            else:
                check_mem = True
            groups.append({
                "name": name,
                "time": time_str,
                "message": message,
                "list_mems": list_mem,
                "check_mems": check_mem
                # "avatar": (nếu cần, thêm screenshot của ImageView con tương tự)
            })
            if len(groups) >= max_groups:
                break

        if len(groups) >= max_groups:
            break

        # Kiểm tra còn nội dung mới
        if items:
            last_raw = items[-1].text or ""
            if last_raw == previous_last:
                same_count += 1
                if same_count >= 2:
                    break
            else:
                same_count = 0
                previous_last = last_raw

        # Scroll lên để load thêm
        w, h = d.window_size()
        d.swipe(w // 2, int(h * 0.8), w // 2, int(h * 0.2), duration=0.8)
        eventlet.sleep(scroll_delay)

    return groups


def get_list_invite_friends_u2(d: u2.Device, max_friends: int = 100, scroll_delay: float = 1.0, retire=3):
    """
    Lấy toàn bộ bạn bè chưa kết bạn (invite) từ tab Danh bạ trên Zalo Android bằng uiautomator2.
    - d: uiautomator2 Device
    - max_friends: giới hạn tối đa số bạn bè thu thập
    """
    d.app_start("com.zing.zalo", stop=True)
    # d.implicitly_wait(3.0)

    invite_friends = []
    seen = set()
    previous_last = ""
    same_count = 0

    # 1) Chuyển sang tab Danh bạ
    try:
        d(resourceId="com.zing.zalo:id/maintab_contact").click()
        eventlet.sleep(1.0)
    except Exception as e:
        if retire > 0:
            get_list_invite_friends_u2(d, retire=retire-1)
        else:
            return False
    try:
        d.xpath(
            '//*[@resource-id="com.zing.zalo:id/layoutTab"]/android.widget.FrameLayout[1]').click()
        eventlet.sleep(1.0)
    except Exception:
        pass
    try:
        d(resourceId="com.zing.zalo:id/suggest_friend_request").click()
        eventlet.sleep(1.0)
        try:
            d.xpath("//android.widget.TextView[@text='XEM THÊM']").click()
            eventlet.sleep(1.0)
        except Exception as e:
            pass
    except Exception as e:
        if retire > 0:
            get_list_invite_friends_u2(d, retire=retire-1)
        else:
            return False
    # 2) Lặp scroll & thu thập
    while len(invite_friends) < max_friends:
        # 2.1 Lấy tất cả ô contact invite
        items = d.xpath(
            "//*[@resource-id='com.zing.zalo:id/info_contact_row']").all()
        id = 1

        if len(items) == 0:
            break

        # start=1 để khớp với indexing 1-based của XPath
        for idx, it in enumerate(items, start=1):
            # Sáu xử lý lấy text theo từng field
            try:
                name_el = d.xpath(
                    f"//*[@resource-id='com.zing.zalo:id/info_contact_row'][{idx}]"
                    f"//*[@resource-id='com.zing.zalo:id/name']"
                )
                name_text = name_el.text
            except Exception:
                name_text = ""
            if name_text in seen or name_text == "":
                continue
            try:
                inv_el = d.xpath(
                    f"//*[@resource-id='com.zing.zalo:id/info_contact_row'][{idx}]"
                    f"//*[@resource-id='com.zing.zalo:id/tvInvitation']"
                )
                inv_text = inv_el.get().text
            except Exception:
                inv_text = ""

            try:
                detail_el = d.xpath(
                    f"//*[@resource-id='com.zing.zalo:id/info_contact_row'][{idx}]"
                    f"//*[@resource-id='com.zing.zalo:id/tvInvitationDetail']"
                )
                detail_text = detail_el.get().text

            except Exception:
                detail_text = ""

            try:
                ivm = d.xpath(
                    f"//*[@resource-id='com.zing.zalo:id/info_contact_row'][{idx}]"
                    f"//*[@resource-id='com.zing.zalo:id/buddy_dp']"
                )

                iv = ivm.get()
                img = iv.screenshot()
                max_w, max_h = 200, 200
                img.thumbnail((max_w, max_h), resample=Image.BILINEAR)
                buf = io.BytesIO()
                img.save(buf, format="JPEG", optimize=True, quality=75)
                avatar_b64 = base64.b64encode(
                    buf.getvalue()).decode("ascii")
                if not already_sent_number(AVA_NUMBER_PATH):
                    ava_number = 0
                else:
                    ava_number = read_sent_number(AVA_NUMBER_PATH)
                with open(f"{IMAGE_FILE_DATA_PATH}/ava_{ava_number}.txt", "w", encoding="utf-8") as f:
                    f.write(avatar_b64)

                log_sent_number(ava_number+1, AVA_NUMBER_PATH)

                avatar_b64 = f"{IMAGE_FILE_DATA_PATH}/ava_{ava_number}.txt"
            except Exception:
                avatar_b64 = ""

    # Loại trùng tên nếu cần
            seen.add(name_text)

            invite_friends.append({
                "name": name_text,
                "ava": avatar_b64,
                "message_invite": inv_text,
                "message_detail": detail_text
            })

        if len(invite_friends) >= max_friends:
            break

        if len(invite_friends) == 0:
            break

        # Kiểm tra có nội dung mới không
        if items:
            id = len(items)
            while True:
                try:
                    last_raw = d.xpath(
                        f"//*[@resource-id='com.zing.zalo:id/info_contact_row'][{id}]"
                        f"//*[@resource-id='com.zing.zalo:id/name']"
                    ).get()
                    break
                except Exception:
                    id = id - 1

            if last_raw.text == previous_last:
                same_count += 1
                if same_count >= 2:
                    break
            else:
                same_count = 0
                previous_last = last_raw.text

        # 2.2 Swipe để load thêm
        w, h = d.window_size()
        d.swipe(w//2, int(h*0.8), w//2, int(h*0.2), duration=0.8)
        eventlet.sleep(scroll_delay)

    return invite_friends


def get_list_prior_chat_boxes_u2(d: u2.Device, tag_name={}, data_chat_boxes={}, friend_or_nots={}, max_chat_boxes: int = 1500, scroll_delay: float = 1.0, retire=3, scroll_or_not=True):
    """
    Lấy toàn bộ nhóm từ tab Danh bạ trên Zalo Android bằng uiautomator2.
    - d: uiautomator2 Device
    - max_groups: giới hạn tối đa số nhóm thu thập
    """
    chat_boxes = []
    seen = set()
    previous_last = ""
    same_count = 0
    # clm = d.xpath('//*[@text="Ưu tiên"]')
    d.app_start("com.zing.zalo", stop=True)
    # d.implicitly_wait(3.0)
    # Phần khởi động

    # 3) Lặp scroll & thu thập
    while len(chat_boxes) < max_chat_boxes:
        # lấy tất cả item nhóm đang hiển thị
        '''
        if d(text="Xem thêm").exists:
            d(text="Xem thêm").click()
            eventlet.sleep(1.0)
        '''
        items = d.xpath("//android.widget.FrameLayout[@text!='']").all()

        for it in items:
            raw = it.text or ""
            if "Media Box" in raw or "Zalo" in raw or "Tin nhắn từ người lạ" in raw or "ngừng hoạt động" in raw or "vào nhóm và cộng đồng" in raw or "Kết bạn" in raw:
                continue
            if "Xem thêm" in raw:
                it.click()
                eventlet.sleep(1.0)
                if d(resourceId="com.zing.zalo:id/txtTitle").exists or d(resourceId="com.zing.zalo:id/action_bar_title").exists:
                    d.press('back')
                    eventlet.sleep(1.0)
                break
            print(raw)
            # Tách thành các dòng không rỗng
            lines = [line.strip()
                        for line in raw.split("\n") if line.strip()]
            name = lines[0] if len(lines) > 0 else ""
            time_str = lines[1] if len(lines) > 1 else ""
            message = "\n".join(lines[2:]) if len(lines) > 2 else ""

            if not name or name in seen:
                continue
            now = datetime.now()
            temp = time_str.replace(" ", "")
            if "phút" in time_str:
                time_str = time_str.replace("phút", "")
                time_str = time_str.replace(" ", "")
                dt_minus_48 = now - timedelta(minutes=int(time_str))
                hour = dt_minus_48.hour
                minute = dt_minus_48.minute
                day = dt_minus_48.day
                month = dt_minus_48.month
                year = dt_minus_48.year
                if len(str(hour)) == 1:
                    hour = f"0{hour}"
                if len(str(minute)) == 1:
                    minute = f"0{minute}"
                if len(str(day)) == 1:
                    day = f"0{day}"
                if len(str(month)) == 1:
                    month = f"0{month}"
                time_str = f"{hour}:{minute} {day}/{month}/{year}"
            elif "giờ" in time_str:
                dt_minus_4h = now - timedelta(hours=4)
                hour = dt_minus_4h.hour
                minute = dt_minus_4h.minute
                day = dt_minus_4h.day
                month = dt_minus_4h.month
                year = dt_minus_4h.year
                if len(str(hour)) == 1:
                    hour = f"0{hour}"
                if len(str(minute)) == 1:
                    minute = f"0{minute}"
                if len(str(day)) == 1:
                    day = f"0{day}"
                if len(str(month)) == 1:
                    month = f"0{month}"
                time_str = f"{hour}:{minute} {day}/{month}/{year}"
            elif "T" in time_str or "CN" in time_str:
                today = date.today()
                time_str = time_str.replace(" ", "")
                target = mapping.get(time_str.upper())
                if target is None:
                    raise ValueError("Không xác định được thứ từ đầu vào.")
                offset = (today.weekday() - target) % 7
                result = today - timedelta(days=offset)
                day = result.day
                month = result.month
                year = result.year
                if len(str(day)) == 1:
                    day = f"0{day}"
                if len(str(month)) == 1:
                    month = f"0{month}"
                print("Ngày của", time_str.upper(),
                        "gần nhất:", result.strftime("%d/%m/%Y"))
                time_str = f"{day}/{month}/{year}"
            elif len(temp) <= 6:
                today = date.today()
                year = today.year
                time_str += "/" + str(year)

            seen.add(name)
            if name in tag_name.keys():
                tag = tag_name[name]
            else:
                tag = ""

            if name in data_chat_boxes.keys():
                print("Đoạn chat này tồn tại lần 2 ", name)
                data_chat_box = data_chat_boxes[name]
            else:
                data_chat_box = ""
                
            if name in friend_or_nots.keys():
                friend_or_not = friend_or_nots[name]
                chat_boxes.append({
                    "name": name,
                    "ava": "",
                    "time": time_str,
                    "message": message,
                    "status": "seen",
                    "tag": tag,
                    "data_chat_box": data_chat_box,
                    "friend_or_not": friend_or_not
                    # "avatar": (nếu cần, thêm screenshot của ImageView con tương tự)
                })
            else:
                chat_boxes.append({
                    "name": name,
                    "ava": "",
                    "time": time_str,
                    "message": message,
                    "status": "seen",
                    "tag": tag,
                    "data_chat_box": data_chat_box
                    # "avatar": (nếu cần, thêm screenshot của ImageView con tương tự)
                })
            if len(chat_boxes) >= max_chat_boxes:
                break

        if len(chat_boxes) >= max_chat_boxes:
            break

        # Kiểm tra còn nội dung mới
        if items:
            last_raw = items[-1].text or ""
            if last_raw == previous_last:
                same_count += 1
                if same_count >= 2:
                    break
            else:
                same_count = 0
                previous_last = last_raw
        if not scroll_or_not:
            break
        # Scroll lên để load thêm
        w, h = d.window_size()
        d.swipe(w // 2, int(h * 0.8), w // 2, int(h * 0.2), duration=0.8)
        eventlet.sleep(scroll_delay)

    return chat_boxes


def get_list_unseen_chat_boxes_u2(d: u2.Device, max_chat_boxes: int = 50, scroll_delay: float = 1.0, retire=3):
    """
    Lấy toàn bộ nhóm từ tab Danh bạ trên Zalo Android bằng uiautomator2.
    - d: uiautomator2 Device
    - max_groups: giới hạn tối đa số nhóm thu thập
    """
    chat_boxes = []
    seen = set()
    previous_last = ""
    same_count = 0
    d.app_start("com.zing.zalo", stop=True)
    # d.implicitly_wait(3.0)
    # Phần khởi động

    eventlet.sleep(1.0)
    d.xpath('//*[@resource-id="com.zing.zalo:id/tab_container_right"]/android.widget.LinearLayout[1]/android.widget.FrameLayout[1]').click()
    eventlet.sleep(1.0)
    d.xpath(
        '//*[@resource-id="com.zing.zalo:id/rv_popover_options"]/android.widget.LinearLayout[1]').click()
    eventlet.sleep(1.0)
    # 3) Lặp scroll & thu thập
    while len(chat_boxes) < max_chat_boxes:
        # lấy tất cả item nhóm đang hiển thị
        items = d.xpath("//android.widget.FrameLayout[@text!='']").all()

        if len(items) == 0:
            break

        for it in items:
            raw = it.text or ""
            if "Media Box" in raw or "Zalo" in raw or "Tin nhắn từ người lạ" in raw:
                continue
            print(raw)
            # Tách thành các dòng không rỗng
            lines = [line.strip()
                        for line in raw.split("\n") if line.strip()]
            name = lines[0] if len(lines) > 0 else ""
            time_str = lines[1] if len(lines) > 1 else ""
            message = "\n".join(lines[2:]) if len(lines) > 2 else ""

            if not name or name in seen:
                continue
            now = datetime.now()
            temp = time_str.replace(" ", "")
            if "phút" in time_str:
                time_str = time_str.replace("phút", "")
                time_str = time_str.replace(" ", "")
                dt_minus_48 = now - timedelta(minutes=int(time_str))
                hour = dt_minus_48.hour
                minute = dt_minus_48.minute
                day = dt_minus_48.day
                month = dt_minus_48.month
                year = dt_minus_48.year
                time_str = f"{hour}:{minute} {day}/{month}/{year}"
            elif "giờ" in time_str:
                dt_minus_4h = now - timedelta(hours=4)
                hour = dt_minus_4h.hour
                minute = dt_minus_4h.minute
                day = dt_minus_4h.day
                month = dt_minus_4h.month
                year = dt_minus_4h.year
                time_str = f"{hour}:{minute} {day}/{month}/{year}"
            elif "T" in time_str or "CN" in time_str:
                today = date.today()
                time_str = time_str.replace(" ", "")
                target = mapping.get(time_str.upper())
                if target is None:
                    raise ValueError("Không xác định được thứ từ đầu vào.")
                offset = (today.weekday() - target) % 7
                result = today - timedelta(days=offset)
                day = result.day
                month = result.month
                year = result.year
                print("Ngày của", time_str.upper(),
                        "gần nhất:", result.strftime("%d/%m/%Y"))
                time_str = f"{day}/{month}/{year}"
            elif len(temp) <= 6:
                today = date.today()
                year = today.year
                time_str += "/" + str(year)

            seen.add(name)
            chat_boxes.append({
                "name": name,
                "time": time_str,
                "message": message,
                "status": "unseen",
                "tag": ""
                # "avatar": (nếu cần, thêm screenshot của ImageView con tương tự)
            })
            if len(chat_boxes) >= max_chat_boxes:
                break

        if len(chat_boxes) >= max_chat_boxes:
            break

        # Kiểm tra còn nội dung mới
        if items:
            last_raw = items[-1].text or ""
            if last_raw == previous_last:
                same_count += 1
                if same_count >= 2:
                    break
            else:
                same_count = 0
                previous_last = last_raw

        # Scroll lên để load thêm
        w, h = d.window_size()
        d.swipe(w // 2, int(h * 0.8), w // 2, int(h * 0.2), duration=0.8)
        eventlet.sleep(scroll_delay)

    return chat_boxes


def get_data_chat_boxes_u2(d: u2.Device, gr_or_pvp: str, time_and_mes, max_scroll: int = 20, scroll_delay: float = 1.0):
    """
    Trả về dict chứa hộp chat hiện có trong màn hình chat của Zalo Android.
    - div_child_selector: dict để tìm tab/conversation (ví dụ {'resourceId': '...', 'text': 'Tên bạn'})
    - device_url: đường dẫn để kết nối uiautomator2 (ví dụ '192.168.1.100:5555')
    - max_scroll: số lần swipe để load tin cũ.
    - scroll_delay: thời gian chờ giữa các lần swipe.
    """
    # d = run_start(d)
    data_chat_box = []
    chat = {}
    seen = set()
    previous_last = None
    same_count = 0
    check_you_or_fr = 0
    check_call = 0
    lack = False
    chat_lack_raw = []
    chat_lack = []
    nope = ["[Hình ảnh]", "[Sticker]", "[Vị trí]", "[Video]"]

    if gr_or_pvp == "pvp":
        try:
            if d(resourceId="com.zing.zalo:id/action_bar_title").exists:
                fr = d(resourceId="com.zing.zalo:id/action_bar_title").get_text()
                name_sender = fr
            else:
                fr = d(resourceId="com.zing.zalo:id/txtTitle").get_text()
                name_sender = fr
        except Exception as e:
            try:
                fr = d(resourceId="com.zing.zalo:id/txtTitle").get_text()
                name_sender = fr
            except Exception as e:
                print("Đã gặp lỗi trong quá trình lấy lịch sử tin nhắn ", e)
                return data_chat_box
    check = True
    for nm in range(max_scroll + 1):
        # 2) Tìm tất cả message bubble
        try:
            items = d.xpath("//android.view.ViewGroup[@text!='']").all()
            num = len(items) - 1
            # Đọc từ dưới lên trên (các tin mới trước)
            for id in range(num+1):
                raw = items[num-id].text or ""

                if gr_or_pvp == 'gr':
                    if raw in seen and not any(it in raw for it in nope) and raw not in chat_lack_raw:
                        continue

                else:
                    if raw in seen:
                        continue

                if "Tin nhắn đã được thu hồi" in raw:
                    continue

                print(time_and_mes)
                print(raw)
                check_1 = True
                if "[Hình ảnh]" in raw or "[Sticker]" in raw:
                    bounds = items[num-id].info['bounds']
                    width, height = bounds['right'] - \
                        bounds['left'], bounds['bottom'] - bounds['top']
                    # if height <= 400:
                    #    continue
                formatted = date.today().strftime("%d/%m/%Y")
                raw = raw.replace("Hôm nay", formatted)
                if "[File]" in raw:
                    raw = raw.split('…')[0]
                    if time_and_mes['message'] != "":
                        if time_and_mes['message'] in raw or raw in time_and_mes['message']:
                            check = False
                            check_1 = False
                            break
                    mes = time_and_mes['message'].split("\n")
                    if len(mes) > 1:
                        if mes[1] in raw:
                            check = False
                            check_1 = False
                            break
                seen.add(raw)
                lines = [l for l in raw.split("\n") if l.strip()]

                if time_and_mes['message'] in lines:
                    if "[Hình ảnh]" not in time_and_mes['message']:
                        check = False
                        check_1 = False
                        break
                    else:
                        if time_and_mes['time'] in lines or time_and_mes['time'] == "":
                            check = False
                            check_1 = False
                            break
                if "Gọi điện" in raw or "Nhắn tin" in raw:
                    check = False
                    check_1 = False

                if not check_1:
                    break

                # Loại bỏ dòng 'Đã nhận'
                if lines and "Đã nhận" in lines[-1]:
                    lines = lines[:-1]
                    check_you_or_fr = 1
                if lines and "Đã gửi" in lines[-1]:
                    lines = lines[:-1]
                    check_you_or_fr = 1
                if lines and "Đã xem" in lines[-1]:
                    lines = lines[:-1]
                    check_you_or_fr = 1

                # Xử lý file/image
                if lines and "[File]" in raw:
                    # time_str = lines[-1] if len(lines) > 1 else ''
                    def has_time_token(s):
                        return any(len(tok) == 5 and tok[2] == ':' for tok in s.split())
                    if has_time_token(lines[0]) and has_time_token(lines[-1]):
                        time_str = lines[-1]
                        message = '\n'.join(lines[1:-1])

                        if check_you_or_fr == 1:
                            data_chat_box.append(chat)
                            chat = {}

                        if check_you_or_fr == 2:
                            data_chat_box.append(chat)
                            chat = {}

                        if gr_or_pvp == 'gr':
                            if len(lines) < 4 or "[Hình ảnh]" in lines[1]:
                                if raw not in chat_lack_raw:
                                    lack = True
                                    chat_lack_raw.append(raw)
                            else:
                                name_sender = lines[1]
                                message = '\n'.join(lines[2:-1])
                        check_you_or_fr = 0

                    elif has_time_token(lines[0]):
                        time_str = lines[0]
                        message = '\n'.join(lines[1:])
                        # name_sender = lines[1]
                        if check_you_or_fr == 1:
                            data_chat_box.append(chat)
                            chat = {}

                        if check_you_or_fr == 2:
                            data_chat_box.append(chat)
                            chat = {}

                        if gr_or_pvp == 'gr':
                            if len(lines) < 3 or "[Hình ảnh]" in lines[1]:
                                if raw not in chat_lack_raw:
                                    lack = True
                                    chat_lack_raw.append(raw)
                            else:
                                name_sender = lines[1]
                                message = '\n'.join(lines[2:])
                        check_you_or_fr = 0

                    elif has_time_token(lines[-1]):
                        time_str = lines[-1]
                        message = '\n'.join(lines[:-1])

                        if check_you_or_fr == 1:
                            data_chat_box.append(chat)
                            chat = {}

                        if check_you_or_fr == 2:
                            data_chat_box.append(chat)
                            chat = {}

                        if gr_or_pvp == 'gr':
                            if len(lines) < 3 or "[Hình ảnh]" in lines[0]:
                                if raw not in chat_lack_raw:
                                    lack = True
                                    chat_lack_raw.append(raw)
                            else:
                                name_sender = lines[0]
                                message = '\n'.join(lines[1:-1])
                        check_you_or_fr = 0
                    else:
                        time_str = ""
                        if gr_or_pvp == 'gr':
                            bounds = items[num-id].info['bounds']
                            width, height = bounds['right'] - \
                                bounds['left'], bounds['bottom'] - \
                                bounds['top']
                            cx = bounds['left'] + int(width * 0.075)
                            cy = bounds['top'] + int(width * 0.075)
                            d = safe_normal_click(d, cx, cy)
                            eventlet.sleep(1.0)
                            btn = d.xpath('//*[@text="Xem trang cá nhân"]')
                            if btn.exists:
                                print("Đây là người nhắn")
                                if raw in chat_lack_raw:
                                    if len(chat_lack) > 0:
                                        del chat_lack[-1]
                                    if len(chat_lack_raw) > 0:
                                        del chat_lack_raw[-1]
                                check_you_or_fr = 2
                                name_sender = lines[0]
                                if not lack:
                                    chat[f'{name_sender}'] = []
                                message = '\n'.join(lines[1:])
                                '''
                                bounds = btn.info['bounds']
                                width, height = bounds['right'] - \
                                    bounds['left'], bounds['bottom'] - \
                                    bounds['top']
                                cx = bounds['left'] + int(width * 0.075)
                                cy = bounds['top'] + int(width * 0.075)
                                '''
                                d.press('back')
                                eventlet.sleep(1.0)
                                items = d.xpath(
                                    "//android.view.ViewGroup[@text!='']").all()
                            else:
                                message = '\n'.join(lines)
                                if raw not in chat_lack_raw:
                                    lack = True
                                    chat_lack_raw.append(raw)
                        else:
                            message = '\n'.join(lines)
                    '''
                    now = datetime.now()
                    print("Ngày:", now.day)
                    print("Tháng:", now.month)
                    print("Năm:", now.year)
                    print("Giờ:", now.hour)
                    print("Phút:", now.minute)
                    print("Giây:", now.second)
                    message = f"{message} {now.hour}:{now.minute}:{now.second} {now.day}/{now.month}/{now.year}"
                    '''
                    numm = message.split("\n")
                    if len(numm) > 2:
                        message = "\n".join(numm[:2])

                    formatted = date.today().strftime("%d/%m/%Y")
                    time_str = time_str.replace("Hôm nay", formatted)
                    typ = 'image' if any(
                        ext in message for ext in ('jpg', 'png')) else 'file'

                    if check_you_or_fr == 0:
                        bounds = items[num-id].info['bounds']
                        width, height = bounds['right'] - \
                            bounds['left'], bounds['bottom'] - bounds['top']
                        cx = bounds['left'] + int(width * 0.24)
                        cy = bounds['top'] + height * 0.5
                        print(f"Toa độ click là {cx}, {cy}")
                        # d.long_click(cx, cy, duration=1.0)
                        d = safe_click(d, cx, cy)
                        eventlet.sleep(1.5)
                        btn = d.xpath('//*[@text="Trả lời"]')
                        if btn.exists:
                            print("Có tồn tại Trả lời")
                            th = d.xpath('//*[@text="Thu hồi"]')
                            if not th.exists:
                                check_you_or_fr = 2
                                if not lack:
                                    chat[f'{name_sender}'] = []
                            else:
                                print("Có tồn tại Thu hồi")
                                check_you_or_fr = 1
                                chat['you'] = []

                            d.press('back')
                            eventlet.sleep(1.0)
                            items = d.xpath(
                                "//android.view.ViewGroup[@text!='']").all()

                        else:
                            print("Không tồn tại Trả lời")
                            check_you_or_fr = 1
                            chat['you'] = []
                            # continue

                    if check_you_or_fr == 1:
                        if typ == 'image':
                            '''
                            img = iv.screenshot()  # trả về PIL.Image
                            buf = io.BytesIO()
                            img.save(buf, format="PNG")
                            avatar_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
                            '''
                            iv = items[num-id].screenshot()
                            img = iv.convert("RGB") if hasattr(
                                iv, "convert") else Image.open(io.BytesIO(iv))

                            bounds = items[num-id].info['bounds']
                            width, height = bounds['right'] - \
                                bounds['left'], bounds['bottom'] - \
                                bounds['top']

                            left = bounds['left'] + int(width * 0.225)
                            right = bounds['left'] + int(width * 0.95)
                        # top = bounds['top']
                            top = 0
                        # bottom = bounds['bottom'] - int(height * 0.08)
                            bottom = int(height * 0.92)

                            cropped = img.crop((left, top, right, bottom))

                            cropped_small = cropped.filter(
                                ImageFilter.UnsharpMask(radius=1, percent=120, threshold=3))

                            buf = io.BytesIO()
                            cropped_small.save(buf, format="PNG")

                            # output_path = "cropped_output.jpg"
                            # cropped.convert("RGB").save(output_path, "JPEG", optimize=True, quality=75)
                            avatar_b64 = base64.b64encode(
                                buf.getvalue()).decode("ascii")

                            if not already_sent_number(MES_NUMBER_PATH):
                                mes_number = 0
                            else:
                                mes_number = read_sent_number(
                                    MES_NUMBER_PATH)
                            with open(f"{IMAGE_FILE_DATA_PATH}/mes_{mes_number}.txt", "w", encoding="utf-8") as f:
                                f.write(avatar_b64)

                            log_sent_number(
                                mes_number+1, MES_NUMBER_PATH)

                            avatar_b64 = f"{IMAGE_FILE_DATA_PATH}/mes_{mes_number}.txt"

                            chat['you'].append(
                                {"time": time_str, "type": typ, "data": message, "image_data": avatar_b64})
                        else:
                            chat['you'].append(
                                {"time": time_str, "type": typ, "data": message})
                    else:
                        if typ == 'image':
                            iv = items[num-id].screenshot()
                            img = iv.convert("RGB") if hasattr(
                                iv, "convert") else Image.open(io.BytesIO(iv))

                            bounds = items[num-id].info['bounds']
                            width, height = bounds['right'] - \
                                bounds['left'], bounds['bottom'] - \
                                bounds['top']

                            left = bounds['left'] + int(width * 0.125)
                            right = bounds['left'] + int(width * 0.85)
                        # top = bounds['top']
                            top = 0
                        # bottom = bounds['bottom'] - int(height * 0.08)
                            bottom = int(height * 0.92)

                            cropped = img.crop((left, top, right, bottom))

                            cropped_small = cropped.filter(
                                ImageFilter.UnsharpMask(radius=1, percent=120, threshold=3))

                            buf = io.BytesIO()
                            cropped_small.save(buf, format="PNG")

                            # output_path = "cropped_output.jpg"
                            # cropped.convert("RGB").save(output_path, "JPEG", optimize=True, quality=75)
                            avatar_b64 = base64.b64encode(
                                buf.getvalue()).decode("ascii")

                            if not already_sent_number(MES_NUMBER_PATH):
                                mes_number = 0
                            else:
                                mes_number = read_sent_number(
                                    MES_NUMBER_PATH)
                            with open(f"{IMAGE_FILE_DATA_PATH}/mes_{mes_number}.txt", "w", encoding="utf-8") as f:
                                f.write(avatar_b64)

                            log_sent_number(
                                mes_number+1, MES_NUMBER_PATH)

                            avatar_b64 = f"{IMAGE_FILE_DATA_PATH}/mes_{mes_number}.txt"

                            if gr_or_pvp == 'pvp':
                                chat[f'{name_sender}'].append(
                                    {"time": time_str, "type": typ, "data": message, "image_data": avatar_b64})
                            else:
                                if lack:
                                    chat_lack.append(
                                        {"time": time_str, "type": typ, "data": message, "image_data": avatar_b64})
                                    lack = False
                                elif not lack and len(chat_lack) > 0:
                                    if raw not in chat_lack_raw:
                                        chat[f'{name_sender}'].append(
                                            {"time": time_str, "type": typ, "data": message, "image_data": avatar_b64})
                                        chat[f'{name_sender}'] = chat_lack + \
                                            chat[f'{name_sender}']
                                        chat_lack = []
                                        chat_lack_raw = []
                                else:
                                    chat[f'{name_sender}'].append(
                                        {"time": time_str, "type": typ, "data": message, "image_data": avatar_b64})
                        else:
                            if gr_or_pvp == 'pvp':
                                chat[f'{name_sender}'].append(
                                    {"time": time_str, "type": typ, "data": message, "file_data": ""})
                            else:
                                if lack:
                                    chat_lack.append(
                                        {"time": time_str, "type": typ, "data": message, "file_data": ""})
                                    lack = False
                                elif not lack and len(chat_lack) > 0:
                                    if raw not in chat_lack_raw:
                                        chat[f'{name_sender}'].append(
                                            {"time": time_str, "type": typ, "data": message, "file_data": ""})
                                        chat[f'{name_sender}'] = chat_lack + \
                                            chat[f'{name_sender}']
                                        chat_lack = []
                                        chat_lack_raw = []
                                else:
                                    chat[f'{name_sender}'].append(
                                        {"time": time_str, "type": typ, "data": message, "file_data": ""})

                else:
                    # Xác định time và message
                    time_str = ''
                    message = ''
                    if lines:
                        # kiểm tra dấu giờ phút
                        def has_time_token(s):
                            return any(len(tok) == 5 and tok[2] == ':' for tok in s.split())
                        if has_time_token(lines[0]) and has_time_token(lines[-1]):
                            time_str = lines[-1]
                            message = '\n'.join(lines[1:-1])

                            if check_you_or_fr == 1:
                                data_chat_box.append(chat)
                                chat = {}

                            if check_you_or_fr == 2:
                                data_chat_box.append(chat)
                                chat = {}

                            if gr_or_pvp == 'gr':
                                if "[Hình ảnh]" in lines[1] or "[Vị trí]" in lines[1] or "[Sticker]" in lines[1]:
                                    if raw not in chat_lack_raw:
                                        lack = True
                                        chat_lack_raw.append(raw)
                                elif len(lines) < 4:
                                    message = "[Video]"
                                    name_sender = lines[1]
                                else:
                                    name_sender = lines[1]
                                    message = '\n'.join(lines[2:-1])

                            check_you_or_fr = 0

                        elif has_time_token(lines[0]):
                            time_str = lines[0]
                            message = '\n'.join(lines[1:])
                            if check_you_or_fr == 1:
                                data_chat_box.append(chat)
                                chat = {}

                            if check_you_or_fr == 2:
                                data_chat_box.append(chat)
                                chat = {}

                            if gr_or_pvp == 'gr':
                                if "[Hình ảnh]" in lines[1] or "[Vị trí]" in lines[1] or "[Sticker]" in lines[1]:
                                    if raw not in chat_lack_raw:
                                        lack = True
                                        chat_lack_raw.append(raw)
                                elif len(lines) < 3:
                                    message = "[Video]"
                                    name_sender = lines[1]
                                else:
                                    name_sender = lines[1]
                                    message = '\n'.join(lines[2:])

                            check_you_or_fr = 0

                        elif has_time_token(lines[-1]):
                            time_str = lines[-1]
                            message = '\n'.join(lines[:-1])
                            if check_you_or_fr == 1:
                                data_chat_box.append(chat)
                                chat = {}
                            if check_you_or_fr == 2:
                                data_chat_box.append(chat)
                                chat = {}

                            check_you_or_fr = 0

                            if gr_or_pvp == 'gr':
                                if "[Hình ảnh]" in lines[0] or "[Vị trí]" in lines[0] or "[Sticker]" in lines[0]:
                                    if raw not in chat_lack_raw:
                                        lack = True
                                        chat_lack_raw.append(raw)
                                else:
                                    bounds = items[num-id].info['bounds']
                                    width, height = bounds['right'] - \
                                        bounds['left'], bounds['bottom'] - \
                                        bounds['top']
                                    cx = bounds['left'] + int(width * 0.075)
                                    cy = bounds['top'] + int(width * 0.075)
                                    d = safe_normal_click(d, cx, cy)
                                    eventlet.sleep(1.0)
                                    btn = d(text="Xem trang cá nhân")
                                    if btn.exists:
                                        print("Đây là người nhắn")
                                        if raw in chat_lack_raw:
                                            if len(chat_lack) > 0:
                                                del chat_lack[-1]
                                            if len(chat_lack_raw) > 0:
                                                del chat_lack_raw[-1]
                                        check_you_or_fr = 2
                                        if len(lines) < 3:
                                            message = "[Video]"
                                            name_sender = lines[0]
                                        else:
                                            name_sender = lines[0]
                                            message = '\n'.join(lines[1:-1])
                                        if not lack:
                                            chat[f'{name_sender}'] = []
                                        d.press('back')
                                        eventlet.sleep(1.0)
                                        items = d.xpath(
                                            "//android.view.ViewGroup[@text!='']").all()
                                    else:
                                        print("Không có người nhắn")
                                        message = '\n'.join(lines[:-1])
                                        if raw not in chat_lack_raw:
                                            lack = True
                                            chat_lack_raw.append(raw)

                        else:
                            time_str = ""
                            if gr_or_pvp == 'gr':
                                bounds = items[num-id].info['bounds']
                                width, height = bounds['right'] - \
                                    bounds['left'], bounds['bottom'] - \
                                    bounds['top']
                                cx = bounds['left'] + int(width * 0.075)
                                cy = bounds['top'] + int(width * 0.075)
                                d = safe_normal_click(d, cx, cy)
                                eventlet.sleep(1.0)
                                btn = d.xpath('//*[@text="Xem trang cá nhân"]')
                                if btn.exists:
                                    print("Đây là người nhắn")
                                    if raw in chat_lack_raw:
                                        if len(chat_lack) > 0:
                                            del chat_lack[-1]
                                        if len(chat_lack_raw) > 0:
                                            del chat_lack_raw[-1]
                                    check_you_or_fr = 2
                                    name_sender = lines[0]
                                    if not lack:
                                        chat[f'{name_sender}'] = []
                                    message = '\n'.join(lines[1:])
                                    d.press('back')
                                    eventlet.sleep(1.0)
                                    items = d.xpath(
                                        "//android.view.ViewGroup[@text!='']").all()
                                else:
                                    message = '\n'.join(lines)
                                    if raw not in chat_lack_raw:
                                        lack = True
                                        chat_lack_raw.append(raw)
                            else:
                                message = '\n'.join(lines)

                        formatted = date.today().strftime("%d/%m/%Y")
                        time_str = time_str.replace("Hôm nay", formatted)
                        if check_you_or_fr == 0 and "Cuộc gọi thoại đi" not in message and "Cuộc gọi thoại đến" not in message and "Tin nhắn đã được thu hồi" not in message:
                            bounds = items[num-id].info['bounds']
                            width, height = bounds['right'] - \
                                bounds['left'], bounds['bottom'] - \
                                bounds['top']
                            cx = bounds['left'] + int(width * 0.24)
                            cy = bounds['top'] + height * 0.5
                            print("Đã click rồi")
                            # d.long_click(cx, cy, duration=1.0)
                            d = safe_click(d, cx, cy)
                            print(f"Tọa độ là {cx}, {cy}")
                            eventlet.sleep(1.5)
                            btn = d.xpath('//*[@text="Trả lời"]')
                            if btn.exists:
                                info = btn.info

                                print("Có tồn tại Trả lời")
                                th = d.xpath('//*[@text="Thu hồi"]')
                                if not th.exists:
                                    check_you_or_fr = 2
                                    if not lack:
                                        chat[f'{name_sender}'] = []
                                else:
                                    print("Có tồn tại Thu hồi")
                                    check_you_or_fr = 1
                                    chat['you'] = []

                                d.press('back')
                                eventlet.sleep(1.0)
                                items = d.xpath(
                                    "//android.view.ViewGroup[@text!='']").all()

                            else:
                                print("Không tồn tại Trả lời")
                                check_you_or_fr = 1
                                chat['you'] = []
                                # items = d.xpath("//android.view.ViewGroup[@text!='']").all()
                    # Phân loại
                    '''
                    if lines and "[Sticker]" in message:
                        if check_you_or_fr == 1:
                        chat[f'you_{id_y}'].append({"time": time_str, "type": "sticker", "data": message})
                        else:
                        chat[f'{sender}_{id_f}'].append({"time": time_str, "type": "sticker", "data": message})
                    '''
                    if "Tin nhắn đã được thu hồi" in message:
                        pass
                    elif "[Hình ảnh]" in message or "[Sticker]" in message or "[Video]" in message:
                        if check_you_or_fr == 1:
                            if "[Sticker]" in message:
                                left_rate = 0.65
                                right_rate = 0.95
                                top_rate = 0.05
                                bottom_rate = 0.95
                            else:
                                left_rate = 0.225
                                right_rate = 0.95
                                top_rate = 0
                                bottom_rate = 0.92

                            if "[Video]" not in message:
                                iv = items[num-id].screenshot()
                                img = iv.convert("RGB") if hasattr(
                                    iv, "convert") else Image.open(io.BytesIO(iv))
                                bounds = items[num-id].info['bounds']
                                width, height = bounds['right'] - \
                                    bounds['left'], bounds['bottom'] - \
                                    bounds['top']
                                left = bounds['left'] + int(width * left_rate)
                                right = bounds['left'] + \
                                    int(width * right_rate)
                            # top = bounds['top']
                                top = 0 + int(height * top_rate)
                            # bottom = bounds['bottom'] - int(height * 0.08)
                                bottom = 0 + int(height * bottom_rate)
                                cropped = img.crop((left, top, right, bottom))
                                cropped_small = cropped.filter(
                                    ImageFilter.UnsharpMask(radius=1, percent=120, threshold=3))

                                buf = io.BytesIO()
                                cropped_small.save(buf, format="PNG")
                            # output_path = "cropped_output.jpg"
                            # cropped.convert("RGB").save(output_path, "JPEG", optimize=True, quality=75)
                                avatar_b64 = base64.b64encode(
                                    buf.getvalue()).decode("ascii")

                                if not already_sent_number(MES_NUMBER_PATH):
                                    mes_number = 0
                                else:
                                    mes_number = read_sent_number(
                                        MES_NUMBER_PATH)
                                with open(f"{IMAGE_FILE_DATA_PATH}/mes_{mes_number}.txt", "w", encoding="utf-8") as f:
                                    f.write(avatar_b64)

                                log_sent_number(
                                    mes_number+1, MES_NUMBER_PATH)

                                avatar_b64 = f"{IMAGE_FILE_DATA_PATH}/mes_{mes_number}.txt"

                            else:
                                avatar_b64 = ""
                                try:
                                    iv = d(resourceId="com.zing.zalo:id/video_view")
                                    img = iv.screenshot()
                                    # max_w, max_h = 200, 200
                                    # nhanh, giữ tỉ lệ gốc :contentReference[oaicite:1]{index=1}
                                    cropped_small = img

                                    buf = io.BytesIO()
                                    cropped_small.save(buf, format="PNG")
                                    # base64 chỉ chứa ký tự ASCII :contentReference[oaicite:3]{index=3}
                                    avatar_b64 = base64.b64encode(
                                        buf.getvalue()).decode("ascii")
                                except Exception as e:
                                    print("Có lỗi trong quá trình lấy dữ liệu video ", e)

                                if not already_sent_number(MES_NUMBER_PATH):
                                    mes_number = 0
                                else:
                                    mes_number = read_sent_number(
                                        MES_NUMBER_PATH)
                                with open(f"{IMAGE_FILE_DATA_PATH}/mes_{mes_number}.txt", "w", encoding="utf-8") as f:
                                    f.write(avatar_b64)

                                log_sent_number(
                                    mes_number+1, MES_NUMBER_PATH)

                                avatar_b64 = f"{IMAGE_FILE_DATA_PATH}/mes_{mes_number}.txt"

                            if "[Sticker]" in message:
                                chat['you'].append(
                                    {"time": time_str, "type": "sticker", "data": message, "sticker_data": avatar_b64})
                            elif "[Video]" in message:
                                chat['you'].append(
                                    {"time": time_str, "type": "video", "data": message, "video_data": avatar_b64})
                            else:
                                chat['you'].append(
                                    {"time": time_str, "type": "image", "data": message, "image_data": avatar_b64})
                        else:
                            if "[Sticker]" in message:
                                left_rate = 0.12
                                right_rate = 0.42
                                top_rate = 0.05
                                bottom_rate = 0.95
                            else:
                                left_rate = 0.125
                                right_rate = 0.85
                                top_rate = 0
                                bottom_rate = 0.92

                            if "[Video]" not in message:
                                iv = items[num-id].screenshot()
                                img = iv.convert("RGB") if hasattr(
                                    iv, "convert") else Image.open(io.BytesIO(iv))
                                bounds = items[num-id].info['bounds']
                                width, height = bounds['right'] - \
                                    bounds['left'], bounds['bottom'] - \
                                    bounds['top']
                                left = bounds['left'] + int(width * left_rate)
                                right = bounds['left'] + \
                                    int(width * right_rate)
                            # top = bounds['top']
                                top = 0 + int(height * top_rate)
                            # bottom = bounds['bottom'] - int(height * 0.08)
                                bottom = 0 + int(height * bottom_rate)
                                cropped = img.crop((left, top, right, bottom))
                                cropped_small = cropped.filter(
                                    ImageFilter.UnsharpMask(radius=1, percent=120, threshold=3))

                                buf = io.BytesIO()
                                cropped_small.save(buf, format="PNG")
                            # output_path = "cropped_output.jpg"
                            # cropped.convert("RGB").save(output_path, "JPEG", optimize=True, quality=75)
                                avatar_b64 = base64.b64encode(
                                    buf.getvalue()).decode("ascii")

                                if not already_sent_number(MES_NUMBER_PATH):
                                    mes_number = 0
                                else:
                                    mes_number = read_sent_number(
                                        MES_NUMBER_PATH)
                                with open(f"{IMAGE_FILE_DATA_PATH}/mes_{mes_number}.txt", "w", encoding="utf-8") as f:
                                    f.write(avatar_b64)

                                log_sent_number(
                                    mes_number+1, MES_NUMBER_PATH)

                                avatar_b64 = f"{IMAGE_FILE_DATA_PATH}/mes_{mes_number}.txt"

                            else:
                                avatar_b64 = ""
                                try:
                                    iv = d(resourceId="com.zing.zalo:id/video_view")
                                    img = iv.screenshot()
                                    max_w, max_h = 200, 200
                                    # nhanh, giữ tỉ lệ gốc :contentReference[oaicite:1]{index=1}
                                    cropped_small = img

                                    buf = io.BytesIO()
                                    cropped_small.save(buf, format="PNG")
                                    # base64 chỉ chứa ký tự ASCII :contentReference[oaicite:3]{index=3}
                                    avatar_b64 = base64.b64encode(
                                        buf.getvalue()).decode("ascii")
                                except Exception as e:
                                    print("Có lỗi trong quá trình lấy dữ liệu video ", e)

                                if not already_sent_number(MES_NUMBER_PATH):
                                    mes_number = 0
                                else:
                                    mes_number = read_sent_number(
                                        MES_NUMBER_PATH)
                                with open(f"{IMAGE_FILE_DATA_PATH}/mes_{mes_number}.txt", "w", encoding="utf-8") as f:
                                    f.write(avatar_b64)

                                log_sent_number(
                                    mes_number+1, MES_NUMBER_PATH)

                                avatar_b64 = f"{IMAGE_FILE_DATA_PATH}/mes_{mes_number}.txt"

                            if "[Sticker]" in message:
                                if gr_or_pvp == 'pvp':
                                    chat[f'{name_sender}'].append(
                                        {"time": time_str, "type": "sticker", "data": message, "sticker_data": avatar_b64})
                                else:
                                    if lack:
                                        chat_lack.append(
                                            {"time": time_str, "type": "sticker", "data": message, "sticker_data": avatar_b64})
                                        lack = False
                                    elif not lack and len(chat_lack) > 0:
                                        if raw not in chat_lack_raw:
                                            chat[f'{name_sender}'].append(
                                                {"time": time_str, "type": "sticker", "data": message, "sticker_data": avatar_b64})
                                            chat[f'{name_sender}'] = chat_lack + \
                                                chat[f'{name_sender}']
                                            chat_lack = []
                                            chat_lack_raw = []
                                    else:
                                        if raw not in chat_lack_raw:
                                            chat[f'{name_sender}'].append(
                                                {"time": time_str, "type": "sticker", "data": message, "sticker_data": avatar_b64})
                            elif "[Hình ảnh]" in message:
                                now = datetime.now()
                                print("Ngày:", now.day)
                                print("Tháng:", now.month)
                                print("Năm:", now.year)
                                print("Giờ:", now.hour)
                                print("Phút:", now.minute)
                                print("Giây:", now.second)
                                message = f"{message} {now.hour}:{now.minute}:{now.second} {now.day}/{now.month}/{now.year}"
                                if gr_or_pvp == 'pvp':
                                    chat[f'{name_sender}'].append(
                                        {"time": time_str, "type": "image", "data": message, "image_data": avatar_b64})
                                else:
                                    if lack:
                                        chat_lack.append(
                                            {"time": time_str, "type": "image", "data": message, "image_data": avatar_b64})
                                        lack = False
                                    elif not lack and len(chat_lack) > 0:
                                        if raw not in chat_lack_raw:
                                            chat[f'{name_sender}'].append(
                                                {"time": time_str, "type": "image", "data": message, "image_data": avatar_b64})
                                            chat[f'{name_sender}'] = chat_lack + \
                                                chat[f'{name_sender}']
                                            chat_lack = []
                                            chat_lack_raw = []
                                    else:
                                        if raw not in chat_lack_raw:
                                            chat[f'{name_sender}'].append(
                                                {"time": time_str, "type": "image", "data": message, "image_data": avatar_b64})
                            else:
                                if gr_or_pvp == 'pvp':
                                    chat[f'{name_sender}'].append(
                                        {"time": time_str, "type": "video", "data": message, "video_data": avatar_b64})
                                else:
                                    if lack:
                                        chat_lack.append(
                                            {"time": time_str, "type": "video", "data": message, "video_data": avatar_b64})
                                        lack = False
                                    elif not lack and len(chat_lack) > 0:
                                        if raw not in chat_lack_raw:
                                            chat[f'{name_sender}'].append(
                                                {"time": time_str, "type": "video", "data": message, "video_data": avatar_b64})
                                            chat[f'{name_sender}'] = chat_lack + \
                                                chat[f'{name_sender}']
                                            chat_lack = []
                                            chat_lack_raw = []
                                    else:
                                        if raw not in chat_lack_raw:
                                            chat[f'{name_sender}'].append(
                                                {"time": time_str, "type": "video", "data": message, "video_data": avatar_b64})
                    elif "Cuộc gọi thoại đi" in message:
                        if check_call == 2:
                            if len(chat['you']) > 0:
                                data_chat_box.append(chat)
                                # sender["you"] += 1
                                chat = {}
                                chat['you'] = []
                        chat['you'].append(
                            {"time": time_str, "type": "call", "data": message})
                        check_you_or_fr = 0
                        check_call = 1
                    elif "Cuộc gọi thoại đến" in message or "Bạn bị nhỡ Cuộc gọi thoại" in message:
                        if check_call == 1:
                            if len(chat[f'{name_sender}']) > 0:
                                data_chat_box.append(chat)
                                # id_f += 1
                                chat = {}
                                chat[f'{name_sender}'] = []
                        chat[f'{name_sender}'].append(
                            {"time": time_str, "type": "call", "data": message})
                        check_you_or_fr = 0
                        check_call = 2
                    elif "http" in message:
                        message = message.replace("html", "html\n")
                        message = message.replace("Tiktok", "\nTiktok")
                        if check_you_or_fr == 1:
                            chat['you'].append(
                                {"time": time_str, "type": "link", "data": message})
                        else:
                            if gr_or_pvp == 'pvp':
                                chat[f'{name_sender}'].append(
                                    {"time": time_str, "type": "link", "data": message})
                            else:
                                if lack:
                                    chat_lack.append(
                                        {"time": time_str, "type": "link", "data": message})
                                    lack = False
                                elif not lack and len(chat_lack) > 0:
                                    if raw not in chat_lack_raw:
                                        chat[f'{name_sender}'].append(
                                            {"time": time_str, "type": "link", "data": message})
                                        chat[f'{name_sender}'] = chat_lack + \
                                            chat[f'{name_sender}']
                                        chat_lack = []
                                        chat_lack_raw = []
                                else:
                                    if raw not in chat_lack_raw:
                                        chat[f'{name_sender}'].append(
                                            {"time": time_str, "type": "link", "data": message})
                    else:
                        if check_you_or_fr == 1:
                            chat['you'].append(
                                {"time": time_str, "type": "text", "data": message})
                            if len(items) == 1:
                                data_chat_box.append(chat)

                        else:
                            if gr_or_pvp == 'pvp':
                                chat[f'{name_sender}'].append(
                                    {"time": time_str, "type": "text", "data": message})
                                if len(items) == 1:
                                    data_chat_box.append(chat)
                            else:
                                if lack:
                                    chat_lack.append(
                                        {"time": time_str, "type": "text", "data": message})
                                    lack = False
                                elif not lack and len(chat_lack) > 0:
                                    if raw not in chat_lack_raw:
                                        chat[f'{name_sender}'].append(
                                            {"time": time_str, "type": "text", "data": message})
                                        chat[f'{name_sender}'] = chat_lack + \
                                            chat[f'{name_sender}']
                                        chat_lack = []
                                        chat_lack_raw = []
                                else:
                                    if raw not in chat_lack_raw:
                                        chat[f'{name_sender}'].append(
                                            {"time": time_str, "type": "text", "data": message})

            if not check:
                data_chat_box.append(chat)
                break
            current_last = items[0].text if items else None
            if current_last is None:
                break
            if current_last == previous_last:
                lines = [l for l in current_last.split("\n") if l.strip()]
                if len(lines) > 1 or not any(it in current_last for it in nope):
                    same_count += 1
                    if same_count >= 2:
                        # Thêm code lấy những phần tử còn thiếu
                        break
            else:
                same_count = 0
                previous_last = current_last

            # Swipe để load tin cũ
            w, h = d.window_size()
            # Cuộn lên (kéo từ trên xuống dưới)
            d.swipe(w // 2, int(h * 0.2), w // 2, int(h * 0.8), duration=0.8)
            eventlet.sleep(scroll_delay)
        except Exception as e:
            print("Đã gặp lỗi trong quá trình lấy lịch sử tin nhắn ", e)
    
    try:

        if check:
            ck_a = False
            for k in chat.keys():
                try:
                    if chat[k]:
                        ck_a = True
                except Exception as e:
                    print(e)

            if ck_a:
                data_chat_box.append(chat)
    except Exception as e:
        print("Lỗi khi thêm chat cuối cùng vào data_chat_box", e)

    rever_data_chat_box = data_chat_box[::-1]
    try:
        for i in range(len(rever_data_chat_box)):
            for key in rever_data_chat_box[i].keys():
                rever_data_chat_box[i][key] = rever_data_chat_box[i][key][::-1]
    except Exception as e:
        print("Lỗi khi đảo ngược thứ tự tin nhắn trong hộp chat", e)
    return rever_data_chat_box

def get_data_chat_boxes_1vs1_u2(d: u2.Device, time_and_mes, max_scroll: int = 5, scroll_delay: float = 1.0):
    """
    Trả về dict chứa hộp chat hiện có trong màn hình chat của Zalo Android.
    - div_child_selector: dict để tìm tab/conversation (ví dụ {'resourceId': '...', 'text': 'Tên bạn'})
    - device_url: đường dẫn để kết nối uiautomator2 (ví dụ '192.168.1.100:5555')
    - max_scroll: số lần swipe để load tin cũ.
    - scroll_delay: thời gian chờ giữa các lần swipe.
    """
    # d = run_start(d)
    data_chat_box = []
    chat = {}
    seen = set()
    lst_time_str = ""
    lst_message = ""
    check_first = False
    check_f_ac = False
    try:
        if d(resourceId="com.zing.zalo:id/txtTitle").exists:
            fr = d(resourceId="com.zing.zalo:id/txtTitle").get_text()
        elif d(resourceId="com.zing.zalo:id/action_bar_title").exists:
            fr = d(resourceId="com.zing.zalo:id/action_bar_title").get_text()

        name_sender = fr
        chat[f'{name_sender}'] = []

        # if True:
        # 2) Tìm tất cả message bubble
        items = d.xpath("//android.view.ViewGroup[@text!='']").all()
        num = len(items) - 1
        # Đọc từ dưới lên trên (các tin mới trước)
        for id in range(num+1):
            raw = items[num-id].text or ""

            print(f"Tin nhắn thứ {id}: {raw}")

            if "đã đồng ý kết bạn" in raw:
                check_f_ac = True

            if raw in seen:
                continue

            if "Tin nhắn đã được thu hồi" in raw:
                continue

            check_1 = True
            if "[Hình ảnh]" in raw or "[Sticker]" in raw:
                bounds = items[num-id].info['bounds']
                width, height = bounds['right'] - \
                    bounds['left'], bounds['bottom'] - bounds['top']
                # if height <= 400:
                #    continue
            formatted = date.today().strftime("%d/%m/%Y")
            raw = raw.replace("Hôm nay", formatted)
            if "[File]" in raw:
                raw = raw.split('…')[0]
                if time_and_mes['message'] != "":
                    if time_and_mes['message'] in raw or raw in time_and_mes['message']:
                        check = False
                        check_1 = False
                        break
                mes = time_and_mes['message'].split("\n")
                if len(mes) > 1:
                    if mes[1] in raw:
                        check = False
                        check_1 = False
                        break
            seen.add(raw)
            lines = [l for l in raw.split("\n") if l.strip()]

            if time_and_mes['message'] in lines:
                if "[Hình ảnh]" not in time_and_mes['message']:
                    check = False
                    check_1 = False
                    break
                else:
                    if time_and_mes['time'] in lines or time_and_mes['time'] == "":
                        check = False
                        check_1 = False
                        break
            if "Gọi điện" in raw or "Nhắn tin" in raw:
                check = False
                check_1 = False

            if not check_1:
                break
            print(raw)
            print(time_and_mes)
            # Loại bỏ dòng 'Đã nhận'
            if lines and "Đã nhận" in lines[-1]:
                lines = lines[:-1]
                # check_you_or_fr = 1
            if lines and "Đã gửi" in lines[-1]:
                lines = lines[:-1]
            if lines and "Đã xem" in lines[-1]:
                lines = lines[:-1]
                check_you_or_fr = 1

            # Xử lý file/image
            if lines and "[File]" in raw:
                # time_str = lines[-1] if len(lines) > 1 else ''
                def has_time_token(s):
                    return any(len(tok) == 5 and tok[2] == ':' for tok in s.split())
                if has_time_token(lines[0]) and has_time_token(lines[-1]):
                    time_str = lines[-1]
                    message = '\n'.join(lines[1:-1])

                elif has_time_token(lines[0]):
                    time_str = lines[0]
                    message = '\n'.join(lines[1:])

                elif has_time_token(lines[-1]):
                    time_str = lines[-1]
                    message = '\n'.join(lines[:-1])

                else:
                    time_str = ""
                    message = '\n'.join(lines)
                numm = message.split('\n')
                if len(numm) > 2:
                    message = "\n".join(numm[:2])

                formatted = date.today().strftime("%d/%m/%Y")
                time_str = time_str.replace("Hôm nay", formatted)
                typ = 'image' if any(
                    ext in message for ext in ('jpg', 'png')) else 'file'

                if typ == 'image':

                    iv = items[num-id].screenshot()
                    img = iv.convert("RGB") if hasattr(
                        iv, "convert") else Image.open(io.BytesIO(iv))

                    bounds = items[num-id].info['bounds']
                    width, height = bounds['right'] - \
                        bounds['left'], bounds['bottom'] - bounds['top']

                    left = bounds['left'] + int(width * 0.125)
                    right = bounds['left'] + int(width * 0.85)
                    # top = bounds['top']
                    top = 0
                    # bottom = bounds['bottom'] - int(height * 0.08)
                    bottom = int(height * 0.92)

                    cropped = img.crop((left, top, right, bottom))

                    cropped_small = cropped.filter(
                        ImageFilter.UnsharpMask(radius=1, percent=120, threshold=3))

                    buf = io.BytesIO()
                    cropped_small.save(buf, format="PNG")

                    # output_path = "cropped_output.jpg"
                    # cropped.convert("RGB").save(output_path, "JPEG", optimize=True, quality=75)
                    avatar_b64 = base64.b64encode(
                        buf.getvalue()).decode("ascii")

                    if not already_sent_number(MES_NUMBER_PATH):
                        mes_number = 0
                    else:
                        mes_number = read_sent_number(MES_NUMBER_PATH)
                    with open(f"{IMAGE_FILE_DATA_PATH}/mes_{mes_number}.txt", "w", encoding="utf-8") as f:
                        f.write(avatar_b64)

                    log_sent_number(mes_number+1, MES_NUMBER_PATH)

                    avatar_b64 = f"{IMAGE_FILE_DATA_PATH}/mes_{mes_number}.txt"

                    chat[f'{name_sender}'].append(
                        {"time": time_str, "type": typ, "data": message, "image_data": avatar_b64})

                else:
                    chat[f'{name_sender}'].append(
                        {"time": time_str, "type": typ, "data": message, "file_data": ""})

            else:
                # Xác định time và message
                time_str = ''
                message = ''
                if lines:
                    # kiểm tra dấu giờ phút
                    def has_time_token(s):
                        return any(len(tok) == 5 and tok[2] == ':' for tok in s.split())
                    if has_time_token(lines[0]) and has_time_token(lines[-1]):
                        time_str = lines[-1]
                        message = '\n'.join(lines[1:-1])

                    elif has_time_token(lines[0]):
                        time_str = lines[0]
                        message = '\n'.join(lines[1:])

                    elif has_time_token(lines[-1]):
                        time_str = lines[-1]
                        message = '\n'.join(lines[:-1])

                    else:
                        time_str = ""
                        message = '\n'.join(lines)

                    formatted = date.today().strftime("%d/%m/%Y")
                    time_str = time_str.replace("Hôm nay", formatted)

                if "Tin nhắn đã được thu hồi" in message:
                    pass
                elif "[Hình ảnh]" in message or "[Sticker]" in message or "[Video]" in message:

                    if "[Sticker]" in message:
                        left_rate = 0.12
                        right_rate = 0.42
                        top_rate = 0.05
                        bottom_rate = 0.95
                    else:
                        left_rate = 0.125
                        right_rate = 0.85
                        top_rate = 0
                        bottom_rate = 0.92

                    if "[Video]" not in message:
                        iv = items[num-id].screenshot()
                        img = iv.convert("RGB") if hasattr(
                            iv, "convert") else Image.open(io.BytesIO(iv))
                        bounds = items[num-id].info['bounds']
                        width, height = bounds['right'] - \
                            bounds['left'], bounds['bottom'] - bounds['top']
                        left = bounds['left'] + int(width * left_rate)
                        right = bounds['left'] + int(width * right_rate)
                    # top = bounds['top']
                        top = 0 + int(height * top_rate)
                    # bottom = bounds['bottom'] - int(height * 0.08)
                        bottom = 0 + int(height * bottom_rate)
                        cropped = img.crop((left, top, right, bottom))
                        cropped_small = cropped.filter(
                            ImageFilter.UnsharpMask(radius=1, percent=120, threshold=3))

                        buf = io.BytesIO()
                        cropped_small.save(buf, format="PNG")
                    # output_path = "cropped_output.jpg"
                    # cropped.convert("RGB").save(output_path, "JPEG", optimize=True, quality=75)
                        avatar_b64 = base64.b64encode(
                            buf.getvalue()).decode("ascii")

                        if not already_sent_number(MES_NUMBER_PATH):
                            mes_number = 0
                        else:
                            mes_number = read_sent_number(MES_NUMBER_PATH)
                        with open(f"{IMAGE_FILE_DATA_PATH}/mes_{mes_number}.txt", "w", encoding="utf-8") as f:
                            f.write(avatar_b64)

                        log_sent_number(mes_number+1, MES_NUMBER_PATH)

                        avatar_b64 = f"{IMAGE_FILE_DATA_PATH}/mes_{mes_number}.txt"

                    else:
                        avatar_b64 = ""
                        try:
                            iv = d(resourceId="com.zing.zalo:id/video_view")
                            img = iv.screenshot()
                            max_w, max_h = 200, 200
                            # nhanh, giữ tỉ lệ gốc :contentReference[oaicite:1]{index=1}
                            cropped_small = img

                            buf = io.BytesIO()
                            cropped_small.save(buf, format="PNG")
                            # base64 chỉ chứa ký tự ASCII :contentReference[oaicite:3]{index=3}
                            avatar_b64 = base64.b64encode(
                                buf.getvalue()).decode("ascii")
                        except Exception as e:
                            print("Đã có lỗi xảy ra trong quá trình lấy dữ liệu video ", e)

                        if not already_sent_number(MES_NUMBER_PATH):
                            mes_number = 0
                        else:
                            mes_number = read_sent_number(MES_NUMBER_PATH)
                        with open(f"{IMAGE_FILE_DATA_PATH}/mes_{mes_number}.txt", "w", encoding="utf-8") as f:
                            f.write(avatar_b64)

                        log_sent_number(mes_number+1, MES_NUMBER_PATH)

                        avatar_b64 = f"{IMAGE_FILE_DATA_PATH}/mes_{mes_number}.txt"

                    if "[Sticker]" in message:
                        chat[f'{name_sender}'].append(
                            {"time": time_str, "type": "sticker", "data": message, "sticker_data": avatar_b64})
                    elif "[Hình ảnh]" in message:
                        chat[f'{name_sender}'].append(
                            {"time": time_str, "type": "image", "data": message, "image_data": avatar_b64})
                    else:
                        chat[f'{name_sender}'].append(
                            {"time": time_str, "type": "video", "data": message, "video_data": avatar_b64})

                elif "Cuộc gọi thoại đi" in message:
                    pass
                elif "Cuộc gọi thoại đến" in message or "Bạn bị nhỡ Cuộc gọi thoại" in message:
                    pass
                elif "http" in message:
                    message = message.replace("html", "html\n")
                    message = message.replace("Tiktok", "\nTiktok")
                    chat[f'{name_sender}'].append(
                        {"time": time_str, "type": "link", "data": message})

                else:
                    chat[f'{name_sender}'].append(
                        {"time": time_str, "type": "text", "data": message})
            if not check_first:
                lst_time_str = time_str
                lst_message = message
                check_first = True
    except Exception as e:
        print("Đã gặp lỗi trong quá trình lấy lịch sử chat 1vs1 ", e)

    if len(chat[f'{name_sender}']) > 0:
        data_chat_box.append(chat)

    rever_data_chat_box = data_chat_box[::-1]
    for i in range(len(rever_data_chat_box)):
        for key in rever_data_chat_box[i].keys():
            rever_data_chat_box[i][key] = rever_data_chat_box[i][key][::-1]

    return rever_data_chat_box, lst_time_str, lst_message, check_f_ac


def get_data_chat_boxes_gr_u2(d: u2.Device, time_and_mes, list_mems, max_scroll: int = 5, scroll_delay: float = 1.0):
    """
    Trả về dict chứa hộp chat hiện có trong màn hình chat của Zalo Android.
    - div_child_selector: dict để tìm tab/conversation (ví dụ {'resourceId': '...', 'text': 'Tên bạn'})
    - device_url: đường dẫn để kết nối uiautomator2 (ví dụ '192.168.1.100:5555')
    - max_scroll: số lần swipe để load tin cũ.
    - scroll_delay: thời gian chờ giữa các lần swipe.
    """
    # d = run_start(d)
    print("Thành viên trong nhóm là: ", list_mems)
    name_members = [l['name_member'] for l in list_mems]
    data_chat_box = []
    chat = {}
    seen = set()
    lst_time_str = ""
    lst_message = ""
    check_first = False
    list_data_chat = []
    name_sender = ""
    # fr = d(resourceId="com.zing.zalo:id/txtTitle").get_text()
    # name_sender = fr
    # chat[f'{name_sender}'] = []

#    if True:
    # 2) Tìm tất cả message bubble
    try:
        items = d.xpath("//android.view.ViewGroup[@text!='']").all()
        num = len(items) - 1
        ck_sender = False
        # Đọc từ dưới lên trên (các tin mới trước)
        for id in range(num+1):
            raw = items[num-id].text or ""

            print("Dữ liệu đoạn chat cào được là", raw)

            if raw in seen:
                continue

            if "Tin nhắn đã được thu hồi" in raw:
                continue

            check_1 = True
            if "[Hình ảnh]" in raw or "[Sticker]" in raw:
                bounds = items[num-id].info['bounds']
                width, height = bounds['right'] - \
                    bounds['left'], bounds['bottom'] - bounds['top']
                # if height <= 400:
                #    continue
            formatted = date.today().strftime("%d/%m/%Y")
            raw = raw.replace("Hôm nay", formatted)
            if "[File]" in raw:
                raw = raw.split('…')[0]
                if time_and_mes['message'] != "":
                    if time_and_mes['message'] in raw or raw in time_and_mes['message']:
                        check = False
                        check_1 = False
                        break
                mes = time_and_mes['message'].split("\n")
                if len(mes) > 1:
                    if mes[1] in raw:
                        check = False
                        check_1 = False
                        break
            seen.add(raw)
            lines = [l for l in raw.split("\n") if l.strip()]

            if time_and_mes['message'] in lines:
                if "[Hình ảnh]" not in time_and_mes['message']:
                    check = False
                    check_1 = False
                    break
                else:
                    if time_and_mes['time'] in lines or time_and_mes['time'] == "":
                        check = False
                        check_1 = False
                        break
            if "Gọi điện" in raw or "Nhắn tin" in raw:
                check = False
                check_1 = False

            if not check_1:
                break
            print(raw)
            print(time_and_mes)
            # Loại bỏ dòng 'Đã nhận'
            if lines and "Đã nhận" in lines[-1]:
                lines = lines[:-1]
                # check_you_or_fr = 1
            if lines and "Đã gửi" in lines[-1]:
                lines = lines[:-1]
            if lines and "Đã xem" in lines[-1]:
                lines = lines[:-1]
                check_you_or_fr = 1

            # Xử lý file/image
            if lines and "[File]" in raw:
                # time_str = lines[-1] if len(lines) > 1 else ''
                def has_time_token(s):
                    return any(len(tok) == 5 and tok[2] == ':' for tok in s.split())
                if has_time_token(lines[0]) and has_time_token(lines[-1]):
                    time_str = lines[-1]
                    if lines[1] in name_members:
                        name_sender = lines[1]
                        ck_sender = True
                        message = '\n'.join(lines[2:-1])
                    else:
                        message = '\n'.join(lines[1:-1])

                elif has_time_token(lines[0]):
                    time_str = lines[0]
                    if lines[1] in name_members:
                        name_sender = lines[0]
                        ck_sender = True
                        message = '\n'.join(lines[2:])
                    else:
                        message = '\n'.join(lines[1:])

                elif has_time_token(lines[-1]):
                    time_str = lines[-1]
                    if lines[0] in name_members:
                        name_sender = lines[0]
                        ck_sender = True
                        message = '\n'.join(lines[1:-1])
                    else:
                        message = '\n'.join(lines[:-1])

                else:
                    time_str = ""
                    if lines[0] in name_members:
                        name_sender = lines[0]
                        ck_sender = True
                        message = '\n'.join(lines[1:])
                    else:
                        message = '\n'.join(lines)

                numm = message.split('\n')
                if len(numm) > 2:
                    message = "\n".join(numm[:2])

                formatted = date.today().strftime("%d/%m/%Y")
                time_str = time_str.replace("Hôm nay", formatted)
                typ = 'image' if any(
                    ext in message for ext in ('jpg', 'png')) else 'file'

                avatar_b64 = ""

                if typ == 'image':

                    iv = items[num-id].screenshot()
                    img = iv.convert("RGB") if hasattr(
                        iv, "convert") else Image.open(io.BytesIO(iv))

                    bounds = items[num-id].info['bounds']
                    width, height = bounds['right'] - \
                        bounds['left'], bounds['bottom'] - bounds['top']

                    left = bounds['left'] + int(width * 0.125)
                    right = bounds['left'] + int(width * 0.85)
                    # top = bounds['top']
                    top = 0
                    # bottom = bounds['bottom'] - int(height * 0.08)
                    bottom = int(height * 0.92)

                    cropped = img.crop((left, top, right, bottom))

                    cropped_small = cropped.filter(
                        ImageFilter.UnsharpMask(radius=1, percent=120, threshold=3))

                    buf = io.BytesIO()
                    cropped_small.save(buf, format="PNG")

                    # output_path = "cropped_output.jpg"
                    # cropped.convert("RGB").save(output_path, "JPEG", optimize=True, quality=75)
                    avatar_b64 = base64.b64encode(
                        buf.getvalue()).decode("ascii")

                    if not already_sent_number(MES_NUMBER_PATH):
                        mes_number = 0
                    else:
                        mes_number = read_sent_number(MES_NUMBER_PATH)
                    with open(f"{IMAGE_FILE_DATA_PATH}/mes_{mes_number}.txt", "w", encoding="utf-8") as f:
                        f.write(avatar_b64)

                    log_sent_number(mes_number+1, MES_NUMBER_PATH)

                    avatar_b64 = f"{IMAGE_FILE_DATA_PATH}/mes_{mes_number}.txt"

                    list_data_chat.append(
                        {"time": time_str, "type": typ, "data": message, "image_data": avatar_b64})

                    if ck_sender:
                        chat[f'{name_sender}'] = list_data_chat
                        list_data_chat = []
                        ck_sender = False

                        data_chat_box.append(chat)
                        chat = {}

                else:

                    list_data_chat.append(
                        {"time": time_str, "type": typ, "data": message, "file_data": avatar_b64})

                    if ck_sender:
                        chat[f'{name_sender}'] = list_data_chat
                        list_data_chat = []
                        ck_sender = False

                        data_chat_box.append(chat)
                        chat = {}

            else:
                # Xác định time và message
                time_str = ''
                message = ''
                if lines:
                    # kiểm tra dấu giờ phút
                    def has_time_token(s):
                        return any(len(tok) == 5 and tok[2] == ':' for tok in s.split())
                if has_time_token(lines[0]) and has_time_token(lines[-1]):
                    time_str = lines[-1]
                    if lines[1] in name_members:
                        name_sender = lines[1]
                        ck_sender = True
                        message = '\n'.join(lines[2:-1])
                    else:
                        message = '\n'.join(lines[1:-1])

                elif has_time_token(lines[0]):
                    time_str = lines[0]
                    if lines[1] in name_members:
                        name_sender = lines[0]
                        ck_sender = True
                        message = '\n'.join(lines[2:])
                    else:
                        message = '\n'.join(lines[1:])

                elif has_time_token(lines[-1]):
                    time_str = lines[-1]
                    if lines[0] in name_members:
                        name_sender = lines[0]
                        ck_sender = True
                        message = '\n'.join(lines[1:-1])
                    else:
                        message = '\n'.join(lines[:-1])

                else:
                    time_str = ""
                    if lines[0] in name_members:
                        name_sender = lines[0]
                        ck_sender = True
                        message = '\n'.join(lines[1:])
                    else:
                        message = '\n'.join(lines)

                    formatted = date.today().strftime("%d/%m/%Y")
                    time_str = time_str.replace("Hôm nay", formatted)

                if "Tin nhắn đã được thu hồi" in message:
                    pass
                elif "[Hình ảnh]" in message or "[Sticker]" in message or "[Video]" in message:

                    if "[Sticker]" in message:
                        left_rate = 0.12
                        right_rate = 0.42
                        top_rate = 0.05
                        bottom_rate = 0.95
                    else:
                        left_rate = 0.125
                        right_rate = 0.85
                        top_rate = 0
                        bottom_rate = 0.92

                    if "[Video]" not in message:
                        iv = items[num-id].screenshot()
                        img = iv.convert("RGB") if hasattr(
                            iv, "convert") else Image.open(io.BytesIO(iv))
                        bounds = items[num-id].info['bounds']
                        width, height = bounds['right'] - \
                            bounds['left'], bounds['bottom'] - bounds['top']
                        left = bounds['left'] + int(width * left_rate)
                        right = bounds['left'] + int(width * right_rate)
                    # top = bounds['top']
                        top = 0 + int(height * top_rate)
                    # bottom = bounds['bottom'] - int(height * 0.08)
                        bottom = 0 + int(height * bottom_rate)
                        cropped = img.crop((left, top, right, bottom))
                        cropped_small = cropped.filter(
                            ImageFilter.UnsharpMask(radius=1, percent=120, threshold=3))

                        buf = io.BytesIO()
                        cropped_small.save(buf, format="PNG")
                    # output_path = "cropped_output.jpg"
                    # cropped.convert("RGB").save(output_path, "JPEG", optimize=True, quality=75)
                        avatar_b64 = base64.b64encode(
                            buf.getvalue()).decode("ascii")

                        if not already_sent_number(MES_NUMBER_PATH):
                            mes_number = 0
                        else:
                            mes_number = read_sent_number(MES_NUMBER_PATH)
                        with open(f"{IMAGE_FILE_DATA_PATH}/mes_{mes_number}.txt", "w", encoding="utf-8") as f:
                            f.write(avatar_b64)

                        log_sent_number(mes_number+1, MES_NUMBER_PATH)

                        avatar_b64 = f"{IMAGE_FILE_DATA_PATH}/mes_{mes_number}.txt"

                    else:
                        avatar_b64 = ""
                        try:
                            iv = d(resourceId="com.zing.zalo:id/video_view")
                            img = iv.screenshot()
                            max_w, max_h = 200, 200
                            # nhanh, giữ tỉ lệ gốc :contentReference[oaicite:1]{index=1}
                            cropped_small = img

                            buf = io.BytesIO()
                            cropped_small.save(buf, format="PNG")
                            # base64 chỉ chứa ký tự ASCII :contentReference[oaicite:3]{index=3}
                            avatar_b64 = base64.b64encode(
                                buf.getvalue()).decode("ascii")
                        except Exception as e:
                            print("Đã có lỗi xảy ra trong quá trình lấy dữ liệu video ", e)

                        if not already_sent_number(MES_NUMBER_PATH):
                            mes_number = 0
                        else:
                            mes_number = read_sent_number(MES_NUMBER_PATH)
                        with open(f"{IMAGE_FILE_DATA_PATH}/mes_{mes_number}.txt", "w", encoding="utf-8") as f:
                            f.write(avatar_b64)

                        log_sent_number(mes_number+1, MES_NUMBER_PATH)

                        avatar_b64 = f"{IMAGE_FILE_DATA_PATH}/mes_{mes_number}.txt"

                    if "[Sticker]" in message:
                        list_data_chat.append(
                            {"time": time_str, "type": "sticker", "data": message, "sticker_data": avatar_b64})

                        if ck_sender:
                            chat[f'{name_sender}'] = list_data_chat
                            list_data_chat = []
                            ck_sender = False

                            data_chat_box.append(chat)
                            chat = {}
                    elif "[Hình ảnh]" in message:
                        list_data_chat.append(
                            {"time": time_str, "type": "image", "data": message, "image_data": avatar_b64})

                        if ck_sender:
                            chat[f'{name_sender}'] = list_data_chat
                            list_data_chat = []
                            ck_sender = False

                            data_chat_box.append(chat)
                            chat = {}
                    else:
                        list_data_chat.append(
                            {"time": time_str, "type": "video", "data": message, "video_data": avatar_b64})

                        if ck_sender:
                            chat[f'{name_sender}'] = list_data_chat
                            list_data_chat = []
                            ck_sender = False

                            data_chat_box.append(chat)
                            chat = {}

                elif "Cuộc gọi thoại đi" in message:
                    pass
                elif "Cuộc gọi thoại đến" in message or "Bạn bị nhỡ Cuộc gọi thoại" in message:
                    pass
                elif "http" in message:
                    message = message.replace("html", "html\n")
                    message = message.replace("Tiktok", "\nTiktok")

                    list_data_chat.append(
                        {"time": time_str, "type": "link", "data": message})

                    if ck_sender:
                        chat[f'{name_sender}'] = list_data_chat
                        list_data_chat = []
                        ck_sender = False

                        data_chat_box.append(chat)
                        chat = {}

                else:
                    list_data_chat.append(
                        {"time": time_str, "type": "text", "data": message})

                    if ck_sender:
                        chat[f'{name_sender}'] = list_data_chat
                        list_data_chat = []
                        ck_sender = False

                        data_chat_box.append(chat)
                        chat = {}

            if not check_first:
                lst_time_str = time_str
                lst_message = message
                check_first = True
    except Exception as e:
        print("Đã xảy ra lỗi trong quá trình lấy lịch sử chat ", e)

#    if len(chat[f'{name_sender}']) > 0:
#        data_chat_box.append(chat)
    ck_st = False
    if len(list_data_chat) > 0:
        ck_st = True
        data_chat_box = list_data_chat
        rever_data_chat_box = data_chat_box[::-1]
    else:
        rever_data_chat_box = data_chat_box[::-1]
        for i in range(len(rever_data_chat_box)):
            for key in rever_data_chat_box[i].keys():
                rever_data_chat_box[i][key] = rever_data_chat_box[i][key][::-1]

    return rever_data_chat_box, lst_time_str, lst_message, ck_st


'''
def get_list_emoji_sticker_u2(d: u2.Device, id_device="", max_emoji_sticker: int = 200, scroll_delay: float = 1.0, retire=3, has_update=False, friend_name=[], num_phone_zalo="", list_friend_old=[]):
    """
    Lấy toàn bộ bạn bè từ tab Danh bạ trên Zalo Android bằng uiautomator2.
    - d: uiautomator2 Device
    - max_friends: giới hạn tối đa số bạn bè thu thập
    """
    emoji_sticker = []
    seen = set()
    previous_last = ""
    same_count = 0

    try:
        d.xpath('//*[@resource-id="com.zing.zalo:id/chat_input_bar_container"]/android.widget.FrameLayout[1]/android.widget.LinearLayout[1]').click()
        eventlet.sleep(1.0)
        d(resourceId="com.zing.zalo:id/blc_input_emoji_tab").click()
    except Exception as e:
        print(e)

    try:
        # 2) Lặp scroll & thu thập
        while len(emoji_sticker) < max_emoji_sticker:
            # 2.1 lấy tất cả item bạn bè đang hiển thị
            # giả sử d là đối tượng uiautomator2 device

            # tìm tất cả LinearLayout con của emoticon_selector_grid
            elems = d.xpath(
                '//*[@resource-id="com.zing.zalo:id/emoticon_selector_grid"]/android.widget.LinearLayout')
            for ll in elems:
                # trong mỗi LinearLayout, tìm các LinearLayout bên trong
                sub_lls = ll.xpath('./android.widget.LinearLayout')
                for sub in sub_lls:
                    # trong mỗi sub-LinearLayout, tìm ImageView đầu tiên
                    img = sub.xpath('./android.widget.ImageView[1]')
                    if img.exists:
                        img.click()

            if len(friends) >= max_friends:
                break

            # Kiểm tra có còn dữ liệu mới không
            if items:
                last_raw = items[-1].text or ""
                if last_raw == previous_last:
                    same_count += 1
                    if same_count >= 2:
                        break
                else:
                    same_count = 0
                    previous_last = last_raw

            # 2.2 scroll lên để load thêm
            w, h = d.window_size()
            d.swipe(w//2, int(h*0.8), w//2, int(h*0.2), duration=0.8)
            eventlet.sleep(scroll_delay)
    except Exception as e:
        print(e)
    return friends
'''


def get_list_members_group_u2(d: u2.Device, max_scroll: int = 100, scroll_delay: float = 1.0):
    # 1) Click vào cuộc nhóm

    # 2) Lấy tên nhóm từ thanh title
    # name = d(resourceId="com.zing.zalo:id/txtTitle").get_text() or ""

    # 3) Mở navigation drawer và click "Xem thành viên"
    d(resourceId="com.zing.zalo:id/menu_drawer").click()
    eventlet.sleep(1.0)
    try:
        d.xpath(
            "//android.widget.FrameLayout[contains(@text, 'Xem thành viên')]").click()
    except Exception as e:
        d.swipe_ext(u2.Direction.FORWARD, scale=0.3)
        eventlet.sleep(1.0)
        d.xpath(
            "//android.widget.FrameLayout[contains(@text, 'Xem thành viên')]").click()
    eventlet.sleep(1.0)

    # 4) Chuẩn bị lưu kết quả
    list_mems = []
    previous_last = ""
    same_count = 0
    scrolls = 0
    check_mems = True
    seen = set()

    # 5) Lặp scroll lấy member
    while scrolls < max_scroll:
        scrolls += 1

        # Lấy tất cả các item trong RecyclerView
        items = d.xpath(
            "//*[@resource-id='com.zing.zalo:id/contact_list']"
            "/android.widget.FrameLayout"
        ).all()

        # Xử lý từng item
        for it in items:
            raw = it.text or ""
            if "Gợi ý thêm thành viên" in raw or "Thành viên" in raw:
                continue
            parts = raw.split("\n")
            name_member = parts[0]
            if name_member in seen:
                continue
            seen.add(name_member)

            role = ""
            if len(parts) > 1:
                if "Trưởng nhóm" in raw:
                    role = "Trưởng nhóm"
                    # check_mems = False
                elif "Trưởng cộng đồng" in raw:
                    role = "Trưởng cộng đồng"
                    # check_mems = False
                elif "Phó cộng đồng" in raw:
                    role = "Phó cộng đồng"
                    # check_mems = False
            list_mems.append({"name_member": name_member, "role": role})

        # Kiểm tra xem có còn mới không
        last_raw = items[-1].text if items else ""
        if last_raw == previous_last:
            same_count += 1
            if same_count >= 2:
                break
        else:
            same_count = 0
            previous_last = last_raw

        # Swipe lên để load thêm
        w, h = d.window_size()
        start_x, start_y = w // 2, int(h * 0.8)
        end_x, end_y = w // 2, int(h * 0.2)
        d.swipe(start_x, start_y, end_x, end_y, duration=0.8)
        eventlet.sleep(scroll_delay)

    return list_mems, check_mems


def api_get_list_friend(data_body, has_first_update):
    # data_body = request.form
    new_id = data_body['num_phone_zalo']
    # global now_phone_zalo
    # global device_connect
    # now_phone_zalo = new_id
    list_socket_call.append("get_list_friend")
    num_phone_zalo = new_id

    docs = get_base_id_zalo_json("D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "num_phone_zalo", f"Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}", {
                                 "num_phone_zalo": num_phone_zalo})
    if len(docs) > 0:
        document = docs[0]
    else:
        print("Có lỗi trong quá trình lấy thông tin tài khoản zalo")
# docs giờ là một list chứa mọi document tìm được
    id_device = document['id_device']
    user_name = document['name']
    list_friend_old = document['list_friend']
    friend_name = [l['name'] for l in list_friend_old]
    try:
        d = u2.connect(id_device)
    except Exception as e:
        print("Thiết bị đã ngắt kết nối", id_device)
        device_connect[id_device] = False
        return [], True

    if (num_phone_zalo in dict_status_zalo.keys()):

        if dict_status_zalo[num_phone_zalo] != '' or dict_status_update_pvp[num_phone_zalo] != 0:
            print("bận rồi !!!", dict_status_zalo[num_phone_zalo])
            # emit("busy",{"status":dict_status_zalo[num_phone_zalo]},room=room)
            return [], True
        else:
            docs = get_base_id_zalo_json(
                "D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "id_device", f"Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}", {"id_device": id_device})
            check_busy = False
            for doc in docs:
                if doc['num_phone_zalo'] != "":
                    if doc['num_phone_zalo'] not in dict_status_zalo.keys():
                        dict_status_zalo[doc['num_phone_zalo']] = ""
                    if dict_status_zalo[doc['num_phone_zalo']] != "":
                        check_busy = True
                        break
            if check_busy:
                print("bận rồi !!!", dict_status_zalo[num_phone_zalo])
                # emit("busy",{"status":dict_status_zalo[num_phone_zalo]},room=room)
                return [], True
            else:
                dict_status_zalo[num_phone_zalo] = "get_list_friend"
                update_again = False
                result = []
                try:
                    result = get_list_friends_u2(
                        d, max_friends=1500, has_update=has_first_update, friend_name=friend_name, num_phone_zalo=num_phone_zalo, list_friend_old=list_friend_old)
                    print("Kết quả trả về là ", result)

                except Exception as e:
                    print("Lỗi xaỷ ra khi cào bb", e)
                    result = list_friend_old
                    update_again = True
                    
                dict_status_zalo[num_phone_zalo] = ""
                if len(result) > 0:
                    result = [box['name'] for box in result]
                return result, update_again


def api_get_list_group(data_body, list_mems, check_mems):
    # data_body = request.form
    new_id = data_body['num_phone_zalo']
    list_socket_call.append("get_list_group")
    # global now_phone_zalo
    # global device_connect
    num_phone_zalo = new_id
    # now_phone_zalo = num_phone_zalo

    docs = get_base_id_zalo_json("D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "num_phone_zalo", f"Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}", {
                                 "num_phone_zalo": num_phone_zalo})
    if len(docs) > 0:
        document = docs[0]
    else:
        print(num_phone_zalo)
# docs giờ là một list chứa mọi document tìm được
    id_device = document['id_device']
    user_name = document['name']
    try:
        d = u2.connect(id_device)
    except Exception as e:
        print("Thiết bị đã ngắt kết nối")
        device_connect[id_device] = False
        return [], True
    if (num_phone_zalo in dict_status_zalo.keys()):
        if dict_status_zalo[num_phone_zalo] != '' or dict_status_update_pvp[num_phone_zalo] != 0:
            print("bận rồi !!!", dict_status_zalo[num_phone_zalo])
            return [], True
            # emit("busy",{"status":dict_status_zalo[num_phone_zalo]},room=room)
        else:
            docs = get_base_id_zalo_json(
                "D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "id_device", f"Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}", {"id_device": id_device})
            check_busy = False
            for doc in docs:
                if doc['num_phone_zalo'] != "":
                    if doc['num_phone_zalo'] not in dict_status_zalo.keys():
                        dict_status_zalo[doc['num_phone_zalo']] = ""
                    if dict_status_zalo[doc['num_phone_zalo']] != "":
                        check_busy = True
                        break
            if check_busy:
                print("bận rồi !!!", dict_status_zalo[num_phone_zalo])
                return [], True
                # emit("busy",{"status":dict_status_zalo[num_phone_zalo]},room=room)
            else:

                dict_status_zalo[num_phone_zalo] = "get_list_friend"
                result = []
                update_again = False
                try:
                    result = get_list_groups_u2(
                        d, list_mems=list_mems, check_mems=check_mems, max_groups=300)
                    data_update = {"list_group": result,
                                   "num_phone_zalo": num_phone_zalo}
                    update_base_document_json(
                        "D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "num_phone_zalo", f"Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}", data_update)
                except Exception as e:
                    update_again = True
                dict_status_zalo[num_phone_zalo] = ""
                if len(result) > 0:
                    result = [box['name'] for box in result]
                return result, update_again


def api_get_list_invite_friend(data_body):
    # data_body = request.form
    new_id = data_body['num_phone_zalo']
    # global now_phone_zalo
    # global device_connect
    list_socket_call.append("get_list_invite_friend")
    num_phone_zalo = new_id
    # now_phone_zalo = num_phone_zalo

    docs = get_base_id_zalo_json("D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "num_phone_zalo", f"Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}", {
                                 "num_phone_zalo": num_phone_zalo})
    if len(docs) > 0:
        document = docs[0]
    else:
        print(num_phone_zalo)
# docs giờ là một list chứa mọi document tìm được
    id_device = document['id_device']

    try:
        d = u2.connect(id_device)
    except Exception as e:
        print("Thiết bị đã ngắt kết nối")
        device_connect[id_device] = False
        return [], True
    if (num_phone_zalo in dict_status_zalo.keys()):
        if dict_status_zalo[num_phone_zalo] != '' or dict_status_update_pvp[num_phone_zalo] != 0:
            print("bận rồi !!!", dict_status_zalo[num_phone_zalo])
            return [], True
            # emit("busy",{"status":dict_status_zalo[num_phone_zalo]},room=room)
        else:
            docs = get_base_id_zalo_json(
                "D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "id_device", f"Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}", {"id_device": id_device})
            check_busy = False
            for doc in docs:
                if doc['num_phone_zalo'] != "":
                    if doc['num_phone_zalo'] not in dict_status_zalo.keys():
                        dict_status_zalo[doc['num_phone_zalo']] = ""
                    if dict_status_zalo[doc['num_phone_zalo']] != "":
                        check_busy = True
                        break
            if check_busy:
                print("bận rồi !!!", dict_status_zalo[num_phone_zalo])
                return [], True
                # emit("busy",{"status":dict_status_zalo[num_phone_zalo]},room=room)
            else:

                dict_status_zalo[num_phone_zalo] = "get_list_friend"
                update_again = False
                result = []
                try:
                    result = get_list_invite_friends_u2(d)
                    data_update = {"list_invite_friend": result,
                                   "num_phone_zalo": num_phone_zalo}
                    update_base_document_json(
                        "D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "num_phone_zalo", f"Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}", data_update)
                except Exception as e:
                    update_again = True
                dict_status_zalo[num_phone_zalo] = ""
                # if len(result) > 0:
                # result = [box['name'] for box in result]
                return result, update_again


def api_update_list_prior_chat_boxes(data_body, tag_name={}, data_chat_boxes={}, friend_or_nots={}, max_chat_boxes=2000, scroll_or_not=True):
    # data_body = request.form
    new_id = data_body['num_phone_zalo']
    list_socket_call.append("get_list_prior_chat_boxes")
    num_phone_zalo = new_id

    docs = get_base_id_zalo_json("D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "num_phone_zalo", f"Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}", {
                                 "num_phone_zalo": num_phone_zalo})
    if len(docs) > 0:
        document = docs[0]
    else:
        print(num_phone_zalo)
# docs giờ là một list chứa mọi document tìm được
    id_device = document['id_device']
    user_name = document['name']
    try:
        d = u2.connect(id_device)
    except Exception as e:
        print("Thiết bị đã ngắt kết nối", id_device)
        device_connect[id_device] = False
        return [], True
    if (num_phone_zalo in dict_status_zalo.keys()):
        if dict_status_zalo[num_phone_zalo] != '' or dict_status_update_pvp[num_phone_zalo] != 0:
            print("bận rồi !!!", dict_status_zalo[num_phone_zalo])
            # emit("busy",{"status":dict_status_zalo[num_phone_zalo]},room=room)
            return [], True
        else:
            docs = get_base_id_zalo_json(
                "D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "id_device", f"Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}", {"id_device": id_device})
            check_busy = False
            for doc in docs:
                if doc['num_phone_zalo'] != "":
                    if doc['num_phone_zalo'] not in dict_status_zalo.keys():
                        dict_status_zalo[doc['num_phone_zalo']] = ""
                    if dict_status_zalo[doc['num_phone_zalo']] != "":
                        check_busy = True
                        break
            if check_busy:
                print("bận rồi !!!", dict_status_zalo[num_phone_zalo])
                # emit("busy",{"status":dict_status_zalo[num_phone_zalo]},room=room)
                return [], True
            else:

                dict_status_zalo[num_phone_zalo] = "update_list_prior_chat_boxes"
                update_again = False
                result = []
                try:
                    result = get_list_prior_chat_boxes_u2(
                        d, tag_name=tag_name, data_chat_boxes=data_chat_boxes, friend_or_nots=friend_or_nots, max_chat_boxes=max_chat_boxes, scroll_or_not=scroll_or_not)
                    data_update = {"list_prior_chat_boxes": result,
                                   "num_phone_zalo": num_phone_zalo}
                    update_base_document_json(
                        "D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "num_phone_zalo", f"Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}", data_update)
                except Exception as e:
                    update_again = True
                dict_status_zalo[num_phone_zalo] = ""

                return result, update_again


def api_update_list_unseen_chat_boxes(data):
    # data_body = request.form
    new_id = data['num_phone_zalo']
    list_socket_call.append("update_list_unseen_chat_boxes")
    num_phone_zalo = new_id

    docs = get_base_id_zalo_json("D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "num_phone_zalo", f"Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}", {
                                 "num_phone_zalo": num_phone_zalo})
    if len(docs) > 0:
        document = docs[0]
    else:
        print(num_phone_zalo)
# docs giờ là một list chứa mọi document tìm được
    id_device = document['id_device']
    user_name = document['name']
    try:
        d = u2.connect(id_device)
        device_connect[id_device] = True
    except Exception as e:
        print("Thiết bị đã ngắt kết nối", id_device)
        device_connect[id_device] = False
        return [], True
    if (num_phone_zalo in dict_status_zalo.keys()):
        if dict_status_zalo[num_phone_zalo] != '' or dict_status_update_pvp[num_phone_zalo] != 0:
            print("bận rồi !!!", dict_status_zalo[num_phone_zalo])
            return [], True
            # emit("busy",{"status":dict_status_zalo[num_phone_zalo]},room=room)
        else:
            docs = get_base_id_zalo_json(
                "D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "id_device", f"Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}", {"id_device": id_device})
            check_busy = False
            for doc in docs:
                if doc['num_phone_zalo'] != "":
                    if doc['num_phone_zalo'] not in dict_status_zalo.keys():
                        dict_status_zalo[doc['num_phone_zalo']] = ""
                    if dict_status_zalo[doc['num_phone_zalo']] != "":
                        check_busy = True
                        break
            if check_busy:
                print("bận rồi !!!", dict_status_zalo[num_phone_zalo])
                # emit("busy",{"status":dict_status_zalo[num_phone_zalo]},room=room)
                return [], True
            else:
                dict_status_zalo[num_phone_zalo] = "update_list_unseen_chat_boxes"
                result = []
                update_again = False
                try:
                    result = get_list_unseen_chat_boxes_u2(
                        d, max_chat_boxes=50)
                    unseen_boxes = [box['name'] for box in result]
                    data_update = {"list_unseen_chat_boxes": result,
                                   "num_phone_zalo": num_phone_zalo}
                    update_base_document_json(
                        "D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "num_phone_zalo", f"Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}", data_update)
                    try:
                        #        print(document)
                        with open(f'D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}.json', 'r', encoding='utf-8') as f:
                            data = json.load(f)
                        for id in range(len(data)):
                            #            print(document[domain])
                            if data[id]['num_phone_zalo'] == num_phone_zalo:
                                for it in range(len(data[id]['list_prior_chat_boxes'])):
                                    if data[id]['list_prior_chat_boxes'][it]['name'] in unseen_boxes:
                                        data[id]['list_prior_chat_boxes'][it]['status'] = "unseen"

                        with open(f'D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}.json', 'w', encoding='utf-8') as f:
                            json.dump(
                                data, f, ensure_ascii=False, indent=4)

                    except Exception as e:
                        print(e)
                        return [], True

                except Exception as e:
                    result = []
                    update_again = True
                dict_status_zalo[num_phone_zalo] = ""
                if len(result) > 0:
                    result = [box['name'] for box in result]
                return result, update_again


def api_update_data_one_chat_box(data, gr_or_pvp="pvp", on_chat=False, update=False):
    # data_body = request.form
    new_id = data['num_phone_zalo']
    # num_phone_ntd = data_body.get('num_phone_ntd')
    name_ntd = data['name_ntd']
    list_socket_call.append("get_data_one_box_chat")
    num_phone_zalo = new_id
    num_phone_ntd = None
    # global device_connect

    docs = get_base_id_zalo_json("D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "num_phone_zalo", f"Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}", {
                                 "num_phone_zalo": num_phone_zalo})
    if len(docs) > 0:
        document = docs[0]
    else:
        print("không có đâu")

# docs giờ là một list chứa mọi document tìm được
    id_device = document['id_device']

    try:
        d = u2.connect(id_device)
        # global device_connect
        device_connect[id_device] = True
    except Exception as e:
        print("Thiết bị đã ngắt kết nối", id_device)
        device_connect[id_device] = False
        return False
    if (num_phone_zalo in dict_status_zalo.keys()):
        if dict_status_zalo[num_phone_zalo] != '' or dict_status_update_pvp[num_phone_zalo] != 0:
            print("bận rồi !!!", dict_status_zalo[num_phone_zalo])
            # emit("busy",{"status":dict_status_zalo[num_phone_zalo]},room=room)
            return False
        else:
            docs = get_base_id_zalo_json(
                "D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "id_device", f"Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}", {"id_device": id_device})
            check_busy = False
            for doc in docs:
                if doc['num_phone_zalo'] != "":
                    if doc['num_phone_zalo'] not in dict_status_zalo.keys():
                        dict_status_zalo[doc['num_phone_zalo']] = ""
                    if dict_status_zalo[doc['num_phone_zalo']] != "":
                        check_busy = True
                        break
            if check_busy:
                print("bận rồi !!!", dict_status_zalo[num_phone_zalo])
                # emit("busy",{"status":dict_status_zalo[num_phone_zalo]},room=room)
                return False
            else:
                dict_status_zalo[num_phone_zalo] = "get_data_one_box_chat"
                if 'list_prior_chat_boxes' not in document.keys():
                    document['list_prior_chat_boxes'] = []
                list_prior_chat_boxes = document['list_prior_chat_boxes']
                check_pvp_or_gr = ""
                result = []
                try:
                    if not on_chat:
                        d.app_start("com.zing.zalo", stop=True)
                        eventlet.sleep(1.0)
                        if num_phone_ntd != None:
                            data_ntd = num_phone_ntd
                            d(text="Tìm kiếm").click()
                            eventlet.sleep(1.0)
                            d.send_keys(data_ntd, clear=True)
                            eventlet.sleep(1.0)
                            d(resourceId="com.zing.zalo:id/btn_search_result").click()
                            eventlet.sleep(0.5)
                            try:
                                btn = d(
                                    resourceId="com.zing.zalo:id/btn_send_message")
                                if btn.exists:
                                    btn.click()
                                    eventlet.sleep(0.5)
                            except Exception:
                                pass
                            d.xpath('//*[@text="Đóng"]')
                            eventlet.sleep(1.0)
                            check_pvp_or_gr = "pvp"
                            if d(textContains="Đóng").exists:
                                d(textContains="Đóng").click()
                                eventlet.sleep(0.5)
                            if d(resourceId="com.zing.zalo:id/txtTitle").exists:
                                data_ntd = d(
                                    resourceId="com.zing.zalo:id/txtTitle").get_text()
                            else:
                                data_ntd = d(
                                    resourceId="com.zing.zalo:id/action_bar_title").get_text()
                                

                        else:
                            data_ntd = name_ntd
                            d(text="Tìm kiếm").click()
                            eventlet.sleep(1.0)
                            d.send_keys(data_ntd, clear=True)
                            eventlet.sleep(1.0)
                            '''
                            elements = d(
                                resourceId="com.zing.zalo:id/btn_search_result")
                            if elements:
                                elements[0].click()
                            eventlet.sleep(1.0)
                            '''
                            try:
                                chat_list = d.xpath(
                                    '//*[@resource-id="com.zing.zalo:id/btn_search_result"]').all()

                                # if len(chat_num) > 0:
                                for item in chat_list:
                                    raw = item.text
                                    print("Raw là", raw)
                                    lines = [l for l in raw.split(
                                        "\n") if l.strip()]
                                    print(lines)
                                    if lines[0] == data_ntd:
                                        print("Có trùng khớp")
                                        item.click()
                                        break

                                eventlet.sleep(1.0)
                            except Exception as e:
                                print('Ellipsis')
                                pass
                            '''
                     for friend in list_friend:
                       if data_ntd == friend['name']:
                           check_pvp_or_gr = "pvp"
                           break
                     for 
                     if check_pvp_or_gr  == "":
                       return  jsonify({"status" : "Không tìm thấy tên NTD hoặc nhóm phù hợp"})
                     '''
                    data_ntd = name_ntd
                    check = False
                    pick = -1
                    for id in range(len(list_prior_chat_boxes)):
                        if list_prior_chat_boxes[id]['name'] == data_ntd:
                            pick = id
                            check = True
                    if not check:
                        list_prior_chat_boxes.append(
                            {"name": data_ntd, "time": "", "message": "", "status": "seen", "tag": "", "data_chat_box": ""})
                        time_and_mes = {'time': "",  "message": ""}
                        data_chat_box = []
                    else:
                        if 'data_chat_box' not in list_prior_chat_boxes[pick].keys() or list_prior_chat_boxes[pick]['data_chat_box'] == "":
                            list_prior_chat_boxes[pick]['data_chat_box'] = ""
                            data_chat_box = []
                            time_and_mes = {'time': "", "message": ""}
                        else:
                            try:
                                data_chat_box = load_data_chat_box_json(
                                    list_prior_chat_boxes[pick]['data_chat_box'])
                                last_data = data_chat_box[-1]
                                last_data_mes = {}
                                for it in last_data.keys():
                                    last_data_mes = last_data[it][-1]
                                time_and_mes = {
                                    "time": last_data_mes['time'], "message": last_data_mes['data']}
                            except Exception as e:
                                data_chat_box = []
                                print("Không lấy được lịch sử chat ", e)
                                time_and_mes = {'time': "", "message": ""}

                    raw_result = get_data_chat_boxes_u2(
                        d, gr_or_pvp, time_and_mes)
                    result = [r for r in raw_result if r]

                    friend_or_not = "no"
                    if gr_or_pvp == 'pvp':
                        if d(text="Đã gửi lời mời kết bạn").exists:
                            friend_or_not = "added"
                        else:
                            btn = d(
                                resourceId="com.zing.zalo:id/tv_function_privacy")
                            kb = d.xpath('//*[@text="Kết bạn"]')
                            if btn.exists or kb.exists:
                                print("chưa kết bạn")
                                friend_or_not = "no"
                            else:
                                friend_or_not = "yes"

                    if len(result) > 0:
                        # if not update:
                        data_chat_box = data_chat_box + result
                        list_prior_chat_boxes[pick]['data_chat_box'] = dump_data_chat_box_json(
                            list_prior_chat_boxes[pick]['data_chat_box'], data_chat_box)

                        if gr_or_pvp == 'pvp':
                            list_prior_chat_boxes[pick]['friend_or_not'] = friend_or_not

                        data_update = {"num_phone_zalo": num_phone_zalo,
                                       "list_prior_chat_boxes": list_prior_chat_boxes}
                        update_base_document_json(
                            "D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "num_phone_zalo", f"Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}", data_update)

                except Exception as e:
                    print(e)
                dict_status_zalo[num_phone_zalo] = ""
                return result


def api_update_data_1vs1_chat_box(d: u2.Device, data, document):

    # data_body = request.form
    new_id = data['num_phone_zalo']
    # num_phone_ntd = data_body.get('num_phone_ntd')
    name_ntd = data['name_ntd']
    num_phone_zalo = new_id
    id_device = document['id_device']
    if (num_phone_zalo in dict_status_zalo.keys()):
        if dict_status_zalo[num_phone_zalo] != '':
            print("bận rồi !!!", dict_status_zalo[num_phone_zalo])
            # emit("busy",{"status":dict_status_zalo[num_phone_zalo]},room=room)
            return False
        else:
            docs = get_base_id_zalo_json(
                "D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "id_device", f"Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}", {"id_device": id_device})
            check_busy = False
            for doc in docs:
                if doc['num_phone_zalo'] != "":
                    if doc['num_phone_zalo'] not in dict_status_zalo.keys():
                        dict_status_zalo[doc['num_phone_zalo']] = ""
                    if dict_status_zalo[doc['num_phone_zalo']] != "":
                        check_busy = True
                        break
            if check_busy:
                print("bận rồi !!!", dict_status_zalo[num_phone_zalo])
                # emit("busy",{"status":dict_status_zalo[num_phone_zalo]},room=room)
                return False
            else:

                # dict_status_zalo[num_phone_zalo] = "get_data_one_box_chat"
                if 'list_prior_chat_boxes' not in document.keys():
                    document['list_prior_chat_boxes'] = []
                list_prior_chat_boxes = document['list_prior_chat_boxes']
                list_friend = document['list_friend']
                data_ntd = name_ntd
                check = False
                pick = -1
                for id in range(len(list_prior_chat_boxes)):
                    if list_prior_chat_boxes[id]['name'] == data_ntd:
                        pick = id
                        check = True
                if not check:
                    list_prior_chat_boxes.append(
                        {"name": data_ntd, "time": "", "message": "", "status": "seen", "tag": "", "data_chat_box": ""})
                    time_and_mes = {'time': "",  "message": ""}
                    data_chat_box = []
                else:
                    if 'data_chat_box' not in list_prior_chat_boxes[pick].keys() or list_prior_chat_boxes[pick]['data_chat_box'] == "":
                        list_prior_chat_boxes[pick]['data_chat_box'] = ""
                        time_and_mes = {'time': "", "message": ""}
                        data_chat_box = []

                    else:
                        try:
                            data_chat_box = load_data_chat_box_json(
                                list_prior_chat_boxes[pick]['data_chat_box'])
                            last_data = data_chat_box[-1]
                            last_data_mes = {}
                            for it in last_data.keys():
                                last_data_mes = last_data[it][-1]
                            time_and_mes = {
                                "time": last_data_mes['time'], "message": last_data_mes['data']}
                        except Exception as e:
                            data_chat_box = []
                            print("Không lấy được lịch sử chat ", e)
                            time_and_mes = {'time': "", "message": ""}
                raw_result, lst_time_str, lst_message, check_f_ac = get_data_chat_boxes_1vs1_u2(
                    d, time_and_mes)
                result = [r for r in raw_result if r]
                # retr = result

                if len(result) > 0:
                    data_chat_box = data_chat_box + result
                    list_prior_chat_boxes[pick]['data_chat_box'] = dump_data_chat_box_json(
                        list_prior_chat_boxes[pick]['data_chat_box'], data_chat_box)
                    list_prior_chat_boxes[pick]['time'] = lst_time_str
                    list_prior_chat_boxes[pick]['message'] = lst_message
                    list_prior_chat_boxes[pick]['status'] = "unseen"
                    list_prior_chat_boxes.insert(
                        0, list_prior_chat_boxes.pop(pick))
                    if check_f_ac:
                        ava = ""
                        if 'ava' in list_prior_chat_boxes[pick].keys():
                            ava = list_prior_chat_boxes[pick]['ava']
                        list_friend_name = [l['name'] for l in list_friend]
                        if data_ntd not in list_friend_name:
                            list_friend.append({"name": data_ntd, "ava": ava})
                            data_update = {"num_phone_zalo": num_phone_zalo,
                                           "list_prior_chat_boxes": list_prior_chat_boxes, "list_friend": list_friend}
                        else:
                            data_update = {"num_phone_zalo": num_phone_zalo,
                                           "list_prior_chat_boxes": list_prior_chat_boxes}
                    else:
                        data_update = {"num_phone_zalo": num_phone_zalo,
                                       "list_prior_chat_boxes": list_prior_chat_boxes}
                    update_base_document_json(
                        "D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "num_phone_zalo", f"Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}", data_update)

                return result


def api_update_data_gr_chat_box(d: u2.Device, data, document):
    # data_body = request.form
    new_id = data['num_phone_zalo']
    # num_phone_ntd = data_body.get('num_phone_ntd')
    name_ntd = data['name_ntd']
    num_phone_zalo = new_id
    user_name = document['name']
    id_device = document['id_device']
    list_group = document['list_group']

    list_group_name = [g['name'] for g in list_group]
    list_mems = []
    for group in list_group:
        if group['name'] == name_ntd:
            if 'list_mems' in group.keys():
                list_mems = group['list_mems']
                break
    if (num_phone_zalo in dict_status_zalo.keys()):
        if dict_status_zalo[num_phone_zalo] != '':
            print("bận rồi !!!", dict_status_zalo[num_phone_zalo])
            # emit("busy",{"status":dict_status_zalo[num_phone_zalo]},room=room)
            return False
        else:
            docs = get_base_id_zalo_json(
                "D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "id_device", f"Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}", {"id_device": id_device})
            check_busy = False
            for doc in docs:
                if doc['num_phone_zalo'] != "":
                    if doc['num_phone_zalo'] not in dict_status_zalo.keys():
                        dict_status_zalo[doc['num_phone_zalo']] = ""
                    if dict_status_zalo[doc['num_phone_zalo']] != "":
                        check_busy = True
                        break
            if check_busy:
                print("bận rồi !!!", dict_status_zalo[num_phone_zalo])
                # emit("busy",{"status":dict_status_zalo[num_phone_zalo]},room=room)
                return False
            else:

                # dict_status_zalo[num_phone_zalo] = "get_data_one_box_chat"
                if 'list_prior_chat_boxes' not in document.keys():
                    document['list_prior_chat_boxes'] = []
                list_prior_chat_boxes = document['list_prior_chat_boxes']
                data_ntd = name_ntd
                check = False
                pick = -1
                for id in range(len(list_prior_chat_boxes)):
                    if list_prior_chat_boxes[id]['name'] == data_ntd:
                        print("Có tồn tại nhà tuyển dụng này nhé")
                        pick = id
                        check = True
                if not check:
                    list_prior_chat_boxes.append(
                        {"name": data_ntd, "time": "", "message": "", "status": "seen", "tag": "", "data_chat_box": ""})
                    time_and_mes = {'time': "",  "message": ""}
                    data_chat_box = []
                else:

                    if 'data_chat_box' not in list_prior_chat_boxes[pick].keys() or list_prior_chat_boxes[pick]['data_chat_box'] == "":
                        list_prior_chat_boxes[pick]['data_chat_box'] = ""
                        time_and_mes = {'time': "", "message": ""}
                        data_chat_box = []

                    else:
                        try:

                            data_chat_box = load_data_chat_box_json(
                                list_prior_chat_boxes[pick]['data_chat_box'])
                            last_data = data_chat_box[-1]
                            last_data_mes = {}
                            for it in last_data.keys():
                                last_data_mes = last_data[it][-1]
                            time_and_mes = {
                                "time": last_data_mes['time'], "message": last_data_mes['data']}
                        except Exception as e:
                            data_chat_box = []
                            print("Không lấy được lịch sử chat ", e)
                            time_and_mes = {'time': "", "message": ""}
                raw_result, lst_time_str, lst_message, ck_sender = get_data_chat_boxes_gr_u2(
                    d, time_and_mes, list_mems)
                result = [r for r in raw_result if r]

                if len(result) > 0:
                    if not ck_sender:
                        for res in result:
                            data_chat_box.append(
                                res)
                    else:
                        for k in data_chat_box[-1].keys():
                            for res in result:
                                data_chat_box[-1][k].append(
                                    res)

                    list_prior_chat_boxes[pick]['data_chat_box'] = dump_data_chat_box_json(
                        list_prior_chat_boxes[pick]['data_chat_box'], data_chat_box)
                    list_prior_chat_boxes[pick]['time'] = lst_time_str
                    list_prior_chat_boxes[pick]['message'] = lst_message
                    list_prior_chat_boxes[pick]['status'] = "unseen"
                    list_prior_chat_boxes.insert(
                        0, list_prior_chat_boxes.pop(pick))
                    data_update = {"num_phone_zalo": num_phone_zalo,
                                   "list_prior_chat_boxes": list_prior_chat_boxes}
                    update_base_document_json(
                        "D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "num_phone_zalo", f"Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}", data_update)

                return result


def api_update_list_mems_one_group(data, on_chat=False, update=False):
    new_id = data['num_phone_zalo']
    name_ntd = data['name_ntd']
    list_socket_call.append("get_list_mems_one_group")
    num_phone_zalo = new_id
    num_phone_ntd = None

    docs = get_base_id_zalo_json("D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "num_phone_zalo", f"Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}", {
                                 "num_phone_zalo": num_phone_zalo})
    if len(docs) > 0:
        document = docs[0]
    else:
        print("không có đâu")

# docs giờ là một list chứa mọi document tìm được
    id_device = document['id_device']

    try:
        d = u2.connect(id_device)
    except Exception as e:
        print("Thiết bị đã ngắt kết nối ", id_device)
        device_connect[id_device] = False
        return False
    if (num_phone_zalo in dict_status_zalo.keys()):
        if dict_status_zalo[num_phone_zalo] != '' or dict_status_update_pvp[num_phone_zalo] != 0:
            print("bận rồi !!!", dict_status_zalo[num_phone_zalo])
            # emit("busy",{"status":dict_status_zalo[num_phone_zalo]},room=room)
            return False
        else:
            docs = get_base_id_zalo_json(
                "D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "id_device", f"Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}", {"id_device": id_device})
            check_busy = False
            for doc in docs:
                if doc['num_phone_zalo'] != "":
                    if doc['num_phone_zalo'] not in dict_status_zalo.keys():
                        dict_status_zalo[doc['num_phone_zalo']] = ""
                    if dict_status_zalo[doc['num_phone_zalo']] != "":
                        check_busy = True
                        break
            if check_busy:
                print("bận rồi !!!", dict_status_zalo[num_phone_zalo])
                # emit("busy",{"status":dict_status_zalo[num_phone_zalo]},room=room)
                return False
            else:

                dict_status_zalo[num_phone_zalo] = "get_data_one_box_chat"
                list_friend = document['list_friend']
                list_group = document['list_group']
                # if 'list_prior_chat_boxes' not in document.keys():
                #    document['list_prior_chat_boxes'] = []
                # list_prior_chat_boxes = document['list_prior_chat_boxes']
                # check_pvp_or_gr = ""
                result = []
                try:
                    if not on_chat:
                        d.app_start("com.zing.zalo", stop=True)
                        eventlet.sleep(1.0)
                        if num_phone_ntd != None:
                            data_ntd = num_phone_ntd
                            d(text="Tìm kiếm").click()
                            eventlet.sleep(1.0)
                            d.send_keys(data_ntd, clear=True)
                            eventlet.sleep(1.0)
                            d(resourceId="com.zing.zalo:id/btn_search_result").click()
                            eventlet.sleep(1.0)
                            check_pvp_or_gr = "pvp"
                            if d(textContains="Đóng").exists:
                                d(textContains="Đóng").click()
                                eventlet.sleep(0.5)
                            if d(resourceId="com.zing.zalo:id/txtTitle").exists:
                                data_ntd = d(
                                    resourceId="com.zing.zalo:id/txtTitle").get_text()
                            else:
                                data_ntd = d(
                                    resourceId="com.zing.zalo:id/action_bar_title").get_text()

                        else:
                            data_ntd = name_ntd
                            d(text="Tìm kiếm").click()
                            eventlet.sleep(1.0)
                            d.send_keys(data_ntd, clear=True)
                            eventlet.sleep(1.0)
                            elements = d(
                                resourceId="com.zing.zalo:id/btn_search_result")
                            if elements:
                                elements[0].click()
                            eventlet.sleep(1.0)
                            try:
                                if d(textContains="Đóng").exists:
                                    d(textContains="Đóng").click()
                                    eventlet.sleep(0.5)
                                chat_num = d(
                                    resourceId="com.zing.zalo:id/btn_search_result")
                                chat_num.click()
                            except Exception as e:
                                pass

                    data_ntd = name_ntd
                    check = False
                    pick = -1
                    for id in range(len(list_group)):
                        if list_group[id]['name'] == data_ntd:
                            pick = id
                            check = True
                    if not check:
                        list_group.append(
                            {"name": data_ntd, "time": "", "message": "", "status": "seen", "list_mems": [], "check_mems": False})
                        # time_and_mes = {'time': "",  "message": ""}
                    else:
                        if 'list_mems' not in list_group[pick].keys() or list_group[pick]['list_mems'] == []:
                            list_group[pick]['list_mems'] = []
                    raw_result, check_mems = get_list_members_group_u2(d)
                    result = [r for r in raw_result if r]

                    if len(result) > 0:
                        # if not update:
                        if 'list_mems' not in list_group[pick].keys():
                            list_group[pick]['list_mems'] = []
                        list_group[pick]['list_mems'] = list_group[pick]['list_mems'] + result
                        # else:
                        #    list_group[pick]['list_mems'] = result
                        list_group[pick]['check_mems'] = check_mems
                        data_update = {"num_phone_zalo": num_phone_zalo,
                                       "list_group": list_group}
                        update_base_document_json(
                            "D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "num_phone_zalo", f"Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}", data_update)
                except Exception as e:
                    print(e)
                    result = [-1]

                dict_status_zalo[num_phone_zalo] = ""
                return result

@app.route('/api_get_list_users', methods=['POST', 'GET'])
def get_list_users_new():
    data_body = request.form
    user_id = data_body.get('user_id')
    if user_id == "22495550":
        device_and_port = []
        for id in list(id_port.keys()):

            device_and_port += id_port[id]
    else:
        device_and_port = id_port[user_id]

    user_db = []
    status = True
    seen = []
    for dp in device_and_port:
        ports = list(dp.keys())
        port = ports[0]
        if port in seen:
            continue
        seen.append(port)
        device_id = dp[port]
        try:
            with open(f'D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/Zalo_data_login_path_{device_id}.json', 'r', encoding='utf-8') as f:
                zalo_data = json.load(f)
            with open(f'D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/device_status_{device_id}.json', 'r') as f:
                device_status = json.load(f)
            if 'update' in device_status.keys():
                if not device_status['update']:
                    continue
                    # status = device_status['update']
            for zalo in zalo_data:
                if zalo['name'] == "" or zalo['name'] == "Thêm tài khoản" or zalo['num_phone_zalo'] == "" or zalo['num_phone_zalo'] not in dict_device_and_phone[device_id]:
                    continue

                if device_id in list(device_connect.keys()):
                    if device_connect[device_id]:
                        connect = True
                    else:
                        connect = False
                else:
                    connect = False

                ava = ""
                if zalo['ava'] != "":
                    if zalo['ava'][0] == 'C':
                        with open(zalo['ava'], "r", encoding="utf-8") as f:
                            ava = f.read()
                    else:
                        ava = zalo['ava']

                user_db.append({"num_phone_zalo": zalo['num_phone_zalo'], "connect": connect, "name_device": port,
                               "status": zalo['status'], "user_name": zalo['name'], "avatar": ava, "port": port})
        except Exception as e:
            print(e)
            pass
    if len(user_db) == 0:
        status = False

    return jsonify({'user_db': user_db, 'update': status})


@app.route('/api_get_list_friend', methods=['POST', 'GET'])
def get_list_friend_new():
    data_body = request.form
    new_id = data_body.get('num_phone_zalo')
    num_phone_zalo = new_id
    # global now_phone_zalo
    # now_phone_zalo = new_id
    list_socket_call.append("get_list_friend")

    docs = get_base_id_zalo_json("D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "num_phone_zalo", f"Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}", {
                                 "num_phone_zalo": num_phone_zalo})
    if len(docs) > 0:
        document = docs[0]
    else:
        print(num_phone_zalo)
# docs giờ là một list chứa mọi document tìm được
    # id_device = document['id_device']
    # now_phone_zalo[id_device] = num_phone_zalo
    user_name = document['name']
    result = document['list_friend']
    for id in range(len(result)):
        if result[id]['ava'] != "":
            if result[id]['ava'][0] == 'C':
                with open(result[id]['ava'], "r", encoding="utf-8") as f:
                    result[id]['ava'] = f.read()
    return jsonify({"num_phone_zalo": num_phone_zalo, "user_name": user_name, "list_friend": result}), 200


@app.route('/api_get_list_group', methods=['POST', 'GET'])
def get_list_group_new():
    data_body = request.form
    new_id = data_body.get('num_phone_zalo')
    list_socket_call.append("get_list_group")
    num_phone_zalo = new_id
    # global now_phone_zalo
    # now_phone_zalo = new_id

    docs = get_base_id_zalo_json("D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "num_phone_zalo", f"Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}", {
                                 "num_phone_zalo": num_phone_zalo})
    if len(docs) > 0:
        document = docs[0]
    else:
        print(num_phone_zalo)
# docs giờ là một list chứa mọi document tìm được
    id_device = document['id_device']
    user_name = document['name']
    # now_phone_zalo[id_device] = num_phone_zalo
    result = document['list_group']
    return jsonify({"num_phone_zalo": num_phone_zalo, "user_name": user_name, "list_group": result}), 200


@app.route('/api_get_list_invite_friend', methods=['POST', 'GET'])
def get_list_invite_friend_new():
    data_body = request.form
    new_id = data_body.get('num_phone_zalo')
    list_socket_call.append("get_list_group")
    num_phone_zalo = new_id
    # global now_phone_zalo
    # now_phone_zalo = new_id

    docs = get_base_id_zalo_json("D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "num_phone_zalo", f"Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}", {
                                 "num_phone_zalo": num_phone_zalo})
    if len(docs) > 0:
        document = docs[0]
    else:
        print(num_phone_zalo)
# docs giờ là một list chứa mọi document tìm được
    # now_phone_zalo[id_device] = num_phone_zalo
    user_name = document['name']
    result = document['list_invite_friend']
    for id in range(len(result)):
        if result[id]['ava'] != "":
            if result[id]['ava'][0] == 'C':
                with open(result[id]['ava'], "r", encoding="utf-8") as f:
                    result[id]['ava'] = f.read()
    return jsonify({"num_phone_zalo": num_phone_zalo, "user_name": user_name, "list_invite_friend": result}), 200


@app.route('/api_get_list_prior_chat_boxes', methods=['POST', 'GET'])
def get_list_prior_chat_boxes_new():
    data_body = request.form
    new_id = data_body.get('num_phone_zalo')
    num_phone_zalo = new_id
    # global now_phone_zalo
    # now_phone_zalo = new_id
    list_socket_call.append("get_list_prior_chat_boxes")
    # print(num_phone_zalo)

    docs = get_base_id_zalo_json("D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "num_phone_zalo", f"Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}", {
                                 "num_phone_zalo": num_phone_zalo})

    if len(docs) > 0:
        document = docs[0]
    else:
        print(num_phone_zalo)

    # docs giờ là một list chứa mọi document tìm được
    # id_device = document['id_device']
    # now_phone_zalo[id_device] = num_phone_zalo
    user_name = document['name']
    list_friend = document['list_friend']
    friend = [fr['name'] for fr in list_friend]
    result = document['list_prior_chat_boxes']
    for id in range(len(list_friend)):
        if list_friend[id]['ava'] != "":
            if list_friend[id]['ava'][0] == 'C':
                with open(list_friend[id]['ava'], "r", encoding="utf-8") as f:
                    list_friend[id]['ava'] = f.read()
    for id in range(len(result)):
        # result[id]['ava'] = ""
        for it in range(len(friend)):
            if result[id]['name'] == friend[it]:
                result[id]['ava'] = list_friend[it]['ava']
                break
    for i1 in range(len(result)):
        if 'data_chat_box' in result[i1].keys():
            result[i1]['data_chat_box'] = load_data_chat_box_json(result[i1]['data_chat_box'])

            for i2 in range(len(result[i1]['data_chat_box'])):
                for k in result[i1]['data_chat_box'][i2].keys():
                    for i3 in range(len(result[i1]['data_chat_box'][i2][k])):
                        if "sticker_data" in result[i1]['data_chat_box'][i2][k][i3].keys():
                            ava64 = result[i1]['data_chat_box'][i2][k][i3]['sticker_data']
                            if ava64 != "":
                                if ava64[0] == 'C':
                                    with open(ava64, "r", encoding="utf-8") as f:
                                        result[i1]['data_chat_box'][i2][k][i3]['sticker_data'] = f.read(
                                        )
                        if "video_data" in result[i1]['data_chat_box'][i2][k][i3].keys():
                            ava64 = result[i1]['data_chat_box'][i2][k][i3]['video_data']
                            if ava64 != "":
                                if ava64[0] == 'C':
                                    with open(ava64, "r", encoding="utf-8") as f:
                                        result[i1]['data_chat_box'][i2][k][i3]['video_data'] = f.read(
                                        )
                        if "image_data" in result[i1]['data_chat_box'][i2][k][i3].keys():
                            ava64 = result[i1]['data_chat_box'][i2][k][i3]['image_data']
                            if ava64 != "":
                                if ava64[0] == 'C':
                                    with open(ava64, "r", encoding="utf-8") as f:
                                        result[i1]['data_chat_box'][i2][k][i3]['image_data'] = f.read(
                                        )

    return jsonify({"num_phone_zalo": num_phone_zalo, "user_name": user_name, "list_prior_chat_boxes": result}), 200


@app.route('/api_get_list_unseen_chat_boxes', methods=['POST', 'GET'])
def get_list_unseen_chat_boxes_new():
    data_body = request.form
    new_id = data_body.get('num_phone_zalo')
    num_phone_zalo = new_id
    # global now_phone_zalo
    # now_phone_zalo = new_id
    list_socket_call.append("get_list_unseen_chat_boxes")
    # print(num_phone_zalo)

    docs = get_base_id_zalo_json("D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "num_phone_zalo", f"Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}", {
                                 "num_phone_zalo": num_phone_zalo})
    if len(docs) > 0:
        document = docs[0]

# docs giờ là một list chứa mọi document tìm được
    # id_device = document['id_device']
    # now_phone_zalo[id_device] = num_phone_zalo
    user_name = document['name']
    result = document['list_unseen_chat_boxes']
    return jsonify({"num_phone_zalo": num_phone_zalo, "user_name": user_name, "list_unseen_chat_boxes": result}), 200


@app.route('/api_get_data_one_chat_box', methods=['POST', 'GET'])
def get_data_one_chat_box():
    data_body = request.form
    new_id = data_body.get('num_phone_zalo')
    # global now_phone_zalo
    # now_phone_zalo = new_id
    name_ntd = data_body.get('name_ntd')
    # num_send_phone_zalo = data_body.get('num_send_phone_zalo')
    num_phone_zalo = new_id
    list_socket_call.append("get_list_prior_chat_boxes")
    result = []
    docs = get_base_id_zalo_json("D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "num_phone_zalo", f"Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}", {
                                 "num_phone_zalo": num_phone_zalo})
    if len(docs) > 0:
        document = docs[0]
        id_device = document['id_device']
        now_phone_zalo[id_device] = num_phone_zalo
    else:
        print(num_phone_zalo)
# docs giờ là một list chứa mọi document tìm được
    # id_device = document['id_device']
    # user_name = document['name']
    list_prior_chat_boxes = document['list_prior_chat_boxes']
    for id in list_prior_chat_boxes:
        if id['name'] == name_ntd:
            if 'data_chat_box' not in id.keys():
                id['data_chat_box'] = ""
            result = load_data_chat_box_json(id['data_chat_box'])
            break
        if "phone" in id.keys():
            if id['phone'] == name_ntd:
                name_ntd = id['name']
                if 'data_chat_box' not in id.keys():
                    id['data_chat_box'] = ""
                result = load_data_chat_box_json(id['data_chat_box'])
                break

    return jsonify({"num_phone_zalo": num_phone_zalo, "user_name": name_ntd, "data_chat_box": result}), 200


@socketio.on('socket_get_data_one_chat_box')
def get_data_one_chat_box(data):
    new_id = data['num_phone_zalo']
    name_ntd = data['name_ntd']
    room = data['id_chat']
    # global now_phone_zalo
    # now_phone_zalo = new_id
    # num_send_phone_zalo = data_body.get('num_send_phone_zalo')
    num_phone_zalo = new_id
    list_socket_call.append("get_list_prior_chat_boxes")
    result = []
    friend_or_not = "yes"
    docs = get_base_id_zalo_json("D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "num_phone_zalo", f"Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}", {
                                 "num_phone_zalo": num_phone_zalo})
    if len(docs) > 0:
        document = docs[0]
        id_device = document['id_device']
        now_phone_zalo[id_device] = num_phone_zalo
    else:
        print(num_phone_zalo)
# docs giờ là một list chứa mọi document tìm được
    # id_device = document['id_device']
    # user_name = document['name']
    list_prior_chat_boxes = document['list_prior_chat_boxes']
    for id in list_prior_chat_boxes:
        if id['name'] == name_ntd:
            if 'data_chat_box' not in id.keys():
                result = []
            else:
                result = load_data_chat_box_json(id['data_chat_box'])

                for i2 in range(len(result)):
                    for k in result[i2].keys():
                        for i3 in range(len(result[i2][k])):
                            if "sticker_data" in result[i2][k][i3].keys():
                                ava64 = result[i2][k][i3]['sticker_data']
                                if ava64 != "":
                                    if ava64[0] == 'C':
                                        with open(ava64, "r", encoding="utf-8") as f:
                                            result[i2][k][i3]['sticker_data'] = f.read(
                                            )
                            if "video_data" in result[i2][k][i3].keys():
                                ava64 = result[i2][k][i3]['video_data']
                                if ava64 != "":
                                    if ava64[0] == 'C':
                                        with open(ava64, "r", encoding="utf-8") as f:
                                            result[i2][k][i3]['video_data'] = f.read()
                            if "image_data" in result[i2][k][i3].keys():
                                ava64 = result[i2][k][i3]['image_data']
                                if ava64 != "":
                                    if ava64[0] == 'C':
                                        with open(ava64, "r", encoding="utf-8") as f:
                                            result[i2][k][i3]['image_data'] = f.read()
            if 'friend_or_not' not in id.keys():
                friend_or_not = "yes"
            else:
                friend_or_not = id['friend_or_not']
            break
    join_room(room)
    emit("receive_data_one_chat_box", {
        "num_phone_zalo": num_phone_zalo, "user_name": name_ntd, "data_chat_box": result, "friend_or_not": friend_or_not}, room=room)

@app.route('/api_click_tag', methods=['POST', 'GET'])
def get_click_tag():
    data_body = request.form
    new_id = data_body.get('num_phone_zalo')
    num_phone_zalo = new_id
    # global now_phone_zalo
    # now_phone_zalo = new_id
    tag = data_body.get('tag')
    name_ntd = data_body.get('name_ntd')
    list_socket_call.append("click_tag")

    docs = get_base_id_zalo_json("D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "num_phone_zalo", f"Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}", {
                                 "num_phone_zalo": num_phone_zalo})
    if len(docs) > 0:
        document = docs[0]
        id_device = document['id_device']
        now_phone_zalo[id_device] = num_phone_zalo
    else:
        print(num_phone_zalo)

    list_prior_chat_boxes = document['list_prior_chat_boxes']
    check = False
    for id in range(len(list_prior_chat_boxes)):
        if list_prior_chat_boxes[id]['name'] == name_ntd:
            if 'tag' not in list_prior_chat_boxes[id].keys():
                list_prior_chat_boxes[id]['tag'] = ""
            if list_prior_chat_boxes[id]['tag'] == "":
                list_prior_chat_boxes[id]['tag'] = tag
                check = True
            else:
                if list_prior_chat_boxes[id]['tag'] != tag:
                    list_prior_chat_boxes[id]['tag'] = tag
                    check = True
                else:
                    list_prior_chat_boxes[id]['tag'] = ""
                    check = True
            if check:
                data_update = {"num_phone_zalo": num_phone_zalo,
                               "list_prior_chat_boxes": list_prior_chat_boxes}
                update_base_document_json(
                    "D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "num_phone_zalo", f"Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}", data_update)
            break
    return jsonify({"status": "Đã click tag thành công"}), 200


@app.route('/find_new_friend', methods=['POST', 'GET'])
def api_find_new_friend():
    data = request.form
    room = data.get('id_chat', '')
    num_phone_zalo = data.get('num_phone_zalo')
    num_send_phone_zalo = data.get('num_send_phone_zalo')

    for k in dict_new_friend[num_phone_zalo].keys():
        if dict_new_friend[num_phone_zalo][k]['phone'] == num_send_phone_zalo:
            name_ntd = k
            avatar_64 = dict_new_friend[num_phone_zalo][k]['ava']
            friend_or_not = dict_new_friend[num_phone_zalo][k]['friend_or_not']
            return jsonify({"num_send_phone_zalo": num_send_phone_zalo, "name_ntd": name_ntd, "ava": avatar_64, "friend_or_not": friend_or_not}), 200

    one = time.time()
    docs = get_base_id_zalo_json("D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "num_phone_zalo", f"Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}", {
                                 "num_phone_zalo": num_phone_zalo})
    if len(docs) > 0:
        document = docs[0]
    else:
        # print(num_phone_zalo)
        print("không có đâu")

# docs giờ là một list chứa mọi document tìm được
    id_device = document['id_device']
    if dict_id_chat[id_device] != "" and dict_id_chat[id_device] != room:
        return jsonify({'status': "Thiết bị đang có người sử dụng"})
    dict_id_chat[id_device] = room
    dict_process_id[id_device] += 1
    id_process = dict_process_id[id_device]
    dict_queue_device[id_device].append(id_process)
    now_phone_zalo[id_device] = num_phone_zalo
    user_name = document['name']
    list_prior_chat_boxes = document['list_prior_chat_boxes']

    # print("Đã vào được điện thoại này chưa")
    if (num_phone_zalo in dict_status_zalo.keys()):
        docs = get_base_id_zalo_json(
            "D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "id_device", f"Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}", {"id_device": id_device})
        check_busy = False
        doc_phone_zalo = []
        for doc in docs:
            if doc['num_phone_zalo'] != "":
                doc_phone_zalo.append(doc['num_phone_zalo'])
                if dict_status_zalo[doc['num_phone_zalo']] != "":
                    check_busy = True
                    break
        current_id_process = dict_queue_device[id_device][0]
        while check_busy or current_id_process != id_process:
            check_busy = False
            for phone in doc_phone_zalo:
                if dict_status_zalo[phone] != "":
                    check_busy = True
            current_id_process = dict_queue_device[id_device][0]
            eventlet.sleep(0.1)

        dict_status_update_pvp[num_phone_zalo] = 1
        dict_status_zalo[num_phone_zalo] = "find_new_friend"

        try:
            d = u2.connect(id_device)
            # global device_connect
            device_connect[id_device] = True
        except Exception as e:
            print("Thiết bị đã ngắt kết nối", id_device)
            device_connect[id_device] = False
            dict_status_zalo[num_phone_zalo] = ""
            del dict_queue_device[id_device][0]
            if len(dict_queue_device[id_device]) == 0:
                dict_id_chat[id_device] = ""
            return jsonify({"status": "Thiết bị đã ngắt kết nối"})

        with open(f'D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/device_status_{dict_phone_device[num_phone_zalo]}.json', 'r') as f:
            device_status = json.load(f)
        try:
            ck_ac = True
            if not device_status['active']:
                device_status['active'] = True
                with open(f"D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/device_status_{dict_phone_device[num_phone_zalo]}.json", 'w') as f:
                    json.dump(device_status, f, indent=4)
                eventlet.sleep(0.1)
                ck_ac = False
                d.app_start("com.zing.zalo", stop=True)
                while not d(resourceId="com.zing.zalo:id/maintab_message").exists:
                    eventlet.sleep(0.05)

            name_ntd = ""
            pick = ""
            avatar_64 = ""
            friend_or_not = ""
            on_chat = False
            for id in range(len(list_prior_chat_boxes)):
                if 'phone' not in list_prior_chat_boxes[id].keys():
                    continue
                if list_prior_chat_boxes[id]['phone'] == num_send_phone_zalo:
                    name_ntd = list_prior_chat_boxes[id]['name']
                    pick = id
                    on_chat = True
                    break
            if d(resourceId="com.zing.zalo:id/action_bar_title").exists:
                btn = d(resourceId="com.zing.zalo:id/action_bar_title")
            else:
                btn = d(resourceId="com.zing.zalo:id/txtTitle")
            if btn.exists:
                current_ntd = btn.get_text()
                if current_ntd == name_ntd:
                    on_chat = True
                else:
                    while not d.xpath('//*[@text="Ưu tiên"]').exists:
                        d.press("back")
                        eventlet.sleep(0.1)
            if not on_chat:
                try:
                    while not d.xpath('//*[@text="Ưu tiên"]').exists:
                        if d.xpath('//*[@text="Zalo"]').exists:
                            try:
                                d.xpath(
                                    '//*[@text="Zalo"]').click()
                                while not d(resourceId="com.zing.zalo:id/maintab_message").exists:
                                    eventlet.sleep(0.05)
                            except Exception:
                                pass
                        else:
                            d.press("back")
                            eventlet.sleep(0.1)

                    d(text="Tìm kiếm").click()
                    eventlet.sleep(0.1)
                # time.sleep(1.0)
                    d.send_keys(f"{num_send_phone_zalo}", clear=True)

                    chat_num = d(
                        resourceId="com.zing.zalo:id/btn_search_result")
                    chat_num.click()

                    if d(resourceId="com.zing.zalo:id/btn_send_message").exists:
                        d(resourceId="com.zing.zalo:id/btn_send_message").click()
                    eventlet.sleep(0.2)
                    if d(textContains="Đóng").exists:
                        d(textContains="Đóng").click()
                        eventlet.sleep(0.5)
                    if d(resourceId="com.zing.zalo:id/txtTitle").exists:
                        ntd = d(
                            resourceId="com.zing.zalo:id/txtTitle")
                    else:
                        ntd = d(
                            resourceId="com.zing.zalo:id/action_bar_title")
                    name_ntd = ntd.get_text()
                    ntd.click()
                    eventlet.sleep(0.1)

                except Exception as e:
                    print(e)
                    dict_status_zalo[num_phone_zalo] = ""
                    del dict_queue_device[id_device][0]
                    if len(dict_queue_device[id_device]) == 0:
                        dict_id_chat[id_device] = ""
                    return jsonify({"status": "Bận rồi ông cháu ơi"})

                iv = d(
                    resourceId="com.zing.zalo:id/rounded_avatar_frame")
                eventlet.sleep(1.0)

                img = iv.screenshot()
                max_w, max_h = 200, 200
                img.thumbnail((max_w, max_h),
                              resample=Image.BILINEAR)
                buf = io.BytesIO()
                img.save(buf, format="JPEG",
                         optimize=True, quality=75)
                avatar_b64 = base64.b64encode(
                    buf.getvalue()).decode("ascii")

                if not already_sent_number(AVA_NUMBER_PATH):
                    ava_number = 0
                else:
                    ava_number = read_sent_number(AVA_NUMBER_PATH)
                with open(f"{IMAGE_FILE_DATA_PATH}/ava_{ava_number}.txt", "w", encoding="utf-8") as f:
                    f.write(avatar_b64)

                log_sent_number(ava_number+1, AVA_NUMBER_PATH)

                avatar_b64 = f"{IMAGE_FILE_DATA_PATH}/ava_{ava_number}.txt"

                d.press('back')

                while not d(resourceId="com.zing.zalo:id/txtTitle").exists and not d(resourceId="com.zing.zalo:id/action_bar_title").exists:
                    eventlet.sleep(0.05)

                # btn = d(resourceId="com.zing.zalo:id/tv_function_privacy")
                if d(text="Đã gửi lời mời kết bạn").exists:
                    friend_or_not = "added"
                else:
                    btn = d(
                        resourceId="com.zing.zalo:id/tv_function_privacy")
                    kb = d.xpath('//*[@text="Kết bạn"]')
                    if btn.exists or kb.exists:
                        friend_or_not = "no"
                    else:
                        friend_or_not = "yes"

                check = False
                for id in range(len(list_prior_chat_boxes)):
                    if list_prior_chat_boxes[id]['name'] == name_ntd:
                        list_prior_chat_boxes[id]['phone'] = num_send_phone_zalo
                        list_prior_chat_boxes[id]['status'] = 'seen'
                        list_prior_chat_boxes[id]['friend_or_not'] = friend_or_not
                        check = True
                        break
                if not check:
                    # list_prior_chat_boxes.append(
                    #    {"name": name_ntd, "phone": num_send_phone_zalo, "ava": avatar_64, "time": "", "friend_or_not": friend_or_not, "message": "", "status": "seen",  "data_chat_box": []})
                    dict_new_friend[num_phone_zalo][name_ntd] = {"phone": num_send_phone_zalo, "ava": avatar_64,
                                                                       "time": "", "friend_or_not": friend_or_not, "message": "", "status": "seen",  "data_chat_box": ""}
                if check:
                    data_update = {"num_phone_zalo": num_phone_zalo,
                                   "list_prior_chat_boxes": list_prior_chat_boxes}
                    update_base_document_json(
                        "D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "num_phone_zalo", f"Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}", data_update)
                if not already_sent(num_send_phone_zalo):
                    log_sent(num_send_phone_zalo)

            else:
                list_friend = document['list_friend']
                for friend in list_friend:
                    if friend['name'] == name_ntd:
                        avatar_64 = friend['ava']
                        break
                friend_or_not = "no"
                for id in range(len(list_prior_chat_boxes)):
                    if list_prior_chat_boxes[id]['name'] == name_ntd:
                        if 'friend_or_not' in list_prior_chat_boxes[id].keys():
                            friend_or_not = list_prior_chat_boxes[id]['friend_or_not']
                        # list_prior_chat_boxes[id]['friend_or_not'] = friend_or_not
                        break
                # data_update = {"num_phone_zalo": num_phone_zalo,
                #               "list_prior_chat_boxes": list_prior_chat_boxes}
                # update_base_document_json(
                #    "D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "num_phone_zalo", f"Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}", data_update)
        except Exception as e:
            print(e)
            pass

        with open(f'D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/device_status_{dict_phone_device[num_phone_zalo]}.json', 'r') as f:
            device_status = json.load(f)
        if device_status['active'] and len(dict_queue_device[id_device]) == 0:
            device_status['active'] = False
            print("Có set về false không")
            with open(f"D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/device_status_{dict_phone_device[num_phone_zalo]}.json", 'w') as f:
                json.dump(device_status, f, indent=4)

        dict_status_zalo[num_phone_zalo] = ""
        dict_status_update_pvp[num_phone_zalo] = 0
        del dict_queue_device[id_device][0]
        if len(dict_queue_device[id_device]) == 0:
            dict_id_chat[id_device] = ""
        return jsonify({"num_send_phone_zalo": num_send_phone_zalo, "name_ntd": name_ntd, "ava": avatar_64, "friend_or_not": friend_or_not}), 200


@app.route('/switch_account', methods=['POST', 'GET'])
def api_switch_account():

    data = request.form
    room = data.get('id_chat', '')
    num_phone_zalo = data.get('num_phone_zalo')
    num_send_phone_zalo = data.get('num_send_phone_zalo')
    one = time.time()
    docs = get_base_id_zalo_json("D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "num_phone_zalo", f"Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}", {
                                 "num_phone_zalo": num_phone_zalo})
    if len(docs) > 0:
        document = docs[0]
    else:
        print("không có đâu")

# docs giờ là một list chứa mọi document tìm được
    id_device = document['id_device']
    if dict_id_chat[id_device] != "" and dict_id_chat[id_device] != room:
        return jsonify({'status': "Thiết bị đang có người sử dụng"})
    dict_id_chat[id_device] = room
    dict_process_id[id_device] += 1
    id_process = dict_process_id[id_device]
    dict_queue_device[id_device].append(id_process)
    now_phone_zalo[id_device] = num_phone_zalo
    user_name = document['name']

    if (num_phone_zalo in dict_status_zalo.keys()):
        docs = get_base_id_zalo_json(
            "D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "id_device", f"Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}", {"id_device": id_device})
        check_busy = False
        doc_phone_zalo = []
        for doc in docs:
            if doc['num_phone_zalo'] != "":
                doc_phone_zalo.append(doc['num_phone_zalo'])
                if dict_status_zalo[doc['num_phone_zalo']] != "":
                    check_busy = True
                    break
        current_id_process = dict_queue_device[id_device][0]
        while check_busy or current_id_process != id_process:
            check_busy = False
            for phone in doc_phone_zalo:
                if dict_status_zalo[phone] != "":
                    check_busy = True
            current_id_process = dict_queue_device[id_device][0]
            eventlet.sleep(0.1)

        dict_status_update_pvp[num_phone_zalo] = 1
        dict_status_zalo[num_phone_zalo] = "switch_account"
        doc = get_base_id_zalo_json("D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "num_phone_zalo", f"Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}", {
            "num_phone_zalo": num_phone_zalo})[0]
        if not doc['status']:

            try:
                with open(f'D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/device_status_{dict_phone_device[num_phone_zalo]}.json', 'r') as f:
                    device_status = json.load(f)
                if not device_status['active']:
                    device_status['active'] = True
                    with open(f"D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/device_status_{dict_phone_device[num_phone_zalo]}.json", 'w') as f:
                        json.dump(device_status, f, indent=4)
                    eventlet.sleep(0.2)
                d = u2.connect(id_device)
                d = switch_account(d, user_name)
                docs = get_base_id_zalo_json(
                    "D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "id_device", f"Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}", {"id_device": id_device})
                for it in docs:
                    if it['status']:
                        current_phone = it['num_phone_zalo']
                        status = update_base_document_json("D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "num_phone_zalo", f"Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}", {
                            "num_phone_zalo": current_phone, "status": False})
                status = update_base_document_json("D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "num_phone_zalo", f"Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}", {
                    "num_phone_zalo": num_phone_zalo, "status": True})

                with open(f'D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/device_status_{dict_phone_device[num_phone_zalo]}.json', 'r') as f:
                    device_status = json.load(f)
                if len(dict_queue_device[id_device]) == 0:
                    dict_id_chat[id_device] = ""
                if device_status['active'] and len(dict_queue_device[id_device]) == 0:
                    device_status['active'] = False
                    print("Có set về false không")
                    with open(f"D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/device_status_{dict_phone_device[num_phone_zalo]}.json", 'w') as f:
                        json.dump(device_status, f, indent=4)

                dict_status_zalo[num_phone_zalo] = ""
                dict_status_update_pvp[num_phone_zalo] = 0
                del dict_queue_device[id_device][0]

                return jsonify({"status": "Chuyển tài khoản thành công"})

            except Exception as e:
                print("Chuyển tài khoản thất bại", id_device)
                dict_status_zalo[num_phone_zalo] = ""
                dict_status_update_pvp[num_phone_zalo] = 0
                del dict_queue_device[id_device][0]
                if len(dict_queue_device[id_device]) == 0:
                    dict_id_chat[id_device] = ""
                if device_status['active'] and len(dict_queue_device[id_device]) == 0:
                    device_status['active'] = False
                return jsonify({"status": "Chuyển tài khoản thất bại"})


@app.route('/api_add_friend_chat_pvp', methods=['POST', 'GET'])
def api_add_friend_chat_pvp():
    data = request.form
    list_socket_call.append("add_friend_chat_pvp")
    room = data.get('id_chat', '')
    num_phone_zalo = data.get('num_phone_zalo')
    name = data.get('name')
    # global now_phone_zalo
    # now_phone_zalo = num_phone_zalo
    global num_add_friend
    global max_add_friend_per_day
    with open(f'D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/device_status_{dict_phone_device[num_phone_zalo]}.json', 'r') as f:
        device_status = json.load(f)

    for id in range(len(device_status['max_add_friend_per_day'])):
        if num_phone_zalo in device_status['max_add_friend_per_day'][id].keys():
            if device_status['max_add_friend_per_day'][id][num_phone_zalo] <= 0:
                return jsonify({"status": "Đã đạt giới hạn kết bạn một ngày"})
    if num_add_friend >= max_add_friend_per_day:
        return jsonify({"status": "Đã đạt giới hạn kết bạn một ngày"})
#    ava = data['ava']
    # dict_status_update_pvp[num_phone_zalo] = 1
    docs = get_base_id_zalo_json("D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "num_phone_zalo", f"Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}", {
                                 "num_phone_zalo": num_phone_zalo})
    if len(docs) > 0:
        document = docs[0]
        id_device = document['id_device']
        now_phone_zalo[id_device] = num_phone_zalo
    else:
        print("không có đâu")

# docs giờ là một list chứa mọi document tìm được
    id_device = document['id_device']
    if dict_id_chat[id_device] != "" and dict_id_chat[id_device] != room:
        return jsonify({'status': "Thiết bị đang có người sử dụng"})
    dict_id_chat[id_device] = room
    dict_process_id[id_device] += 1
    id_process = dict_process_id[id_device]
    dict_queue_device[id_device].append(id_process)
    user_name = document['name']
    list_prior_chat_boxes = document['list_prior_chat_boxes']
    # d = u2.connect(id_device)
    if (num_phone_zalo in dict_status_zalo.keys()):
        docs = get_base_id_zalo_json(
            "D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "id_device", f"Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}", {"id_device": id_device})
        check_busy = False
        doc_phone_zalo = []
        for doc in docs:
            if doc['num_phone_zalo'] != "":
                doc_phone_zalo.append(doc['num_phone_zalo'])
                if dict_status_zalo[doc['num_phone_zalo']] != "":
                    check_busy = True
                    break
        current_id_process = dict_queue_device[id_device][0]
        while check_busy or current_id_process != id_process:
            check_busy = False
            for phone in doc_phone_zalo:
                if dict_status_zalo[phone] != "":
                    check_busy = True
            current_id_process = dict_queue_device[id_device][0]
            eventlet.sleep(0.1)

        dict_status_update_pvp[num_phone_zalo] = 1
        dict_status_zalo[num_phone_zalo] = "add_friend_chat_pvp"

        try:
            d = u2.connect(id_device)
            device_connect[id_device] = True
        except Exception as e:
            print("Thiết bị đã ngắt kết nối ", id_device)
            device_connect[id_device] = False
            dict_status_zalo[num_phone_zalo] = ""
            del dict_queue_device[id_device][0]
            if len(dict_queue_device[id_device]) == 0:
                dict_id_chat[id_device] = ""
            return jsonify({"status": "Thiết bị đã ngắt kết nối"})

        if not device_status['active']:
            device_status['active'] = True
            with open(f"D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/device_status_{dict_phone_device[num_phone_zalo]}.json", 'w') as f:
                json.dump(device_status, f, indent=4)
            eventlet.sleep(0.1)
            try:
                d.app_start("com.zing.zalo", stop=True)
                # eventlet.sleep(1.0)
                while not d(resourceId="com.zing.zalo:id/maintab_message").exists:
                    eventlet.sleep(0.05)
            except Exception as e:
                print("Thiết bị đã ngắt kết nối ", id_device)
                device_connect[id_device] = False
                dict_status_zalo[num_phone_zalo] = ""
                del dict_queue_device[id_device][0]
                if len(dict_queue_device[id_device]) == 0:
                    dict_id_chat[id_device] = ""
                return jsonify({"status": "Thiết bị đã ngắt kết nối"})
                # if d is None:

        try:
            if d(text="Kết bạn").exists:
                d(resourceId="com.zing.zalo:id/tv_function_privacy").click()
                eventlet.sleep(1.0)
                d(resourceId="com.zing.zalo:id/btnSendInvitation").click()
                eventlet.sleep(1.0)
            else:
                dict_status_zalo[num_phone_zalo] = ""
                del dict_queue_device[id_device][0]
                if len(dict_queue_device[id_device]) == 0:
                    dict_id_chat[id_device] = ""
                return jsonify({"status": "Đã gửi kết bạn trước đó hoặc đã là bạn bè"})

        except Exception as e:
            print("Lỗi gặp phải là: ", e)
            dict_status_zalo[num_phone_zalo] = ""
            return jsonify({"status": "Gửi kết bạn thất bại"})

        check_add = False

        for id in range(len(list_prior_chat_boxes)):
            if list_prior_chat_boxes[id]['name'] == name:
                list_prior_chat_boxes[id]['friend_or_not'] = "added"
                check_add = True
                break
        if not check_add:
            if name in dict_new_friend[num_phone_zalo].keys:
                dict_new_friend[num_phone_zalo][name]['friend_or_not'] = "added"
        data_update = {"num_phone_zalo": num_phone_zalo,
                       "list_prior_chat_boxes": list_prior_chat_boxes}
        update_base_document_json(
            "D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "num_phone_zalo", f"Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}", data_update)

        num_add_friend += 1
        with open(f'D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/device_status_{dict_phone_device[num_phone_zalo]}.json', 'r') as f:
            device_status = json.load(f)

        for id in range(len(device_status['max_add_friend_per_day'])):
            if num_phone_zalo in device_status['max_add_friend_per_day'][id].keys():
                device_status['max_add_friend_per_day'][id][num_phone_zalo] -= 1
                # handle_chat_view(d, num_phone_zalo)

        if device_status['active'] and len(dict_queue_device[id_device]) == 0:
            device_status['active'] = False

        with open(f"D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/device_status_{dict_phone_device[num_phone_zalo]}.json", 'w') as f:
            json.dump(device_status, f, indent=4)

        dict_status_zalo[num_phone_zalo] = ""
        del dict_queue_device[id_device][0]
        dict_status_update_pvp[num_phone_zalo] = 0
        if len(dict_queue_device[id_device]) == 0:
            dict_id_chat[id_device] = ""

        return jsonify({"status": "Gửi kết bạn thành công"})


@app.route('/api_create_group_chat_pvp', methods=['POST', 'GET'])
def api_add_create_group_chat_pvp():
    data = request.form
    list_socket_call.append("create_group_chat_pvp")
    num_phone_zalo = data.get('num_phone_zalo')
    name_group = data.get('name_group')
    mem_list_str = data.get('mem_list')
    room = data.get('id_chat', '')
    mem_list = json.loads(mem_list_str)
    # print("Danh sách thành viên nhóm là: ", mem_list)
    ava = data.get('group_avatar')
    # global now_phone_zalo
    # now_phone_zalo = num_phone_zalo
    global num_add_friend
    global max_add_friend_per_day

    with open(f'D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/device_status_{dict_phone_device[num_phone_zalo]}.json', 'r') as f:
        device_status = json.load(f)

    # ava = data.get('ava')
    # dict_status_update_pvp[num_phone_zalo] = 1
    docs = get_base_id_zalo_json("D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "num_phone_zalo", f"Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}", {
                                 "num_phone_zalo": num_phone_zalo})
    if len(docs) > 0:
        document = docs[0]
        id_device = document['id_device']
        now_phone_zalo[id_device] = num_phone_zalo
    else:
        print(num_phone_zalo)
        # print("không có đâu")

# docs giờ là một list chứa mọi document tìm được
    id_device = document['id_device']
    if dict_id_chat[id_device] != "" and dict_id_chat[id_device] != room:
        return jsonify({'status': "Thiết bị đang có người sử dụng"})
    dict_id_chat[id_device] = room
    dict_process_id[id_device] += 1
    id_process = dict_process_id[id_device]
    dict_queue_device[id_device].append(id_process)
    user_name = document['name']
    list_prior_chat_boxes = document['list_prior_chat_boxes']
    list_group = document['list_group']
    try:
        d = u2.connect(id_device)
        device_connect[id_device] = True
    except Exception as e:
        print("Thiết bị đã ngắt kết nối ", id_device)
        device_connect[id_device] = False
        return jsonify({"status": "Thiết bị đã ngắt kết nối"})

    if (num_phone_zalo in dict_status_zalo.keys()):
        docs = get_base_id_zalo_json(
            "D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "id_device", f"Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}", {"id_device": id_device})
        check_busy = False
        doc_phone_zalo = []
        for doc in docs:
            if doc['num_phone_zalo'] != "":
                doc_phone_zalo.append(doc['num_phone_zalo'])
                if dict_status_zalo[doc['num_phone_zalo']] != "":
                    check_busy = True
                    break
        current_id_process = dict_queue_device[id_device][0]
        while check_busy or current_id_process != id_process:
            check_busy = False
            for phone in doc_phone_zalo:
                if dict_status_zalo[phone] != "":
                    check_busy = True
            current_id_process = dict_queue_device[id_device][0]
            eventlet.sleep(0.1)

        dict_status_update_pvp[num_phone_zalo] = 1
        dict_status_zalo[num_phone_zalo] = "create_group_chat_pvp"

        try:
            d = u2.connect(id_device)
            # global device_connect
            device_connect[id_device] = True
        except Exception as e:
            print("Thiết bị đã ngắt kết nối ", id_device)
            device_connect[id_device] = False
            dict_status_zalo[num_phone_zalo] = ""
            del dict_queue_device[id_device][0]
            if len(dict_queue_device[id_device]) == 0:
                dict_id_chat[id_device] = ""
            return jsonify({"status": "Thiết bị đã ngắt kết nối"})

        if not device_status['active']:
            device_status['active'] = True
            with open(f"D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/device_status_{dict_phone_device[num_phone_zalo]}.json", 'w') as f:
                json.dump(device_status, f, indent=4)
            eventlet.sleep(0.2)
            try:
                d.app_start("com.zing.zalo", stop=True)
                # eventlet.sleep(1.0)
                while not d(resourceId="com.zing.zalo:id/maintab_message").exists:
                    eventlet.sleep(0.05)
            except Exception as e:
                print("Thiết bị đã ngắt kết nối ", id_device)
                device_connect[id_device] = False
                dict_status_zalo[num_phone_zalo] = ""
                del dict_queue_device[id_device][0]
                if len(dict_queue_device[id_device]) == 0:
                    dict_id_chat[id_device] = ""
                return jsonify({"status": "Thiết bị đã ngắt kết nối"})
                # if d is None:

        try:
            d.app_start("com.zing.zalo", stop=True)
            eventlet.sleep(1.0)
            d(resourceId="com.zing.zalo:id/action_bar_plus_btn").click()
            eventlet.sleep(0.1)
            d(text="Tạo nhóm").click()
            eventlet.sleep(0.1)
            if name_group != "":
                gr_names = [group['name'] for group in list_group]
                if name_group in gr_names:
                    dict_status_zalo[num_phone_zalo] = ""
                    return jsonify({"status": "Tên nhóm này tồn tại, yêu cầu đặt tên khác"})
                try:
                    d.xpath(
                        '//*[@resource-id="com.zing.zalo:id/header_section"]/android.widget.LinearLayout[1]').click()
                except Exception as e:
                    d.xpath(
                        '//*[@resource-id="com.zing.zalo:id/layout_update_avatar"]/android.widget.LinearLayout[1]').click()
                eventlet.sleep(0.1)
                d.send_keys(name_group, clear=True)
                eventlet.sleep(0.1)
                d(resourceId="com.zing.zalo:id/btn_done_input_group_name").click()
                eventlet.sleep(0.1)
            else:
                if len(mem_list) > 2:
                    name_group = ", ".join(mem_list[:2])
                else:
                    name_group = ", ".join(mem_list)
            avatar = ava
            if ava != "":
                pass
            print("Danh sách thành viên là: ", mem_list)
            for mem in mem_list:
                d(resourceId="com.zing.zalo:id/edt_search").click()
                eventlet.sleep(0.1)
                d.send_keys(mem, clear=True)
                eventlet.sleep(0.1)
                # if d(textContains=mem).exists:
                #    d(textContains=mem).click()
                el = d.xpath(
                    '//android.widget.FrameLayout[@index="0" and normalize-space(@text) != ""]')
                if el.exists:
                    el.click()
                    eventlet.sleep(0.1)

            d(resourceId="com.zing.zalo:id/btn_done_create_group").click()
            eventlet.sleep(1.0)

            if d(resourceId="com.zing.zalo:id/btn_done_create_group").exists:
                dict_status_zalo[num_phone_zalo] = ""
                try:
                    d.press('back')
                    eventlet.sleep(0.1)
                    d.xpath(
                        '//*[@resource-id="com.zing.zalo:id/modal_cta_custom_layout"]/android.widget.RelativeLayout[2]').click()
                    eventlet.sleep(0.1)
                except Exception as e:
                    print("Lỗi gặp phải là: ", e)
                return jsonify({"status": "Cần tối thiểu 1 người là bạn bè"})

            list_mems = [{"name_member": user_name, "role": "Trưởng nhóm"}]
            for mem in mem_list:
                list_mems.append({"name_member": mem, "role": ""})
            check_mems = True

            list_prior_chat_boxes.append({"name": name_group, "phone": "", "ava": avatar, "time": "", "message": "",
                                         "status": "seen", "data_chat_box": "", "list_mems": list_mems, "check_mems": check_mems})
            list_prior_chat_boxes.insert(
                0, list_prior_chat_boxes.pop(-1))
            list_group.append(
                {"name": name_group, "ava": avatar, "list_mems": list_mems, "check_mems": check_mems})
        except Exception as e:
            print("Lỗi gặp phải là: ", e)
            dict_status_zalo[num_phone_zalo] = ""
            del dict_queue_device[id_device][0]
            if len(dict_queue_device[id_device]) == 0:
                dict_id_chat[id_device] = ""
            try:
                d.press('back')
                eventlet.sleep(0.1)
                d.xpath(
                    '//*[@resource-id="com.zing.zalo:id/modal_cta_custom_layout"]/android.widget.RelativeLayout[2]').click()
                eventlet.sleep(0.1)
            except Exception as e:
                print("Lỗi gặp phải là: ", e)
            return jsonify({"status": "Tạo nhóm thất bại"})

        data_update = {"num_phone_zalo": num_phone_zalo,
                       "list_prior_chat_boxes": list_prior_chat_boxes, "list_group": list_group}
        update_base_document_json(
            "D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "num_phone_zalo", f"Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}", data_update)

        with open(f'D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/device_status_{dict_phone_device[num_phone_zalo]}.json', 'r') as f:
            device_status = json.load(f)

        if device_status['active'] and len(dict_queue_device[id_device]) == 0:
            device_status['active'] = False
            # print("Có set về false không")
            with open(f"D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/device_status_{dict_phone_device[num_phone_zalo]}.json", 'w') as f:
                json.dump(device_status, f, indent=4)

        dict_status_zalo[num_phone_zalo] = ""
        del dict_queue_device[id_device][0]
        dict_status_update_pvp[num_phone_zalo] = 0
        if len(dict_queue_device[id_device]) == 0:
            dict_id_chat[id_device] = ""

        return jsonify({"status": "Tạo nhóm thành công"})

@socketio.on('connect')
def handle_connect():
    list_socket_call.append("connect")
    print(f"Client connected: {request.sid}")
    # while True:
    #     print ("list_socket_call ----", list_socket_call)
    #     eventlet.sleep(2)


@socketio.on('join')
def handle_join(data):
    list_socket_call.append("join")
    global id_chat
    room = data['id_chat']
    id_chat = room
    # global folder_data_zalo
    # folder_data_zalo = data['folder_data_zalo']
    # os.makedirs(os.path.join(folder_data_zalo, 'data'), exist_ok=True)
    join_room(room)
    # print(f"Client {request.sid} joined room {room}")
    # res =  update_port_base_id_chat("D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "Zalo_data_login_port",room)
    emit("status_update_list_chat", {"status": "1"}, room=room)
    # print ("update_port_base_id_chat------------------", res )


@socketio.on('leave')
def handle_leave(data):
    list_socket_call.append("leave")
    room = data['id_chat']
    leave_room(room)
    print(f"Client {request.sid} left room {room}")


@socketio.on('open_chat_pvp')
def handle_chat_pvp(data):
    list_socket_call.append("open_chat_pvp")
    room = data['id_chat']
    num_phone_zalo = data['num_phone_zalo']
    num_send_phone_zalo = data['num_send_phone_zalo']
    name = data['name']
#    ava = data['ava']
    docs = get_base_id_zalo_json("D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "num_phone_zalo", f"Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}", {
                                 "num_phone_zalo": num_phone_zalo})
    if len(docs) > 0:
        document = docs[0]
    else:
        # print(num_phone_zalo)
        print("không có đâu")

# docs giờ là một list chứa mọi document tìm được
    id_device = document['id_device']
    if dict_id_chat[id_device] != "" and dict_id_chat[id_device] != room:
        join_room(room)
        emit("receive_device_status", {
            "status": "Thiết bị đang có người sử dụng", 'name_ntd': name}, room=room)
        return False
    dict_id_chat[id_device] = room
    dict_process_id[id_device] += 1
    id_process = dict_process_id[id_device]
    dict_queue_device[id_device].append(id_process)
    now_phone_zalo[id_device] = num_phone_zalo
    list_prior_chat_boxes = document['list_prior_chat_boxes']
    list_friend = document['list_friend']

    if (num_phone_zalo in dict_status_zalo.keys()):
        docs = get_base_id_zalo_json(
            "D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "id_device", f"Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}", {"id_device": id_device})
        check_busy = False
        doc_phone_zalo = []
        for doc in docs:
            if doc['num_phone_zalo'] != "":
                doc_phone_zalo.append(doc['num_phone_zalo'])
                if dict_status_zalo[doc['num_phone_zalo']] != "":
                    check_busy = True
                    break
        current_id_process = dict_queue_device[id_device][0]
        while check_busy or current_id_process != id_process:
            check_busy = False
            for phone in doc_phone_zalo:
                if dict_status_zalo[phone] != "":
                    check_busy = True
            current_id_process = dict_queue_device[id_device][0]
            eventlet.sleep(0.1)

        dict_status_update_pvp[num_phone_zalo] = 1
        dict_status_zalo[num_phone_zalo] = "handle_chat_pvp"

        try:
            d = u2.connect(id_device)
            # global device_connect
            device_connect[id_device] = True
        except Exception as e:
            print("Thiết bị đã ngắt kết nối", id_device)
            device_connect[id_device] = False
            join_room(room)
            emit("receive_device_status", {
                "status": "Thiết bị đã ngắt kết nối", 'name_ntd': name}, room=room)
            dict_status_zalo[num_phone_zalo] = ""
            del dict_queue_device[id_device][0]
            if len(dict_queue_device[id_device]) == 0:
                dict_id_chat[id_device] = ""
            return False
        try:
            with open(f'D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/device_status_{dict_phone_device[num_phone_zalo]}.json', 'r') as f:
                device_status = json.load(f)
            if not device_status['active']:
                device_status['active'] = True
                with open(f"D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/device_status_{dict_phone_device[num_phone_zalo]}.json", 'w') as f:
                    json.dump(device_status, f, indent=4)
                eventlet.sleep(0.2)
                d.app_start("com.zing.zalo", stop=True)
                # eventlet.sleep(1.0)
                while not d(resourceId="com.zing.zalo:id/maintab_message").exists:
                    eventlet.sleep(0.05)
        except Exception as e:
            print("Thiết bị đã ngắt kết nối", id_device)
            device_connect[id_device] = False
            join_room(room)
            emit("receive_device_status", {
                "status": "Thiết bị đã ngắt kết nối", 'name_ntd': name}, room=room)
            dict_status_zalo[num_phone_zalo] = ""
            del dict_queue_device[id_device][0]
            if len(dict_queue_device[id_device]) == 0:
                dict_id_chat[id_device] = ""
            return False

        try:
            if num_phone_zalo in dict_new_friend.keys():
                if name in dict_new_friend[num_phone_zalo].keys():
                    num_send_phone_zalo = dict_new_friend[num_phone_zalo][name]['phone']

            if num_send_phone_zalo != "":
                name_ntd = ""
                # pick = ""
                for id in range(len(list_prior_chat_boxes)):
                    if list_prior_chat_boxes[id]['phone'] == num_send_phone_zalo:
                        name_ntd = list_prior_chat_boxes[id]['name']
                        # pick = id
                        break
                on_chat = False
                btn = d(resourceId="com.zing.zalo:id/txtTitle")
                if btn.exists:
                    current_ntd = btn.get_text()
                    if current_ntd == name_ntd:
                        on_chat = True
                    else:

                        while not d.xpath('//*[@text="Ưu tiên"]').exists:
                            d.press("back")
                            eventlet.sleep(0.1)
                elif d(resourceId="com.zing.zalo:id/action_bar_title").exists:
                    current_ntd = d(resourceId="com.zing.zalo:id/action_bar_title").get_text()
                    if current_ntd == name_ntd:
                        on_chat = True
                    else:

                        while not d.xpath('//*[@text="Ưu tiên"]').exists:
                            d.press("back")
                            eventlet.sleep(0.1)
                if not on_chat:
                    items = d.xpath(
                        "//android.widget.FrameLayout[@text!='']").all()
                    check_ex = False
                    for item in items:
                        raw = item.text
                        lines = [l for l in raw.split(
                            "\n") if l.strip()]
                        if lines[0] == name_ntd:
                            try:
                                item.click()
                                check_ex = True
                            except Exception as e:
                                print(e)
                            break
                    if not check_ex:
                        while not d.xpath('//*[@text="Ưu tiên"]').exists:
                            if d.xpath('//*[@text="Zalo"]').exists:
                                # try:
                                d.xpath(
                                    '//*[@text="Zalo"]').click()
                                while not d(resourceId="com.zing.zalo:id/maintab_message").exists:
                                    eventlet.sleep(0.05)
                                # except Exception:
                                #    pass
                            else:
                                d.press("back")
                                eventlet.sleep(0.1)
                        # d.app_start("com.zing.zalo", stop=True)
                        # d = run_start(d)
                        try:

                            d(text="Tìm kiếm").click()
                            eventlet.sleep(0.1)
                            d.send_keys(
                                f"{num_send_phone_zalo}", clear=True)
                        except Exception as e:
                            print(e)
                            dict_status_zalo[num_phone_zalo] = ""
                            del dict_queue_device[id_device][0]
                            if len(dict_queue_device[id_device]) == 0:
                                dict_id_chat[id_device] = ""
                            emit("receive_chat_view_status", {
                                "status": "Mở chat thất bại, hãy thử mở lại", "name_ntd": name}, room=room)
                            return False

                        try:
                            chat_num = d(
                                resourceId="com.zing.zalo:id/btn_search_result")
                            chat_num.click()

                        except Exception:
                            emit("receive_chat_view_status", {
                                "status": "Số điện thoại chưa tạo tài khoản zalo", "name_ntd": name}, room=room)
                            dict_status_zalo[num_phone_zalo] = ""
                            del dict_queue_device[id_device][0]
                            if len(dict_queue_device[id_device]) == 0:
                                dict_id_chat[id_device] = ""
                            return False

                        try:
                            btn = d(
                                resourceId="com.zing.zalo:id/btn_send_message")
                            if btn.exists:
                                btn.click()
                                eventlet.sleep(0.1)
                        except Exception:
                            pass

                    try:
                        if d(textContains="Đóng").exists:
                            d(textContains="Đóng").click()
                            eventlet.sleep(0.5)
                        if d(resourceId="com.zing.zalo:id/action_bar_title").exists:
                            name_ntd = d(resourceId="com.zing.zalo:id/action_bar_title").get_text()
                        else:
                            name_ntd = d(
                                resourceId="com.zing.zalo:id/txtTitle").get_text()
                    except Exception as e:
                        print(e)
                        dict_status_zalo[num_phone_zalo] = ""
                        del dict_queue_device[id_device][0]
                        if len(dict_queue_device[id_device]) == 0:
                            dict_id_chat[id_device] = ""
                        emit("receive_chat_view_status", {
                            "status": "Mở chat thất bại, hãy thử mở lại", "name_ntd": name}, room=room)
                        return False
                    if d(text="Đã gửi lời mời kết bạn").exists:
                        friend_or_not = "added"
                    else:
                        btn = d(
                            resourceId="com.zing.zalo:id/tv_function_privacy")
                        kb = d.xpath('//*[@text="Kết bạn"]')
                        if btn.exists or kb.exists:
                            friend_or_not = "no"
                        else:
                            friend_or_not = "yes"
                    check = False
                    for id in range(len(list_prior_chat_boxes)):
                        if list_prior_chat_boxes[id]['name'] == name_ntd:
                            list_prior_chat_boxes[id]['phone'] = num_send_phone_zalo
                            list_prior_chat_boxes[id]['status'] = 'seen'
                            list_prior_chat_boxes[id]['friend_or_not'] = friend_or_not
                            check = True
                            break

                    # for id in range(len(list_friend)):
                    ck_add_or_dl = False

                    if friend_or_not == 'yes':
                        fr_name = [l['name'] for l in list_friend]
                        if name_ntd not in fr_name:
                            ava = ""
                            if name_ntd in dict_new_friend[num_phone_zalo].keys:
                                ava = dict_new_friend[num_phone_zalo][name]['ava']
                            list_friend.append(
                                {"name": name_ntd, "ava": ava, "phone": num_send_phone_zalo})
                            ck_add_or_dl = True
                    elif friend_or_not == 'no':
                        for id in range(len(list_friend)):
                            if list_friend[id]['name'] == name_ntd:
                                del list_friend[id]
                                ck_add_or_dl = True

                    if not check:
                        if name_ntd in dict_new_friend[num_phone_zalo].keys:
                            if friend_or_not == 'added':
                                dict_new_friend[num_phone_zalo][name_ntd]['friend_or_not'] = "added"

                    if ck_add_or_dl:
                        data_update = {"num_phone_zalo": num_phone_zalo,
                                       "list_prior_chat_boxes": list_prior_chat_boxes, "list_friend": list_friend}
                    else:
                        data_update = {"num_phone_zalo": num_phone_zalo,
                                       "list_prior_chat_boxes": list_prior_chat_boxes}
                    update_base_document_json(
                        "D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "num_phone_zalo", f"Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}", data_update)
                else:
                    for id in range(len(list_prior_chat_boxes)):
                        if list_prior_chat_boxes[id]['name'] == name_ntd:
                            if list_prior_chat_boxes[id]['status'] == 'unseen':
                                list_prior_chat_boxes[id]['status'] = 'seen'
                                data_update = {"num_phone_zalo": num_phone_zalo,
                                               "list_prior_chat_boxes": list_prior_chat_boxes}
                                update_base_document_json(
                                    "D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "num_phone_zalo", f"Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}", data_update)
                            break
                join_room(room)
                # emit("receive_chat_view_status",{"status":"Cuộc hội thoại bắt đầu", "name_ntd": name_ntd}, room=room)
                if not already_sent(num_send_phone_zalo):
                    log_sent(num_send_phone_zalo)
                print("Cuộc hội thoại bắt đầu")
                emit("receive_chat_view_status", {
                    "status": "Cuộc hội thoại bắt đầu"}, room=room)

            else:
                on_chat = False
                if d(resourceId="com.zing.zalo:id/action_bar_title").exists:
                    btn = d(resourceId="com.zing.zalo:id/action_bar_title")
                else:
                    btn = d(resourceId="com.zing.zalo:id/txtTitle")
                if btn.exists:
                    current_ntd = btn.get_text()
                    print(current_ntd)
                    if current_ntd == name:
                        on_chat = True
                    else:
                        # d.press("back")
                        # if d(resourceId="com.zing.zalo:id/txtTitle").exists:
                        #    d.press("back")
                        #    eventlet.sleep(0.1)
                        while not d.xpath('//*[@text="Ưu tiên"]').exists:
                            d.press("back")
                            eventlet.sleep(0.1)
                if not on_chat:
                    items = d.xpath(
                        "//android.widget.FrameLayout[@text!='']").all()
                    check_ex = False
                    for item in items:
                        raw = item.text
                        lines = [l for l in raw.split(
                            "\n") if l.strip()]
                        if lines[0] == name:
                            item.click()
                            check_ex = True
                            break
                    if not check_ex:
                        while not d.xpath('//*[@text="Ưu tiên"]').exists:
                            if d.xpath('//*[@text="Zalo"]').exists:
                                # try:
                                d.xpath(
                                    '//*[@text="Zalo"]').click()
                                while not d(resourceId="com.zing.zalo:id/maintab_message").exists:
                                    eventlet.sleep(0.05)
                                # except Exception:
                                #    pass
                            else:
                                d.press("back")
                                eventlet.sleep(0.1)

                        try:
                            d(text="Tìm kiếm").click()
                            eventlet.sleep(0.1)
                        except Exception as e:
                            print(e)
                            dict_status_zalo[num_phone_zalo] = ""
                            del dict_queue_device[id_device][0]
                            if len(dict_queue_device[id_device]) == 0:
                                dict_id_chat[id_device] = ""
                            emit("receive_chat_view_status", {
                                "status": "Mở chat thất bại, hãy thử mở lại", "name_ntd": name}, room=room)
                            return False
                        # time.sleep(1.0)
                        d.send_keys(f"{name}", clear=True)
                        eventlet.sleep(0.15)

                        try:

                            chat_list = d.xpath(
                                '//*[@resource-id="com.zing.zalo:id/btn_search_result"]').all()

                            ck_ex = False

                            # if len(chat_num) > 0:
                            for item in chat_list:
                                raw = item.text
                                print("Raw là", raw)
                                lines = [l for l in raw.split(
                                    "\n") if l.strip()]
                                print(lines)
                                if lines[0] == name:
                                    print("Có trùng khớp")
                                    item.click()
                                    ck_ex = True
                                    break
                            eventlet.sleep(0.1)

                            if not ck_ex:
                                for id in range(len(list_prior_chat_boxes)):
                                    if list_prior_chat_boxes[id]['name'] == name:
                                        del list_prior_chat_boxes[id]
                                        data_update = {"num_phone_zalo": num_phone_zalo,
                                                       "list_prior_chat_boxes": list_prior_chat_boxes}
                                        update_base_document_json(
                                            "D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "num_phone_zalo", f"Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}", data_update)
                                        break

                                for id in range(len(list_friend)):
                                    if list_friend[id]['name'] == name:
                                        del list_friend[id]
                                        data_update = {"num_phone_zalo": num_phone_zalo,
                                                       "list_friend": list_friend}
                                        update_base_document_json(
                                            "D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "num_phone_zalo", f"Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}", data_update)
                                        break

                                emit("receive status", {
                                    "status": "Tài khoản không tồn tại"}, room=room)
                                dict_status_zalo[num_phone_zalo] = ""
                                del dict_queue_device[id_device][0]
                                if len(dict_queue_device[id_device]) == 0:
                                    dict_id_chat[id_device] = ""
                                print("Tài khoản không tồn tại")
                                return False

                        except Exception:
                            emit("receive status", {
                                "status": "Tài khoản không tồn tại"}, room=room)
                            dict_status_zalo[num_phone_zalo] = ""
                            del dict_queue_device[id_device][0]
                            if len(dict_queue_device[id_device]) == 0:
                                dict_id_chat[id_device] = ""
                            print("Có lỗi à cậu")
                            return False

                        try:
                            btn = d(
                                resourceId="com.zing.zalo:id/btn_send_message")
                            if btn.exists:
                                btn.click()
                                eventlet.sleep(0.5)
                        except Exception:
                            print("Lỗi có ở đây không")
                            pass

                    check = False
                    check_seen = False
                    check_add_friend = False
                    if d(text="Đã gửi lời mời kết bạn").exists:
                        friend_or_not = "added"
                        print("Đã gửi lời mời kết bạn")
                    else:
                        btn = d(
                            resourceId="com.zing.zalo:id/tv_function_privacy")
                        kb = d.xpath('//*[@text="Kết bạn"]')
                        if btn.exists or kb.exists:
                            print("chưa kết bạn")
                            friend_or_not = "no"
                        else:
                            friend_or_not = "yes"
                            print("Dẫ kết bạn")

                    for id in range(len(list_prior_chat_boxes)):
                        if list_prior_chat_boxes[id]['name'] == name:
                            if list_prior_chat_boxes[id]['status'] == 'unseen':
                                check_seen = True
                            list_prior_chat_boxes[id]['status'] = 'seen'
                            if 'friend_or_not' not in list_prior_chat_boxes[id].keys():
                                check_add_friend = True
                                list_prior_chat_boxes[id]['friend_or_not'] = friend_or_not
                            else:
                                if list_prior_chat_boxes[id]['friend_or_not'] != friend_or_not:
                                    list_prior_chat_boxes[id]['friend_or_not'] = friend_or_not
                                    check_add_friend = True
                            check = True
                            # check_add_friend = True
                            break

                    ck_add_or_dl = False

                    if friend_or_not == 'yes':
                        fr_name = [l['name'] for l in list_friend]
                        if name not in fr_name:
                            ava = ""
                            if name in dict_new_friend[num_phone_zalo].keys:
                                ava = dict_new_friend[num_phone_zalo][name]['ava']
                            list_friend.append(
                                {"name": name, "ava": ava, "phone": num_send_phone_zalo})
                            ck_add_or_dl = True
                    elif friend_or_not == 'no':
                        for id in range(len(list_friend)):
                            if list_friend[id]['name'] == name:
                                del list_friend[id]
                                ck_add_or_dl = True

                    if not check:
                        if name in dict_new_friend[num_phone_zalo].keys:
                            if friend_or_not == 'added':
                                dict_new_friend[num_phone_zalo][name]['friend_or_not'] = "added"

                    if not check or check_seen or check_add_friend or ck_add_or_dl:
                        if ck_add_or_dl:
                            data_update = {"num_phone_zalo": num_phone_zalo,
                                           "list_prior_chat_boxes": list_prior_chat_boxes, "list_friend": list_friend}
                        else:
                            data_update = {"num_phone_zalo": num_phone_zalo,
                                           "list_prior_chat_boxes": list_prior_chat_boxes}
                        update_base_document_json(
                            "D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "num_phone_zalo", f"Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}", data_update)
                else:
                    for id in range(len(list_prior_chat_boxes)):
                        if list_prior_chat_boxes[id]['name'] == name:
                            if list_prior_chat_boxes[id]['status'] == 'unseen':
                                list_prior_chat_boxes[id]['status'] = 'seen'
                                data_update = {"num_phone_zalo": num_phone_zalo,
                                               "list_prior_chat_boxes": list_prior_chat_boxes}
                                update_base_document_json(
                                    "D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "num_phone_zalo", f"Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}", data_update)
                            break

                join_room(room)
                # emit("receive status",{"status":"Cuộc hội thoại bắt đầu"}, room=room)
                print("Cuộc hội thoại bắt đầu")
                emit("receive_chat_view_status", {
                    "status": "Cuộc hội thoại bắt đầu"}, room=room)
        except Exception as e:
            print(e)
            dict_status_zalo[num_phone_zalo] = ""
            del dict_queue_device[id_device][0]
            if len(dict_queue_device[id_device]) == 0:
                dict_id_chat[id_device] = ""
            return False

        dict_status_zalo[num_phone_zalo] = ""
        del dict_queue_device[id_device][0]
        if len(dict_queue_device[id_device]) == 0:
            dict_id_chat[id_device] = ""

        dict_status_update_pvp[num_phone_zalo] = 2
        handle_chat_view(d, num_phone_zalo)
        return True


@socketio.on('send_message_chat_pvp')
def handle_send_message_chat_pvp(data):
    list_socket_call.append("send_message_chat_pvp")
#    room = data["id_chat"]
    room = data['id_chat']
    num_phone_zalo = data['num_phone_zalo']
    # num_send_phone_zalo = data['num_send_phone_zalo']
    message = data['message']
    name = data['name']
    type = data['type']
    name_card = data['name_card']
    num_phone_card = data['num_phone_card']
    # mime = data['mime']
    image_data = data['image_data']
    file_size = data['file_size']
    file_data = data['file_data']
    file_type = data['file_type']
    file_name = data['file_name']
    # global now_phone_zalo
    # now_phone_zalo = num_phone_zalo
    global num_message
    global max_message_per_day
    global image_number
    with open(f'D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/device_status_{dict_phone_device[num_phone_zalo]}.json', 'r') as f:
        device_status = json.load(f)

#    ava = data['ava']
    # dict_status_update_pvp[num_phone_zalo] = 1
    docs = get_base_id_zalo_json("D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "num_phone_zalo", f"Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}", {
                                 "num_phone_zalo": num_phone_zalo})
    if len(docs) > 0:
        document = docs[0]
        id_device = document['id_device']
        now_phone_zalo[id_device] = num_phone_zalo
    else:
        print("không có đâu")

# docs giờ là một list chứa mọi document tìm được
    id_device = document['id_device']
    if dict_id_chat[id_device] != "" and dict_id_chat[id_device] != room:
        join_room(room)
        emit("receive_device_status", {
            "status": "Thiết bị đang có người sử dụng", 'name_ntd': name}, room=room)
        return False
    dict_id_chat[id_device] = room
    dict_process_id[id_device] += 1
    id_process = dict_process_id[id_device]
    dict_queue_device[id_device].append(id_process)
    user_name = document['name']
    list_prior_chat_boxes = document['list_prior_chat_boxes']
    list_friend = document['list_friend']
    # d = u2.connect(id_device)
    for id in range(len(device_status['max_message_per_day'])):
        if num_phone_zalo in device_status['max_message_per_day'][id].keys():
            if device_status['max_message_per_day'][id][num_phone_zalo] <= 0:
                join_room(room)
                emit(
                    "receive status", {"status": "Đã đạt giới hạn nhắn tin một ngày"}, room=room)
                return False

    if num_message >= max_message_per_day:
        emit(
            "limit", {"status": "Đã đạt giới hạn nhắn tin một ngày"}, room=room)
        return False
    if (num_phone_zalo in dict_status_zalo.keys()):
        docs = get_base_id_zalo_json(
            "D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "id_device", f"Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}", {"id_device": id_device})
        check_busy = False
        doc_phone_zalo = []
        for doc in docs:
            if doc['num_phone_zalo'] != "":
                doc_phone_zalo.append(doc['num_phone_zalo'])
                if dict_status_zalo[doc['num_phone_zalo']] != "":
                    check_busy = True
                    break
        current_id_process = dict_queue_device[id_device][0]
        while check_busy or current_id_process != id_process:
            check_busy = False
            for phone in doc_phone_zalo:
                if dict_status_zalo[phone] != "":
                    check_busy = True
            current_id_process = dict_queue_device[id_device][0]
            eventlet.sleep(0.1)

        dict_status_update_pvp[num_phone_zalo] = 1

        dict_status_zalo[num_phone_zalo] = "send_message_chat_pvp"
        # with open(f'D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/device_status_{dict_phone_device[num_phone_zalo]}.json', 'r') as f:
        #    device_status = json.load(f)
        try:
            try:
                d = u2.connect(id_device)
                device_connect[id_device] = True
            except Exception as e:
                print("Thiết bị đã ngắt kết nối ", id_device)
                device_connect[id_device] = False
                join_room(room)
                emit("receive_device_status", {
                    "status": "Thiết bị đã ngắt kết nối", 'name_ntd': name}, room=room)
                dict_status_zalo[num_phone_zalo] = ""
                del dict_queue_device[id_device][0]
                if len(dict_queue_device[id_device]) == 0:
                    dict_id_chat[id_device] = ""
                return False
            if not device_status['active'] or (not d(resourceId="com.zing.zalo:id/txtTitle").exists and not d(resourceId="com.zing.zalo:id/action_bar_title").exists):
                device_status['active'] = True
                with open(f"D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/device_status_{dict_phone_device[num_phone_zalo]}.json", 'w') as f:
                    json.dump(device_status, f, indent=4)
                eventlet.sleep(0.1)
                try:
                    d.app_start("com.zing.zalo", stop=True)
                    while not d(resourceId="com.zing.zalo:id/maintab_message").exists:
                        eventlet.sleep(0.05)
                except Exception as e:
                    print("Thiết bị đã ngắt kết nối ", id_device)
                    device_connect[id_device] = False
                    join_room(room)
                    emit("receive_device_status", {
                        "status": "Thiết bị đã ngắt kết nối", 'name_ntd': name}, room=room)
                    dict_status_zalo[num_phone_zalo] = ""
                    del dict_queue_device[id_device][0]
                    if len(dict_queue_device[id_device]) == 0:
                        dict_id_chat[id_device] = ""
                    return False

                # eventlet.sleep(1.0)
                try:
                    items = d.xpath(
                        "//android.widget.FrameLayout[@text!='']").all()
                    check_ex = False
                    for item in items:
                        raw = item.text
                        lines = [l for l in raw.split(
                            "\n") if l.strip()]
                        if lines[0] == name:
                            item.click()
                            check_ex = True
                            break
                    if not check_ex:
                        try:
                            d(text="Tìm kiếm").click_exists(
                                timeout=1)
                        except Exception as e:
                            print(e)
                            dict_status_zalo[num_phone_zalo] = ""
                            del dict_queue_device[id_device][0]
                            if len(dict_queue_device[id_device]) == 0:
                                dict_id_chat[id_device] = ""
                            emit("receive_chat_view_status", {
                                "status": "Mở chat thất bại, hãy thử mở lại", "name_ntd": name}, room=room)
                            return False

                        d.send_keys(f"{name}", clear=True)
                        eventlet.sleep(0.15)

                        try:
                            chat_list = d.xpath(
                                '//*[@resource-id="com.zing.zalo:id/btn_search_result"]').all()

                            ck_ex = False
                            # if len(chat_num) > 0:
                            for item in chat_list:
                                raw = item.text
                                print("Raw là", raw)
                                lines = [l for l in raw.split(
                                    "\n") if l.strip()]
                                print(lines)
                                if lines[0] == name:
                                    print("Có trùng khớp")
                                    item.click()
                                    ck_ex = True
                                    break
                            eventlet.sleep(0.5)
                            if not ck_ex:
                                for id in range(len(list_prior_chat_boxes)):
                                    if list_prior_chat_boxes[id]['name'] == name:
                                        del list_prior_chat_boxes[id]
                                        data_update = {"num_phone_zalo": num_phone_zalo,
                                                       "list_prior_chat_boxes": list_prior_chat_boxes}
                                        update_base_document_json(
                                            "D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "num_phone_zalo", f"Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}", data_update)
                                        break

                                for id in range(len(list_friend)):
                                    if list_friend[id]['name'] == name:
                                        del list_friend[id]
                                        data_update = {"num_phone_zalo": num_phone_zalo,
                                                       "list_friend": list_friend}
                                        update_base_document_json(
                                            "D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "num_phone_zalo", f"Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}", data_update)
                                        break

                                emit("receive status", {
                                    "status": "Tài khoản không tồn tại"}, room=room)
                                dict_status_zalo[num_phone_zalo] = ""
                                del dict_queue_device[id_device][0]
                                if len(dict_queue_device[id_device]) == 0:
                                    dict_id_chat[id_device] = ""
                                print("Tài khoản không tồn tại")
                                return False

                        except Exception:
                            emit("receive status", {
                                "status": "Tài khoản không tồn tại"}, room=room)
                            dict_status_zalo[num_phone_zalo] = ""
                            del dict_queue_device[id_device][0]
                            if len(dict_queue_device[id_device]) == 0:
                                dict_id_chat[id_device] = ""
                            print("Có lỗi à cậu")
                            return False

                        try:
                            btn = d(
                                resourceId="com.zing.zalo:id/btn_send_message")
                            if btn.exists:
                                btn.click()
                                eventlet.sleep(0.5)
                            if d(textContains="Đóng").exists:
                                d(textContains="Đóng").click()
                                eventlet.sleep(0.5)
                        except Exception:
                            print("Lỗi có ở đây không")
                            pass
                except Exception as e:
                    print(e)
                # if d is None:

            if type == 'image':
                avatar = base64.b64decode(image_data)
                current_name = image_number
                with open(f'D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/image{image_number}.jpg', 'wb') as f:
                    f.write(avatar)
                print(
                    f"Đã lưu vào thư mục Zalo_base/image{image_number}.jpg")
                d.push(f'D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/image{image_number}.jpg',
                       f'/sdcard/Download/image{image_number}.jpg')
                # d.push('D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/image.jpg', f'/sdcard/DCIM/Zalo/image{image_number}.jpg')
                # d.push('D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/image.jpg', f'/sdcard/Pictures/Zalo/image{image_number}.jpg')
                eventlet.sleep(0.1)
                image_number -= 1
                '''
                    if id_device in special_device:
                        try:
                           d(resourceId="com.zing.zalo:id/new_chat_input_btn_show_gallery").click_exists(timeout=3)
                           d.xpath('//*[@resource-id="com.zing.zalo:id/recycler_view"]/android.widget.FrameLayout[2]').click_exists(timeout=3)
                           #d(resourceId="com.zing.zalo:id/new_chat_input_btn_send_media").click_exists(timeout=3)
                           d(resourceId="com.zing.zalo:id/landing_page_layout_send").click_exists(timeout=3)
                        except Exception as e:
                            print(e)
                            dict_status_zalo[num_phone_zalo] = ""
                            emit("receive_send_message_status", {
                            "status": "Gửi tin nhắn thất bại",  "name_ntd": name, "message": message}, room=room)
                            return False 
                    '''
                # if id_device in special_device:

                target_file_name = f'image{current_name}.jpg'

                try:
                    d(resourceId="com.zing.zalo:id/new_chat_input_btn_attach").click()
                    d.xpath('//*[@text="Tài liệu"]').click()
                    eventlet.sleep(1)

                    # Điều hướng đến thư mục Tệp đã tải xuống
                    if d.xpath('//*[@text="Gần đây"]').exists:
                        d(description="Hiển thị gốc").click_exists(timeout=3)
                        eventlet.sleep(0.2)
                        
                        # Ưu tiên click vào mục Tệp đã tải xuống bằng nhiều cách định danh
                        download_folder = d(text="Tệp đã tải xuống")
                        if download_folder.exists:
                            download_folder.click()
                        else:
                            # Nếu không thấy text, thử bằng resource-id của Android System
                            d(resourceId="android:id/title", text="Tệp đã tải xuống").click_exists(timeout=3)
                    
                    eventlet.sleep(0.5)

                    # --- PHẦN QUAN TRỌNG: CHỌN ĐÚNG FILE, BỎ QUA THƯ MỤC ---
                    # Tìm đúng file có tên là imageX.jpg
                    try:
                        # 1. Xác định tên file đầy đủ (phải khớp với tên bạn đã d.push lên điện thoại)
                        # Giả sử bạn lưu là image1.jpg, image2.jpg...
                        target_file_name = f"image{image_number}.jpg" 
                        
                        # 2. Tìm file có tên chính xác trong danh sách hiển thị
                        file_element = d(text=target_file_name)
                        
                        if file_element.exists(timeout=5):
                            file_element.click()
                            print(f"Đã chọn đúng file: {target_file_name}")
                        else:
                            # 3. Nếu không thấy, thử dùng scroll để tìm (phòng trường hợp file nằm ở dưới)
                            print(f"Không thấy trực tiếp, đang cuộn tìm file: {target_file_name}")
                            if d(scrollable=True).exists:
                                d(scrollable=True).scroll.to(text=target_file_name)
                            
                            # Thử lại bằng XPath sau khi cuộn hoặc tìm kiếm chứa chuỗi tên file
                            file_xpath = d.xpath(f'//*[contains(@text, "{target_file_name}")]')
                            if file_xpath.exists:
                                file_xpath.click()
                                print(f"Đã click vào file qua XPath: {target_file_name}")
                            else:
                                print(f"Không tìm thấy file {target_file_name} trên màn hình điện thoại")
                                raise Exception("FileNotFoundOnDevice")

                    except Exception as e:
                        print(f"Lỗi khi chọn file: {e}")
                except Exception as e:
                    print(e)
                    dict_status_zalo[num_phone_zalo] = ""
                    del dict_queue_device[id_device][0]
                    if len(dict_queue_device[id_device]) == 0:
                        dict_id_chat[id_device] = ""
                    emit("receive_send_message_status", {
                        "status": "Gửi tin nhắn thất bại",  "name_ntd": name, "message": message}, room=room)
                    return False
            elif type == 'file':
                file_decode = base64.b64decode(file_data)
                # Lưu file vào thư mục local tương tự như image
                with open(f'D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/{file_name}', 'wb') as f:
                    f.write(file_decode)
                print(f"Đã lưu vào thư mục Zalo_base/{file_name}")

                # Push file lên điện thoại
                d.push(f'D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/{file_name}',
                       f'/sdcard/Download/{file_name}')
                
                eventlet.sleep(0.1)

                try:
                    # Bắt đầu thao tác UI giống hệt hàm gửi ảnh
                    d(resourceId="com.zing.zalo:id/new_chat_input_btn_attach").click()
                    d.xpath('//*[@text="Tài liệu"]').click()
                    eventlet.sleep(1)

                    # Điều hướng tìm thư mục Download
                    if d.xpath('//*[@text="Gần đây"]').exists:
                        d(description="Hiển thị gốc").click_exists(timeout=3)
                        eventlet.sleep(0.2)
                        td = d.xpath('//*[@text="Tệp đã tải xuống"]')
                        if td.exists:
                            td.click()
                            eventlet.sleep(0.1)
                        else:
                            if d(resourceId="android:id/title", text="Tệp đã tải xuống").exists:
                                d(resourceId="android:id/title", text="Tệp đã tải xuống").click()
                            else:
                                d.xpath('//*[@resource-id="com.google.android.documentsui:id/roots_list"]/android.widget.LinearLayout[4]').click_exists(timeout=3)
                        eventlet.sleep(0.1)

                    # Tìm và chọn file theo đúng tên file_name
                    target_file = d(text=file_name)
                    if target_file.exists(timeout=5):
                        target_file.click()
                        print(f"Đã chọn đúng file: {file_name}")
                    else:
                        # Cuộn tìm nếu file nằm ở dưới (giống logic image)
                        if d(scrollable=True).exists:
                            d(scrollable=True).scroll.to(text=file_name)
                        
                        file_xpath = d.xpath(f'//*[contains(@text, "{file_name}")]')
                        if file_xpath.exists:
                            file_xpath.click()
                        else:
                            print(f"Không tìm thấy file {file_name}")
                            raise Exception("FileNotFoundOnDevice")

                except Exception as e:
                    print(e)
                    dict_status_zalo[num_phone_zalo] = ""
                    del dict_queue_device[id_device][0]
                    if len(dict_queue_device[id_device]) == 0:
                        dict_id_chat[id_device] = ""
                    emit("receive_send_message_status", {
                        "status": "Gửi file thất bại", "name_ntd": name, "message": message}, room=room)
                    return False
                file_decode = base64.b64decode(file_data)
                local_path = f'D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/{file_name}'
                remote_path = f'/sdcard/Download/{file_name}'

                # 1. Lưu file local và đẩy vào điện thoại
                with open(local_path, 'wb') as f:
                    f.write(file_decode)
                
                d.push(local_path, remote_path)
                print(f"Đã push file {file_name} vào Download")
                
                # Ép điện thoại quét lại thư viện để thấy file mới ngay lập tức
                d.shell(f"am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE -d file://{remote_path}")
                eventlet.sleep(0.5)

                try:
                    # 2. Mở menu đính kèm và chọn Tài liệu
                    d(resourceId="com.zing.zalo:id/new_chat_input_btn_attach").click()
                    if d(text="Tài liệu").wait(timeout=3):
                        d(text="Tài liệu").click()
                    else:
                        # Nếu không tìm thấy text, dùng tọa độ dự phòng (khoảng giữa màn hình phía dưới)
                        d.click(0.5, 0.8) 
                    eventlet.sleep(1)

                    # 3. Điều hướng trong trình chọn file của Android (DocumentsUI)
                    # Bấm vào menu "Gạch ngang" để chọn thư mục nếu cần
                    if d(description="Hiển thị gốc").exists:
                        d(description="Hiển thị gốc").click()
                        eventlet.sleep(0.5)
                        
                    # Tìm và chọn thư mục "Tải xuống" hoặc "Download" bằng text (Thay cho LinearLayout[4])
                    target_folder = d(textMatches="(?i)Tải xuống|Download")
                    if target_folder.exists:
                        target_folder.click()
                        eventlet.sleep(0.5)

                    # 4. Tìm đúng file theo tên (Đây là phần quan trọng nhất)
                    # Thay thế hoàn toàn các CardView[1], CardView[2] bằng file_name thực tế
                    target_file = d(text=file_name)
                    
                    if target_file.wait(timeout=5):
                        target_file.click()
                        print(f"Đã chọn file: {file_name}")
                    else:
                        # Nếu file nằm sâu phía dưới, thực hiện cuộn để tìm
                        print(f"Đang cuộn tìm file: {file_name}")
                        d(scrollable=True).scroll.to(text=file_name)
                        d(text=file_name).click()

                    # 5. Xác nhận gửi nếu Zalo yêu cầu (tùy phiên bản)
                    btn_send = d(resourceId="com.zing.zalo:id/landing_page_layout_send")
                    if btn_send.exists(timeout=3):
                        btn_send.click()

                except Exception as e:
                    print(f"Lỗi khi gửi file: {e}")
                    dict_status_zalo[num_phone_zalo] = ""
                    # Dọn dẹp queue nếu lỗi
                    if id_device in dict_queue_device and dict_queue_device[id_device]:
                        del dict_queue_device[id_device][0]
                    
                    emit("receive_send_message_status", {
                        "status": "Gửi file thất bại", 
                        "name_ntd": name, 
                        "message": str(e)
                    }, room=room)
                    return False
                file_decode = base64.b64decode(file_data)

                with open(f'D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/{file_name}', 'wb') as f:
                    f.write(file_decode)
                d.push(f'D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/{file_name}',
                       f'/sdcard/Download/{file_name}')
                # d.push('D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/image.jpg', f'/sdcard/DCIM/Zalo/image{image_number}.jpg')
                # d.push('D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/image.jpg', f'/sdcard/Pictures/Zalo/image{image_number}.jpg')
                eventlet.sleep(0.1)
                # image_number -= 1
                try:
                    d(resourceId="com.zing.zalo:id/new_chat_input_btn_attach").click()
                    d.xpath(
                        '//*[@text="Tài liệu"]').click()
                    eventlet.sleep(1)

                    if d.xpath('//*[@text="Gần đây"]').exists:
                        d(description="Hiển thị gốc").click_exists(
                            timeout=3)
                        eventlet.sleep(0.2)
                        td = d.xpath('//*[@text="Tệp đã tải xuống"]')
                        if td.exists:
                            td.click()
                            eventlet.sleep(0.1)
                        else:
                            if d(resourceId="android:id/title", text="Tệp đã tải xuống").exists:
                                d(resourceId="android:id/title",
                                  text="Tệp đã tải xuống").click()
                                # eventlet.sleep(0.1)
                            else:
                                d.xpath(
                                    '//*[@resource-id="com.google.android.documentsui:id/roots_list"]/android.widget.LinearLayout[4]').click_exists(timeout=3)
                        eventlet.sleep(0.1)
                    click_btn = d.xpath(
                        '//*[@resource-id="com.google.android.documentsui:id/dir_list"]/androidx.cardview.widget.CardView[1]/androidx.cardview.widget.CardView[1]/android.widget.RelativeLayout[1]/android.widget.FrameLayout[1]')
                    if click_btn.exists:
                        click_btn.click_exists(timeout=0.1)
                        eventlet.sleep(0.1)
                    btn1 = d.xpath(
                        '//*[@resource-id="com.google.android.documentsui:id/dir_list"]/android.widget.LinearLayout[1]/android.widget.LinearLayout[1]')
                    btn2 = d(
                        resourceId="com.google.android.documentsui:id/item_root")
                    if btn1.exists:
                        btn1.click()
                    elif btn2.exists:
                        btn2.click()
                    if d(resourceId="com.zing.zalo:id/chatinput_text").exists:
                        d(resourceId="com.zing.zalo:id/new_chat_input_btn_attach").click()
                        # pass
                    else:
                        # d.xpath('//*[@resource-id="com.google.android.documentsui:id/dir_list"]/androidx.cardview.widget.CardView[1]').click_exists(timeout=3)
                        d.xpath(
                            '//*[@resource-id="com.google.android.documentsui:id/dir_list"]/androidx.cardview.widget.CardView[2]').click_exists(timeout=3)
                        eventlet.sleep(0.1)
                        d(resourceId="com.zing.zalo:id/new_chat_input_btn_attach").click()
                except Exception as e:
                    print(e)
                    dict_status_zalo[num_phone_zalo] = ""
                    del dict_queue_device[id_device][0]
                    if len(dict_queue_device[id_device]) == 0:
                        dict_id_chat[id_device] = ""
                    emit("receive_send_message_status", {
                        "status": "Gửi tin nhắn thất bại",  "name_ntd": name, "message": message}, room=room)
                    return False

                file_decode = base64.b64decode(file_data)

                with open(f'D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/{file_name}', 'wb') as f:
                    f.write(file_decode)
                d.push(f'D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/{file_name}',
                       f'/sdcard/Download/{file_name}')
                # d.push('D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/image.jpg', f'/sdcard/DCIM/Zalo/image{image_number}.jpg')
                # d.push('D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/image.jpg', f'/sdcard/Pictures/Zalo/image{image_number}.jpg')
                eventlet.sleep(0.1)
                # image_number -= 1
                try:
                    d(resourceId="com.zing.zalo:id/new_chat_input_btn_attach").click()
                    d.xpath(
                        '//*[@text="Tài liệu"]').click()
                    eventlet.sleep(1)

                    if d.xpath('//*[@text="Gần đây"]').exists:
                        d(description="Hiển thị gốc").click_exists(
                            timeout=3)
                        eventlet.sleep(0.2)
                        td = d.xpath('//*[@text="Tệp đã tải xuống"]')
                        if td.exists:
                            td.click()
                            eventlet.sleep(0.1)
                        else:
                            if d(resourceId="android:id/title", text="Tệp đã tải xuống").exists:
                                d(resourceId="android:id/title",
                                  text="Tệp đã tải xuống").click()
                                # eventlet.sleep(0.1)
                            else:
                                d.xpath(
                                    '//*[@resource-id="com.google.android.documentsui:id/roots_list"]/android.widget.LinearLayout[4]').click_exists(timeout=3)
                        eventlet.sleep(0.1)
                    click_btn = d.xpath(
                        '//*[@resource-id="com.google.android.documentsui:id/dir_list"]/androidx.cardview.widget.CardView[1]/androidx.cardview.widget.CardView[1]/android.widget.RelativeLayout[1]/android.widget.FrameLayout[1]')
                    if click_btn.exists:
                        click_btn.click_exists(timeout=0.1)
                        eventlet.sleep(0.1)
                    btn1 = d.xpath(
                        '//*[@resource-id="com.google.android.documentsui:id/dir_list"]/android.widget.LinearLayout[1]/android.widget.LinearLayout[1]')
                    btn2 = d(
                        resourceId="com.google.android.documentsui:id/item_root")
                    if btn1.exists:
                        btn1.click()
                    elif btn2.exists:
                        btn2.click()
                    if d(resourceId="com.zing.zalo:id/chatinput_text").exists:
                        d(resourceId="com.zing.zalo:id/new_chat_input_btn_attach").click()
                        # pass
                    else:
                        # d.xpath('//*[@resource-id="com.google.android.documentsui:id/dir_list"]/androidx.cardview.widget.CardView[1]').click_exists(timeout=3)
                        d.xpath(
                            '//*[@resource-id="com.google.android.documentsui:id/dir_list"]/androidx.cardview.widget.CardView[2]').click_exists(timeout=3)
                        eventlet.sleep(0.1)
                        d(resourceId="com.zing.zalo:id/new_chat_input_btn_attach").click()
                except Exception as e:
                    print(e)
                    dict_status_zalo[num_phone_zalo] = ""
                    del dict_queue_device[id_device][0]
                    if len(dict_queue_device[id_device]) == 0:
                        dict_id_chat[id_device] = ""
                    emit("receive_send_message_status", {
                        "status": "Gửi tin nhắn thất bại",  "name_ntd": name, "message": message}, room=room)
                    return False

            elif type == 'text':

                try:
                    d(resourceId="com.zing.zalo:id/chatinput_text").click()
                    eventlet.sleep(0.1)
                    d.send_keys(message, clear=True)
                    eventlet.sleep(0.1)
                    d(resourceId="com.zing.zalo:id/new_chat_input_btn_chat_send").click()
                except Exception as e:
                    print(e)
                    dict_status_zalo[num_phone_zalo] = ""
                    del dict_queue_device[id_device][0]
                    if len(dict_queue_device[id_device]) == 0:
                        dict_id_chat[id_device] = ""
                    emit("receive_send_message_status", {
                        "status": "Gửi tin nhắn thất bại",  "name_ntd": name, "message": message}, room=room)
                    return False
            elif type == 'card':
                try:
                    d(resourceId="com.zing.zalo:id/new_chat_input_btn_attach").click()
                    eventlet.sleep(0.1)
                    d.xpath('//*[@text="Danh thiếp"]').click()
                    eventlet.sleep(0.1)
                    d(resourceId="com.zing.zalo:id/search_input_text").click()
                    eventlet.sleep(0.1)
                    d.send_keys(name_card, clear=True)
                    eventlet.sleep(0.1)
                    d(resourceId="com.zing.zalo:id/layoutcontact").click()
                    eventlet.sleep(0.1)
                    d(resourceId="com.zing.zalo:id/btn_done_add_item").click()
                    eventlet.sleep(0.1)
                    d(resourceId="com.zing.zalo:id/new_chat_input_btn_attach").click()
                    eventlet.sleep(0.1)
                except Exception as e:
                    print(e)
                    dict_status_zalo[num_phone_zalo] = ""
                    del dict_queue_device[id_device][0]
                    if len(dict_queue_device[id_device]) == 0:
                        dict_id_chat[id_device] = ""
                    emit("receive_send_message_status", {
                        "status": "Gửi tin nhắn thất bại",  "name_ntd": name, "message": message}, room=room)
                    return False

            now = datetime.now()
            print("Ngày:", now.day)
            print("Tháng:", now.month)
            print("Năm:", now.year)
            print("Giờ:", now.hour)
            print("Phút:", now.minute)
            print("Giây:", now.second)
            hour = str(now.hour)
            minute = str(now.hour)
            if len(hour) == 1:
                hour = f"0{hour}"
            if len(minute) == 1:
                minute = f"0{minute}"
            time_str = f"{hour}:{minute} {now.day}/{now.month}/{now.year}"
            eventlet.sleep(1.0)
            join_room(room)
            check = False
            # pick = -1
            for id in range(len(list_prior_chat_boxes)):
                if list_prior_chat_boxes[id]['name'] == name:
                    check = True
                    if 'data_chat_box' not in list_prior_chat_boxes[id].keys():
                        list_prior_chat_boxes[id]['data_chat_box'] = ""
                    if type == 'text':
                        list_prior_chat_boxes[id]['time'] = time_str
                        list_prior_chat_boxes[id]['message'] = message
                        list_prior_chat_boxes[id]['status'] = "seen"
                        data_chat_box = load_data_chat_box_json(
                            list_prior_chat_boxes[id]['data_chat_box'])
                        data_chat_box.append(
                            {"you": [{'time': time_str, 'type': "text", "data": message}]})
                        list_prior_chat_boxes[id]['data_chat_box'] = dump_data_chat_box_json(
                            list_prior_chat_boxes[id]['data_chat_box'], data_chat_box)
                        list_prior_chat_boxes.insert(
                            0, list_prior_chat_boxes.pop(id))
                    elif type == 'image':
                        list_prior_chat_boxes[id]['time'] = time_str
                        list_prior_chat_boxes[id]['message'] = message
                        list_prior_chat_boxes[id]['status'] = "seen"
                        data_chat_box = load_data_chat_box_json(
                            list_prior_chat_boxes[id]['data_chat_box'])
                        data_chat_box.append(
                            {"you": [{'time': time_str, 'type': "image", "data": f"image{image_number+1}.jpg", "image_data": image_data}]})
                        list_prior_chat_boxes[id]['data_chat_box'] = dump_data_chat_box_json(
                            list_prior_chat_boxes[id]['data_chat_box'], data_chat_box)
                        list_prior_chat_boxes.insert(
                            0, list_prior_chat_boxes.pop(id))
                    elif type == 'file':
                        list_prior_chat_boxes[id]['time'] = time_str
                        list_prior_chat_boxes[id]['message'] = message
                        list_prior_chat_boxes[id]['status'] = "seen"
                        data_chat_box = load_data_chat_box_json(
                            list_prior_chat_boxes[id]['data_chat_box'])
                        data_chat_box.append(
                            {"you": [{'time': time_str, 'type': "file", "data": f"[File]\n{file_name}\n{file_size}", "file_data": file_data}]})
                        list_prior_chat_boxes[id]['data_chat_box'] = dump_data_chat_box_json(
                            list_prior_chat_boxes[id]['data_chat_box'], data_chat_box)
                        list_prior_chat_boxes.insert(
                            0, list_prior_chat_boxes.pop(id))
                    elif type == 'card':
                        list_prior_chat_boxes[id]['time'] = time_str
                        list_prior_chat_boxes[id]['message'] = message
                        list_prior_chat_boxes[id]['status'] = "seen"
                        data_chat_box = load_data_chat_box_json(
                            list_prior_chat_boxes[id]['data_chat_box'])
                        data_chat_box.append(
                            {"you": [{'time': time_str, 'type': "card", "data": f"{name_card}\nGọi điện\nNhắn tin", "card_data": {"name_card": name_card, "num_phone_card": num_phone_card}}]})
                        list_prior_chat_boxes[id]['data_chat_box'] = dump_data_chat_box_json(
                            list_prior_chat_boxes[id]['data_chat_box'], data_chat_box)
                        list_prior_chat_boxes.insert(
                            0, list_prior_chat_boxes.pop(id))
                    break

                    # pick = id
            if not check:
                data_chat_box = []
                ava = ""
                phone = ""
                friend_or_not = "no"
                if name in dict_new_friend[num_phone_zalo].keys():
                    ava = dict_new_friend[num_phone_zalo][name]['ava']
                    phone = dict_new_friend[num_phone_zalo][name]['phone']
                    friend_or_not = dict_new_friend[num_phone_zalo][name]['friend_or_not']
                    del dict_new_friend[num_phone_zalo][name]
                if type == 'text':
                    list_prior_chat_boxes.append(
                        {"name": name, "ava": ava, "phone": phone, "friend_or_not": friend_or_not, "time": time_str, "message": message, "status": "seen", "data_chat_box": ""})
                    data_chat_box = load_data_chat_box_json(
                        list_prior_chat_boxes[-1]['data_chat_box'])
                    data_chat_box.append(
                        {"you": [{'time': time_str, 'type': "text", "data": message}]})
                    list_prior_chat_boxes[-1]['data_chat_box'] = dump_data_chat_box_json(
                        list_prior_chat_boxes[-1]['data_chat_box'], data_chat_box)
                    list_prior_chat_boxes.insert(
                        0, list_prior_chat_boxes.pop(-1))
                elif type == 'image':
                    list_prior_chat_boxes.append(
                        {"name": name, "ava": ava, "phone": phone, "friend_or_not": friend_or_not, "time": time_str, "message": f"image{image_number+1}.jpg", "status": "seen", "data_chat_box": ""})
                    data_chat_box = load_data_chat_box_json(
                        list_prior_chat_boxes[-1]['data_chat_box'])
                    data_chat_box.append(
                        {"you": [{'time': time_str, 'type': "image", "data": f"image{image_number+1}.jpg", "image_data": image_data}]})
                    list_prior_chat_boxes[-1]['data_chat_box'] = dump_data_chat_box_json(
                        list_prior_chat_boxes[-1]['data_chat_box'], data_chat_box)
                    list_prior_chat_boxes.insert(
                        0, list_prior_chat_boxes.pop(-1))
                elif type == 'file':
                    list_prior_chat_boxes.append(
                        {"name": name, "ava": ava, "phone": phone, "friend_or_not": friend_or_not, "time": time_str, "message": f"[File]\n{file_name}\n{file_size}", "status": "seen", "data_chat_box": ""})
                    data_chat_box = load_data_chat_box_json(
                        list_prior_chat_boxes[-1]['data_chat_box'])
                    data_chat_box.append(
                        {"you": [{'time': time_str, 'type': "file", "data": f"[File]\n{file_name}\n{file_size}", "file_data": file_data}]})
                    list_prior_chat_boxes[-1]['data_chat_box'] = dump_data_chat_box_json(
                        list_prior_chat_boxes[-1]['data_chat_box'], data_chat_box)
                    list_prior_chat_boxes.insert(
                        0, list_prior_chat_boxes.pop(-1))
                elif type == 'card':
                    list_prior_chat_boxes.append(
                        {"name": name, "ava": ava, "phone": phone, "friend_or_not": friend_or_not, "time": time_str, "message": message, "status": "seen", "data_chat_box": ""})
                    data_chat_box = load_data_chat_box_json(
                        list_prior_chat_boxes[-1]['data_chat_box'])
                    data_chat_box.append(
                        {"you": [{'time': time_str, 'type': "card", "data": message, "card_data": {"name_card": name_card, "num_phone_card": num_phone_card}}]})
                    list_prior_chat_boxes[-1]['data_chat_box'] = dump_data_chat_box_json(
                        list_prior_chat_boxes[-1]['data_chat_box'], data_chat_box)
                    list_prior_chat_boxes.insert(
                        0, list_prior_chat_boxes.pop(-1))

            data_update = {"num_phone_zalo": num_phone_zalo,
                           "list_prior_chat_boxes": list_prior_chat_boxes}
            update_base_document_json(
                "D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "num_phone_zalo", f"Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}", data_update)
            if type == 'card':
                emit("receive status", {
                    "status": "Đã gửi tin nhắn thành công"}, room=room)
            emit('receive_list_prior_chat_box', {
                'user_name': user_name, 'list_prior_chat_boxes': list_prior_chat_boxes, 'status': "Có dữ liệu mới từ khách hàng gửi đến"}, room=room)
        except Exception as e:
            print(e)
            emit("receive status", {
                "status": "Đã gửi tin nhắn thất bại"}, room=room)
            dict_status_zalo[num_phone_zalo] = ""
            del dict_queue_device[id_device][0]
            if len(dict_queue_device[id_device]) == 0:
                dict_id_chat[id_device] = ""
            dict_status_update_pvp[num_phone_zalo] = 2
            handle_chat_view(d, num_phone_zalo)
            return False
        num_message += 1
        with open(f'D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/device_status_{dict_phone_device[num_phone_zalo]}.json', 'r') as f:
            device_status = json.load(f)

        for id in range(len(device_status['max_message_per_day'])):
            if num_phone_zalo in device_status['max_message_per_day'][id].keys():
                device_status['max_message_per_day'][id][num_phone_zalo] -= 1

        with open(f"D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/device_status_{dict_phone_device[num_phone_zalo]}.json", 'w') as f:
            json.dump(device_status, f, indent=4)

        dict_status_zalo[num_phone_zalo] = ""
        dict_status_update_pvp[num_phone_zalo] = 2
        del dict_queue_device[id_device][0]
        if len(dict_queue_device[id_device]) == 0:
            dict_id_chat[id_device] = ""
        # two = time.time()
        # print(two-one)
        handle_chat_view(d, num_phone_zalo)


@socketio.on('share_message_chat_pvp')
def handle_share_message_chat_pvp(data):
    list_socket_call.append("share_message_chat_pvp")
#    room = data["id_chat"]
    room = data['id_chat']
    # print("helloo every one")
    num_phone_zalo = data['num_phone_zalo']
    # num_send_phone_zalo = data['num_send_phone_zalo']
    message = data['message']
    names = data['names']
    name_share = data['name_share']
    name_card = data['name_card']
    num_phone_card = data['num_phone_card']
    extra_message = data['extra_message']
    type = data['type']
    # mime = data['mime']
    image_data = data['image_data']
    file_size = data['file_size']
    file_data = data['file_data']
    file_type = data['file_type']
    file_name = data['file_name']
    # global now_phone_zalo
    # now_phone_zalo = num_phone_zalo
    global num_message
    global max_message_per_day
    global image_number

    with open(f'D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/device_status_{dict_phone_device[num_phone_zalo]}.json', 'r') as f:
        device_status = json.load(f)

    for id in range(len(device_status['max_message_per_day'])):
        if num_phone_zalo in device_status['max_message_per_day'][id].keys():
            if device_status['max_message_per_day'][id][num_phone_zalo] <= 0:
                emit(
                    "limit", {"status": "Đã đạt giới hạn nhắn tin một ngày"}, room=room)
                return False

    if num_message >= max_message_per_day:
        emit(
            "limit", {"status": "Đã đạt giới hạn nhắn tin một ngày"}, room=room)
        return False
#    ava = data['ava']
    # dict_status_update_pvp[num_phone_zalo] = 1
    docs = get_base_id_zalo_json("D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "num_phone_zalo", f"Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}", {
                                 "num_phone_zalo": num_phone_zalo})
    if len(docs) > 0:
        document = docs[0]
        id_device = document['id_device']
    else:
        print("không có đâu")

# docs giờ là một list chứa mọi document tìm được
    id_device = document['id_device']
    if dict_id_chat[id_device] != "" and dict_id_chat[id_device] != room:
        join_room(room)
        emit("receive_device_status", {
            "status": "Thiết bị đang có người sử dụng", 'name_ntd': name}, room=room)
        return False
    dict_id_chat[id_device] = room
    dict_process_id[id_device] += 1
    id_process = dict_process_id[id_device]
    dict_queue_device[id_device].append(id_process)
    user_name = document['name']
    list_prior_chat_boxes = document['list_prior_chat_boxes']
    list_friend = document['list_friend']
    # d = u2.connect(id_device)
    if (num_phone_zalo in dict_status_zalo.keys()):
        docs = get_base_id_zalo_json(
            "D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "id_device", f"Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}", {"id_device": id_device})
        check_busy = False
        doc_phone_zalo = []
        for doc in docs:
            if doc['num_phone_zalo'] != "":
                doc_phone_zalo.append(doc['num_phone_zalo'])
                if dict_status_zalo[doc['num_phone_zalo']] != "":
                    check_busy = True
                    break
        current_id_process = dict_queue_device[id_device][0]
        while check_busy or current_id_process != id_process:
            check_busy = False
            for phone in doc_phone_zalo:
                if dict_status_zalo[phone] != "":
                    check_busy = True
            current_id_process = dict_queue_device[id_device][0]
            eventlet.sleep(0.1)
        dict_status_update_pvp[num_phone_zalo] = 1

        dict_status_zalo[num_phone_zalo] = "share_message_chat_pvp"
#                with open(f'D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/device_status_{dict_phone_device[num_phone_zalo]}.json', 'r') as f:
#                    device_status = json.load(f)
        try:
            d = u2.connect(id_device)
            device_connect[id_device] = True
        except Exception as e:
            print("Thiết bị đã ngắt kết nối ", id_device)
            device_connect[id_device] = False
            join_room(room)
            emit("receive_device_status", {
                "status": "Thiết bị đã ngắt kết nối", 'name_ntd': name}, room=room)
            dict_status_zalo[num_phone_zalo] = ""
            del dict_queue_device[id_device][0]
            if len(dict_queue_device[id_device]) == 0:
                dict_id_chat[id_device] = ""
            return False
        try:

            if not device_status['active']:
                device_status['active'] = True
                with open(f"D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/device_status_{dict_phone_device[num_phone_zalo]}.json", 'w') as f:
                    json.dump(device_status, f, indent=4)
                eventlet.sleep(0.1)
                d.app_start("com.zing.zalo", stop=True)
                while not d(resourceId="com.zing.zalo:id/maintab_message").exists:
                    eventlet.sleep(0.05)

            if type == 'image':
                avatar = base64.b64decode(image_data)
                with open(f'D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/image{image_number}.jpg', 'wb') as f:
                    f.write(avatar)
                print(
                    f"Đã lưu vào thư mục Zalo_base/image{image_number}.jpg")
                d.push(f'D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/image{image_number}.jpg',
                       f'/sdcard/Download/image{image_number}.jpg')
            elif type == 'file':
                file_decode = base64.b64decode(file_data)
                with open(f'D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/{file_name}', 'wb') as f:
                    f.write(file_decode)
                d.push(f'D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/{file_name}',
                       f'/sdcard/Download/{file_name}')
            for name in names:

                on_chat = False
                if d(resourceId="com.zing.zalo:id/action_bar_title").exists:
                    btn = d(resourceId="com.zing.zalo:id/action_bar_title")
                else:
                    btn = d(resourceId="com.zing.zalo:id/txtTitle")
                if btn.exists:
                    current_ntd = btn.get_text()

                    if current_ntd == name:
                        on_chat = True
                    else:
                        try:
                            # d.press("back")
                            # if d(resourceId="com.zing.zalo:id/txtTitle").exists:
                            #    d.press("back")
                            while not d.xpath('//*[@text="Ưu tiên"]').exists:
                                d.press("back")
                                eventlet.sleep(0.1)
                        except Exception as e:
                            dict_status_zalo[num_phone_zalo] = ""
                            del dict_queue_device[id_device][0]
                            if len(dict_queue_device[id_device]) == 0:
                                dict_id_chat[id_device] = ""
                            print(e)
                            return False
                if not on_chat:
                    try:
                        items = d.xpath(
                            "//android.widget.FrameLayout[@text!='']").all()
                        check_ex = False
                        for item in items:
                            raw = item.text
                            lines = [l for l in raw.split(
                                "\n") if l.strip()]
                            if lines[0] == name:
                                item.click()
                                check_ex = True
                                break
                        if not check_ex:
                            while not d.xpath('//*[@text="Ưu tiên"]').exists:
                                if d.xpath('//*[@text="Zalo"]').exists:
                                    try:
                                        d.xpath(
                                            '//*[@text="Zalo"]').click()
                                        eventlet.sleep(1.5)
                                    except Exception:
                                        pass
                                else:
                                    d.press("back")

                            try:
                                d(text="Tìm kiếm").click()

                            except Exception as e:
                                print(e)
                                dict_status_zalo[num_phone_zalo] = ""
                                del dict_queue_device[id_device][0]
                                if len(dict_queue_device[id_device]) == 0:
                                    dict_id_chat[id_device] = ""
                                emit("receive_chat_view_status", {
                                    "status": "Mở chat thất bại, hãy thử mở lại", "name_ntd": name}, room=room)
                                return False
                            # time.sleep(1.0)
                            d.send_keys(f"{name}", clear=True)
                            eventlet.sleep(0.15)

                            try:
                                ck_ex = False
                                chat_list = d.xpath(
                                    '//*[@resource-id="com.zing.zalo:id/btn_search_result"]').all()

                                # if len(chat_num) > 0:
                                for item in chat_list:
                                    raw = item.text
                                    print("Raw là", raw)
                                    lines = [l for l in raw.split(
                                        "\n") if l.strip()]
                                    print(lines)
                                    if lines[0] == name:
                                        print("Có trùng khớp")
                                        item.click()
                                        ck_ex = True
                                        break
                                eventlet.sleep(1.0)
                                if not ck_ex:
                                    for id in range(len(list_prior_chat_boxes)):
                                        if list_prior_chat_boxes[id]['name'] == name:
                                            del list_prior_chat_boxes[id]
                                            data_update = {"num_phone_zalo": num_phone_zalo,
                                                           "list_prior_chat_boxes": list_prior_chat_boxes}
                                            update_base_document_json(
                                                "D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "num_phone_zalo", f"Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}", data_update)
                                            break

                                    for id in range(len(list_friend)):
                                        if list_friend[id]['name'] == name:
                                            del list_friend[id]
                                            data_update = {"num_phone_zalo": num_phone_zalo,
                                                           "list_friend": list_friend}
                                            update_base_document_json(
                                                "D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "num_phone_zalo", f"Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}", data_update)
                                            break
                                    continue

                            except Exception:
                                emit("receive status", {
                                    "status": "Tài khoản không tồn tại"}, room=room)
                                dict_status_zalo[num_phone_zalo] = ""
                                del dict_queue_device[id_device][0]
                                if len(dict_queue_device[id_device]) == 0:
                                    dict_id_chat[id_device] = ""
                                print("Có lỗi à cậu")
                                return False

                            try:
                                btn = d(
                                    resourceId="com.zing.zalo:id/btn_send_message")
                                if btn.exists:
                                    btn.click()
                                    eventlet.sleep(1.0)
                                if d(textContains="Đóng").exists:
                                    d(textContains="Đóng").click()
                                    eventlet.sleep(0.5)
                            except Exception:
                                print("Lỗi có ở đây không")
                                pass
                    except Exception as e:
                        print(e)

                    check = False
                    check_seen = False
                    check_add_friend = False
                    if d(text="Đã gửi lời mời kết bạn").exists:
                        friend_or_not = "added"
                    else:
                        btn = d(
                            resourceId="com.zing.zalo:id/tv_function_privacy")
                        kb = d.xpath('//*[@text="Kết bạn"]')
                        if btn.exists or kb.exists:
                            print("chưa kết bạn")
                            friend_or_not = "no"
                        else:
                            friend_or_not = "yes"

                    for id in range(len(list_prior_chat_boxes)):
                        if list_prior_chat_boxes[id]['name'] == name:
                            if list_prior_chat_boxes[id]['status'] == 'unseen':
                                check_seen = True
                            list_prior_chat_boxes[id]['status'] = 'seen'
                            if 'friend_or_not' not in list_prior_chat_boxes[id].keys():
                                check_add_friend = True
                                list_prior_chat_boxes[id]['friend_or_not'] = friend_or_not
                            else:
                                if list_prior_chat_boxes[id]['friend_or_not'] != friend_or_not:
                                    list_prior_chat_boxes[id]['friend_or_not'] = friend_or_not
                                    check_add_friend = True
                            check = True
                            # check_add_friend = True
                            break

                    if not check:
                        list_prior_chat_boxes.append(
                            {"name": name, "phone": "", "time": "", "message": "", "status": "seen",  "friend_or_not": friend_or_not, "data_chat_box": ""})

                else:
                    for id in range(len(list_prior_chat_boxes)):
                        if list_prior_chat_boxes[id]['name'] == name:
                            if list_prior_chat_boxes[id]['status'] == 'unseen':
                                list_prior_chat_boxes[id]['status'] = 'seen'

                            break

                if type == 'image':

                    image_number -= 1

                    try:
                      # if id_device not in special_device:
                        d(resourceId="com.zing.zalo:id/new_chat_input_btn_attach").click()
                        d.xpath(
                            '//*[@text="Tài liệu"]').click()
                        eventlet.sleep(1)

                        if d.xpath('//*[@text="Gần đây"]').exists:
                            d(description="Hiển thị gốc").click_exists(
                                timeout=3)
                            eventlet.sleep(0.2)
                            td = d.xpath(
                                '//*[@text="Tệp đã tải xuống"]')
                            if td.exists:
                                td.click()
                                eventlet.sleep(0.1)
                            else:
                                if d(resourceId="android:id/title", text="Tệp đã tải xuống").exists:
                                    d(resourceId="android:id/title",
                                      text="Tệp đã tải xuống").click()
                                    # eventlet.sleep(0.1)
                                else:
                                    d.xpath(
                                        '//*[@resource-id="com.google.android.documentsui:id/roots_list"]/android.widget.LinearLayout[4]').click_exists(timeout=3)
                            eventlet.sleep(0.1)
                        click_btn = d.xpath(
                            '//*[@resource-id="com.google.android.documentsui:id/dir_list"]/androidx.cardview.widget.CardView[1]/androidx.cardview.widget.CardView[1]/android.widget.RelativeLayout[1]/android.widget.FrameLayout[1]')
                        if click_btn.exists:
                            click_btn.click_exists(timeout=0.1)
                            eventlet.sleep(0.1)
                        btn1 = d.xpath(
                            '//*[@resource-id="com.google.android.documentsui:id/dir_list"]/android.widget.LinearLayout[1]/android.widget.LinearLayout[1]')
                        btn2 = d(
                            resourceId="com.google.android.documentsui:id/item_root")
                        if btn1.exists:
                            btn1.click()
                            eventlet.sleep(0.1)
                        elif btn2.exists:
                            btn2.click()
                            eventlet.sleep(0.1)
                        if d(resourceId="com.zing.zalo:id/chatinput_text").exists:
                            d(resourceId="com.zing.zalo:id/new_chat_input_btn_attach").click_exists(
                                timeout=1)
                            # pass
                        else:
                            # d.xpath('//*[@resource-id="com.google.android.documentsui:id/dir_list"]/androidx.cardview.widget.CardView[1]').click_exists(timeout=3)
                            d.xpath(
                                '//*[@resource-id="com.google.android.documentsui:id/dir_list"]/androidx.cardview.widget.CardView[2]').click_exists(timeout=3)
                            eventlet.sleep(0.1)
                            d(resourceId="com.zing.zalo:id/new_chat_input_btn_attach").click_exists(
                                timeout=1)
                    except Exception as e:
                        print("Lỗi gặp phải là: ", e)
                        dict_status_zalo[num_phone_zalo] = ""
                        del dict_queue_device[id_device][0]
                        if len(dict_queue_device[id_device]) == 0:
                            dict_id_chat[id_device] = ""
                        emit("receive_send_message_status", {
                            "status": "Gửi tin nhắn thất bại",  "name_ntd": name, "message": message}, room=room)
                        return False
                elif type == 'file':

                    try:
                        d(resourceId="com.zing.zalo:id/new_chat_input_btn_attach").click()
                        d.xpath(
                            '//*[@text="Tài liệu"]').click()
                        eventlet.sleep(1)

                        if d.xpath('//*[@text="Gần đây"]').exists:
                            d(description="Hiển thị gốc").click_exists(
                                timeout=3)
                            eventlet.sleep(0.2)
                            td = d.xpath(
                                '//*[@text="Tệp đã tải xuống"]')
                            if td.exists:
                                td.click()
                                eventlet.sleep(0.1)
                            else:
                                if d(resourceId="android:id/title", text="Tệp đã tải xuống").exists:
                                    d(resourceId="android:id/title",
                                      text="Tệp đã tải xuống").click()
                                    # eventlet.sleep(0.1)
                                else:
                                    d.xpath(
                                        '//*[@resource-id="com.google.android.documentsui:id/roots_list"]/android.widget.LinearLayout[4]').click_exists(timeout=3)
                            eventlet.sleep(0.1)
                        click_btn = d.xpath(
                            '//*[@resource-id="com.google.android.documentsui:id/dir_list"]/androidx.cardview.widget.CardView[1]/androidx.cardview.widget.CardView[1]/android.widget.RelativeLayout[1]/android.widget.FrameLayout[1]')
                        if click_btn.exists:
                            click_btn.click_exists(timeout=0.1)
                            eventlet.sleep(0.1)
                        btn1 = d.xpath(
                            '//*[@resource-id="com.google.android.documentsui:id/dir_list"]/android.widget.LinearLayout[1]/android.widget.LinearLayout[1]')
                        btn2 = d(
                            resourceId="com.google.android.documentsui:id/item_root")
                        if btn1.exists:
                            btn1.click()
                        elif btn2.exists:
                            btn2.click()
                        if d(resourceId="com.zing.zalo:id/chatinput_text").exists:
                            d(resourceId="com.zing.zalo:id/new_chat_input_btn_attach").click_exists(
                                timeout=1)
                            # pass
                        else:
                            # d.xpath('//*[@resource-id="com.google.android.documentsui:id/dir_list"]/androidx.cardview.widget.CardView[1]').click_exists(timeout=3)
                            d.xpath(
                                '//*[@resource-id="com.google.android.documentsui:id/dir_list"]/androidx.cardview.widget.CardView[2]').click_exists(timeout=3)
                            eventlet.sleep(0.1)
                            d(resourceId="com.zing.zalo:id/new_chat_input_btn_attach").click_exists(
                                timeout=1)
                    except Exception as e:
                        print("Lỗi gặp phải là: ", e)
                        dict_status_zalo[num_phone_zalo] = ""
                        del dict_queue_device[id_device][0]
                        if len(dict_queue_device[id_device]) == 0:
                            dict_id_chat[id_device] = ""
                        emit("receive_send_message_status", {
                            "status": "Gửi tin nhắn thất bại",  "name_ntd": name, "message": message}, room=room)
                        return False

                elif type == 'text':

                    try:
                        d(resourceId="com.zing.zalo:id/chatinput_text").click()
                        eventlet.sleep(0.1)
                        d.send_keys(message, clear=True)
                        eventlet.sleep(0.1)
                        d(resourceId="com.zing.zalo:id/new_chat_input_btn_chat_send").click()
                    except Exception as e:
                        print("Lỗi gặp phải là: ", e)
                        dict_status_zalo[num_phone_zalo] = ""
                        del dict_queue_device[id_device][0]
                        if len(dict_queue_device[id_device]) == 0:
                            dict_id_chat[id_device] = ""
                        emit("receive_send_message_status", {
                            "status": "Gửi tin nhắn thất bại",  "name_ntd": name, "message": message}, room=room)
                        return False
                elif type == 'card':
                    try:
                        d(resourceId="com.zing.zalo:id/new_chat_input_btn_attach").click()
                        eventlet.sleep(0.1)
                        d.xpath('//*[@text="Danh thiếp"]').click()
                        eventlet.sleep(0.1)
                        d(resourceId="com.zing.zalo:id/search_input_text").click()
                        eventlet.sleep(0.1)
                        d.send_keys(name_card, clear=True)
                        eventlet.sleep(0.1)
                        d(resourceId="com.zing.zalo:id/layoutcontact").click()
                        eventlet.sleep(0.1)
                        d(resourceId="com.zing.zalo:id/btn_done_add_item").click()
                        eventlet.sleep(0.1)
                        d(resourceId="com.zing.zalo:id/new_chat_input_btn_attach").click()
                        eventlet.sleep(0.1)
                    except Exception as e:
                        print("Lỗi gặp phải là: ", e)
                        dict_status_zalo[num_phone_zalo] = ""
                        del dict_queue_device[id_device][0]
                        if len(dict_queue_device[id_device]) == 0:
                            dict_id_chat[id_device] = ""
                        emit("receive_send_message_status", {
                            "status": "Gửi tin nhắn thất bại",  "name_ntd": name, "message": message}, room=room)

                now = datetime.now()
                print("Ngày:", now.day)
                print("Tháng:", now.month)
                print("Năm:", now.year)
                print("Giờ:", now.hour)
                print("Phút:", now.minute)
                print("Giây:", now.second)
                hour = str(now.hour)
                minute = str(now.hour)
                if len(hour) == 1:
                    hour = f"0{hour}"
                if len(minute) == 1:
                    minute = f"0{minute}"
                time_str = f"{hour}:{minute} {now.day}/{now.month}/{now.year}"
                eventlet.sleep(1.0)
                join_room(room)
                check = False
                # pick = -1
                for id in range(len(list_prior_chat_boxes)):
                    if list_prior_chat_boxes[id]['name'] == name:
                        check = True
                        if 'data_chat_box' not in list_prior_chat_boxes[id].keys():
                            list_prior_chat_boxes[id]['data_chat_box'] = ""
                        if type == 'text':
                            list_prior_chat_boxes[id]['time'] = time_str
                            list_prior_chat_boxes[id]['message'] = message
                            list_prior_chat_boxes[id]['status'] = "seen"
                            data_chat_box = load_data_chat_box_json(
                                list_prior_chat_boxes[id]['data_chat_box'])
                            data_chat_box.append(
                                {"you": [{'time': time_str, 'type': "text", "data": message}]})
                            list_prior_chat_boxes[id]['data_chat_box'] = dump_data_chat_box_json(
                                list_prior_chat_boxes[id]['data_chat_box'], data_chat_box)
                            list_prior_chat_boxes.insert(
                                0, list_prior_chat_boxes.pop(id))
                        elif type == 'image':
                            list_prior_chat_boxes[id]['time'] = time_str
                            list_prior_chat_boxes[id]['message'] = message
                            list_prior_chat_boxes[id]['status'] = "seen"
                            data_chat_box = load_data_chat_box_json(
                                list_prior_chat_boxes[id]['data_chat_box'])
                            data_chat_box.append(
                                {"you": [{'time': time_str, 'type': "image", "data": f"image{image_number+1}.jpg", "image_data": image_data}]})
                            list_prior_chat_boxes[id]['data_chat_box'] = dump_data_chat_box_json(
                                list_prior_chat_boxes[id]['data_chat_box'], data_chat_box)
                            list_prior_chat_boxes.insert(
                                0, list_prior_chat_boxes.pop(id))
                        elif type == 'file':
                            list_prior_chat_boxes[id]['time'] = time_str
                            list_prior_chat_boxes[id]['message'] = message
                            list_prior_chat_boxes[id]['status'] = "seen"
                            data_chat_box = load_data_chat_box_json(
                                list_prior_chat_boxes[id]['data_chat_box'])
                            data_chat_box.append(
                                {"you": [{'time': time_str, 'type': "file", "data": f"[File]\n{file_name}\n{file_size}", "file_data": file_data}]})
                            list_prior_chat_boxes[id]['data_chat_box'] = dump_data_chat_box_json(
                                list_prior_chat_boxes[id]['data_chat_box'], data_chat_box)
                            list_prior_chat_boxes.insert(
                                0, list_prior_chat_boxes.pop(id))
                        elif type == 'card':
                            list_prior_chat_boxes[id]['time'] = time_str
                            list_prior_chat_boxes[id]['message'] = message
                            list_prior_chat_boxes[id]['status'] = "seen"
                            data_chat_box = load_data_chat_box_json(
                                list_prior_chat_boxes[id]['data_chat_box'])
                            data_chat_box.append(
                                {"you": [{'time': time_str, 'type': "card", "data": f"{name_card}\nGọi điện\nNhắn tin", "card_data": {"name_card": name_card, "num_phone_card": num_phone_card}}]})
                            list_prior_chat_boxes[id]['data_chat_box'] = dump_data_chat_box_json(
                                list_prior_chat_boxes[id]['data_chat_box'], data_chat_box)
                            list_prior_chat_boxes.insert(
                                0, list_prior_chat_boxes.pop(id))
                        break

                        # pick = id
                if not check:
                    data_chat_box = []
                    if type == 'text':
                        list_prior_chat_boxes.append(
                            {"name": name, "ava": "", "phone": phone, "friend_or_not": friend_or_not, "time": time_str, "message": message, "status": "seen", "data_chat_box": ""})
                        data_chat_box = load_data_chat_box_json(
                            list_prior_chat_boxes[-1]['data_chat_box'])
                        data_chat_box.append(
                            {"you": [{'time': time_str, 'type': "text", "data": message}]})
                        list_prior_chat_boxes[-1]['data_chat_box'] = dump_data_chat_box_json(
                            list_prior_chat_boxes[-1]['data_chat_box'], data_chat_box)
                        list_prior_chat_boxes.insert(
                            0, list_prior_chat_boxes.pop(-1))
                    elif type == 'image':
                        list_prior_chat_boxes.append(
                            {"name": name, "ava": "", "phone": phone, "friend_or_not": friend_or_not, "time": time_str, "message": f"image{image_number+1}.jpg", "status": "seen", "data_chat_box": ""})
                        data_chat_box = load_data_chat_box_json(
                            list_prior_chat_boxes[-1]['data_chat_box'])
                        data_chat_box.append(
                            {"you": [{'time': time_str, 'type': "image", "data": f"image{image_number+1}.jpg", "image_data": image_data}]})
                        list_prior_chat_boxes[-1]['data_chat_box'] = dump_data_chat_box_json(
                            list_prior_chat_boxes[-1]['data_chat_box'], data_chat_box)
                        list_prior_chat_boxes.insert(
                            0, list_prior_chat_boxes.pop(-1))
                    elif type == 'file':
                        list_prior_chat_boxes.append(
                            {"name": name, "ava": "", "phone": phone, "friend_or_not": friend_or_not, "time": time_str, "message": f"[File]\n{file_name}\n{file_size}", "status": "seen", "data_chat_box": ""})
                        data_chat_box = load_data_chat_box_json(
                            list_prior_chat_boxes[-1]['data_chat_box'])
                        data_chat_box.append(
                            {"you": [{'time': time_str, 'type': "file", "data": f"[File]\n{file_name}\n{file_size}", "file_data": file_data}]})
                        list_prior_chat_boxes[-1]['data_chat_box'] = dump_data_chat_box_json(
                            list_prior_chat_boxes[-1]['data_chat_box'], data_chat_box)
                        list_prior_chat_boxes.insert(
                            0, list_prior_chat_boxes.pop(-1))
                    elif type == 'card':
                        list_prior_chat_boxes.append(
                            {"name": name, "ava": "", "phone": phone, "friend_or_not": friend_or_not, "time": time_str, "message": message, "status": "seen", "data_chat_box": ""})
                        data_chat_box = load_data_chat_box_json(
                            list_prior_chat_boxes[-1]['data_chat_box'])
                        data_chat_box.append(
                            {"you": [{'time': time_str, 'type': "card", "data": message, "card_data": {"name_card": name_card, "num_phone_card": num_phone_card}}]})
                        list_prior_chat_boxes[-1]['data_chat_box'] = dump_data_chat_box_json(
                            list_prior_chat_boxes[-1]['data_chat_box'], data_chat_box)
                        list_prior_chat_boxes.insert(
                            0, list_prior_chat_boxes.pop(-1))

                if extra_message != "":
                    try:
                        d(resourceId="com.zing.zalo:id/chatinput_text").click()
                        eventlet.sleep(0.1)
                        d.send_keys(extra_message, clear=True)
                        eventlet.sleep(0.1)
                        d(resourceId="com.zing.zalo:id/new_chat_input_btn_chat_send").click()
                    except Exception as e:
                        print("Lỗi gặp phải là: ", e)
                        dict_status_zalo[num_phone_zalo] = ""
                        del dict_queue_device[id_device][0]
                        if len(dict_queue_device[id_device]) == 0:
                            dict_id_chat[id_device] = ""
                        emit("receive_send_message_status", {
                            "status": "Gửi tin nhắn thất bại",  "name_ntd": name, "message": extra_message}, room=room)
                        return False
                    now = datetime.now()
                    print("Ngày:", now.day)
                    print("Tháng:", now.month)
                    print("Năm:", now.year)
                    print("Giờ:", now.hour)
                    print("Phút:", now.minute)
                    print("Giây:", now.second)
                    hour = str(now.hour)
                    minute = str(now.hour)
                    if len(hour) == 1:
                        hour = f"0{hour}"
                    if len(minute) == 1:
                        minute = f"0{minute}"
                    time_str = f"{hour}:{minute} {now.day}/{now.month}/{now.year}"
                    eventlet.sleep(1.0)

                    id = 0
                    if 'data_chat_box' not in list_prior_chat_boxes[id].keys():
                        list_prior_chat_boxes[id]['data_chat_box'] = ""

                    list_prior_chat_boxes[id]['time'] = time_str
                    list_prior_chat_boxes[id]['message'] = extra_message
                    list_prior_chat_boxes[id]['status'] = "seen"
                    data_chat_box = load_data_chat_box_json(
                        list_prior_chat_boxes[id]['data_chat_box'])
                    data_chat_box.append(
                        {"you": [{'time': time_str, 'type': "text", "data": extra_message}]})
                    list_prior_chat_boxes[id]['data_chat_box'] = dump_data_chat_box_json(
                        list_prior_chat_boxes[id]['data_chat_box'], data_chat_box)

            data_update = {"num_phone_zalo": num_phone_zalo,
                           "list_prior_chat_boxes": list_prior_chat_boxes}
            update_base_document_json(
                "D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "num_phone_zalo", f"Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}", data_update)
            if True:
                on_chat = False
                if d(resourceId="com.zing.zalo:id/action_bar_title").exists:
                    btn = d(resourceId="com.zing.zalo:id/action_bar_title") 
                else:
                    btn = d(resourceId="com.zing.zalo:id/txtTitle")
                if btn.exists:
                    current_ntd = btn.get_text()

                    if current_ntd == name_share:
                        on_chat = True
                    else:
                        d.press("back")
                        if d(resourceId="com.zing.zalo:id/txtTitle").exists or d(resourceId="com.zing.zalo:id/action_bar_title").exists:
                            d.press("back")
                        while not d.xpath('//*[@text="Ưu tiên"]').exists:
                            d.press("back")
                if not on_chat:
                    items = d.xpath(
                        "//android.widget.FrameLayout[@text!='']").all()
                    check_ex = False
                    for item in items:
                        raw = item.text
                        lines = [l for l in raw.split(
                            "\n") if l.strip()]
                        if lines[0] == name_share:
                            item.click()
                            check_ex = True
                            break
                    if not check_ex:
                        while not d.xpath('//*[@text="Ưu tiên"]').exists:
                            if d.xpath('//*[@text="Zalo"]').exists:
                                try:
                                    d.xpath(
                                        '//*[@text="Zalo"]').click()
                                    eventlet.sleep(1.5)
                                except Exception:
                                    pass
                            else:
                                d.press("back")

                        try:
                            # d(text="Tìm kiếm").click()
                            d(text="Tìm kiếm").click()
                        except Exception as e:
                            print(e)
                            dict_status_zalo[num_phone_zalo] = ""
                            del dict_queue_device[id_device][0]
                            if len(dict_queue_device[id_device]) == 0:
                                dict_id_chat[id_device] = ""
                            emit("receive_chat_view_status", {
                                "status": "Mở chat thất bại, hãy thử mở lại", "name_ntd": name_share}, room=room)
                            return False
                        # time.sleep(1.0)
                        d.send_keys(f"{name_share}", clear=True)
                        eventlet.sleep(0.15)

                        try:
                            chat_list = d.xpath(
                                '//*[@resource-id="com.zing.zalo:id/btn_search_result"]').all()

                            # if len(chat_num) > 0:
                            for item in chat_list:
                                raw = item.text
                                print("Raw là", raw)
                                lines = [l for l in raw.split(
                                    "\n") if l.strip()]
                                if lines[0] == name_share:
                                    print("Có trùng khớp")
                                    item.click()
                                    break
                            eventlet.sleep(1.0)
                            # print("Nhấn được chat  chưa")
                        except Exception as e:
                            emit("receive status", {
                                "status": "Tài khoản không tồn tại"}, room=room)
                            dict_status_zalo[num_phone_zalo] = ""
                            del dict_queue_device[id_device][0]
                            if len(dict_queue_device[id_device]) == 0:
                                dict_id_chat[id_device] = ""
                            # print("Có lỗi à cậu")
                            print("Lỗi gặp phải là: ", e)
                            return False

                        try:
                            btn = d(
                                resourceId="com.zing.zalo:id/btn_send_message")
                            if btn.exists:
                                btn.click()
                                eventlet.sleep(1.0)
                        except Exception as e:
                            print("Lỗi gặp phải là: ", e)
                            pass

            emit("receive_share_status", {
                "status": "Đã chia sẻ tin nhắn thành công"}, room=room)
            emit('receive_list_prior_chat_box', {
                'user_name': name_share, 'list_prior_chat_boxes': list_prior_chat_boxes, 'status': "Có dữ liệu mới từ khách hàng gửi đến"}, room=room)
        except Exception as e:
            print(e)

            dict_status_zalo[num_phone_zalo] = ""
            dict_status_update_pvp[num_phone_zalo] = 2
            del dict_queue_device[id_device][0]
            if len(dict_queue_device[id_device]) == 0:
                dict_id_chat[id_device] = ""
            handle_chat_view(d, num_phone_zalo)
            return False
        num_message += 1
        with open(f'D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/device_status_{dict_phone_device[num_phone_zalo]}.json', 'r') as f:
            device_status = json.load(f)

        for id in range(len(device_status['max_message_per_day'])):
            if num_phone_zalo in device_status['max_message_per_day'][id].keys():
                device_status['max_message_per_day'][id][num_phone_zalo] -= 1
                if extra_message != "":
                    device_status['max_message_per_day'][id][num_phone_zalo] -= 1
        dict_status_zalo[num_phone_zalo] = ""
        dict_status_update_pvp[num_phone_zalo] = 2
        del dict_queue_device[id_device][0]
        if len(dict_queue_device[id_device]) == 0:
            dict_id_chat[id_device] = ""
        # two = time.time()
        # print(two-one)
        handle_chat_view(d, num_phone_zalo)

        with open(f"D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/device_status_{dict_phone_device[num_phone_zalo]}.json", 'w') as f:
            json.dump(device_status, f, indent=4)

def background_first_crawl_per_day(id_device):

    file_path = f"D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/device_status_{id_device}.json"
    # global device_connect
    # global dict_devices
    # print("Id của máy là ", id_device)
    try:
        d = u2.connect(id_device)
        # d.app_start('com.zing.zalo', stop=True)
        print("Id của máy là ", id_device)
        device_connect[id_device] = True
        # dict_devices.append(id_device)
    except Exception as e:
        print(e)
        print("Thiết bị đã ngắt kết nối", id_device)
        eventlet.sleep(5)
        device_connect[id_device] = False
        background_first_crawl_per_day(id_device)
        return True

    print("Bắt đầu cào dữ liệu và lấy dữ liệu người dùng ", id_device)
    max_mes = []
    max_add = []
    if os.path.exists(file_path):
        os.remove(file_path)  # Xóa file nếu có

    device_status = {
        "active": True,
        "max_message_per_day": max_mes,
        "max_add_friend_per_day": max_add,
        "update": False
    }

    with open(f"D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/device_status_{id_device}.json", 'w') as f:
        json.dump(device_status, f, indent=4)

    file_path_new = f"D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/Zalo_data_login_path_{id_device}.json"
    if os.path.exists(file_path_new):
        with open(f'D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/Zalo_data_login_path_{id_device}.json', 'r', encoding='utf-8') as f:
            zalo_data = json.load(f)
        for i in range(len(zalo_data)):
            zalo_data[i]['status'] = False
    else:
        zalo_data = []

    name_zalos = []
    try:
        # if True:
        # Lấy danh sách tên các tài khoản zalo hiện có
        d.app_start("com.zing.zalo", stop=True)
        eventlet.sleep(1.0)
        d(resourceId="com.zing.zalo:id/maintab_metab").click()
        eventlet.sleep(1.0)
        d.xpath(
            '//*[@resource-id="com.zing.zalo:id/zalo_action_bar"]/android.widget.LinearLayout[2]').click()

        eventlet.sleep(1.5)
        d.swipe_ext(u2.Direction.FORWARD)
        eventlet.sleep(1.0)
        try:
            d(resourceId="com.zing.zalo:id/itemSwitchAccount").click()
        except Exception as e:
            background_first_crawl_per_day(id_device)
            return True
        eventlet.sleep(1.0)

        # items = d.xpath("//android.widget.TextView[@text!='']").all()
        nope = ['Đã đăng nhập',
                "Chuyển tài khoản", "Thêm tài khoản để đăng nhập nhanh.", "Thêm tài khoản"]
        elements = d(resourceId="com.zing.zalo:id/name")

        for elem in elements:
            name_zalo_1 = elem.info.get('text', '').strip()
            if name_zalo_1 not in nope:
                # print("Có tồn tại thêm tài khoản không ", name_zalo_1)
                name_zalos.append(name_zalo_1)

        print(name_zalos)

        for id in range(len(name_zalos)):
            if "Thêm tài khoản" in name_zalos[id] or name_zalos[id] == "Thêm tài khoản":
                del name_zalos[id]
                break

        for id in range(len(name_zalos)):
            if name_zalos[id] == "Thêm tài khoản":
                continue

            for it in range(len(zalo_data)):
                if zalo_data[it]['name'] == name_zalos[id] and zalo_data[it]['num_phone_zalo'] != "":
                    zalo_data[it]['status'] = True

            d.app_start("com.zing.zalo", stop=True)
            eventlet.sleep(1)
            d(resourceId="com.zing.zalo:id/maintab_metab").click()
            eventlet.sleep(1.5)
            # if d(resourceId="com.zing.zalo:id/subtitle_list_me_tab").exists:
            #     d(resourceId="com.zing.zalo:id/subtitle_list_me_tab").click()
            # else:
            #     d(resourceId="com.zing.zalo:id/heading_list_setting_container").click()
            # eventlet.sleep(1.5)
            d.click(0.5, 0.18)
            avatar_b64 = ""
            try:
                iv = d(resourceId="com.zing.zalo:id/rounded_avatar_frame")
                img = iv.screenshot()

                # 2. Giảm độ phân giải ảnh nhỏ hơn, vẫn giữ aspect ratio
                max_w, max_h = 200, 200
                # nhanh, giữ tỉ lệ gốc :contentReference[oaicite:1]{index=1}
                img.thumbnail((max_w, max_h), resample=Image.BILINEAR)

                # 3. Nén ảnh với JPEG (chất lượng vừa phải kèm optimize) để có kích thước tối ưu
                buf = io.BytesIO()
                # giảm kích thước, chất lượng vẫn tốt :contentReference[oaicite:2]{index=2}
                img.save(buf, format="JPEG", optimize=True, quality=75)

                # 4. Encode sang base64 (chuỗi ASCII ngắn và nhẹ hơn)
                # base64 chỉ chứa ký tự ASCII :contentReference[oaicite:3]{index=3}
                avatar_b64 = base64.b64encode(
                    buf.getvalue()).decode("ascii")

                if not already_sent_number(AVA_NUMBER_PATH):
                    ava_number = 0
                else:
                    ava_number = read_sent_number(AVA_NUMBER_PATH)
                with open(f"{IMAGE_FILE_DATA_PATH}/ava_{ava_number}.txt", "w", encoding="utf-8") as f:
                    f.write(avatar_b64)

                log_sent_number(ava_number+1, AVA_NUMBER_PATH)

                avatar_b64 = f"{IMAGE_FILE_DATA_PATH}/ava_{ava_number}.txt"

            except Exception as e:
                pass
            # zalo_data[id]['ava'] = avatar_b64
            eventlet.sleep(1.0)
            d.xpath(
                '//*[@resource-id="com.zing.zalo:id/zalo_action_bar"]/android.widget.LinearLayout[1]/android.widget.FrameLayout[2]').click()
            eventlet.sleep(1.0)
            d(resourceId="com.zing.zalo:id/setting_text_primary",
              text="Thông tin").click()
            eventlet.sleep(1.5)
            num_phone_zalo = d(
                resourceId="com.zing.zalo:id/tv_phone_number").get_text()
            print("Số điện thoại là: ", num_phone_zalo)
            num_phone_zalo = num_phone_zalo.replace(" ", "")
            num_phone_zalo = num_phone_zalo.replace("+84", "0")
            if not already_sent_phone_zalo(num_phone_zalo):
                log_sent_phone_zalo(num_phone_zalo)
                dict_new_friend[num_phone_zalo] = {}

            ck_zalo = False
            for it in range(len(zalo_data)):
                if zalo_data[it]['num_phone_zalo'] == num_phone_zalo:
                    zalo_data[it]['name'] = name_zalos[id]
                    zalo_data[it]['ava'] = avatar_b64
                    zalo_data[it]['status'] = True
                    list_prior_chat_boxes = zalo_data[it]['list_prior_chat_boxes']
                    list_group = zalo_data[it]['list_group']
                    if "has_first_update" not in zalo_data[it].keys():
                        zalo_data[it]['has_first_update'] = False
                    ck_zalo = True
                    break
            if not ck_zalo:
                for it in range(len(zalo_data)):
                    if zalo_data[it]['name'] == name_zalos[id] and zalo_data[it]['num_phone_zalo'] != "":
                        zalo_data[it]['num_phone_zalo'] = num_phone_zalo
                        zalo_data[it]['ava'] = avatar_b64
                        zalo_data[it]['status'] = True
                        list_prior_chat_boxes = zalo_data[it]['list_prior_chat_boxes']
                        list_group = zalo_data[it]['list_group']
                        if "has_first_update" not in zalo_data[it].keys():
                            zalo_data[it]['has_first_update'] = False
                        ck_zalo = True
                        break
            if not ck_zalo:
                # for it in range(len(zalo_data)):
                #    if zalo_data[it]['name'] == name_zalos[id] and zalo_data[it]['num_phone_zalo'] != "":
                zalo_data.append({"id_device": id_device, "num_phone_zalo": num_phone_zalo, "name": name_zalos[id], "ava": avatar_b64, "list_friend": [
                ], "list_group": [], "list_invite_friend": [], "list_prior_chat_boxes": [], "list_unseen_chat_boxes": [], "status": True, "has_first_update": False})
                list_prior_chat_boxes = []
                list_group = []
            # zalo_data[id]['num_phone_zalo'] = num_phone_zalo
            dict_device_and_phone[id_device].append(num_phone_zalo)
            dict_status_zalo[num_phone_zalo] = ""
            dict_status_update_pvp[num_phone_zalo] = 0
            dict_phone_device[num_phone_zalo] = id_device
            with open(f"D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/Zalo_data_login_path_{id_device}.json", 'w', encoding="utf-8") as f:
                json.dump(zalo_data, f, ensure_ascii=False, indent=4)
            eventlet.sleep(5.0)

            tag_name = {}
            data_chat_boxes = {}
            friend_or_nots = {}
            list_mems = {}
            check_mems = {}

            for chat in list_prior_chat_boxes:
                if 'tag' in chat.keys():
                    if chat['tag'] != "":
                        tag_name[chat['name']] = chat['tag']
                if "data_chat_box" in chat.keys():
                    if chat["data_chat_box"] == []:
                        chat["data_chat_box"] = ""
                        data_chat_boxes[chat['name']] = chat["data_chat_box"]
                    
                    if isinstance(chat["data_chat_box"], list):
                        chat["data_chat_box"] = dump_data_chat_box_json(chat["data_chat_box"], chat["data_chat_box"])

                    if chat["data_chat_box"]:
                        print("Đoạn chat này khác rỗng ", chat['name'])
                        data_chat_boxes[chat['name']] = chat["data_chat_box"]
                if 'friend_or_not' in chat.keys():
                    if chat['friend_or_not'] != "":
                        friend_or_nots[chat['name']] = chat['friend_or_not']
            
            for group in list_group:
                if 'list_mems' in group.keys():
                    list_mems[group['name']] = group['list_mems']
                if 'check_mems' in group.keys():
                    check_mems[group['name']] = group['check_mems']
                    
        # phone = [zalo['num_phone_zalo'] for zalo in zalo_data if zalo['num_phone_zalo'] != ""]
        # for num_phone_zalo in phone:

            try:
                print("Bắt đầu lấy danh sách bạn bè")
                has_first_update = False
                pick = -1
                for it in range(len(zalo_data)):
                    if zalo_data[it]['num_phone_zalo'] == num_phone_zalo:
                        list_friend = zalo_data[it]['list_friend']
                        if 'list_prior_chat_boxes' in zalo_data[it].keys():
                            old_list_prior_chat_boxes = zalo_data[it]['list_prior_chat_boxes']
                        else:
                            old_list_prior_chat_boxes = []
                        has_first_update = zalo_data[it]['has_first_update']
                        # has_first_update = has_first_update
                        zalo_data[it]['ava'] = avatar_b64
                        zalo_data[it]['status'] = True
                        ck_zalo = True
                        pick = it
                        break
                result1, update_again = api_get_list_friend(
                    {"num_phone_zalo": num_phone_zalo}, has_first_update)

                while update_again:
                    # dict_devices = [dv for dv in dict_devices if dv != id_device]
                    eventlet.sleep(2.0)
                    result1, update_again = api_get_list_friend(
                        {"num_phone_zalo": num_phone_zalo}, has_first_update)
                    # dict_devices = [dv for dv in dict_devices if dv != id_device]

                if not has_first_update:
                    print("Bắt đầu lấy danh sách nhóm")
                    result2, update_again = api_get_list_group(
                        {"num_phone_zalo": num_phone_zalo}, list_mems, check_mems)
                    while update_again:
                        # dict_devices = [dv for dv in dict_devices if dv != id_device]
                        # background_first_crawl_per_day(id_device)
                        # dict_devices = [dv for dv in dict_devices if dv != id_device]
                        eventlet.sleep(2.0)
                        result2, update_again = api_get_list_group(
                            {"num_phone_zalo": num_phone_zalo}, list_mems, check_mems)
                        # return True   

                    print("Bắt đầu lấy danh sách gửi kết bạn")
                    result3, update_again = api_get_list_invite_friend(
                        {"num_phone_zalo": num_phone_zalo})

                    while update_again:
                        # dict_devices = [dv for dv in dict_devices if dv != id_device]
                        # background_first_crawl_per_day(id_device)
                        # return True
                        eventlet.sleep(2.0)
                        result3, update_again = api_get_list_invite_friend(
                            {"num_phone_zalo": num_phone_zalo})

                print("Bắt đầu lấy danh sách chat ưu tiến")
                result4, update_again = api_update_list_prior_chat_boxes(
                    {"num_phone_zalo": num_phone_zalo}, tag_name=tag_name, data_chat_boxes=data_chat_boxes, friend_or_nots=friend_or_nots, max_chat_boxes=2000, scroll_or_not=True)
                while update_again:
                    # dict_devices = [dv for dv in dict_devices if dv != id_device]
                    eventlet.sleep(2.0)
                    result4, update_again = api_update_list_prior_chat_boxes(
                        {"num_phone_zalo": num_phone_zalo}, tag_name=tag_name, data_chat_boxes=data_chat_boxes)
                    # return True

                print("Bắt đầu lấy danh sách chat chưa đọc")
                result5, update_again = api_update_list_unseen_chat_boxes({"num_phone_zalo":  num_phone_zalo})
                while update_again:
                    # dict_devices = [dv for dv in dict_devices if dv != id_device]
                    eventlet.sleep(2.0)
                    result5, update_again = api_update_list_unseen_chat_boxes(
                        {"num_phone_zalo": num_phone_zalo})
                    
                if has_first_update and len(result4) > 0 and len(old_list_prior_chat_boxes) > 0:
                    try:
                        to_remove = {(old.get('name'), old.get('message'))
                                     for old in old_list_prior_chat_boxes}
                        result4 = [new for new in result4 if (
                            new.get('name'), new.get('message')) not in to_remove]
                    except Exception as e:
                        print(e)

                result4 = [r['name'] for r in result4]

                print("Bắt đầu lấy lịch sử chat")
                try:
                    for name_ntd in result4:
                        # if name_ntd not in result2:
                        try:
                            gr_or_pvp = "pvp"
                            if name_ntd in result2:
                                gr_or_pvp = "gr"
                            result6 = api_update_data_one_chat_box(
                                {"num_phone_zalo": num_phone_zalo, "name_ntd": name_ntd}, gr_or_pvp=gr_or_pvp, update=True)
                            while not device_connect[id_device]:
                                # dict_devices = [
                                #    dv for dv in dict_devices if dv != id_device]
                                # background_first_crawl_per_day(id_device)
                                eventlet.sleep(2.0)
                                print("Đang chờ thiết bị kết nối lại ", id_device)
                                # dict_devices = [dv for dv in dict_devices if dv != id_device]
                                # return True
                        except Exception as e:
                            result6 = api_update_data_one_chat_box(
                                {"num_phone_zalo": num_phone_zalo, "name_ntd": name_ntd}, gr_or_pvp=gr_or_pvp, update=True)

                except Exception as e:
                    print(e)

                if not has_first_update:

                    for name_ntd in result2:
                        try:
                            result7 = api_update_list_mems_one_group(
                                {"num_phone_zalo": num_phone_zalo, "name_ntd": name_ntd}, update=True)
                            while not device_connect[id_device]:
                                # dict_devices = [
                                #    dv for dv in dict_devices if dv != id_device]
                                # background_first_crawl_per_day(id_device)
                                eventlet.sleep(2.0)
                                print("Đang chờ thiết bị kết nối lại ", id_device)
                                # dict_devices = [dv for dv in dict_devices if dv != id_device]
                                # return True
                        except Exception as e:
                            result7 = api_update_list_mems_one_group(
                                {"num_phone_zalo": num_phone_zalo, "name_ntd": name_ntd}, update=True)

                # if not has_first_update:
                    zalo_data[pick]['has_first_update'] = True
                    with open(f"D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/Zalo_data_login_path_{id_device}.json", 'w', encoding="utf-8") as f:
                        json.dump(
                            zalo_data, f, ensure_ascii=False, indent=4)
            except Exception as e:
                print("Đã gặp lỗi trong quá trình lấy thông tin zalo ", e)

            if id < len(name_zalos)-1:
                try:
                    d = switch_account(d, name_zalos[id+1])
                    docs = get_base_id_zalo_json(
                        "D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "id_device", f"Zalo_data_login_path_{dict_phone_device[num_phone_zalo]}", {"id_device": id_device})
                    current_phone = ""
                    for it in docs:
                        if it['status']:
                            current_phone = it['num_phone_zalo']
                            if current_phone != "":
                                status = update_base_document_json("D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "num_phone_zalo", f"Zalo_data_login_path_{id_device}", {
                                    "num_phone_zalo": current_phone, "status": False})
                    status = update_base_document_json("D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "num_phone_zalo", f"Zalo_data_login_path_{id_device}", {
                        "num_phone_zalo": num_phone_zalo, "status": False})
                    with open(f'D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/Zalo_data_login_path_{id_device}.json', 'r', encoding='utf-8') as f:
                        zalo_data = json.load(f)
                except Exception as e:
                    print("Đã gặp lỗi trong quá trình đổi tài khoản zalo ", e)

    except Exception as e:
        print("Đã gặp lỗi trong quá trình lấy thông tin zalo ", e)
        eventlet.sleep(5)
        background_first_crawl_per_day(id_device)
        return True

    print("Cào dữ liệu thành công ", id_device)
    # cập nhật ngày cào 
    mark_zalo_completed(id_device)
    with open(f'D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/device_status_{id_device}.json', 'r') as f:
        device_status = json.load(f)
    d.app_start("com.zing.zalo", stop=True)
    eventlet.sleep(2.0)
    device_status['active'] = False
    device_status['update'] = True

    for phone in dict_device_and_phone[id_device]:
        device_status['max_message_per_day'].append({phone: 300})
        device_status['max_add_friend_per_day'].append({phone: 5})

    with open(f"D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/device_status_{id_device}.json", 'w') as f:
        json.dump(device_status, f, indent=4)
    # dict_devices.append(id_device)
    print("Tiến trình bắt đầu ", id_device)
    # dict_devices.append(id_device)
    # return [result1, result2, result3, result4, result5, result6]
    return True


def background_update_data_loop(id_device):

    while True:
        try:

            try:
                update_d = u2.connect(id_device)
                # global device_connect
                device_connect[id_device] = True
            except Exception as e:
                print("Thiết bị đã ngắt kết nối", id_device)
                device_connect[id_device] = False
                continue

            try:
                with open(f'D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/device_status_{id_device}.json', 'r') as f:
                    device_status = json.load(f)
                if not device_status['update']:
                    print("Dữ liệu chưa update xong ", id_device)
                    eventlet.sleep(2)
                    continue
            except Exception as e:
                print(f"Dữ liệu chưa update xong {id_device} ", e)
                eventlet.sleep(2)
                continue

            if now_phone_zalo[id_device] == "":
                print("Đang không có số điện thoại nào gọi đến, id là ", id_device)
                eventlet.sleep(2)
                continue

            print(
                f"Số điện thoại hiện tại của {id_device} là: ", now_phone_zalo[id_device])
            try:
                document = get_base_id_zalo_json("D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "num_phone_zalo", f"Zalo_data_login_path_{id_device}", {
                    "num_phone_zalo": now_phone_zalo[id_device]})[0]
            except Exception as e:
                print(e)
                continue

            # id_device = document['id_device']

            try:
                documents = get_base_id_zalo_json(
                    "D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "id_device", f"Zalo_data_login_path_{id_device}", {"id_device": id_device})
            except Exception as e:
                print(e)
                continue

            current_phone = ""
            for it in documents:
                if it['status']:
                    current_phone = it['num_phone_zalo']
                    if current_phone != "" and current_phone != now_phone_zalo[id_device]:
                        now_phone_zalo[id_device] = current_phone
                        document = it
                    break

            list_group = document['list_group']
            list_prior_chat_boxes = document['list_prior_chat_boxes']
            if len(list_group) > 0:
                name_group = [group['name'] for group in list_group]
            else:
                name_group = []

            # docs giờ là một list chứa mọi document tìm được
            # id_device = document['id_device']
            user_name = document['name']
            # list_prior_chat_boxes = document['list_prior_chat_boxes']
            curr_phone_zalo = now_phone_zalo[id_device]
            if dict_status_zalo[curr_phone_zalo] != "":
                print("Có tiến trình đang chạy ",
                    dict_status_zalo[curr_phone_zalo])
                eventlet.sleep(1)
                continue

            while True:
                if dict_status_zalo[curr_phone_zalo] != "":
                    print("Có tiến trình đang chạy ",
                        dict_status_zalo[curr_phone_zalo])
                    break
                if curr_phone_zalo != now_phone_zalo[id_device]:
                    print("Số điện thoại đã bị thay đổi: ",
                        now_phone_zalo[id_device])
                    break
                print("Trạng thái hiện tại là: ",
                    dict_status_zalo[curr_phone_zalo])
                if update_d(resourceId="com.zing.zalo:id/action_bar_title").exists:
                    btn = update_d(resourceId="com.zing.zalo:id/action_bar_title")
                else:
                    btn = update_d(resourceId="com.zing.zalo:id/txtTitle")

                if btn.exists:

                    try:
                        # chỗ này cần bỏ đi vì tốn tài nguyên
                        name_ntd = btn.get_text()
                        if update_d(text="Đã gửi lời mời kết bạn").exists:
                            friend_or_not = "added"
                        else:
                            btn = update_d(
                                resourceId="com.zing.zalo:id/tv_function_privacy")
                            kb = update_d.xpath('//*[@text="Kết bạn"]')
                            if btn.exists or kb.exists:
                                friend_or_not = "no"
                            else:
                                friend_or_not = "yes"

                        if 'list_prior_chat_boxes' not in document.keys():
                            document['list_prior_chat_boxes'] = []
                        list_prior_chat_boxes = document['list_prior_chat_boxes']

                        elems = update_d.xpath(
                            '//*[@content-desc and contains(@content-desc, "Thông báo của Zalo")]').all()
                        for el in elems:
                            print("Có tồn tại phần tử thông báo ",
                                el.info.get("contentDescription"))

                        if len(elems) > 0:
                            ck_noti = False
                            for elem in elems:
                                # if elem.exists:
                                desc_value = elem.info.get("contentDescription")
                                print(desc_value)
                                new_name_ntd = desc_value.replace(
                                    "Thông báo của Zalo:", "")
                                if ":" in new_name_ntd:
                                    new_name_ntd = new_name_ntd.split(':')[0]
                                new_name_ntd = new_name_ntd.strip()
                                data_ntd = new_name_ntd
                                # print(data_ntd)
                                print("Tên cuộc thông báo mới là: ", new_name_ntd)
                                check = False
                                pick = -1
                                for id in range(len(list_prior_chat_boxes)):
                                    if list_prior_chat_boxes[id]['name'] == data_ntd:
                                        pick = id
                                        check = True
                                        break
                                if not check:
                                    list_prior_chat_boxes.append(
                                        {"name": data_ntd, "time": "", "message": "", "status": "unseen", "data_chat_box": ""})
                                # print("Đã đọc chưa nhỉ:",
                                #      list_prior_chat_boxes[pick]['status'])
                                if list_prior_chat_boxes[pick]['status'] == "seen":
                                    list_prior_chat_boxes[pick]['status'] = "unseen"
                                    list_prior_chat_boxes.insert(
                                        0, list_prior_chat_boxes.pop(pick))
                                    data_update = {"num_phone_zalo": curr_phone_zalo,
                                                "list_prior_chat_boxes": list_prior_chat_boxes}
                                    ck_noti = True
                            if ck_noti:
                                # print("Đã gửi socket chưa")
                                update_base_document_json(
                                    "D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "num_phone_zalo", f"Zalo_data_login_path_{id_device}", data_update)
                                socketio.emit('receive_list_prior_chat_box', {
                                    'user_name': user_name, 'list_prior_chat_boxes': list_prior_chat_boxes, 'status': "Có dữ liệu mới từ khách hàng gửi đến"})

                        try:
                            with open(f'D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base/device_status_{id_device}.json', 'r') as f:
                                device_status = json.load(f)
                        except Exception as e:
                            print("Có lỗi xảy ra", e)
                            break

                        if device_status['active']:
                            # print("Có active không")
                            if name_ntd in name_group:
                                # if True:
                                try:
                                    print("Tên nhóm là: ", name_ntd)
                                    if curr_phone_zalo != now_phone_zalo[id_device]:
                                        print(
                                            "Số điện thoại hiện tại đã bị thay đổi: ", now_phone_zalo[id_device])
                                        break
                                    chat_box_on_chat = api_update_data_gr_chat_box(
                                        update_d, {"num_phone_zalo": curr_phone_zalo, "name_ntd": name_ntd}, document)
                                    print("Dữ liễu đoạn chat là:",
                                        chat_box_on_chat)
                                except Exception as e:
                                    print("Có lỗi khi cào tin nhắn nhóm: ", e)
                                    dict_status_zalo[curr_phone_zalo] = ""
                                    chat_box_on_chat = False
                                if not chat_box_on_chat:
                                    break
                                if chat_box_on_chat and len(chat_box_on_chat) > 0:
                                    document = get_base_id_zalo_json("D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "num_phone_zalo", f"Zalo_data_login_path_{id_device}", {
                                        "num_phone_zalo": curr_phone_zalo})[0]
                                    list_prior_chat_boxes = document['list_prior_chat_boxes']

                                    socketio.emit('receive_new_message_from_ntd', {
                                        'name_ntd': name_ntd, 'friend_or_not': friend_or_not, 'status': "Có dữ liệu mới từ khách hàng gửi đến"})
                                    socketio.emit('receive_list_prior_chat_box', {
                                        'user_name': user_name, 'list_prior_chat_boxes': list_prior_chat_boxes, 'status': "Có dữ liệu mới từ khách hàng gửi đến"})
                            else:
                                document = get_base_id_zalo_json("D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "num_phone_zalo", f"Zalo_data_login_path_{id_device}", {
                                    "num_phone_zalo": curr_phone_zalo})[0]
                                if curr_phone_zalo != now_phone_zalo[id_device]:
                                    print(
                                        "Số điện thoại hiện tại đã bị thay đổi: ", now_phone_zalo[id_device])
                                    break
                                try:
                                    chat_box_on_chat = api_update_data_1vs1_chat_box(
                                        update_d, {"num_phone_zalo": curr_phone_zalo, "name_ntd": name_ntd}, document)
                                except Exception as e:
                                    print("Có lỗi khi lấy tin nhắn 1vs1", e)
                                    dict_status_zalo[curr_phone_zalo] = ""
                                    chat_box_on_chat = False
                                if not chat_box_on_chat:
                                    break
                                if chat_box_on_chat and len(chat_box_on_chat) > 0:
                                    document = get_base_id_zalo_json("D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "num_phone_zalo", f"Zalo_data_login_path_{id_device}", {
                                        "num_phone_zalo": curr_phone_zalo})[0]
                                    list_prior_chat_boxes = document['list_prior_chat_boxes']
                                    # print("Lịch sử box chat được thêm vào là:",
                                    #    chat_box_on_chat)
                                    socketio.emit('receive_new_message_from_ntd', {
                                        'name_ntd': name_ntd, 'friend_or_not': friend_or_not, 'status': "Có dữ liệu mới từ khách hàng gửi đến"})
                                    socketio.emit('receive_list_prior_chat_box', {
                                        'user_name': user_name, 'list_prior_chat_boxes': list_prior_chat_boxes, 'status': "Có dữ liệu mới từ khách hàng gửi đến"})
                    except Exception as e:
                        dict_status_zalo[curr_phone_zalo] = ""
                        print("Lỗi gặp phải là ", e)
                        break

                else:
                    break
                eventlet.sleep(3)
            try:
                ut = update_d.xpath('//*[@text="Ưu tiên"]')
            except Exception as e:
                continue

            if ut.exists:
                if curr_phone_zalo != now_phone_zalo[id_device]:
                    print("Số điện thoại đã bị thay đổi: ",
                        now_phone_zalo[id_device])
                    continue
                print("Có tồn tại ưu tiên")
                ck_noti = False
                boxes = api_update_list_prior_chat_boxes(
                    {"num_phone_zalo": curr_phone_zalo}, max_chat_boxes=8, scroll_or_not=False)

                if not boxes or len(boxes) == 0:
                    eventlet.sleep(3)
                    continue
                num = len(boxes)-1
                if num == -1:
                    continue
                # print(boxes)
                for ik in range(len(boxes)):
                    it = num-ik
                    for id in range(len(list_prior_chat_boxes)):
                        if boxes[it]['name'] == list_prior_chat_boxes[id]['name']:
                            if ":" in boxes[it]['message']:
                                boxes[it]['message'] = boxes[it]['message'].split(":")[
                                    1].strip()
                            if "…" in boxes[it]['message']:
                                boxes[it]['message'] = boxes[it]['message'].split("…")[
                                    0].strip()

                            if boxes[it]['message'] not in list_prior_chat_boxes[id]['message'] and list_prior_chat_boxes[id]['message'] not in boxes[it]['message']:
                                print("Tin nhắn 1 là: ", boxes[it]['message'])
                                print("Tin nhắn 2 là: ",
                                    list_prior_chat_boxes[id]['message'])
                                list_prior_chat_boxes[id]['time'] = boxes[it]['time']
                                list_prior_chat_boxes[id]['message'] = boxes[it]['message']
                                list_prior_chat_boxes[id]['status'] = "unseen"
                                list_prior_chat_boxes.insert(
                                    0, list_prior_chat_boxes.pop(id))
                                data_update = {"num_phone_zalo": curr_phone_zalo,
                                            "list_prior_chat_boxes": list_prior_chat_boxes}
                                ck_noti = True

                            break
                if ck_noti:
                    print("Có gọi socket phiên bản 2 không")
                    update_base_document_json(
                        "D:/tool-fb-mobile/tool-fb-mobile - Copy - stagging/Zalo_base", "num_phone_zalo", f"Zalo_data_login_path_{id_device}", data_update)
                    socketio.emit('receive_list_prior_chat_box', {
                        'user_name': user_name, 'list_prior_chat_boxes': list_prior_chat_boxes, 'status': "Có dữ liệu mới từ khách hàng gửi đến"})
        except Exception as e:
            print("Lỗi trong quá trình lắng nghe tin nhắn ", e)

        eventlet.sleep(2)







# Hàm lấy dữ liệu 
def load_log_check_ran():
    """Đọc file log, nếu không có thì trả về list rỗng"""
    if not os.path.exists(LOG_FILE_CHECK_RAN_PATH):
        return []
    try:
        with open(LOG_FILE_CHECK_RAN_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return []

def save_log_check_ran(data):
    """Lưu dữ liệu vào file log"""
    with open(LOG_FILE_CHECK_RAN_PATH, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

def check_zalo_ran_today(device_id):
    """
    Kiểm tra xem device_id đã chạy trong ngày hôm nay chưa.
    Trả về True nếu đã chạy, False nếu chưa.
    """
    logs = load_log_check_ran()
    today_str = datetime.now().strftime("%Y-%m-%d")
    
    for entry in logs:
        if entry.get('device_id') == device_id and entry.get('last_run_date') == today_str:
            return True
    return False

def mark_zalo_completed(device_id):
    """
    Ghi nhận device_id đã chạy xong hôm nay.
    Cập nhật ngày nếu đã có, thêm mới nếu chưa có.
    """
    logs = load_log_check_ran()
    today_str = datetime.now().strftime("%Y-%m-%d")
    found = False
    
    for entry in logs:
        if entry.get('device_id') == device_id:
            entry['last_run_date'] = today_str
            found = True
            break
    
    if not found:
        logs.append({
            "device_id": device_id,
            "last_run_date": today_str
        })
    
    save_log_check_ran(logs)
    print(f"✅ Đã ghi nhận log chạy Zalo cho thiết bị {device_id} vào ngày {today_str}")


if __name__ == "__main__":
    for device_id in list(dict_device_and_phone.keys()):
        now_phone_zalo[device_id] = ""
        dict_process_id[device_id] = 0
        dict_queue_device[device_id] = []
        dict_id_chat[device_id] = ""
        dict_device_and_phone[device_id] = []
        # if not check_zalo_ran_today(device_id):
        socketio.start_background_task(
            target=background_first_crawl_per_day, id_device=device_id)
        socketio.start_background_task(
            target=background_update_data_loop, id_device=device_id)
    #socketio.run(app, host="0.0.0.0", port=8001,
    #                   debug=True, use_reloader=False)
    
    socketio.run(
        app,
        host="0.0.0.0",
        port=8001,
        debug=True,
        use_reloader=False,
        certfile="ssl/fullchain.pem",
        keyfile="ssl/privkey.pem",
        server_side=True
    )
