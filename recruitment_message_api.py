from __future__ import annotations

import json
import os
import random
import re
import unicodedata
import zipfile
import ast
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import xml.etree.ElementTree as ET

import requests
from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pymongo import MongoClient
from pymongo_management import get_client_name

def _dotenv_value(name: str) -> str:
    env_path = Path(".env")
    if not env_path.exists():
        return ""
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() == name:
                return value.strip().strip('"').strip("'")
    except Exception:
        return ""
    return ""

def _env_value(name: str) -> str:
    return os.getenv(name, "").strip() or _dotenv_value(name)

def _bool_value(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default

OPENAI_MODEL = _env_value("FACEBOOK_RECRUITMENT_OPENAI_MODEL") or "gpt-4o-mini"
OPENAI_TIMEOUT = float(os.getenv("FACEBOOK_RECRUITMENT_OPENAI_TIMEOUT", "60"))
API_PORT = int(os.getenv("FACEBOOK_RECRUITMENT_MESSAGE_API_PORT", "8025"))
MONGO_DB_NAME = os.getenv("FACEBOOK_RECRUITMENT_MONGO_DB", "FB_database")
MONGO_SOURCE_COLLECTION = os.getenv("FACEBOOK_RECRUITMENT_SOURCE_COLLECTION", "data_successful")
MONGO_TARGET_COLLECTION = os.getenv("FACEBOOK_RECRUITMENT_TARGET_COLLECTION", "data_ntd_spam")
INDUSTRY_XLSX_PATH = Path(os.getenv("FACEBOOK_RECRUITMENT_INDUSTRY_XLSX", "Điểm thao ngành nghề.xlsx"))
DEFAULT_USE_OPENAI = _bool_value(_env_value("FACEBOOK_RECRUITMENT_USE_OPENAI"), default=True)
FALLBACK_POSITION = "vị trí trong bài tuyển dụng"
MALE_NAME_HINTS = {
    "anh", "ông", "mr", "a",
    "an", "bảo", "bình", "cường", "dũng", "duy", "đạt", "đức", "giang",
    "hải", "hiếu", "hoàng", "hùng", "hưng", "huy", "khải", "khang", "khoa",
    "khôi", "kiên", "long", "mạnh", "minh", "nam", "nghĩa", "nhân", "phát",
    "phong", "phúc", "quang", "quốc", "sơn", "tài", "thành", "thắng",
    "thiện", "trí", "trung", "trường", "tuấn", "tùng", "tân", "tấn", "việt", "vinh",
    "peter", "john", "david", "michael",
}

FEMALE_NAME_HINTS = {
    "chị", "bà", "ms", "mrs", "c",
    "anh",  # Vietnamese given name "Anh" is often female; handled only as token after first token.
    "ánh", "an", "bích", "chi", "diễm", "dung", "giang", "giao", "hà", "hằng", "hoa",
    "hồng", "hương", "khánh", "lan", "linh", "loan", "mai", "my", "nga",
    "ngân", "ngọc", "như", "oanh", "phương", "quỳnh", "thảo", "thu",
    "thư", "thúy", "trang", "trinh", "tuyết", "uyên", "vy", "yến",
    "mary", "anna", "jane",
}

VIETNAMESE_SURNAME_HINTS = {
    "nguyen", "tran", "le", "pham", "hoang", "huynh", "phan", "vu", "vo",
    "dang", "bui", "do", "ho", "ngo", "duong", "ly", "truong", "dinh",
    "mai", "cao", "dao", "luu", "trinh", "ta", "ha", "chau", "lam",
}

MALE_NAME_HINTS_ASCII = {
    "ach", "an", "ba", "bach", "bang", "bao", "binh", "buu", "canh",
    "chien", "chinh", "cong", "cu", "cuong", "dai", "dang", "danh",
    "dat", "dien", "dinh", "doan", "dong", "duan", "duc", "dung",
    "duong", "duy", "duyen", "duyeth", "gia", "hai", "hanh", "hau",
    "hien", "hiep", "hieu", "hoa", "hoai", "hoang", "huan", "hung",
    "huu", "huy", "kha", "khac", "khai", "khang", "khoa", "khoi",
    "kien", "lap", "loc", "loi", "long", "luan", "luc", "man",
    "manh", "minh", "nam", "nghia", "nhan", "nhat", "ninh", "phat",
    "phong", "phu", "phuc", "phuoc", "quang", "quoc", "quy", "sang",
    "sinh", "son", "sy", "tai", "tam", "tan", "thanh", "thang",
    "the", "thien", "thinh", "tho", "thong", "thuan", "toan", "tri",
    "trieu", "trung", "truong", "tu", "tuan", "tung", "viet", "vin",
    "vinh", "vuong", "xuan", "y",
    "alex", "andrew", "anthony", "ben", "brian", "chris", "daniel",
    "david", "eric", "henry", "james", "john", "kevin", "mark",
    "michael", "paul", "peter", "robert", "steven", "tony",
}

FEMALE_NAME_HINTS_ASCII = {
    "ai", "an", "anh", "anhh", "bich", "cam", "chi", "cuc", "dao",
    "diem", "dung", "duyen", "giang", "giao", "ha", "han", "hang",
    "hanh", "hien", "hoa", "hong", "hue", "huong", "huyen", "khanh",
    "khue", "kieu", "lan", "lieu", "linh", "loan", "ly", "mai",
    "mi", "mieu", "my", "nga", "ngan", "nhi", "nhien", "nhu",
    "nhung", "nu", "nuyen", "oanh", "phuong", "que", "quynh", "sa",
    "sam", "suong", "tam", "thanh", "thao", "thien", "thu", "thuy",
    "thy", "tien", "tienn", "tram", "trang", "trinh", "truc", "tu",
    "tuyet", "uyen", "van", "vi", "vy", "xuan", "y", "yen", "yngoc",
    "alice", "amanda", "amy", "angela", "anna", "bella", "catherine",
    "christine", "diana", "emily", "emma", "helen", "jane", "jessica",
    "julie", "kate", "kelly", "linda", "lisa", "lucy", "mary",
    "michelle", "nancy", "sarah", "susan",
}

FEMALE_MIDDLE_NAME_HINTS_ASCII = {"thi"}
MALE_MIDDLE_NAME_HINTS_ASCII = {"van"}
MALE_COMPOUND_ANH_PREFIXES = {"hoang", "quoc", "duc", "tuan", "nhat", "viet"}
FEMALE_COMPOUND_ANH_PREFIXES = {"van", "thuc", "ngoc", "mai", "lan", "minh", "thuy", "ha"}

NAME_GENDER_WEIGHTS_ASCII = {
    # Mostly male given names
    "an": 0.80, "bao": 0.80, "bach": 1.10, "bac": 1.00, "bang": 1.00,
    "binh": 0.35, "canh": 1.10, "chien": 1.20, "chinh": 0.85,
    "cong": 1.05, "cuong": 1.30, "dai": 1.05, "dang": 0.90,
    "danh": 1.05, "dat": 1.30, "dien": 1.05, "dinh": 0.85,
    "doan": 0.80, "dong": 1.10, "duc": 1.30, "duy": 1.20,
    "hieu": 1.20, "hoang": 0.75, "hung": 1.30, "huy": 1.20, "hai": 0.70,
    "hau": 0.75, "hiep": 1.15, "huan": 1.15, "huu": 1.10, "kha": 0.85,
    "khac": 1.10,
    "khai": 1.20, "khang": 1.25, "khoa": 1.20, "khoi": 1.20, "kien": 1.25,
    "lap": 1.05, "loc": 1.10, "loi": 1.05, "long": 1.25, "luan": 1.10,
    "luc": 1.05, "manh": 1.25, "nam": 1.30, "nghia": 1.15,
    "nhan": 0.95, "phat": 1.20, "phong": 1.20, "phuc": 1.20, "quan": 1.25,
    "quang": 1.30, "quoc": 1.15, "nhat": 1.15, "ninh": 0.85,
    "phu": 1.10, "phuoc": 1.15, "quy": 0.70, "sang": 1.05,
    "sinh": 0.90, "son": 1.20, "sy": 1.05, "tai": 1.15, "tan": 1.30,
    "tien": 1.05, "thanh": 0.55, "thang": 1.25, "the": 1.05,
    "thien": 1.15, "thinh": 1.20, "tho": 0.75, "thong": 1.10,
    "toan": 1.20, "tri": 1.20, "trieu": 1.00, "truong": 1.15,
    "trung": 1.15, "tuan": 1.25, "tung": 1.20, "vinh": 1.20,
    "vuong": 1.10,
    "vy": -1.10,

    # Mostly female given names
    "ai": -1.05, "anh": -0.40, "anhh": -0.40, "anhthu": -1.25,
    "anhthuong": -1.10, "bich": -1.20, "cam": -1.05, "chau": -0.90,
    "chi": -1.20, "cuc": -1.10, "dao": -0.85, "diem": -1.20,
    "diep": -1.05, "duyen": -1.10, "han": -1.10, "hang": -1.20,
    "hoa": -1.15, "hong": -1.15, "hue": -1.10, "huong": -1.20,
    "huyen": -1.20, "khue": -1.10, "kieu": -1.00, "lan": -1.20, "lieu": -1.05,
    "linh": -1.05, "ly": -0.90, "mi": -0.95,
    "loan": -1.20, "mai": -1.15, "my": -1.15, "nga": -1.15, "ngan": -1.20,
    "nhi": -1.15, "nhien": -0.90, "nhu": -1.15, "nhung": -1.15,
    "nu": -1.20, "oanh": -1.20, "phuong": -0.95, "que": -1.00,
    "quynh": -1.20, "sa": -0.90, "suong": -1.05, "thao": -1.15,
    "thu": -1.10, "thuy": -1.20, "thy": -1.10, "tienn": -0.80,
    "tram": -1.15, "trang": -1.25, "trinh": -1.15, "truc": -1.05,
    "tuyen": -0.85, "tuyet": -1.20, "uyen": -1.20, "vi": -1.00,
    "yen": -1.15,

    # Common unisex/ambiguous names: intentionally weak
    "ngoc": -0.20, "minh": 0.15, "khanh": -0.10, "dung": 0.05,
    "giang": -0.10, "duong": 0.15, "gia": 0.00,
    "viet": 0.55, "ha": -0.90,
    "hanh": -0.35, "hien": -0.25, "hoai": -0.10, "lam": -0.90,
    "tam": -0.10, "thuan": 0.25, "tu": -0.15, "van": -0.25,
    "xuan": -0.10, "y": -0.15,
}

FEMALE_COMPOUND_GIVEN_ASCII = {
    ("ai", "linh"), ("an", "nhi"), ("anh", "dao"), ("anh", "duong"),
    ("anh", "ngoc"), ("anh", "thu"), ("anh", "thy"), ("bao", "chau"),
    ("bao", "han"), ("bao", "ngoc"), ("bao", "nhi"), ("bao", "tran"),
    ("cam", "ly"), ("diem", "my"), ("diep", "anh"), ("gia", "han"),
    ("gia", "linh"), ("ha", "anh"), ("ha", "linh"), ("hai", "yen"),
    ("hoai", "an"), ("hoai", "thu"), ("hong", "anh"), ("hong", "nhung"),
    ("huong", "giang"), ("khanh", "linh"), ("kieu", "anh"),
    ("lan", "anh"), ("lan", "chi"), ("mai", "anh"), ("mai", "chi"),
    ("mai", "lan"), ("mai", "linh"), ("minh", "anh"), ("minh", "chau"),
    ("minh", "hang"), ("minh", "huyen"), ("minh", "ngoc"),
    ("minh", "phuong"), ("minh", "thu"), ("minh", "trang"),
    ("moc", "an"), ("my", "duyen"), ("my", "hanh"), ("my", "linh"),
    ("my", "tien"), ("ngoc", "anh"), ("ngoc", "ha"), ("ngoc", "han"),
    ("ngoc", "huyen"), ("ngoc", "linh"), ("ngoc", "mai"), ("ngoc", "nhi"),
    ("ngoc", "tram"), ("ngoc", "trinh"), ("nhat", "le"), ("phuong", "anh"),
    ("phuong", "linh"), ("phuong", "thao"), ("quynh", "anh"),
    ("quynh", "chi"), ("quynh", "nhu"), ("thanh", "ha"),
    ("thanh", "huyen"), ("thanh", "tam"), ("thao", "anh"),
    ("thao", "my"), ("thu", "ha"), ("thu", "thao"), ("thuy", "anh"),
    ("thuy", "duong"), ("thuy", "linh"), ("tram", "anh"), ("tu", "anh"),
    ("tuyet", "mai"), ("xuan", "mai"),
}

MALE_COMPOUND_GIVEN_ASCII = {
    ("anh", "duc"), ("anh", "khoa"), ("anh", "minh"), ("anh", "quan"),
    ("anh", "tuan"), ("bao", "khang"), ("bao", "long"), ("bao", "nam"),
    ("bao", "phuc"), ("chi", "bao"), ("dang", "khoa"), ("dang", "quang"),
    ("duc", "anh"), ("duc", "huy"), ("duc", "minh"), ("duc", "thang"),
    ("duc", "tuan"), ("gia", "bao"), ("gia", "huy"), ("gia", "khang"),
    ("gia", "minh"), ("hai", "dang"), ("hoang", "anh"), ("hoang", "duy"),
    ("hoang", "long"), ("hoang", "minh"), ("hoang", "nam"), ("hoang", "phuc"),
    ("huu", "nghia"), ("huu", "phuoc"), ("khac", "huy"), ("manh", "cuong"),
    ("manh", "huy"), ("minh", "duc"), ("minh", "hieu"), ("minh", "khang"),
    ("minh", "nhat"), ("minh", "phuc"), ("minh", "quan"), ("minh", "quang"),
    ("minh", "tri"), ("minh", "tuan"), ("ngoc", "huy"), ("ngoc", "quan"),
    ("ngoc", "tan"), ("nhat", "anh"), ("nhat", "minh"), ("quang", "huy"),
    ("quang", "minh"), ("quoc", "anh"), ("quoc", "bao"), ("quoc", "dat"),
    ("quoc", "huy"), ("quoc", "viet"), ("son", "tung"), ("thanh", "binh"),
    ("thanh", "dat"), ("thanh", "long"), ("thanh", "phong"),
    ("thanh", "tung"), ("tuan", "anh"), ("tuan", "kiet"), ("viet", "anh"),
    ("viet", "duc"), ("viet", "hoang"), ("viet", "khanh"),
}

LAO_DONG_PHO_THONG_PATTERNS = [
    r"\blao\s+dong\s+pho\s+thong\b",
    r"\bldpt\b",
    r"\bcong\s+nhan\b",
    r"\bcong\s+viec\s+pho\s+thong\b",
    r"\bviec\s+pho\s+thong\b",
    r"\bnam\s+nu\s+pho\s+thong\b",
]

@dataclass
class RecruitmentJob:
    raw: Dict[str, Any]
    link_user_post: str
    user_name: str
    text: str
    link_post: str = ""

    @property
    def dedupe_key(self) -> str:
        post_id = str(self.raw.get("_id") or self.raw.get("id") or "").strip()
        return post_id or self.link_user_post or self.link_post

def _safe_str(value: Any) -> str:
    return str(value or "").strip()

def normalize_spaces(value: str) -> str:
    value = unicodedata.normalize("NFKC", _safe_str(value))
    return re.sub(r"\s+", " ", value).strip()

def strip_accents(value: str) -> str:
    value = unicodedata.normalize("NFD", _safe_str(value))
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    return value.replace("đ", "d").replace("Đ", "D")

PROFILE_NAME_SUFFIX_STARTERS = {
    "hr", "hcns", "recruiter", "recruitment", "jobs", "job", "career", "careers",
}

PROFILE_NAME_SUFFIX_PHRASES = {
    ("tuyen", "dung"),
    ("viec", "lam"),
    ("tim", "viec"),
    ("nhan", "su"),
    ("hanh", "chinh"),
}

BUSINESS_PROFILE_PREFIX_PHRASES = {
    ("cong", "ty"),
    ("cty",),
    ("cua", "hang"),
    ("shop",),
    ("quan",),
    ("nha", "hang"),
    ("khach", "san"),
    ("spa",),
    ("salon",),
    ("trung", "tam"),
    ("truong",),
    ("xuong",),
    ("nha", "may"),
    ("trang", "trai"),
    ("nong", "trai"),
    ("hop", "tac", "xa"),
    ("htx",),
}

def _name_tokens(user_name: str) -> List[Tuple[str, str]]:
    raw_tokens = [t.strip(".,:;()[]{}'\"") for t in re.split(r"\s+", normalize_spaces(user_name))]
    tokens = []
    for token in raw_tokens:
        if token:
            tokens.append((token, strip_accents(token).lower()))
    while tokens and tokens[0][1] in {"mr", "ms", "mrs", "anh", "chi", "ong", "ba", "a", "c"}:
        tokens.pop(0)

    normalized_tokens = [normalized for _, normalized in tokens]
    for phrase in BUSINESS_PROFILE_PREFIX_PHRASES:
        if tuple(normalized_tokens[:len(phrase)]) == phrase:
            return []

    for index, (_, normalized) in enumerate(tokens):
        next_normalized = tokens[index + 1][1] if index + 1 < len(tokens) else ""
        if index > 0 and (
            normalized in PROFILE_NAME_SUFFIX_STARTERS
            or (normalized, next_normalized) in PROFILE_NAME_SUFFIX_PHRASES
        ):
            return tokens[:index]
    return tokens

def first_name_token(user_name: str) -> str:
    tokens = _name_tokens(user_name)
    if not tokens:
        return ""

    raw = [token for token, _ in tokens]
    norm = [token for _, token in tokens]

    if len(tokens) >= 3 and norm[0] in VIETNAMESE_SURNAME_HINTS and norm[-1] in VIETNAMESE_SURNAME_HINTS and norm[-2] == "anh":
        return " ".join(raw[-3:-1])

    if len(tokens) >= 3 and norm[0] in VIETNAMESE_SURNAME_HINTS:
        if norm[-1] == "anh":
            return " ".join(raw[-2:])
        return raw[-1]

    if len(tokens) >= 3 and norm[-1] in VIETNAMESE_SURNAME_HINTS:
        if norm[-2] == "anh":
            return " ".join(raw[-3:-1])
        return raw[-2]

    if len(tokens) == 2:
        pair = tuple(norm)
        if norm[0] == norm[1]:
            return raw[0]
        if pair in MALE_COMPOUND_GIVEN_ASCII or pair in FEMALE_COMPOUND_GIVEN_ASCII:
            return " ".join(raw)
        first_weight = abs(NAME_GENDER_WEIGHTS_ASCII.get(norm[0], 0.0))
        last_weight = abs(NAME_GENDER_WEIGHTS_ASCII.get(norm[-1], 0.0))
        if 0 < first_weight <= 0.60 and last_weight >= 0.75:
            return raw[1]
        if norm[0] in VIETNAMESE_SURNAME_HINTS:
            return raw[1]
        if norm[-1] in VIETNAMESE_SURNAME_HINTS:
            return raw[0]
        return " ".join(raw)

    return raw[0]

def _name_gender_weight(token: str) -> float:
    token = _safe_str(token).lower()
    if not token:
        return 0.0
    if token in NAME_GENDER_WEIGHTS_ASCII:
        return NAME_GENDER_WEIGHTS_ASCII[token]
    if token in MALE_NAME_HINTS_ASCII or (token in MALE_NAME_HINTS and token != "anh"):
        return 0.70
    if token in FEMALE_NAME_HINTS_ASCII or token in FEMALE_NAME_HINTS:
        return -0.70
    return 0.0

def infer_honorific(user_name: str) -> Optional[str]:
    raw_norm_tokens = [strip_accents(t.strip(".,:;()[]{}'\"")).lower() for t in re.split(r"\s+", normalize_spaces(user_name)) if t.strip(".,:;()[]{}'\"")]
    tokens = [token for _, token in _name_tokens(user_name)]
    if not tokens:
        return None

    given_name = first_name_token(user_name)
    given_tokens = [token for _, token in _name_tokens(given_name)]
    if not given_tokens:
        given_tokens = tokens[-2:] if len(tokens) >= 2 else tokens

    last_given = given_tokens[-1]
    middle_tokens = tokens[1:-1] if len(tokens) >= 3 else []
    compound_last_2 = tuple(given_tokens[-2:]) if len(given_tokens) >= 2 else ()

    if raw_norm_tokens[:1] == ["anh"] and last_given in MALE_NAME_HINTS_ASCII:
        return "anh"

    # Strong Vietnamese middle-name signals.
    if any(token in FEMALE_MIDDLE_NAME_HINTS_ASCII for token in middle_tokens):
        return "chị"
    if any(token in MALE_MIDDLE_NAME_HINTS_ASCII for token in middle_tokens):
        return "anh"

    # Strong compound given-name signals, especially names ending in "Anh".
    if compound_last_2 in MALE_COMPOUND_GIVEN_ASCII:
        return "anh"
    if compound_last_2 in FEMALE_COMPOUND_GIVEN_ASCII:
        return "chị"

    if last_given == "anh" and len(given_tokens) >= 2:
        previous_given = given_tokens[-2]
        if previous_given in MALE_COMPOUND_ANH_PREFIXES:
            return "anh"
        if previous_given in FEMALE_COMPOUND_ANH_PREFIXES:
            return "chị"
        return None

    # Weighted scoring. Last given name is the strongest signal.
    score = 0.0
    for index, token in enumerate(given_tokens):
        weight = _name_gender_weight(token)
        if not weight:
            continue
        if index == len(given_tokens) - 1:
            score += weight * 2.0
        else:
            score += weight * 0.85

    # Middle names are useful, but weaker than the actual given name.
    for token in middle_tokens:
        if token not in given_tokens:
            score += _name_gender_weight(token) * 0.35

    # Require enough confidence. If ambiguous, return None and use neutral greeting.
    if score >= 1.15:
        return "anh"
    if score <= -1.15:
        return "chị"
    return None

def clean_post_lines(text: str, limit: int = 40) -> List[str]:
    seen = set()
    lines = []
    for raw_line in _safe_str(text).splitlines():
        line = normalize_spaces(raw_line)
        if not line:
            continue
        line = re.sub(r"^[\s\-\*\+•–—]+", "", line).strip()
        line = re.sub(r"\s*(Ẩn bớt|@nêu bật)\s*$", "", line, flags=re.I).strip()
        if not line:
            continue
        key = strip_accents(line).lower()
        if key in seen:
            continue
        seen.add(key)
        lines.append(line)
        if len(lines) >= limit:
            break
    return lines

def is_recruitment_post(text: str, item: Optional[Dict[str, Any]] = None) -> bool:
    lines = clean_post_lines(text, limit=5)
    first_lines = " ".join(lines[:2])
    first_lines_norm = strip_accents(first_lines).lower()
    norm = strip_accents(normalize_spaces(text)).lower()
    if not norm:
        return False
    early_recruitment_signal = bool(re.search(
        r"\b(?:tuyen\s+dung|can\s+tuyen|dang\s+tuyen|ung\s+tuyen)\b|\btuyen\s+(?:gap\s+)?(?:\d+\s+)?(?:nhan\s+vien|nv|lao\s+dong|nam|nu|tho|ke\s+toan|bao\s+ve|moi\s+gioi|sales|sale|kinh\s+doanh)\b",
        norm,
    ))
    is_candidate_seeking = bool(re.search(
        r"^\s*(?:em|e|minh|toi|t|anh|chi|ban)?\s*(?:la\s+)?(?:sinh vien|nam|nu|mẹ|me|lao dong)?[^.]{0,80}\b(?:can|muon|mong|dang)\s+(?:tim|xin)\s+(?:viec|viec lam|cv)\b",
        first_lines_norm,
    ))
    contact_count = len(re.findall(r"(?<!\d)(?:0|\+84)\d(?:[\s.-]?\d){8,9}(?!\d)", _safe_str(text)))
    if is_candidate_seeking:
        return False

    education_ad_patterns = [
        r"\btuyen\s+sinh\s+(?:lop|khoa)\b",
        r"\bkhoa\s+hoc\b",
        r"\blop\s+(?:tieng|hoc)\b",
    ]
    education_context_patterns = [
        r"\bhoc\s+tieng\b",
        r"\bthi\s+hsk\b",
        r"\bgiao\s+trinh\b",
        r"\bgiao\s+vien\b",
        r"\bthoi\s+gian\s+hoc\b",
        r"\bgiu\s+cho\b",
    ]
    staff_recruitment_patterns = [
        r"\b(?:can\s+tuyen|dang\s+tuyen|tuyen\s+dung)\b",
        r"\btuyen\s+(?:gap\s+)?(?:\d+\s+)?(?:nhan\s+vien|nv|giao\s+vien|tro\s+giang|tu\s+van|sales|sale)\b",
        r"\bnhan\s+vien\b",
        r"\bthu\s+nhap\b|\bmuc\s+luong\b|\bluong\s+(?:co\s+ban|cung|tu|thoa\s+thuan)\b",
    ]
    if (
        any(re.search(pattern, norm) for pattern in education_ad_patterns)
        and any(re.search(pattern, norm) for pattern in education_context_patterns)
        and not any(re.search(pattern, norm) for pattern in staff_recruitment_patterns)
    ):
        return False

    recruitment_signals = [
        r"\btuyen\s+dung\b",
        r"\btuyendung\d*\b",
        r"\bcan\s+tuyen\b",
        r"\bdang\s+tuyen\b",
        r"\btuyen\s+(?:gap\s+)?(?:\d+\s+)?(?:nhan\s+vien|nv|lao\s+dong|nam|nu|tho|ke\s+toan|bao\s+ve|moi\s+gioi|sales|sale|kinh\s+doanh)\b",
        r"\bung\s+tuyen\b",
        r"\bviec\s+lam\b",
    ]
    has_recruitment_signal = any(re.search(pattern, norm) for pattern in recruitment_signals)

    real_estate_transaction_patterns = [
        r"\bcan\s+mua\s+(?:dat|nha|can\s+ho|chung\s+cu|bat\s+dong\s+san)\b",
        r"\bmua\s+(?:dat|nha|can\s+ho|chung\s+cu|bat\s+dong\s+san)\b",
        r"\bban\s+(?:dat|nha|can\s+ho|chung\s+cu|bat\s+dong\s+san)\b",
        r"\bdat\s+(?:nen|khu\s+do\s+thi|du\s+an)\b",
        r"\btai\s+chinh\s+[\d,.]+\s*(?:ty|ti|trieu|m)\s+can\s+mua\b",
    ]
    if not has_recruitment_signal and any(re.search(pattern, norm) for pattern in real_estate_transaction_patterns):
        return False

    if item is not None:
        label = _safe_str(item.get("predicted_label"))
        is_labeled_recruitment = strip_accents(label).lower() == "tin tuyen dung"
        if label and not is_labeled_recruitment and not has_recruitment_signal:
            return False

    ad_patterns = [
        r"\bcombo\b",
        r"\bdong\s+phuc\b",
        r"\bao\s+thun\b",
        r"\bao\s+lop\b",
        r"\bao\s+cap\b",
        r"\bqua\s+tang\b",
        r"\bphu\s+kien\b",
        r"\bsan\s+pham\b",
        r"\bthoi\s+trang\b",
        r"\bkhach\s+hang\s+co\s+nhu\s+cau\b",
        r"\bdat\s+hang\b",
        r"\bshop\b",
        r"\bin\s+(?:logo|anh|san\s+pham|theo\s+yeu\s+cau)\b",
        r"\bthiet\s+ke\s+va\s+in\b",
        r"\btem\s+nhan\s+mac\b",
        r"\bbao\s+in\s+theo\s+yeu\s+cau\b",
    ]
    ad_score = sum(1 for pattern in ad_patterns if re.search(pattern, norm))
    has_order_contact = bool(re.search(r"\b(?:zalo|dt|sdt|so\s+dien\s+thoai|lien\s+he)\b", norm))
    if not has_recruitment_signal and ad_score >= 3 and has_order_contact:
        return False

    return True

def _openai_api_key() -> str:
    api_key = _env_value("OPENAI_API_KEY")
    if api_key:
        return api_key

    key_file = os.getenv("OPENAI_API_KEY_FILE", "").strip()
    if key_file:
        try:
            return Path(key_file).read_text(encoding="utf-8").strip()
        except Exception as exc:
            print(f"[FACEBOOK_PHASE] Khong doc duoc OPENAI_API_KEY_FILE: {exc}")
    return ""

def _response_output_text(response_data: Dict[str, Any]) -> str:
    output_text = response_data.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    parts = []
    for item in response_data.get("output", []):
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []):
            if isinstance(content, dict) and isinstance(content.get("text"), str):
                parts.append(content["text"])
    return "\n".join(parts).strip()

def extract_recruitment_info_with_openai(text: str, user_name: str = "") -> Dict[str, Any]:
    api_key = _openai_api_key()
    if not api_key:
        raise ValueError("Missing OPENAI_API_KEY environment variable")

    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "is_recruitment": {"type": "boolean"},
            "position": {"type": "string"},
            "salary": {"type": "string"},
            "location": {"type": "string"},
            "company": {"type": "string"},
            "work_time": {"type": "string"},
            "requirements": {"type": "string"},
            "contact": {"type": "string"},
            "confidence": {"type": "number"},
        },
        "required": [
            "is_recruitment",
            "position",
            "salary",
            "location",
            "company",
            "work_time",
            "requirements",
            "contact",
            "confidence",
        ],
    }
    payload = {
        "model": OPENAI_MODEL,
        "input": [
            {
                "role": "developer",
                "content": (
                    "Extract structured information from a Vietnamese Facebook recruitment post. "
                    "Return only facts present in the post. Use empty strings for missing fields. "
                    "Keep values short, clean, and in Vietnamese."
                ),
            },
            {
                "role": "user",
                "content": f"User name: {user_name}\nPost:\n{text[:6000]}",
            },
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "recruitment_info",
                "strict": True,
                "schema": schema,
            }
        },
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    response = requests.post(
        "https://api.openai.com/v1/responses",
        headers=headers,
        json=payload,
        timeout=OPENAI_TIMEOUT,
    )
    response.raise_for_status()
    ai_info = json.loads(_response_output_text(response.json()))

    return {
        "position": normalize_spaces(ai_info.get("position", "")),
        "salary": normalize_spaces(ai_info.get("salary", "")),
        "location": normalize_spaces(ai_info.get("location", "")),
        "company": normalize_spaces(ai_info.get("company", "")),
        "work_time": normalize_spaces(ai_info.get("work_time", "")),
        "requirements": normalize_spaces(ai_info.get("requirements", "")),
        "contact": normalize_spaces(ai_info.get("contact", "")),
        "is_recruitment": bool(ai_info.get("is_recruitment")),
        "confidence": ai_info.get("confidence", ""),
        "source": "openai",
        "ai_used": True,
        "ai_error": "",
    }

def _is_generic_position_intro(norm: str) -> bool:
    return bool(re.search(
        r"\b(?:cac\s+vi\s+tri\s+sau|nhan\s+luc\s+cac\s+vi\s+tri|bo\s+sung\s+nhan\s+luc|nhu\s+cau\s+san\s+xuat|can\s+tuyen\s+dung\s+bo\s+sung)\b",
        norm,
    ))

def _clean_position_candidate(line: str) -> str:
    cleaned = re.sub(r"^\s*(?:\d+[\.\)]|[-+*•])\s*", "", normalize_spaces(line)).strip(" :-–—")
    cleaned = re.sub(r"\s*\([^)]*(?:kinh nghiệm|tiếng|yêu cầu|kinh nghiem|tieng|yeu cau)[^)]*\)\s*$", "", cleaned, flags=re.I)
    return normalize_spaces(cleaned)

def _is_position_candidate(norm: str) -> bool:
    if not norm or _is_generic_position_intro(norm):
        return False
    if re.search(r"\b(?:cach\s+thuc|ung\s+tuyen|gui\s+cv|email|zalo|hotline|lien\s+he|dia\s+chi|luong|thu\s+nhap|quyen\s+loi|yeu\s+cau)\b", norm):
        return False
    return bool(re.search(
        r"\b(?:truong\s+phong|nhan\s+vien|quan\s+ly|giam\s+sat|ke\s+toan|bao\s+ve|lai\s+xe|cong\s+nhan|tho|tu\s+van|kinh\s+doanh|sales|sale|nv)\b",
        norm,
    ))

def _extract_position_list_after_intro(norm_lines: List[Tuple[str, str]]) -> str:
    for index, (_, norm) in enumerate(norm_lines):
        if not _is_generic_position_intro(norm):
            continue

        positions = []
        for line, next_norm in norm_lines[index + 1:index + 8]:
            if re.search(r"\b(?:cach\s+thuc|ung\s+tuyen|gui\s+cv|email|zalo|hotline|lien\s+he|dia\s+chi)\b", next_norm):
                break
            is_list_item = bool(re.search(r"^\s*(?:\d+[\.\)]|[-+*•])\s*", line))
            if not is_list_item and not _is_position_candidate(next_norm):
                continue
            cleaned = _clean_position_candidate(line)
            cleaned_norm = strip_accents(cleaned).lower()
            if cleaned and _is_position_candidate(cleaned_norm) and cleaned not in positions:
                positions.append(cleaned)
            if len(positions) >= 4:
                break

        if positions:
            return ", ".join(positions)
    return ""

def extract_recruitment_info_rule_based(text: str, user_name: str = "") -> Dict[str, Any]:
    lines = clean_post_lines(text, limit=30)
    norm_lines = [(line, strip_accents(line).lower()) for line in lines]

    position = _extract_position_list_after_intro(norm_lines)
    for line, norm in norm_lines:
        if position:
            break
        if re.search(r"\b(?:vi tri|can tuyen|tuyen gap|tuyen dung|dang tuyen|tuyen)\b", norm):
            cleaned = re.sub(
                r"^\s*(?:[-+*•]\s*)?(?:vi\s*tri|can\s*tuyen|tuyen\s*gap|tuyen\s*dung|dang\s*tuyen|tuyen)\s*[:\-–—]*\s*",
                "",
                line,
                flags=re.I,
            ).strip(" :-–—")
            if cleaned and len(cleaned) <= 90 and not _is_generic_position_intro(strip_accents(cleaned).lower()):
                position = cleaned
                break

    if not position:
        for line, norm in norm_lines:
            if re.search(r"\b(?:nhan vien|nv|sales|sale|ke toan|bao ve|lai xe|cong nhan|tho|tu van|kinh doanh)\b", norm):
                position = line.strip(" :-–—")
                break

    salary = ""
    salary_pattern = re.compile(
        r"(?i)(?:lương|thu nhập|mức lương|salary)\s*[:\-–—]?\s*([^\n.;]{0,80}(?:triệu|tr|tr/tháng|k|đ|vnd|usd|thỏa thuận|thoả thuận)[^\n.;]{0,40})"
    )
    salary_match = salary_pattern.search(text)
    if salary_match:
        salary = normalize_spaces(salary_match.group(0))[:100]

    location = ""
    location_pattern = re.compile(
        r"(?i)(?:địa điểm|địa chỉ|làm việc tại|nơi làm việc)\s*[:\-–—]?\s*([^\n.;]{3,100})"
    )
    location_match = location_pattern.search(text)
    if location_match:
        location = normalize_spaces(location_match.group(1))[:100]

    return {
        "position": normalize_spaces(position),
        "salary": normalize_spaces(salary),
        "location": normalize_spaces(location),
        "company": "",
        "work_time": "",
        "requirements": "",
        "contact": "",
        "is_recruitment": is_recruitment_post(text),
        "confidence": 0.55,
        "source": "rule_based",
        "ai_used": False,
        "ai_error": "",
    }

def extract_recruitment_info(text: str, user_name: str = "", use_openai: Optional[bool] = None) -> Dict[str, Any]:
    if use_openai is False:
        return extract_recruitment_info_rule_based(text, user_name)
    return extract_recruitment_info_with_openai(text, user_name)

def _message_value_missing(value: Any) -> bool:
    value = _safe_str(value).lower()
    return not value or value in {"none", "null", "n/a", "không rõ", "khong ro", "chưa rõ", "chua ro"}

def _missing_recruitment_fields(info: Dict[str, Any]) -> List[str]:
    labels = []
    for key, label in (
        ("salary", "thu nhập"),
        ("location", "địa điểm làm việc"),
        ("work_time", "thời gian làm việc"),
        ("requirements", "yêu cầu đối với ứng viên"),
        ("contact", "cách ứng tuyển/liên hệ"),
    ):
        if _message_value_missing(info.get(key)):
            labels.append(label)
    return labels

def _join_vietnamese_items(items: List[str]) -> str:
    items = [item for item in items if item]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} và {items[1]}"
    return f"{', '.join(items[:-1])} và {items[-1]}"

def _build_follow_up_question(info: Dict[str, Any], honorific: str = "") -> str:
    missing = _missing_recruitment_fields(info)
    prefix = f"{honorific.capitalize()} cho em hỏi" if honorific else "Cho em hỏi"

    if missing:
        missing_text = _join_vietnamese_items(missing)
        variants = [
            f"{prefix} vị trí này hiện còn tuyển không ạ? Nếu còn, {honorific + ' ' if honorific else ''}cho em xin thêm thông tin về {missing_text} với ạ.",
            f"{prefix} tin tuyển này còn nhận hồ sơ không ạ? Em muốn hỏi thêm về {missing_text} để nắm rõ hơn ạ.",
            f"{prefix} bên mình còn tuyển vị trí này không ạ? Nếu còn, {honorific + ' ' if honorific else ''}có thể cho em biết thêm {missing_text} được không ạ?",
            f"{prefix} công việc này còn tuyển không ạ? Em chưa thấy rõ phần {missing_text}, {honorific + ' ' if honorific else ''}cho em xin thêm thông tin với ạ.",
        ]
        return random.choice(variants)

    variants = [
        f"{prefix} vị trí này hiện còn tuyển không ạ? Nếu còn, {honorific + ' ' if honorific else ''}cho em xin hướng dẫn ứng tuyển với ạ.",
        f"{prefix} tin tuyển này còn nhận ứng viên không ạ? Em muốn ứng tuyển thì gửi thông tin qua đâu ạ?",
        f"{prefix} hiện bên mình còn tuyển vị trí này không ạ? Nếu còn, {honorific + ' ' if honorific else ''}cho em xin bước tiếp theo để ứng tuyển ạ.",
    ]
    return random.choice(variants)

def build_recruitment_message(job: RecruitmentJob, info: Optional[Dict[str, Any]] = None) -> str:
    text = job.text
    info = info or extract_recruitment_info(text, job.user_name)
    position = _safe_str(info.get("position"))
    salary = _safe_str(info.get("salary"))
    location = _safe_str(info.get("location"))
    honorific = _safe_str(info.get("honorific"))
    name = _safe_str(info.get("name"))
    has_specific_position = position != "vị trí trong bài tuyển dụng"

    if honorific and name:
        greeting = f"Em chào {honorific} {name}"
        subject = f"em thấy {honorific} có đăng tin tuyển {position}" if has_specific_position else f"em thấy {honorific} có đăng bài tuyển dụng"
        ask = _build_follow_up_question(info, honorific)
        close = f"Em cảm ơn {honorific}."
    else:
        greeting = "Em chào bên mình ạ"
        subject = f"em thấy bên mình có đăng tin tuyển {position}" if has_specific_position else "em thấy bên mình có đăng bài tuyển dụng"
        ask = _build_follow_up_question(info)
        close = "Em cảm ơn."

    detail_parts = []
    if location:
        detail_parts.append(location)
    # if salary and position != "vị trí trong bài tuyển dụng":
    #     detail_parts.append(salary)
    detail_parts = detail_parts[:2]
    detail = f" ({', '.join(detail_parts)})" if detail_parts else ""

    if has_specific_position:
        variants = [
            f"{greeting}, {subject}{detail}. {ask} {close}",
            f"{greeting}, em đọc được tin tuyển {position}{detail}. {ask} {close}",
            f"{greeting}, em quan tâm tin tuyển {position}{detail}. {ask} {close}",
        ]
    else:
        variants = [
            f"{greeting}, {subject}{detail}. {ask} {close}",
            f"{greeting}, em đọc được bài tuyển dụng của bên mình {detail}. {ask} {close}",
            f"{greeting}, em quan tâm bài tuyển dụng bên mình đăng {detail}. {ask} {close}",
        ]
    return normalize_spaces(random.choice(variants))
OUTPUT_FIELDS = [
    "index", "_id", "predicted_label", "score", "post_time", "user_name",
    "position", "salary", "location", "honorific", "name",
    "company", "work_time", "requirements", "confidence", "ai_used",
    "ai_error", "message", "text_excerpt",
    "link_user_post", "link_post", "list_phone", "list_email",
]

app = FastAPI(title="Facebook Recruitment Message API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

def safe_str(value: Any) -> str:
    return str(value or "").strip()

def load_records(path: Path) -> List[Dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("data", "results", "items"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    raise ValueError(f"Unsupported JSON structure: {path}")

def clean_oid(value: Any) -> str:
    if isinstance(value, dict) and "$oid" in value:
        return safe_str(value.get("$oid"))
    return safe_str(value)

def excerpt(text: str, limit: int = 260) -> str:
    text = re.sub(r"\s+", " ", safe_str(text))
    return text[:limit]

def make_job(record: Dict[str, Any]) -> RecruitmentJob:
    return RecruitmentJob(
        raw=record,
        link_user_post=safe_str(record.get("link_user_post")),
        user_name=safe_str(record.get("user_name")),
        text=safe_str(record.get("text")),
        link_post=safe_str(record.get("link_post")),
    )

def detect_flags(record: Dict[str, Any], parsed: Dict[str, Any]) -> str:
    flags = []
    text = safe_str(record.get("text"))
    position = safe_str(parsed.get("position"))
    salary = safe_str(parsed.get("salary"))
    location = safe_str(parsed.get("location"))
    honorific = safe_str(parsed.get("honorific"))
    name = safe_str(parsed.get("name"))

    if position == FALLBACK_POSITION:
        flags.append("position_fallback")
    if len(position.split()) > 9:
        flags.append("position_long")
    if re.search(r"\b(tương đương|đọc thông tin|liên hệ|ứng tuyển|ngôi nhà|đi làm ngay)\b", position, flags=re.I):
        flags.append("position_suspicious")
    if re.search(r"\b(lương|thu nhập|mức lương)\b", text, flags=re.I) and not salary:
        flags.append("missing_salary")
    if salary and len(salary) > 85:
        flags.append("salary_long")
    if re.search(r"\b(địa điểm|làm việc tại|địa chỉ|\[[^\]]+\])", text, flags=re.I) and not location:
        flags.append("missing_location")
    if location and len(location) > 90:
        flags.append("location_long")
    if not honorific:
        flags.append("missing_honorific")
    if len(name.split()) > 2:
        flags.append("name_long")
    if strip_accents(safe_str(record.get("predicted_label"))).lower() != "tin tuyen dung":
        flags.append("not_recruitment_label")
    return "|".join(flags)

def evaluate_record(index: int, record: Dict[str, Any], use_openai: bool = False, total: int = 0, verbose: bool = False) -> Dict[str, Any]:
    job = make_job(record)
    if verbose:
        suffix = f"/{total}" if total else ""
        print(f"Dang goi OpenAI cho bai {index}{suffix}: {excerpt(job.text, 90)}", flush=True)

    info = extract_recruitment_info(job.text, job.user_name, use_openai=use_openai)
    position = safe_str(info.get("position")) or FALLBACK_POSITION
    salary = safe_str(info.get("salary"))
    location = safe_str(info.get("location"))
    honorific = infer_honorific(job.user_name)
    name = first_name_token(job.user_name)
    has_specific_position = position != FALLBACK_POSITION

    parsed = {
        "position": position,
        "salary": salary,
        "location": location,
        "honorific": honorific or "",
        "name": name,
        "has_specific_position": has_specific_position,
        "company": safe_str(info.get("company")),
        "work_time": safe_str(info.get("work_time")),
        "requirements": safe_str(info.get("requirements")),
        "contact": safe_str(info.get("contact")),
        "confidence": info.get("confidence", ""),
        "info_source": safe_str(info.get("source")),
        "ai_used": bool(info.get("ai_used")),
        "ai_error": safe_str(info.get("ai_error")),
    }
    message_info = dict(parsed)
    random.seed(index)
    message_to_employer = build_recruitment_message(job, message_info)

    return {
        "index": index,
        "_id": clean_oid(record.get("_id")),
        "predicted_label": safe_str(record.get("predicted_label")),
        "score": record.get("score", ""),
        "post_time": safe_str(record.get("post_time")),
        "user_name": job.user_name,
        **parsed,
        "flags": detect_flags(record, parsed),
        "message": message_to_employer,
        "text_excerpt": job.text,
        "link_user_post": job.link_user_post,
        "link_post": job.link_post,
        "list_phone": normalize_phone_list(record.get("list_phone")),
        "list_email": normalize_email_list(
            record.get("list_email") or record.get("list_mail") or record.get("email") or record.get("mail"),
            record.get("text"),
        ),
    }

def summarize(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    flag_counts: Dict[str, int] = {}
    for row in rows:
        for flag in safe_str(row.get("flags")).split("|"):
            if flag:
                flag_counts[flag] = flag_counts.get(flag, 0) + 1
    return {
        "total": len(rows),
        "specific_position": sum(1 for row in rows if row.get("has_specific_position")),
        "fallback_position": sum(1 for row in rows if not row.get("has_specific_position")),
        "with_salary": sum(1 for row in rows if row.get("salary")),
        "with_location": sum(1 for row in rows if row.get("location")),
        "with_honorific": sum(1 for row in rows if row.get("honorific")),
        "flag_counts": dict(sorted(flag_counts.items(), key=lambda item: (-item[1], item[0]))),
    }

def _record_from_body(body: Dict[str, Any]) -> Dict[str, Any]:
    record = body.get("record")
    merged = dict(record) if isinstance(record, dict) else {}
    for source_key, target_key in (
        ("text", "text"), ("content", "text"), ("post_text", "text"),
        ("user_name", "user_name"), ("author", "user_name"),
        ("link_post", "link_post"), ("post_url", "link_post"),
        ("link_user_post", "link_user_post"), ("profile_url", "link_user_post"),
        ("predicted_label", "predicted_label"), ("score", "score"),
        ("post_time", "post_time"), ("ngay_dang", "ngay_dang"),
        ("list_phone", "list_phone"), ("list_email", "list_email"), ("list_mail", "list_mail"),
        ("email", "email"), ("mail", "mail"), ("_id", "_id"),
    ):
        value = body.get(source_key)
        if value not in (None, ""):
            merged[target_key] = value
    if not safe_str(merged.get("text")):
        raise HTTPException(status_code=400, detail="Missing text/record.text")
    return merged

def _normalize_row(row: Dict[str, Any]) -> Dict[str, Any]:
    defaults = {
        "company": "", "work_time": "", "requirements": "", "contact": "",
        "confidence": "", "info_source": "openai", "ai_used": True, "ai_error": "",
    }
    normalized = {**defaults, **row}
    return {key: normalized.get(key, "") for key in OUTPUT_FIELDS}

def _evaluate_one(record: Dict[str, Any], index: int, verbose: bool = False, use_openai: bool = True) -> Dict[str, Any]:
    return _normalize_row(evaluate_record(index, record, use_openai=use_openai, verbose=verbose))

def _xlsx_cell_value(cell: ET.Element, shared_strings: List[str], ns: Dict[str, str]) -> str:
    if cell.get("t") == "inlineStr":
        return "".join(node.text or "" for node in cell.findall(".//a:t", ns)).strip()

    value_node = cell.find("a:v", ns)
    if value_node is None or value_node.text is None:
        return ""

    value = value_node.text
    if cell.get("t") == "s":
        try:
            return shared_strings[int(value)].strip()
        except Exception:
            return ""
    return value.strip()

def _column_index(cell_ref: str) -> int:
    letters = re.sub(r"[^A-Z]", "", cell_ref.upper())
    index = 0
    for char in letters:
        index = index * 26 + ord(char) - ord("A") + 1
    return max(index - 1, 0)

def load_industries_by_type(path: Path = INDUSTRY_XLSX_PATH) -> Dict[str, List[str]]:
    if not path.exists():
        raise HTTPException(status_code=500, detail=f"Industry file not found: {path}")

    ns = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        shared_strings: List[str] = []
        if "xl/sharedStrings.xml" in names:
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            shared_strings = [
                "".join(node.text or "" for node in item.findall(".//a:t", ns))
                for item in root.findall("a:si", ns)
            ]

        sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        industries_by_type: Dict[str, List[str]] = {}
        for row in sheet.findall(".//a:row", ns):
            values: Dict[int, str] = {}
            for cell in row.findall("a:c", ns):
                values[_column_index(cell.get("r", ""))] = _xlsx_cell_value(cell, shared_strings, ns)

            industry = normalize_spaces(values.get(1, ""))
            industry_type = normalize_spaces(values.get(2, ""))
            if industry and industry_type and strip_accents(industry).lower() != "nganh nghe":
                industries_by_type.setdefault(industry_type, []).append(industry)
        return industries_by_type

def load_industry_names(path: Path = INDUSTRY_XLSX_PATH) -> List[str]:
    industries_by_type = load_industries_by_type(path)
    industries: List[str] = []
    for industry_type in ("1", "2"):
        industries.extend(industries_by_type.get(industry_type, []))
    return industries

def load_excluded_industry_names(path: Path = INDUSTRY_XLSX_PATH) -> List[str]:
    industries_by_type = load_industries_by_type(path)
    excluded: List[str] = []
    for industry_type, industries in industries_by_type.items():
        if industry_type not in {"1", "2"}:
            excluded.extend(industries)
    return excluded

def _industry_keywords(industry_names: List[str]) -> List[Tuple[str, str]]:
    keywords: Dict[str, str] = {}
    ignored = {
        "nganh nghe khac", "luong cao", "moi truong", "dich vu", "bao hiem",
        "y te", "duoc", "luat", "nong", "lam", "ngu", "nghiep", "cao", "thuc tap",
    }
    for industry in industry_names:
        parts = [industry]
        parts.extend(part.strip() for part in re.split(r"\s*-\s*", industry) if part.strip())
        for part in parts:
            normalized = strip_accents(normalize_spaces(part)).lower()
            normalized = re.sub(r"[^a-z0-9\s]+", " ", normalized)
            normalized = re.sub(r"\s+", " ", normalized).strip()
            if len(normalized) < 4 or normalized in ignored:
                continue
            keywords.setdefault(normalized, industry)

    return sorted(keywords.items(), key=lambda item: len(item[0]), reverse=True)

def match_industries(text: str, industry_names: List[str]) -> List[str]:
    norm = strip_accents(normalize_spaces(text)).lower()
    norm = re.sub(r"[^a-z0-9\s]+", " ", norm)
    norm = re.sub(r"\s+", " ", norm).strip()
    matches: List[str] = []
    for keyword, industry in _industry_keywords(industry_names):
        if re.search(rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])", norm):
            if industry not in matches:
                matches.append(industry)
        if len(matches) >= 5:
            break
    return matches

def is_lao_dong_pho_thong_post(text: str, industries: Optional[List[str]] = None) -> bool:
    norm = strip_accents(normalize_spaces(text)).lower()
    if any(re.search(pattern, norm) for pattern in LAO_DONG_PHO_THONG_PATTERNS):
        return True
    for industry in industries or []:
        if strip_accents(industry).lower() == "lao dong pho thong":
            return True
    return False

def normalize_phone(value: Any) -> str:
    digits = re.sub(r"\D+", "", safe_str(value))
    if digits.startswith("84") and len(digits) in {11, 12}:
        digits = "0" + digits[2:]
    return digits if 9 <= len(digits) <= 11 else ""

def normalize_phone_list(value: Any) -> List[str]:
    raw_items: List[Any]
    if isinstance(value, list):
        raw_items = value
    elif isinstance(value, tuple):
        raw_items = list(value)
    elif isinstance(value, str):
        try:
            parsed = ast.literal_eval(value)
            raw_items = parsed if isinstance(parsed, list) else [value]
        except Exception:
            raw_items = re.findall(r"(?:\+?84|0)\d(?:[\s.\-]?\d){8,9}", value)
    else:
        raw_items = []

    phones = []
    for item in raw_items:
        phone = normalize_phone(item)
        if phone and phone not in phones:
            phones.append(phone)
    return phones

def normalize_email_list(value: Any, fallback_text: Any = "") -> List[str]:
    raw_items: List[Any]
    if isinstance(value, list):
        raw_items = value
    elif isinstance(value, tuple):
        raw_items = list(value)
    elif isinstance(value, str):
        try:
            parsed = ast.literal_eval(value)
            raw_items = parsed if isinstance(parsed, list) else [value]
        except Exception:
            raw_items = [value]
    else:
        raw_items = []

    emails = []
    for item in raw_items:
        for email in re.findall(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", safe_str(item)):
            email = email.strip(".,;:()[]{}<>").lower()
            if email and email not in emails:
                emails.append(email)

    if not emails:
        for email in re.findall(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", safe_str(fallback_text)):
            email = email.strip(".,;:()[]{}<>").lower()
            if email and email not in emails:
                emails.append(email)

    return emails

def phone_dedupe_key(phones: List[str]) -> Tuple[str, ...]:
    return tuple(sorted(set(phones)))

def _parse_datetime_score(value: Any) -> float:
    text = safe_str(value)
    if not text:
        return 0.0
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y"):
        try:
            return datetime.strptime(text[:len(fmt)], fmt).timestamp()
        except Exception:
            continue
    return 0.0

def _parse_date_value(value: Any) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    text = safe_str(value)
    if not text:
        return None

    normalized = text.replace("T", " ").replace("Z", "")
    normalized = re.sub(r"([+-]\d{2}:\d{2})$", "", normalized).strip()
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y",
        "%d-%m-%Y %H:%M:%S",
        "%d-%m-%Y %H:%M",
        "%d-%m-%Y",
    ):
        try:
            return datetime.strptime(normalized[:len(fmt)], fmt).date()
        except Exception:
            continue

    match = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", normalized)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except Exception:
            return None

    match = re.search(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b", normalized)
    if match:
        try:
            return date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
        except Exception:
            return None

    return None

def _matches_today_by_post_date(record: Dict[str, Any], today: date) -> bool:
    for key in ("post_time", "ngay_dang"):
        parsed = _parse_date_value(record.get(key))
        if parsed == today:
            return True
    return False

def record_newness_score(record: Dict[str, Any]) -> float:
    for key in ("post_time", "ngay_dang", "created_at", "time"):
        score = _parse_datetime_score(record.get(key))
        if score:
            return score
    source_id = clean_oid(record.get("_id"))
    if re.fullmatch(r"[0-9a-fA-F]{24}", source_id):
        try:
            return int(source_id[:8], 16)
        except Exception:
            return 0.0
    return 0.0

def _mongo_database():
    client = MongoClient(get_client_name(), serverSelectionTimeoutMS=10000)
    return client[MONGO_DB_NAME]

def _upsert_saved_message(target, filter_query: Dict[str, Any], row: Dict[str, Any]) -> None:
    target.update_one(filter_query, {"$set": row}, upsert=True)

def _update_saved_message(target, filter_query: Dict[str, Any], row: Dict[str, Any]) -> None:
    target.update_one(filter_query, {"$set": row}, upsert=False)

def _max_saved_index(target) -> int:
    doc = target.find_one(
        {"index": {"$type": "number"}},
        {"index": 1},
        sort=[("index", -1)],
    )
    try:
        return int(doc.get("index") or 0) if doc else 0
    except Exception:
        return 0

def _assign_saved_index(target, row: Dict[str, Any], next_index: int) -> int:
    existing = target.find_one({"source_id": row.get("source_id")}, {"index": 1})
    if existing and existing.get("index") not in (None, ""):
        row["index"] = existing.get("index")
        return next_index
    row["index"] = next_index
    return next_index + 1

def _skip_entry(
    record: Dict[str, Any],
    reason_code: str,
    reason: str,
    matched_industries: Optional[List[str]] = None,
) -> Dict[str, Any]:
    clean_record = _record_for_mongo(record)
    return {
        "source_id": clean_record.get("_id"),
        "reason_code": reason_code,
        "reason": reason,
        "predicted_label": clean_record.get("predicted_label", ""),
        "post_time": clean_record.get("post_time", ""),
        "ngay_dang": clean_record.get("ngay_dang", ""),
        "user_name": clean_record.get("user_name", ""),
        "link_post": clean_record.get("link_post", ""),
        "link_user_post": clean_record.get("link_user_post", ""),
        "list_phone": clean_record.get("list_phone", []),
        "matched_industries": matched_industries or [],
        "text_excerpt": excerpt(clean_record.get("text", "")),
    }

def _skip_summary(skipped: List[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for item in skipped:
        reason_code = safe_str(item.get("reason_code"))
        if reason_code:
            counts[reason_code] = counts.get(reason_code, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))

def fetch_save_messages_today_from_mongo(
    limit: int = 100,
    scan_limit: int = 5000,
    batch_size: int = 200,
    skipped_limit: int = 200,
    verbose: bool = False,
    use_openai: bool = DEFAULT_USE_OPENAI,
) -> Dict[str, Any]:
    industry_names = load_industry_names()
    excluded_industry_names = load_excluded_industry_names()
    db = _mongo_database()
    source = db[MONGO_SOURCE_COLLECTION]
    target = db[MONGO_TARGET_COLLECTION]
    today = datetime.now().date()
    tomorrow = today + timedelta(days=1)
    query = {
        "predicted_label": "Tin tuyển dụng",
        "text": {"$type": "string", "$ne": ""},
        "$or": [
            {"post_time": {"$gte": datetime.combine(today, datetime.min.time()), "$lt": datetime.combine(tomorrow, datetime.min.time())}},
            {"ngay_dang": {"$gte": datetime.combine(today, datetime.min.time()), "$lt": datetime.combine(tomorrow, datetime.min.time())}},
            {"post_time": {"$exists": True, "$ne": ""}},
            {"ngay_dang": {"$exists": True, "$ne": ""}},
        ]
    }
    projection = {
        "text": 1, "content": 1, "post_text": 1, "link_user_post": 1, "profile_url": 1,
        "link_post": 1, "post_url": 1, "user_name": 1, "author": 1, "predicted_label": 1,
        "score": 1, "post_time": 1, "ngay_dang": 1, "created_at": 1, "time": 1,
        "list_phone": 1, "list_email": 1, "list_mail": 1, "email": 1, "mail": 1,
    }

    candidates: List[Dict[str, Any]] = []
    candidates_by_phone: Dict[Tuple[str, ...], Dict[str, Any]] = {}
    skipped: List[Dict[str, Any]] = []
    skipped_total = 0
    skip_counts: Dict[str, int] = {}
    scanned = 0

    def add_skip(
        record: Dict[str, Any],
        reason_code: str,
        reason: str,
        matched_industries: Optional[List[str]] = None,
    ) -> None:
        nonlocal skipped_total
        skipped_total += 1
        skip_counts[reason_code] = skip_counts.get(reason_code, 0) + 1
        if len(skipped) < skipped_limit:
            skipped.append(_skip_entry(record, reason_code, reason, matched_industries))

    cursor = source.find(query, projection, no_cursor_timeout=True).sort("_id", -1).batch_size(batch_size)
    try:
        for record in cursor:
            scanned += 1
            if scanned > scan_limit:
                break
            if not _matches_today_by_post_date(record, today):
                add_skip(record, "not_today", "Bai viet khong thuoc ngay hom nay")
                continue

            clean_record = _record_for_mongo(record)
            if not is_recruitment_post(clean_record.get("text", ""), clean_record):
                add_skip(record, "not_recruitment_post", "Bai viet khong duoc nhan dien la tin tuyen dung")
                continue

            excluded_industries = match_industries(clean_record.get("text", ""), excluded_industry_names)
            if excluded_industries:
                add_skip(
                    record,
                    "excluded_industry_score",
                    "Bai viet bi loai vi nganh nghe trong file Excel khong thuoc diem 1 hoac 2",
                    excluded_industries,
                )
                continue

            matched_industries = match_industries(clean_record.get("text", ""), industry_names)
            if not matched_industries:
                add_skip(record, "no_matched_industry", "Bai viet khong khop nganh nghe trong danh sach")
                continue
            if is_lao_dong_pho_thong_post(clean_record.get("text", ""), matched_industries):
                add_skip(record, "lao_dong_pho_thong", "Bai viet bi loai vi thuoc nhom Lao dong pho thong", matched_industries)
                continue

            candidate = {
                "record": clean_record,
                "matched_industries": matched_industries,
                "newness_score": record_newness_score(record),
            }
            key = phone_dedupe_key(clean_record.get("list_phone", []))
            if key:
                existing = candidates_by_phone.get(key)
                if existing is None or candidate["newness_score"] > existing["newness_score"]:
                    if existing is not None:
                        add_skip(
                            existing["record"],
                            "duplicate_phone_older",
                            "Bai viet bi loai do trung so dien thoai voi bai moi hon",
                            existing.get("matched_industries", []),
                        )
                    candidates_by_phone[key] = candidate
                else:
                    add_skip(
                        clean_record,
                        "duplicate_phone_older",
                        "Bai viet bi loai do trung so dien thoai voi bai moi hon",
                        matched_industries,
                    )
            else:
                candidates.append(candidate)
    finally:
        cursor.close()

    candidates.extend(candidates_by_phone.values())
    candidates.sort(key=lambda item: item["newness_score"], reverse=True)
    selected_candidates = candidates[:limit]
    for candidate in candidates[limit:]:
        add_skip(
            candidate["record"],
            "limit_reached",
            "Bai viet hop le nhung khong luu vi vuot qua limit",
            candidate.get("matched_industries", []),
        )

    rows: List[Dict[str, Any]] = []
    next_index = _max_saved_index(target) + 1
    for candidate in selected_candidates:
        clean_record = candidate["record"]
        row = _evaluate_one(clean_record, len(rows) + 1, verbose=verbose, use_openai=use_openai)
        row["ngay_dang"] = clean_record.get("ngay_dang", "")
        row["matched_industries"] = candidate["matched_industries"]
        row["source_collection"] = MONGO_SOURCE_COLLECTION
        row["source_id"] = clean_record.get("_id")
        row["list_phone"] = clean_record.get("list_phone", [])
        row["list_email"] = clean_record.get("list_email", [])
        row["dedupe_phone_key"] = list(phone_dedupe_key(clean_record.get("list_phone", [])))
        row["saved_at"] = datetime.now().isoformat(timespec="seconds")
        next_index = _assign_saved_index(target, row, next_index)
        _upsert_saved_message(target, {"source_id": row["source_id"]}, row)
        rows.append(row)

    return {
        "total": len(rows),
        "scanned": scanned,
        "deduped_candidates": len(candidates),
        "skipped_total": skipped_total,
        "skipped_returned": len(skipped),
        "skip_summary": dict(sorted(skip_counts.items(), key=lambda item: (-item[1], item[0]))),
        "source_collection": MONGO_SOURCE_COLLECTION,
        "target_collection": MONGO_TARGET_COLLECTION,
        "industry_count": len(industry_names),
        "date": today.isoformat(),
        "date_fields": ["post_time", "ngay_dang"],
        "openai": use_openai,
        "summary": summarize(rows),
        "results": rows,
        "messages": rows,
        "skipped": skipped,
    }

def _record_for_mongo(record: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "_id": clean_oid(record.get("_id")),
        "text": safe_str(record.get("text") or record.get("content") or record.get("post_text")),
        "link_user_post": safe_str(record.get("link_user_post") or record.get("profile_url")),
        "link_post": safe_str(record.get("link_post") or record.get("post_url")),
        "user_name": safe_str(record.get("user_name") or record.get("author")),
        "predicted_label": safe_str(record.get("predicted_label")),
        "score": record.get("score", ""),
        "post_time": safe_str(record.get("post_time") or record.get("created_at") or record.get("time")),
        "ngay_dang": safe_str(record.get("ngay_dang")),
        "list_phone": normalize_phone_list(record.get("list_phone")),
        "list_email": normalize_email_list(
            record.get("list_email") or record.get("list_mail") or record.get("email") or record.get("mail"),
            record.get("text") or record.get("content") or record.get("post_text"),
        ),
    }

def fetch_filter_save_from_mongo(
    limit: int = 10,
    scan_limit: int = 5000,
    batch_size: int = 200,
    skipped_limit: int = 200,
    verbose: bool = False,
    use_openai: bool = DEFAULT_USE_OPENAI,
) -> Dict[str, Any]:
    industry_names = load_industry_names()
    excluded_industry_names = load_excluded_industry_names()
    db = _mongo_database()
    source = db[MONGO_SOURCE_COLLECTION]
    target = db[MONGO_TARGET_COLLECTION]
    query = {"predicted_label": "Tin tuyển dụng", "text": {"$type": "string", "$ne": ""}}
    projection = {
        "text": 1, "content": 1, "post_text": 1, "link_user_post": 1, "profile_url": 1,
        "link_post": 1, "post_url": 1, "user_name": 1, "author": 1, "predicted_label": 1,
        "score": 1, "post_time": 1, "ngay_dang": 1, "created_at": 1, "time": 1,
        "list_phone": 1, "list_email": 1, "list_mail": 1, "email": 1, "mail": 1,
    }

    candidates: List[Dict[str, Any]] = []
    candidates_by_phone: Dict[Tuple[str, ...], Dict[str, Any]] = {}
    skipped: List[Dict[str, Any]] = []
    skipped_total = 0
    skip_counts: Dict[str, int] = {}
    scanned = 0

    def add_skip(
        record: Dict[str, Any],
        reason_code: str,
        reason: str,
        matched_industries: Optional[List[str]] = None,
    ) -> None:
        nonlocal skipped_total
        skipped_total += 1
        skip_counts[reason_code] = skip_counts.get(reason_code, 0) + 1
        if len(skipped) < skipped_limit:
            skipped.append(_skip_entry(record, reason_code, reason, matched_industries))

    cursor = source.find(query, projection, no_cursor_timeout=True).sort("_id", -1).batch_size(batch_size)
    try:
        for record in cursor:
            scanned += 1
            if scanned > scan_limit:
                break

            clean_record = _record_for_mongo(record)
            if not is_recruitment_post(clean_record.get("text", ""), clean_record):
                add_skip(record, "not_recruitment_post", "Bai viet khong duoc nhan dien la tin tuyen dung")
                continue

            excluded_industries = match_industries(clean_record.get("text", ""), excluded_industry_names)
            if excluded_industries:
                add_skip(
                    record,
                    "excluded_industry_score",
                    "Bai viet bi loai vi nganh nghe trong file Excel khong thuoc diem 1 hoac 2",
                    excluded_industries,
                )
                continue

            matched_industries = match_industries(clean_record.get("text", ""), industry_names)
            if not matched_industries:
                add_skip(record, "no_matched_industry", "Bai viet khong khop nganh nghe trong danh sach")
                continue
            if is_lao_dong_pho_thong_post(clean_record.get("text", ""), matched_industries):
                add_skip(record, "lao_dong_pho_thong", "Bai viet bi loai vi thuoc nhom Lao dong pho thong", matched_industries)
                continue

            candidate = {
                "record": clean_record,
                "matched_industries": matched_industries,
                "newness_score": record_newness_score(record),
            }
            key = phone_dedupe_key(clean_record.get("list_phone", []))
            if key:
                existing = candidates_by_phone.get(key)
                if existing is None or candidate["newness_score"] > existing["newness_score"]:
                    if existing is not None:
                        add_skip(
                            existing["record"],
                            "duplicate_phone_older",
                            "Bai viet bi loai do trung so dien thoai voi bai moi hon",
                            existing.get("matched_industries", []),
                        )
                    candidates_by_phone[key] = candidate
                else:
                    add_skip(
                        clean_record,
                        "duplicate_phone_older",
                        "Bai viet bi loai do trung so dien thoai voi bai moi hon",
                        matched_industries,
                    )
            else:
                candidates.append(candidate)
    finally:
        cursor.close()

    candidates.extend(candidates_by_phone.values())
    candidates.sort(key=lambda item: item["newness_score"], reverse=True)
    selected_candidates = candidates[:limit]
    for candidate in candidates[limit:]:
        add_skip(
            candidate["record"],
            "limit_reached",
            "Bai viet hop le nhung khong luu vi vuot qua limit",
            candidate.get("matched_industries", []),
        )

    saved_rows: List[Dict[str, Any]] = []
    next_index = _max_saved_index(target) + 1
    for candidate in selected_candidates:
        clean_record = candidate["record"]
        row = _evaluate_one(clean_record, len(saved_rows) + 1, verbose=verbose, use_openai=use_openai)
        row["matched_industries"] = candidate["matched_industries"]
        row["source_collection"] = MONGO_SOURCE_COLLECTION
        row["source_id"] = clean_record.get("_id")
        row["list_phone"] = clean_record.get("list_phone", [])
        row["list_email"] = clean_record.get("list_email", [])
        row["dedupe_phone_key"] = list(phone_dedupe_key(clean_record.get("list_phone", [])))
        row["saved_at"] = datetime.now().isoformat(timespec="seconds")
        next_index = _assign_saved_index(target, row, next_index)
        _upsert_saved_message(target, {"source_id": row["source_id"]}, row)
        saved_rows.append(row)

    return {
        "total": len(saved_rows),
        "scanned": scanned,
        "deduped_candidates": len(candidates),
        "skipped_total": skipped_total,
        "skipped_returned": len(skipped),
        "skip_summary": dict(sorted(skip_counts.items(), key=lambda item: (-item[1], item[0]))),
        "source_collection": MONGO_SOURCE_COLLECTION,
        "target_collection": MONGO_TARGET_COLLECTION,
        "industry_count": len(industry_names),
        "summary": summarize(saved_rows),
        "results": saved_rows,
        "skipped": skipped,
    }

def refresh_saved_mongo_records(
    limit: int = 10,
    verbose: bool = False,
    use_openai: bool = DEFAULT_USE_OPENAI,
) -> Dict[str, Any]:
    db = _mongo_database()
    target = db[MONGO_TARGET_COLLECTION]
    docs = list(
        target.find(
            {},
            {
                "text_excerpt": 1, "text": 1, "link_user_post": 1, "link_post": 1, "user_name": 1,
                "predicted_label": 1, "score": 1, "post_time": 1, "source_id": 1, "matched_industries": 1,
                "source_collection": 1, "saved_at": 1, "index": 1,
                "list_phone": 1, "list_email": 1, "list_mail": 1,
            },
        ).sort("saved_at", -1).limit(limit)
    )

    rows: List[Dict[str, Any]] = []
    for index, doc in enumerate(docs, 1):
        record = {
            "_id": doc.get("source_id") or clean_oid(doc.get("_id")),
            "text": safe_str(doc.get("text") or doc.get("text_excerpt")),
            "link_user_post": safe_str(doc.get("link_user_post")),
            "link_post": safe_str(doc.get("link_post")),
            "user_name": safe_str(doc.get("user_name")),
            "predicted_label": safe_str(doc.get("predicted_label") or "Tin tuyển dụng"),
            "score": doc.get("score", ""),
            "post_time": safe_str(doc.get("post_time")),
            "list_phone": doc.get("list_phone", []),
            "list_email": doc.get("list_email") or doc.get("list_mail", []),
        }
        row = _evaluate_one(record, index, verbose=verbose, use_openai=use_openai)
        row["index"] = doc.get("index", index)
        row["matched_industries"] = doc.get("matched_industries", [])
        row["source_collection"] = doc.get("source_collection") or MONGO_SOURCE_COLLECTION
        row["source_id"] = record["_id"]
        row["list_phone"] = normalize_phone_list(doc.get("list_phone"))
        row["list_email"] = normalize_email_list(doc.get("list_email") or doc.get("list_mail"), record.get("text"))
        row["saved_at"] = doc.get("saved_at") or datetime.now().isoformat(timespec="seconds")
        row["refreshed_at"] = datetime.now().isoformat(timespec="seconds")
        _update_saved_message(target, {"_id": doc["_id"]}, row)
        rows.append(row)

    return {
        "total": len(rows),
        "target_collection": MONGO_TARGET_COLLECTION,
        "openai": use_openai,
        "summary": summarize(rows),
        "results": rows,
    }

@app.get("/health")
def health() -> Dict[str, Any]:
    return {"ok": True, "service": "facebook_recruitment_message_api"}

@app.post("/api/recruitment-message/generate-one")
def generate_one_message(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    index = int(body.get("index") or 1)
    record = _record_from_body(body)
    if body.get("only_recruitment", False) and not is_recruitment_post(safe_str(record.get("text")), record):
        raise HTTPException(status_code=422, detail="Record is not detected as a recruitment post")
    return _evaluate_one(record, index, verbose=bool(body.get("verbose")))

@app.post("/api/recruitment-message/generate")
def generate_messages(body: Any = Body(...)) -> Dict[str, Any]:
    if isinstance(body, list):
        body = {"records": body}
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Body must be an object or a list of records")

    only_recruitment = bool(body.get("only_recruitment", True))
    verbose = bool(body.get("verbose"))
    records = body.get("records")
    if records is None:
        input_path = safe_str(body.get("input") or body.get("input_path"))
        records = load_records(Path(input_path)) if input_path else [_record_from_body(body)]
    if not isinstance(records, list):
        raise HTTPException(status_code=400, detail="records must be a list")

    clean_records = [item for item in records if isinstance(item, dict)]
    limit = int(body.get("limit") or 0)
    if limit > 0:
        clean_records = clean_records[:limit]
    if only_recruitment:
        clean_records = [record for record in clean_records if is_recruitment_post(safe_str(record.get("text")), record)]

    start_index = int(body.get("start_index") or 1)
    rows = [_evaluate_one(record, start_index + offset, verbose=verbose) for offset, record in enumerate(clean_records)]
    return {"total": len(rows), "openai": True, "summary": summarize(rows), "messages": rows, "results": rows}

@app.get("/api/recruitment-message/saved-today")
def get_saved_messages_today(
    limit: int = Query(100, ge=1, le=500),
    scan_limit: int = Query(5000, ge=1),
    batch_size: int = Query(200, ge=1),
    skipped_limit: int = Query(200, ge=0, le=5000),
    use_openai: bool = Query(DEFAULT_USE_OPENAI),
    verbose: bool = Query(False),
) -> Dict[str, Any]:
    return fetch_save_messages_today_from_mongo(
        limit=limit,
        scan_limit=scan_limit,
        batch_size=batch_size,
        skipped_limit=skipped_limit,
        verbose=verbose,
        use_openai=use_openai,
    )

@app.post("/api/recruitment-message/saved-today")
def post_saved_messages_today(body: Dict[str, Any] = Body(default_factory=dict)) -> Dict[str, Any]:
    limit = int(body.get("limit") or 10)
    scan_limit = int(body.get("scan_limit") or 50)
    batch_size = int(body.get("batch_size") or 200)
    skipped_limit = int(body.get("skipped_limit") if body.get("skipped_limit") is not None else 200)
    use_openai = _bool_value(body.get("use_openai"), default=DEFAULT_USE_OPENAI)
    if limit <= 0 or limit > 500:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 500")
    if scan_limit <= 0:
        raise HTTPException(status_code=400, detail="scan_limit must be greater than 0")
    if batch_size <= 0:
        raise HTTPException(status_code=400, detail="batch_size must be greater than 0")
    if skipped_limit < 0 or skipped_limit > 5000:
        raise HTTPException(status_code=400, detail="skipped_limit must be between 0 and 5000")
    return fetch_save_messages_today_from_mongo(
        limit=limit,
        scan_limit=scan_limit,
        batch_size=batch_size,
        skipped_limit=skipped_limit,
        verbose=bool(body.get("verbose")),
        use_openai=use_openai,
    )

@app.post("/api/recruitment-message/save-from-mongo")
def save_messages_from_mongo(body: Dict[str, Any] = Body(default_factory=dict)) -> Dict[str, Any]:
    limit = int(body.get("limit") or 10)
    scan_limit = int(body.get("scan_limit") or 5000)
    batch_size = int(body.get("batch_size") or 200)
    skipped_limit = int(body.get("skipped_limit") if body.get("skipped_limit") is not None else 200)
    use_openai = _bool_value(body.get("use_openai"), default=DEFAULT_USE_OPENAI)
    if limit <= 0 or limit > 200:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 200")
    if scan_limit <= 0:
        raise HTTPException(status_code=400, detail="scan_limit must be greater than 0")
    if batch_size <= 0:
        raise HTTPException(status_code=400, detail="batch_size must be greater than 0")
    if skipped_limit < 0 or skipped_limit > 5000:
        raise HTTPException(status_code=400, detail="skipped_limit must be between 0 and 5000")
    return fetch_filter_save_from_mongo(
        limit=limit,
        scan_limit=scan_limit,
        batch_size=batch_size,
        skipped_limit=skipped_limit,
        verbose=bool(body.get("verbose")),
        use_openai=use_openai,
    )

@app.post("/api/recruitment-message/refresh-saved")
def refresh_saved_messages(body: Dict[str, Any] = Body(default_factory=dict)) -> Dict[str, Any]:
    limit = int(body.get("limit") or 10)
    use_openai = _bool_value(body.get("use_openai"), default=DEFAULT_USE_OPENAI)
    if limit <= 0 or limit > 200:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 200")
    return refresh_saved_mongo_records(
        limit=limit,
        verbose=bool(body.get("verbose")),
        use_openai=use_openai,
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=API_PORT)
