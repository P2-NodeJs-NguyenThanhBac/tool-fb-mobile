import json
import os
from util.const_values import DEVICE_STATUS_PATH

dict_device_and_phone = {
    "TSPNH6GYZLPJBY6X": ['0971335869', '0978025150', '0967791241'], #A03
    "9PAM7DIFW87DOBEU": ['0869773496', '0395459520', '0988003410'], #A09
    "2926294610DA007N": [],
    "7DXCUKKB6DVWDAQO": ['0385765903', '0963904347', '0969106521'], #A02
    "7HYP4T4XTS4DXKCY": ['0968082574', '0964876703', '0978722184'], #A04
    "CQIZKJ8P59AY7DHI": ['0963101851', '0971207216', '0969824937'], #A08
    "CEIN4X45I7ZHFEFU": ['0973418952', '0971412658', '0961991742'], #A06
    "8HMN4T9575HAQWLN": ['0978585641', '0982305784', '0973106450'], #A07
    "UWJJOJLB85SO7LIZ": ['0865772900', '0852039719', '0963563276'], #A05
    "MJZDFY896TMJBUPN": ['0367614180', '0975629854', '0966049501'], #A12
    "F6NZ5LRKWWGACYQ8": ['0913591672', '0359615945', '0972381905'], #A14
    "QK8TEMKZMBYHPV6P": ['0984485936', '0968902871', '0985771347'], #A15
    "IJP78949G69DKNHM": ['0987608429', '0337416995', '0356468640'], #A16
    "EM4DYTEITCCYJNFU": ['0966117160', '0961963671', '0966578630'], #A17
    "PN59BMHYPFXCPN8T": ['0966338017', '0383757614', '0982470403'], #A18
    "Z5LVOF4PRGXGTS9H": ['0988658315', '0338734680', '0967791241'] #A19
}

for id_device in dict_device_and_phone.keys():
    file_path = f"Zalo_base/device_status_{id_device}.json"
    if os.path.exists(file_path):
       os.remove(file_path)  # Xóa file nếu có
    device_status = {
        "active": False,
        "max_message_per_day": [],
        "max_add_friend_per_day": []
    }
    for phone in dict_device_and_phone[id_device]:
        device_status['max_message_per_day'].append({phone: 10})
        device_status['max_add_friend_per_day'].append({phone: 1})
    with open(DEVICE_STATUS_PATH(id_device), 'w') as f:
        json.dump(device_status, f, indent=4)