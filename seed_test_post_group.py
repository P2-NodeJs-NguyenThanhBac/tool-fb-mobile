from datetime import datetime
from bson import ObjectId
from pymongo import MongoClient
import urllib.parse
import json

def get_client_name(Type="Base"):
    with open("DatabaseAccounts.env", "r", encoding="utf-8") as file:
        data = json.load(file).get(Type, {})
    username = urllib.parse.quote_plus(data["username"])
    password = urllib.parse.quote_plus(data["pwd"])
    return f"mongodb://{username}:{password}@123.24.206.25:27017/?authSource=admin"

client = MongoClient(get_client_name())
db = client["Facebook"]

# ===== sửa 3 giá trị này =====
USER_ID = "0987610815"   # tài khoản FB / số dùng để switch account
GROUP_LINK = "groups/vieclamcokhitaihanoi"
CONTENT = """TUYỂN GẤP THỢ CƠ KHÍ & LAO ĐỘNG PHỔ THÔNG
Làm việc tại: Vân Canh, Hoài Đức, Hà Nội
Vị trí:
Thợ cơ khí
Thợ phụ
Bốc xếp
 Thu nhập: 9 – 15 triệu/tháng + thưởng
Quyền lợi:
Bao ăn ở cho người ở xa
Đóng BH đầy đủ
Tăng lương theo năng lực
Được đào tạo tay nghề
 Yêu cầu:
Chăm chỉ, có sức khỏe
Không cần kinh nghiệm (được đào tạo)
 Thời gian: Thứ 2 – Thứ 7 (8h – 17h)
 Inbox hoặc liên hệ ngay để nhận việc sớm!
"""

# 1) tạo bài test trong Bai-dang
post_doc = {
    "user_id": USER_ID,
    "content": CONTENT,
    "group_link": GROUP_LINK,
    "status": "Đang chờ đăng",
    "created_at": datetime.now(),
    "updated_at": datetime.now(),
}
post_res = db["Bai-dang"].insert_one(post_doc)
post_oid = post_res.inserted_id

# 2) tạo command test trong Commands
cmd_doc = {
    "type": "post_to_group",
    "user_id": USER_ID,
    "Status": "Pending",
    "Created_at": datetime.now(),
    "Executed_at": None,
    "params": {
        "oid": str(post_oid),
        "content": CONTENT,
        "files": [],
        "group_link": GROUP_LINK,
        "auto_join_if_needed": False,
        "join_timeout_sec": 15,
    },
}
cmd_res = db["Commands"].insert_one(cmd_doc)
command_id = cmd_res.inserted_id

print("=== CREATED TEST DATA ===")
print("post_oid   =", str(post_oid))
print("command_id =", str(command_id))
print("group_link =", GROUP_LINK)
print("user_id    =", USER_ID)