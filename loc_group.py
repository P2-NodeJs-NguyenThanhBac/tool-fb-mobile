import re
import unicodedata
from pymongo import MongoClient

MONGO_URI = "mongodb://myuser_duc:Anhduc14062002%40%23%24@123.24.206.25:27017/?authSource=admin"  # sửa lại
client = MongoClient(MONGO_URI)

db = client["Facebook"]
col = db["Link-groups"]
# col = db["Link_groups_test"]


def normalize_text(text: str) -> str:
    if not text:
        return ""

    text = str(text).strip().lower()

    # Chuẩn hóa unicode tổ hợp -> dạng chuẩn
    text = unicodedata.normalize("NFKC", text)
    text = unicodedata.normalize("NFD", text)

    # Bỏ dấu tiếng Việt
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = text.replace("đ", "d").replace("Đ", "d")

    # chuẩn hóa ký tự đặc biệt thành khoảng trắng
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    return text


# Từ khóa mạnh, thấy là gần như đúng tuyển dụng
strong_include_pattern = re.compile(
    r"\b("
    r"tuyen|"
    r"tuyen nv|"
    r"tuyen nhan vien|"
    r"tuyen dung|"
    r"tim viec|"
    r"viec lam|"
    r"tuyen nhan su|"
    r"tuyen gap|"
    r"recruitment|"
    r"recruit|"
    r"hiring|"
    r"job|"
    r"jobs"
    r")\b",
    re.IGNORECASE
)

# Từ khóa trung bình, có thể liên quan việc làm nhưng chưa chắc
medium_include_pattern = re.compile(
    r"\b("
    r"career|careers|"
    r"hr|"
    r"nhan su|"
    r"ung tuyen"
    r")\b",
    re.IGNORECASE
)

# Các từ dễ gây nhiễu nếu KHÔNG có strong include
soft_exclude_pattern = re.compile(
    r"\b("
    r"cong dong|"
    r"hoi nhom|"
    r"giao luu|"
    r"chia se|"
    r"kien thuc|"
    r"hoc tap|"
    r"dao tao|"
    r"chung khoan|"
    r"dau tu|"
    r"crypto|"
    r"coin|"
    r"forex|"
    r"bat dong san|"
    r"mua ban|"
    r"bien phien dich|"
    r"to chuc su kien|"
    r"vien chuc|"
    r"cong chuc"
    r")\b",
    re.IGNORECASE
)


def classify_group(name: str) -> str:
    norm_name = normalize_text(name)

    has_strong = bool(strong_include_pattern.search(norm_name))
    has_medium = bool(medium_include_pattern.search(norm_name))
    has_soft_exclude = bool(soft_exclude_pattern.search(norm_name))

    # Có từ khóa tuyển dụng mạnh -> ưu tiên là đúng
    if has_strong:
        return "relevant"

    # Không có strong, có medium nhưng dính soft exclude -> nghi ngờ / sai
    if has_medium and not has_soft_exclude:
        return "suspicious"

    return "irrelevant"


relevant = []
suspicious = []
irrelevant = []

docs = col.find({}, {"Name": 1, "Link": 1})

for doc in docs:
    name = doc.get("Name", "") or ""
    result = classify_group(name)

    if result == "relevant":
        relevant.append(doc)
    elif result == "suspicious":
        suspicious.append(doc)
    else:
        irrelevant.append(doc)

print(f"Tổng đúng tuyển dụng: {len(relevant)}")
print(f"Tổng nghi ngờ: {len(suspicious)}")
print(f"Tổng không phù hợp: {len(irrelevant)}")

print("\nVí dụ group đúng:")
for d in relevant[:20]:
    print("-", d.get("Name"))

print("\nVí dụ group nghi ngờ:")
for d in suspicious[:20]:
    print("-", d.get("Name"))

print("\nVí dụ group sai:")
for d in irrelevant[:20]:
    print("-", d.get("Name"))