#!/usr/bin/env python3
"""
Clean API Sender for Facebook 1-1 tool.

Mục tiêu:
- Giữ tên hàm/endpoint/socket event cũ để các file khác vẫn gọi được.
- Chỉ tập trung vào luồng gửi tin nhắn: CRM -> API Sender -> bot Socket.IO -> cập nhật DB.
- Bot được coi là ONLINE khi register qua Socket.IO hoặc heartbeat HTTP gần đây.
"""

from __future__ import annotations

import json
import logging
import os
import re
import socket
import sqlite3
import sys
import threading
import time
import unicodedata
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pymongo
import requests

# IMPORTANT:
# Project has a local file named websocket.py.
# python-engineio imports the third-party package `websocket` while Flask-SocketIO loads.
# If the project root stays in sys.path during this import, Python may import local websocket.py
# instead of websocket-client and cause a circular import:
#   flask_socketio -> socketio -> engineio -> websocket -> local websocket.py -> module -> socketio.Client
_PROJECT_ROOT = Path(__file__).resolve().parent
_ORIGINAL_SYS_PATH = list(sys.path)
sys.path = [
    item
    for item in sys.path
    if item and Path(item).resolve() != _PROJECT_ROOT
]
try:
    from flask import Flask, jsonify, request
    from flask_cors import CORS
    from flask_socketio import SocketIO, emit, join_room, leave_room
finally:
    sys.path = _ORIGINAL_SYS_PATH

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("fb_1_1_api_sender")

API_SENDER_PORT = int(os.getenv("API_SENDER_PORT", "8024"))
RECEIVER_API_URL = os.getenv("RECEIVER_API_URL", "http://123.24.206.25:8023")
DATABASE_FILE = os.getenv("CRM_FACEBOOK_DB", str(_PROJECT_ROOT / "crm_facebook.db"))
DB_TIN_NHAN_FILE = os.getenv("DB_TIN_NHAN_FILE", "db_tin_nhan.json")
FACEBOOK_DEVICES_FILE = os.getenv("FACEBOOK_DEVICES_FILE", "Facebook.devices.json")
DATABASE_ACCOUNTS_FILE = os.getenv("DATABASE_ACCOUNTS_FILE", str(_PROJECT_ROOT / "DatabaseAccounts.env"))
MONGODB_DB_NAME = os.getenv("MONGODB_DB_NAME", "Facebook")
FOLDER1_DEFAULT_USER_ID_CHAT = os.getenv("FOLDER1_DEFAULT_USER_ID_CHAT", "").strip()
ACCOUNT_METADATA_CACHE_SECONDS = int(os.getenv("FB_1_1_ACCOUNT_METADATA_CACHE_SECONDS", "60"))
BOT_STALE_SECONDS = int(os.getenv("FB_1_1_BOT_STALE_SECONDS", "90"))
SEND_COMMAND_TIMEOUT = int(os.getenv("FB_1_1_SEND_COMMAND_TIMEOUT", "600"))
MAX_QUEUE_ATTEMPTS = int(os.getenv("FB_1_1_MAX_QUEUE_ATTEMPTS", "3"))

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("API_SENDER_SECRET", "sender-secret-key")
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# Runtime state
bot_instances: Dict[str, str] = {}          # user_id_chat -> socket sid
bot_device_sessions: Dict[str, Dict[str, Any]] = {}  # sid -> session info
bot_last_seen: Dict[str, Dict[str, Any]] = {}
bot_send_locks: Dict[str, threading.Lock] = {}
bot_queue_workers: set[str] = set()
bot_queue_workers_lock = threading.Lock()
_ACCOUNT_METADATA_CACHE = {"loaded_at": 0.0, "accounts": {}, "source": ""}
_MONGO_CLIENT = None


def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DATABASE_FILE, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def local_now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def parse_env_value_file(path, key):
    try:
        text = Path(path).read_text(encoding="utf-8")
    except Exception:
        return ""
    match = re.search(rf"^{re.escape(key)}=(.+)$", text, re.M)
    if not match:
        return ""
    return match.group(1).strip().strip('"').strip("'")

def build_mongo_uri_from_database_accounts(account_type="Base"):
    data = json.loads(Path(DATABASE_ACCOUNTS_FILE).read_text(encoding="utf-8")).get(account_type, {})
    username = urllib.parse.quote_plus(data["username"])
    password = urllib.parse.quote_plus(data["pwd"])
    host = os.getenv("MONGODB_HOST", "123.24.206.25")
    port = os.getenv("MONGODB_PORT", "27017")
    auth_source = os.getenv("MONGODB_AUTH_SOURCE", "admin")
    return f"mongodb://{username}:{password}@{host}:{port}/?authSource={auth_source}"

def get_mongodb_url():
    return os.getenv("MONGODB_URL") or build_mongo_uri_from_database_accounts(
        os.getenv("DATABASE_ACCOUNTS_TYPE", "Base")
    )


def get_mongo_commands_collection():
    global _MONGO_CLIENT
    url = get_mongodb_url()
    if not url:
        return None
    db_name = os.getenv("MONGODB_DB_NAME") or MONGODB_DB_NAME
    if _MONGO_CLIENT is None:
        _MONGO_CLIENT = pymongo.MongoClient(url, serverSelectionTimeoutMS=5000)
    return _MONGO_CLIENT[db_name]["Commands"]


def get_mongo_usermessages_collection():
    collection = get_mongo_commands_collection()
    return collection.database["usermessages"] if collection is not None else None


def to_int_or_none(value):
    text = safe_text(value)
    if not text:
        return None
    try:
        return int(text)
    except Exception:
        return None


def get_numeric_message_id(*values, fallback=None):
    for value in values:
        parsed = to_int_or_none(value)
        if parsed is not None:
            return parsed
    return fallback


def command_account_filter(user_id_chat):
    user_id_chat = safe_text(user_id_chat)
    return {
        "$or": [
            {"user_id_chat": user_id_chat},
            {"userId_chat": user_id_chat},
            {"accountKey": user_id_chat},
            {"facebookAccountId": user_id_chat},
            {"userId": user_id_chat},
        ]
    }


def queued_command_filter(user_id_chat):
    return {
        "$and": [
            {"type": "send_message"},
            command_account_filter(user_id_chat),
            {"$or": [
                {"queue_status": {"$in": ["queued", "pending"]}},
                {"status": {"$in": ["queued", "pending"]}},
                {"Status": {"$in": ["Đang xử lý", "Dang xu ly", "queued", "pending"]}},
                {"queue_status": {"$exists": False}, "status": {"$exists": False}},
            ]},
            {"attempts": {"$lt": MAX_QUEUE_ATTEMPTS}},
        ],
    }


def build_command_doc_from_payload(data, *, user_id_chat=None):
    data = data or {}
    account_context = data.get("accountContext") or data.get("account_context") or {}
    user_id_chat = safe_text(
        user_id_chat
        or data.get("user_id_chat")
        or data.get("userId_chat")
        or data.get("accountKey")
        or data.get("facebookAccountId")
        or account_context.get("user_id_chat")
        or account_context.get("accountKey")
        or account_context.get("facebookAccountId")
    )
    thread_id = get_message_thread_id(
        data.get("threadId"), data.get("thread_id"), data.get("chat_id"),
        data.get("recipientId"), data.get("facebookId"), data.get("participant_url"),
        data.get("recipient_url"),
    )
    participant_url = safe_text(data.get("participant_url") or data.get("recipient_url") or thread_id)
    content = safe_text(data.get("content") or data.get("message") or data.get("message_content"))
    now_ms = int(time.time() * 1000)
    command_id = get_numeric_message_id(
        data.get("message_id"),
        data.get("messageId"),
        data.get("crm_message_id"),
        fallback=now_ms,
    )
    return {
        "type": "send_message",
        "Status": "Đang xử lý",
        "queue_status": "queued",
        "status": "queued",
        "message_id": command_id,
        "crm_message_id": command_id,
        "client_message_id": safe_text(data.get("client_message_id")),
        "conversation_id": safe_text(data.get("conversation_id")) or thread_id,
        "threadId": thread_id,
        "thread_id": thread_id,
        "recipientId": thread_id,
        "participant_url": participant_url,
        "recipient_url": participant_url,
        "facebookId": participant_url,
        "participant_name": safe_text(data.get("participant_name") or data.get("fbName")) or thread_id,
        "content": content,
        "message": content,
        "message_content": content,
        "user_id_chat": user_id_chat,
        "userId_chat": user_id_chat,
        "accountKey": user_id_chat,
        "facebookAccountId": user_id_chat,
        "accountContext": account_context,
        "attempts": 0,
        "send_error": "",
        "created_at": local_now_iso(),
        "updated_at": local_now_iso(),
        "createdAt": now_ms,
        "updatedAt": now_ms,
        "__v": 0,
    }


def insert_send_message_command(data, *, user_id_chat=None):
    doc = build_command_doc_from_payload(data, user_id_chat=user_id_chat)
    collection = get_mongo_commands_collection()
    if collection is None:
        raise RuntimeError("MONGODB_URL is not configured")
    key = safe_text(doc.get("client_message_id")) or safe_text(doc.get("message_id"))
    query = {"type": "send_message", "message_id": doc["message_id"]}
    if key and safe_text(doc.get("client_message_id")):
        query = {"type": "send_message", "client_message_id": key}
    collection.update_one(
        query,
        {"$setOnInsert": doc},
        upsert=True,
    )
    stored = collection.find_one(query) or doc
    return stored


def claim_next_mongo_command(user_id_chat):
    collection = get_mongo_commands_collection()
    if collection is None:
        return None
    now_ms = int(time.time() * 1000)
    return collection.find_one_and_update(
        queued_command_filter(user_id_chat),
        {
            "$set": {
                "queue_status": "sending",
                "status": "sending",
                "Status": "Đang xử lý",
                "updatedAt": now_ms,
                "updated_at": local_now_iso(),
            },
            "$inc": {"attempts": 1},
        },
        sort=[("createdAt", 1), ("created_at", 1)],
        return_document=pymongo.ReturnDocument.AFTER,
    )


def update_mongo_command_result(command, success, error=""):
    collection = get_mongo_commands_collection()
    if collection is None or not command:
        return
    final = "sent" if success else ("queued" if int(command.get("attempts") or 0) < MAX_QUEUE_ATTEMPTS else "failed")
    status_text = "Đã gửi" if final == "sent" else ("Thất bại" if final == "failed" else "Đang xử lý")
    collection.update_one(
        {"_id": command["_id"]},
        {"$set": {
            "queue_status": final,
            "status": final,
            "Status": status_text,
            "send_error": "" if success else safe_text(error),
            "updatedAt": int(time.time() * 1000),
            "updated_at": local_now_iso(),
        }},
    )
    if success:
        upsert_outbound_command_to_usermessages(command)


def upsert_outbound_command_to_usermessages(command):
    collection = get_mongo_usermessages_collection()
    if collection is None or not command:
        return False
    user_id = safe_text(command.get("user_id_chat") or command.get("accountKey") or command.get("facebookAccountId"))
    facebook_id = safe_text(command.get("participant_url") or command.get("facebookId") or command.get("recipient_url"))
    if user_id and facebook_id and not facebook_id.startswith(user_id + ":"):
        facebook_id = f"{user_id}:{facebook_id}"
    now_ms = int(time.time() * 1000)
    crm_message_id = get_numeric_message_id(
        command.get("crm_message_id"),
        command.get("message_id"),
        fallback=command.get("createdAt") or now_ms,
    )
    doc = {
        "messageId": facebook_id,
        "userId": user_id,
        "facebookId": facebook_id,
        "sender": safe_text(command.get("participant_name") or facebook_id),
        "lastMessage": safe_text(command.get("content") or command.get("message")),
        "conversation_id": command.get("conversation_id"),
        "crm_message_id": crm_message_id,
        "sender_name": "Tôi",
        "is_from_crm": True,
        "timestamp": local_now_iso(),
        "is_read": True,
        "send_status": "sent",
        "send_error": "",
        "createdAt": command.get("createdAt") or now_ms,
        "updatedAt": now_ms,
        "__v": 0,
    }
    collection.update_one({"crm_message_id": crm_message_id}, {"$set": doc}, upsert=True)
    return True


def parse_message_time(value):
    if not value:
        return datetime.now().astimezone()
    text = str(value).strip()
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        try:
            dt = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
        except Exception:
            return datetime.now().astimezone()
    if dt.tzinfo is None:
        # SQLite CURRENT_TIMESTAMP is UTC in this project.
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone()


def command_status_from_queue_status(status):
    status = safe_text(status).lower()
    if status == "sent":
        return "Đã gửi", "sent"
    if status == "failed":
        return "Thất bại", "failed"
    return "Đang xử lý", "queued"


def build_send_message_command_doc(message_id, queue_status=None, queue_row=None):
    if not message_id:
        return None
    try:
        message_id = int(message_id)
    except Exception:
        return None
    with db_connect() as conn:
        row = conn.execute("""
            SELECT
                m.id AS message_id,
                m.conversation_id,
                m.sender_name,
                m.content,
                m.is_from_crm,
                m.timestamp,
                m.is_read,
                m.send_status,
                m.send_error,
                q.status AS queue_status,
                q.error AS queue_error,
                q.created_at AS queue_created_at,
                q.updated_at AS queue_updated_at,
                q.user_id_chat,
                q.participant_url,
                q.participant_name,
                q.client_message_id,
                q.attempts
            FROM messages m
            LEFT JOIN message_send_queue q ON q.message_id = m.id
            WHERE m.id=?
        """, (message_id,)).fetchone()
    if not row:
        return None
    effective_queue_status = queue_status or row["queue_status"] or row["send_status"] or "queued"
    status_text, queue_status_text = command_status_from_queue_status(effective_queue_status)
    created_dt = parse_message_time(row["queue_created_at"] or row["timestamp"])
    timestamp = safe_text(row["timestamp"]) or created_dt.isoformat(timespec="seconds")
    send_error = safe_text(row["queue_error"] or row["send_error"])
    return {
        "type": "send_message",
        "Status": status_text,
        "queue_status": queue_status_text,
        "time": int(parse_message_time(timestamp).timestamp()),
        "created_at": created_dt.isoformat(timespec="seconds"),
        "message_id": message_id,
        "crm_message_id": message_id,
        "conversation_id": safe_text(row["conversation_id"]),
        "sender_name": safe_text(row["sender_name"]),
        "content": safe_text(row["content"]),
        "is_from_crm": bool(row["is_from_crm"]),
        "timestamp": timestamp,
        "is_read": bool(row["is_read"]),
        "send_status": effective_queue_status,
        "send_error": send_error,
        "user_id_chat": safe_text(row["user_id_chat"]),
        "participant_url": safe_text(row["participant_url"]),
        "participant_name": safe_text(row["participant_name"]),
        "client_message_id": safe_text(row["client_message_id"]),
        "attempts": int(row["attempts"] or 0),
        "updated_at": local_now_iso(),
    }


def upsert_send_message_command(message_id, queue_status=None):
    doc = build_send_message_command_doc(message_id, queue_status=queue_status)
    if not doc:
        return False
    try:
        collection = get_mongo_commands_collection()
        if collection is None:
            return False
        collection.update_one(
            {"type": "send_message", "message_id": doc["message_id"]},
            {"$set": doc, "$setOnInsert": {"createdAt": int(time.time() * 1000), "__v": 0}},
            upsert=True,
        )
        return True
    except Exception as exc:
        logger.error("Cannot upsert send_message command message_id=%s: %s", message_id, exc)
        return False


def build_user_message_doc_from_sqlite_message(message_id):
    if not message_id:
        return None
    try:
        message_id = int(message_id)
    except Exception:
        return None
    with db_connect() as conn:
        row = conn.execute("""
            SELECT
                m.id AS crm_message_id,
                m.conversation_id,
                m.sender_name,
                m.content,
                m.is_from_crm,
                m.timestamp,
                m.is_read,
                m.send_status,
                m.send_error,
                c.participant_name,
                c.participant_url,
                c.conversation_url,
                fa.user_id_chat
            FROM messages m
            JOIN conversations c ON c.id = m.conversation_id
            LEFT JOIN facebook_accounts fa ON fa.id = c.facebook_account_id
            WHERE m.id=?
        """, (message_id,)).fetchone()
    if not row:
        return None
    user_id = safe_text(row["user_id_chat"])
    facebook_id = safe_text(row["participant_url"] or row["conversation_url"])
    if user_id and facebook_id and not facebook_id.startswith(user_id + ":"):
        facebook_id = f"{user_id}:{facebook_id}"
    elif not user_id and facebook_id:
        user_id = facebook_id.split(":", 1)[0]
    timestamp = safe_text(row["timestamp"]) or local_now_iso()
    ts_ms = int(parse_message_time(timestamp).timestamp() * 1000)
    return {
        "messageId": facebook_id,
        "userId": user_id,
        "facebookId": facebook_id,
        "sender": safe_text(row["participant_name"] or row["sender_name"] or facebook_id),
        "lastMessage": safe_text(row["content"]),
        "conversation_id": safe_text(row["conversation_id"]),
        "crm_message_id": int(row["crm_message_id"]),
        "sender_name": safe_text(row["sender_name"]),
        "is_from_crm": bool(row["is_from_crm"]),
        "timestamp": timestamp,
        "is_read": bool(row["is_read"]),
        "send_status": safe_text(row["send_status"]),
        "send_error": safe_text(row["send_error"]),
        "createdAt": ts_ms,
        "updatedAt": ts_ms,
        "__v": 0,
    }


def upsert_sent_message_to_usermessages(message_id):
    doc = build_user_message_doc_from_sqlite_message(message_id)
    if not doc:
        return False
    try:
        collection = get_mongo_commands_collection()
        if collection is None:
            return False
        collection.database["usermessages"].update_one(
            {"crm_message_id": doc["crm_message_id"]},
            {"$set": doc},
            upsert=True,
        )
        return True
    except Exception as exc:
        logger.error("Cannot upsert sent message to usermessages message_id=%s: %s", message_id, exc)
        return False


def get_time_ago_string(timestamp_str):
    try:
        msg_time = datetime.fromisoformat(str(timestamp_str).replace("Z", "+00:00"))
        now = datetime.now().astimezone()
        if msg_time.tzinfo is None:
            msg_time = msg_time.replace(tzinfo=now.tzinfo)
        diff = now - msg_time
        if diff < timedelta(minutes=1):
            return "now"
        if diff < timedelta(hours=1):
            return f"{diff.seconds // 60}m"
        if diff < timedelta(days=1):
            return f"{diff.seconds // 3600}h"
        if diff < timedelta(days=7):
            return f"{diff.days}d"
        if diff < timedelta(days=30):
            return f"{diff.days // 7}w"
        return f"{diff.days // 30}mo"
    except Exception:
        return "now"


def get_time_ago_text(timestamp_str):
    try:
        msg_time = datetime.fromisoformat(str(timestamp_str).replace("Z", "+00:00"))
        now = datetime.now().astimezone()
        if msg_time.tzinfo is None:
            msg_time = msg_time.replace(tzinfo=now.tzinfo)
        diff = now - msg_time
        if diff < timedelta(minutes=1):
            return "just now"
        if diff < timedelta(hours=1):
            n = diff.seconds // 60
            return f"{n} minute{'s' if n > 1 else ''} ago"
        if diff < timedelta(days=1):
            n = diff.seconds // 3600
            return f"{n} hour{'s' if n > 1 else ''} ago"
        if diff < timedelta(days=7):
            return f"{diff.days} day{'s' if diff.days > 1 else ''} ago"
        if diff < timedelta(days=30):
            n = diff.days // 7
            return f"{n} week{'s' if n > 1 else ''} ago"
        n = diff.days // 30
        return f"{n} month{'s' if n > 1 else ''} ago"
    except Exception:
        return "just now"


def safe_text(value):
    return str(value or "").strip()


def extract_chat_id_from_url(url):
    value = safe_text(url)
    if not value:
        return None
    match = re.search(r"/messages/t/([^/?#]+)", value)
    if match:
        return match.group(1)
    if not value.startswith(("http://", "https://")):
        return value
    return None


def extract_mobile_visible_account_key(*values):
    for value in values:
        text = safe_text(value)
        match = re.match(r"^([^:]+):mobile_visible:", text)
        if match:
            return match.group(1)
    return ""


def conversation_belongs_to_account(user_id_chat, *conversation_refs):
    owner_key = extract_mobile_visible_account_key(*conversation_refs)
    return not owner_key or owner_key == safe_text(user_id_chat)


def get_message_thread_id(*values):
    for value in values:
        thread_id = extract_chat_id_from_url(value)
        if thread_id:
            return thread_id
    return None


def extract_message_thread_id(url):
    return get_message_thread_id(url)


def build_messenger_url(chat_id):
    return extract_message_thread_id(chat_id)


def resolve_db_tin_nhan_file():
    candidates = []
    configured = os.getenv("DB_TIN_NHAN_FILE") or os.getenv("FB_1_1_DB_TIN_NHAN_FILE")
    if configured:
        candidates.append(Path(configured))
        if not Path(configured).is_absolute():
            candidates.append(_PROJECT_ROOT / configured)
    candidates.extend([_PROJECT_ROOT / "db_tin_nhan.json", Path.cwd() / "db_tin_nhan.json"])
    seen = set()
    for candidate in candidates:
        path = candidate.resolve()
        if str(path) in seen:
            continue
        seen.add(str(path))
        if path.exists():
            return str(path)
    return str((_PROJECT_ROOT / "db_tin_nhan.json").resolve())


def parse_stat_time(value):
    value = safe_text(value)
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(value, fmt)
            except Exception:
                pass
    return None


def message_sort_key(message):
    if not isinstance(message, dict):
        return (datetime.min, 0, "")
    raw_id = safe_text(message.get("crm_message_id") or message.get("id"))
    return (
        parse_stat_time(message.get("time") or message.get("timestamp")) or datetime.min,
        int(raw_id) if raw_id.isdigit() else 0,
        safe_text(message.get("_id")),
    )


def is_time_in_range(value, start_time=None, end_time=None):
    dt = parse_stat_time(value)
    if not dt:
        return not (start_time or end_time)
    return (not start_time or dt >= start_time) and (not end_time or dt <= end_time)


def format_device_label(device_name, device_id=""):
    raw = safe_text(device_name) or safe_text(device_id)
    if not raw:
        return ""
    return raw if raw.lower().startswith(("máy ", "may ")) else f"Máy {raw}"


def _normalize_chat_text(value):
    return safe_text(value).lower()


def _normalize_chat_key(value):
    text = unicodedata.normalize("NFKD", safe_text(value).lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text)


def is_ignorable_json_conversation(item):
    if not isinstance(item, dict):
        return True
    sender = _normalize_chat_key(item.get("sender") or item.get("participant_name"))
    last = _normalize_chat_key(item.get("last_message") or item.get("last_message_content"))
    messages = item.get("last_5_messages") or []
    content = " | ".join([sender, last] + [
        _normalize_chat_key(m.get("content")) for m in messages if isinstance(m, dict)
    ])
    blocked_exact = {"tat ca", "anh", "reels", "vi tri", "que quan", "ngay sinh", "tao", "all", "photos"}
    blocked_fragments = (
        "chua xem", "ban moi", "tin chua doc", "dang hoat dong", "thong bao",
        "trong so", "active now", "delivered", "seen", "end-to-end encrypted",
        "ma hoa dau cuoi", "tab 1/", "tab 2/", "tab 3/", "trang chu", "nhom",
        "khoi phuc lich su chat", "de xem tin nhan bi thieu"
    )
    if sender.startswith("tin cua ") or sender in blocked_exact:
        return True
    return any(x in content for x in blocked_fragments)


def ensure_message_delivery_columns(cursor):
    cursor.execute("PRAGMA table_info(messages)")
    columns = {row[1] for row in cursor.fetchall()}
    if "send_status" not in columns:
        cursor.execute("ALTER TABLE messages ADD COLUMN send_status TEXT DEFAULT 'sent'")
    if "send_error" not in columns:
        cursor.execute("ALTER TABLE messages ADD COLUMN send_error TEXT DEFAULT ''")


def ensure_message_send_queue_table(cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS message_send_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id INTEGER UNIQUE NOT NULL,
            conversation_id INTEGER,
            user_id_chat TEXT NOT NULL,
            bot_sid TEXT,
            participant_url TEXT NOT NULL,
            participant_name TEXT,
            content TEXT NOT NULL,
            client_message_id TEXT,
            account_context TEXT DEFAULT '',
            result_callback_url TEXT DEFAULT '',
            status TEXT DEFAULT 'queued',
            attempts INTEGER DEFAULT 0,
            error TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("PRAGMA table_info(message_send_queue)")
    columns = {row[1] for row in cursor.fetchall()}
    for col, ddl in {
        "conversation_id": "ALTER TABLE message_send_queue ADD COLUMN conversation_id INTEGER",
        "account_context": "ALTER TABLE message_send_queue ADD COLUMN account_context TEXT DEFAULT ''",
        "result_callback_url": "ALTER TABLE message_send_queue ADD COLUMN result_callback_url TEXT DEFAULT ''",
    }.items():
        if col not in columns:
            cursor.execute(ddl)


def init_database():
    with db_connect() as conn:
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS facebook_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id_chat TEXT UNIQUE NOT NULL,
                username TEXT NOT NULL,
                encrypted_password TEXT NOT NULL,
                two_fa_code TEXT,
                is_active BOOLEAN DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_online TIMESTAMP
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                facebook_account_id INTEGER,
                participant_name TEXT NOT NULL,
                participant_url TEXT NOT NULL,
                conversation_url TEXT NOT NULL,
                unread_count INTEGER DEFAULT 0,
                last_message_timestamp TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER,
                sender_name TEXT NOT NULL,
                content TEXT NOT NULL,
                is_from_crm BOOLEAN DEFAULT 0,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_read BOOLEAN DEFAULT 0,
                send_status TEXT DEFAULT 'sent',
                send_error TEXT DEFAULT ''
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                facebook_account_id INTEGER,
                content TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                posted_at TIMESTAMP
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                facebook_account_id INTEGER,
                content TEXT NOT NULL,
                notification_type TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_read BOOLEAN DEFAULT 0
            )
        """)
        ensure_message_delivery_columns(c)
        ensure_message_send_queue_table(c)
        conn.commit()


def set_message_delivery_status(message_id, status, error=""):
    if not message_id:
        return
    try:
        with db_connect() as conn:
            c = conn.cursor()
            ensure_message_delivery_columns(c)
            c.execute("UPDATE messages SET send_status=?, send_error=? WHERE id=?", (status, error or "", message_id))
            conn.commit()
        upsert_send_message_command(message_id, status)
        if safe_text(status).lower() == "sent":
            upsert_sent_message_to_usermessages(message_id)
    except Exception as exc:
        logger.error("Cannot update message delivery status message_id=%s: %s", message_id, exc)


def resolve_account_context_for_queue(user_id_chat, account_context=None):
    context = account_context if isinstance(account_context, dict) else {}
    uid = safe_text(
        context.get("user_id_chat")
        or context.get("accountKey")
        or context.get("facebookAccountId")
        or context.get("account")
        or user_id_chat
    )
    if not uid:
        return context

    metadata = enrich_facebook_account({
        "user_id_chat": uid,
        "account": uid,
        "accountKey": uid,
        "facebookAccountId": uid,
        "username": uid,
    })
    merged = {**metadata, **context}
    merged["user_id_chat"] = uid
    merged["account"] = safe_text(merged.get("account")) or uid
    merged["accountKey"] = safe_text(merged.get("accountKey")) or uid
    merged["facebookAccountId"] = safe_text(merged.get("facebookAccountId")) or uid
    display = safe_text(
        merged.get("displayName")
        or merged.get("name")
        or merged.get("username")
        or uid
    )
    merged["displayName"] = display
    merged["name"] = safe_text(merged.get("name")) or display
    merged["username"] = safe_text(merged.get("username")) or display
    return merged


def enqueue_send_message(message_id, conversation_id, user_id_chat, participant_url, participant_name, content,
                         client_message_id=None, account_context=None, result_callback_url=None):
    user_id_chat = safe_text(user_id_chat)
    account_context = resolve_account_context_for_queue(user_id_chat, account_context)
    queue_bot_sid = ""
    socket_sid = bot_instances.get(user_id_chat)
    if socket_sid:
        device_id = safe_text((bot_device_sessions.get(socket_sid) or {}).get("device_id"))
        if device_id:
            queue_bot_sid = f"mobile_priority:{device_id}"
    with db_connect() as conn:
        c = conn.cursor()
        ensure_message_send_queue_table(c)
        c.execute("""
            INSERT OR REPLACE INTO message_send_queue (
                message_id, conversation_id, user_id_chat, bot_sid, participant_url, participant_name,
                content, client_message_id, account_context, result_callback_url, status, attempts, error, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', 0, '', CURRENT_TIMESTAMP)
        """, (
            int(message_id), conversation_id, user_id_chat, queue_bot_sid,
            safe_text(participant_url), safe_text(participant_name), safe_text(content), safe_text(client_message_id),
            json.dumps(account_context or {}, ensure_ascii=False), safe_text(result_callback_url)
        ))
        conn.commit()
    upsert_send_message_command(message_id, "queued")


def reassign_queued_messages_to_bot(source_user_id_chat, target_user_id_chat, target_bot_sid):
    if not source_user_id_chat or not target_user_id_chat:
        return 0
    with db_connect() as conn:
        c = conn.cursor()
        ensure_message_send_queue_table(c)
        c.execute("""
            UPDATE message_send_queue
            SET user_id_chat=?, bot_sid=?, updated_at=CURRENT_TIMESTAMP
            WHERE user_id_chat=? AND status IN ('queued','failed') AND attempts < ?
        """, (target_user_id_chat, target_bot_sid, source_user_id_chat, MAX_QUEUE_ATTEMPTS))
        count = c.rowcount
        rows = c.execute("""
            SELECT message_id FROM message_send_queue
            WHERE user_id_chat=? AND bot_sid=? AND status IN ('queued','failed') AND attempts < ?
        """, (target_user_id_chat, target_bot_sid, MAX_QUEUE_ATTEMPTS)).fetchall()
        conn.commit()
    for row in rows:
        upsert_send_message_command(row["message_id"], "queued")
    return count


def adopt_queued_messages_for_single_online_bot(user_id_chat):
    bot_sid = bot_instances.get(safe_text(user_id_chat))
    if not bot_sid:
        return 0
    device_id = safe_text((bot_device_sessions.get(bot_sid) or {}).get("device_id"))
    queue_bot_sid = f"mobile_priority:{device_id}" if device_id else ""
    with db_connect() as conn:
        c = conn.cursor()
        ensure_message_send_queue_table(c)
        c.execute("""
            UPDATE message_send_queue
            SET bot_sid=?, updated_at=CURRENT_TIMESTAMP
            WHERE user_id_chat=? AND status='queued'
        """, (queue_bot_sid, user_id_chat))
        count = c.rowcount
        rows = c.execute("SELECT message_id FROM message_send_queue WHERE user_id_chat=? AND status='queued'", (user_id_chat,)).fetchall()
        conn.commit()
    for row in rows:
        upsert_send_message_command(row["message_id"], "queued")
    return count


def claim_next_queued_message(user_id_chat, bot_sid):
    with db_connect() as conn:
        c = conn.cursor()
        ensure_message_send_queue_table(c)
        c.execute("""
            UPDATE message_send_queue
            SET status='failed', error='Max send attempts reached', updated_at=CURRENT_TIMESTAMP
            WHERE user_id_chat=? AND status='queued' AND attempts >= ?
        """, (user_id_chat, MAX_QUEUE_ATTEMPTS))
        failed_rows = c.execute("""
            SELECT message_id FROM message_send_queue
            WHERE user_id_chat=? AND status='failed' AND error='Max send attempts reached'
        """, (user_id_chat,)).fetchall()
        c.execute("""
            SELECT id, message_id, conversation_id, participant_url, participant_name, content,
                   client_message_id, attempts, account_context, result_callback_url
            FROM message_send_queue
            WHERE user_id_chat=? AND status='queued' AND attempts < ?
            ORDER BY id ASC
            LIMIT 1
        """, (user_id_chat, MAX_QUEUE_ATTEMPTS))
        row = c.fetchone()
        if not row:
            conn.commit()
            for failed_row in failed_rows:
                upsert_send_message_command(failed_row["message_id"], "failed")
            return None
        c.execute("""
            UPDATE message_send_queue
            SET status='sending', bot_sid=?, attempts=attempts+1, error='', updated_at=CURRENT_TIMESTAMP
            WHERE id=? AND status='queued'
        """, (bot_sid, row["id"]))
        if c.rowcount != 1:
            conn.commit()
            return None
        conn.commit()
        upsert_send_message_command(row["message_id"], "queued")
        return tuple(row)


def finish_queued_message(queue_id, message_id, success, error="", attempts=0, max_attempts=MAX_QUEUE_ATTEMPTS):
    with db_connect() as conn:
        c = conn.cursor()
        ensure_message_send_queue_table(c)
        if success:
            c.execute("DELETE FROM message_send_queue WHERE id=?", (queue_id,))
            final = "sent"
        elif attempts < max_attempts:
            c.execute("""
                UPDATE message_send_queue
                SET status='queued', error=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=?
            """, (error or "", queue_id))
            final = "queued"
        else:
            c.execute("""
                UPDATE message_send_queue
                SET status='failed', error=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=?
            """, (error or "Max send attempts reached", queue_id))
            final = "failed"
        conn.commit()
    set_message_delivery_status(message_id, final, "" if success else (error or ""))
    upsert_send_message_command(message_id, final)
    return final


def notify_sqlite_message_result(result_callback_url, message_id, success, error="", conversation_id=None,
                                 client_message_id=None, user_id_chat=None):
    url = safe_text(result_callback_url) or safe_text(os.getenv("FB_1_1_RESULT_CALLBACK_URL"))
    if not url:
        return False
    payload = {
        "message_id": str(message_id),
        "messageId": str(message_id),
        "status": "sent" if success else "failed",
        "success": bool(success),
        "error": error or "",
        "conversation_id": conversation_id,
        "client_message_id": client_message_id,
        "user_id_chat": user_id_chat,
        "transport": "sqlite_message_send_queue",
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        r.raise_for_status()
        return True
    except Exception as exc:
        logger.error("Cannot post queue result callback: %s", exc)
        return False


def get_bot_send_lock(bot_sid):
    bot_sid = safe_text(bot_sid)
    if bot_sid not in bot_send_locks:
        bot_send_locks[bot_sid] = threading.Lock()
    return bot_send_locks[bot_sid]


def is_bot_fresh(user_id_chat):
    info = bot_last_seen.get(safe_text(user_id_chat)) or {}
    ts = info.get("last_seen_epoch")
    return bool(ts and time.time() - float(ts) <= BOT_STALE_SECONDS)


def start_send_queue_for_bot(user_id_chat):
    user_id_chat = safe_text(user_id_chat)
    bot_sid = bot_instances.get(user_id_chat)

    if not bot_sid:
        # Nếu account cụ thể chưa map nhưng chỉ có 1 bot online, dùng bot đó để gửi.
        online = [(uid, sid) for uid, sid in bot_instances.items() if sid]
        if len(online) == 1:
            fallback_uid, bot_sid = online[0]
            logger.warning("No exact bot for %s; fallback to single online bot %s", user_id_chat, fallback_uid)
            user_id_chat = fallback_uid
        else:
            logger.warning(
                "Cannot start send queue for user_id_chat=%s because no matching bot is online. Online bots=%s sessions=%s",
                user_id_chat, list(bot_instances.keys()), bot_device_sessions,
            )
            return False

    with bot_queue_workers_lock:
        if user_id_chat in bot_queue_workers:
            return True
        bot_queue_workers.add(user_id_chat)
    socketio.start_background_task(process_send_queue_for_bot, user_id_chat, bot_sid)
    return True


def fail_stale_queued_messages(max_attempts=MAX_QUEUE_ATTEMPTS):
    collection = get_mongo_commands_collection()
    if collection is None:
        return
    result = collection.update_many(
        {
            "type": "send_message",
            "queue_status": {"$in": ["queued", "sending"]},
            "attempts": {"$gte": max_attempts},
        },
        {"$set": {
            "queue_status": "failed",
            "status": "failed",
            "Status": "Thất bại",
            "send_error": "Max send attempts reached",
            "updatedAt": int(time.time() * 1000),
            "updated_at": local_now_iso(),
        }},
    )
    if result.modified_count:
        logger.warning("Marked %s stale Mongo Commands as failed", result.modified_count)


def process_send_queue_for_bot(user_id_chat, bot_sid):
    logger.info("Start FIFO queue worker user_id_chat=%s sid=%s", user_id_chat, bot_sid)
    try:
        while bot_instances.get(user_id_chat) == bot_sid:
            command = claim_next_mongo_command(user_id_chat)
            if not command:
                return
            message_id = safe_text(command.get("message_id") or command.get("crm_message_id") or command.get("_id"))
            conversation_id = safe_text(command.get("conversation_id") or command.get("threadId") or command.get("thread_id"))
            participant_url = safe_text(command.get("participant_url") or command.get("recipient_url") or command.get("facebookId"))
            participant_name = safe_text(command.get("participant_name") or command.get("fbName"))
            content = safe_text(command.get("content") or command.get("message") or command.get("message_content"))
            client_message_id = safe_text(command.get("client_message_id"))
            account_context = command.get("accountContext") or command.get("account_context") or {}
            if not isinstance(account_context, dict):
                account_context = {}

            try:
                result = call_send_message_command(
                    bot_sid, participant_url, content, message_id, conversation_id,
                    client_message_id, participant_name, timeout=SEND_COMMAND_TIMEOUT,
                    account_context=account_context,
                ) or {}
            except Exception as exc:
                result = {"success": False, "error": f"Bot không phản hồi lệnh gửi: {exc}"}

            success = bool(result.get("success"))
            error = safe_text(result.get("error"))
            update_mongo_command_result(command, success, error)
            socketio.emit("crm_send_message_status", {
                "message_id": message_id,
                "client_message_id": client_message_id,
                "conversation_id": result.get("conversation_id") or conversation_id,
                "success": success,
                "error": "" if success else error,
            })
    finally:
        with bot_queue_workers_lock:
            bot_queue_workers.discard(user_id_chat)


def update_db_tin_nhan_on_success(conversation_id, content):
    path = resolve_db_tin_nhan_file()
    try:
        with db_connect() as conn:
            row = conn.execute("SELECT participant_url, participant_name FROM conversations WHERE id=?", (conversation_id,)).fetchone()
        if not row or not Path(path).exists():
            return
        chat_id = extract_chat_id_from_url(row["participant_url"])
        if not chat_id:
            return
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if chat_id not in data or not isinstance(data[chat_id], dict):
            data[chat_id] = {"sender": row["participant_name"], "last_5_messages": []}
        conv = data[chat_id]
        conv.setdefault("last_5_messages", []).append({"sender": "Tôi", "content": content, "time": local_now_iso()})
        conv["last_5_messages"] = conv["last_5_messages"][-5:]
        conv["last_message"] = get_time_ago_string(local_now_iso())
        conv["last_message_time"] = get_time_ago_text(local_now_iso())
        Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=4), encoding="utf-8")
    except Exception as exc:
        logger.error("Cannot update db_tin_nhan.json: %s", exc)


def build_account_metadata_from_devices(devices, source=""):
    accounts = {}
    if not isinstance(devices, list):
        return accounts
    for device in devices:
        if not isinstance(device, dict):
            continue
        device_id = safe_text(device.get("device_id"))
        device_name = format_device_label(device.get("device_name"), device_id)
        crm_user = device.get("user") if isinstance(device.get("user"), dict) else {}
        for acc in device.get("accounts") or []:
            if not isinstance(acc, dict):
                continue
            account = safe_text(acc.get("account") or acc.get("user_id_chat") or acc.get("accountKey"))
            if not account:
                continue
            name = safe_text(acc.get("name") or acc.get("displayName") or account)
            accounts[account] = {
                "account": account, "accountKey": account, "facebookAccountId": account,
                "user_id_chat": account, "username": account, "displayName": name, "name": name,
                "status": safe_text(acc.get("status")), "emp_id": acc.get("emp_id"),
                "device_id": device_id, "device_name": device_name, "crm_id": crm_user.get("crm_id"),
                "crm_name": crm_user.get("name"), "source": source or "Facebook.devices",
            }
    return accounts


def load_account_metadata_from_file():
    path = Path(FACEBOOK_DEVICES_FILE)
    if not path.is_absolute():
        path = _PROJECT_ROOT / path
    if not path.exists():
        return {}
    return build_account_metadata_from_devices(json.loads(path.read_text(encoding="utf-8")), source=str(path.name))


def build_mongo_uri_from_database_accounts_env(account_type="Base"):
    data = json.loads(Path(DATABASE_ACCOUNTS_FILE).read_text(encoding="utf-8")).get(account_type, {})
    username = urllib.parse.quote_plus(data["username"])
    password = urllib.parse.quote_plus(data["pwd"])
    return f"mongodb://{username}:{password}@123.24.206.25:27017/?authSource=admin"


def load_account_metadata_from_mongo():
    try:
        import pymongo
    except Exception:
        return {}
    client = pymongo.MongoClient(build_mongo_uri_from_database_accounts_env(), serverSelectionTimeoutMS=3000)
    try:
        devices = list(client["Facebook"]["devices"].find({}, {"device_id": 1, "device_name": 1, "user": 1, "accounts": 1}))
        return build_account_metadata_from_devices(devices, source="MongoDB")
    finally:
        client.close()


def get_account_metadata_map():
    now = time.time()
    if now - _ACCOUNT_METADATA_CACHE["loaded_at"] < ACCOUNT_METADATA_CACHE_SECONDS:
        return _ACCOUNT_METADATA_CACHE["accounts"]
    accounts = {}
    try:
        accounts = load_account_metadata_from_mongo()
    except Exception as exc:
        logger.warning("Cannot load Mongo account metadata: %s", exc)
    if not accounts:
        try:
            accounts = load_account_metadata_from_file()
        except Exception as exc:
            logger.warning("Cannot load file account metadata: %s", exc)
    _ACCOUNT_METADATA_CACHE.update({"loaded_at": now, "accounts": accounts, "source": "cache"})
    return accounts


def enrich_facebook_account(row_account):
    row_account = row_account or {}
    key = safe_text(row_account.get("user_id_chat") or row_account.get("account") or row_account.get("username"))
    meta = get_account_metadata_map().get(key, {})
    display = meta.get("displayName") or meta.get("name") or row_account.get("displayName") or row_account.get("username") or key
    return {
        **row_account, **meta,
        "id": row_account.get("id") or meta.get("id") or key,
        "user_id_chat": key,
        "account": meta.get("account") or key,
        "accountKey": meta.get("accountKey") or key,
        "facebookAccountId": meta.get("facebookAccountId") or key,
        "username": meta.get("username") or key,
        "displayName": display,
        "name": meta.get("name") or display,
        "device_name": meta.get("device_name") or row_account.get("device_name") or "Android USB",
        "is_online": key in bot_instances or bool(row_account.get("is_online")),
        "status": "Online" if (key in bot_instances or row_account.get("is_online")) else "Offline",
    }


def load_json_chat_history():
    path = Path(resolve_db_tin_nhan_file())
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def safe_registered_device_accounts(data):
    data = data or {}
    active = safe_text(data.get("user_id_chat"))
    accounts = []
    seen = set()
    for item in data.get("active_device_accounts") or data.get("accounts") or []:
        if not isinstance(item, dict):
            continue
        uid = safe_text(item.get("user_id_chat") or item.get("account") or item.get("accountKey") or item.get("facebookAccountId"))
        if not uid or uid in seen:
            continue
        seen.add(uid)
        accounts.append({
            "user_id_chat": uid,
            "account": safe_text(item.get("account")) or uid,
            "accountKey": uid,
            "facebookAccountId": uid,
            "username": safe_text(item.get("username")) or uid,
            "displayName": safe_text(item.get("displayName") or item.get("name") or item.get("username")) or uid,
            "name": safe_text(item.get("name") or item.get("displayName") or item.get("username")) or uid,
            "device_id": safe_text(item.get("device_id") or data.get("device_id")),
            "device_name": safe_text(item.get("device_name") or data.get("device_name")),
            "is_online": True if uid == active else bool(item.get("is_online")),
            "status": "Online",
        })
    if active and not any(a["user_id_chat"] == active for a in accounts):
        accounts.append({
            "user_id_chat": active, "account": active, "accountKey": active, "facebookAccountId": active,
            "username": safe_text(data.get("username")) or active,
            "displayName": safe_text(data.get("displayName") or data.get("username")) or active,
            "name": safe_text(data.get("name") or data.get("username")) or active,
            "device_id": safe_text(data.get("device_id")), "device_name": safe_text(data.get("device_name")),
            "is_online": True, "status": "Online",
        })
    return accounts


def register_bot_session(sid: str, data: Dict[str, Any]):
    user_id_chat = safe_text(
        data.get("user_id_chat") or data.get("userId_chat") or data.get("account")
        or data.get("accountKey") or data.get("facebookAccountId")
    )
    username = safe_text(data.get("username") or data.get("displayName") or data.get("name") or user_id_chat)
    accounts = safe_registered_device_accounts(data)
    if not user_id_chat and accounts:
        user_id_chat = accounts[0]["user_id_chat"]
    if not user_id_chat:
        user_id_chat = f"sid:{sid}"

    now = time.time()
    session = {
        "sid": sid,
        "user_id_chat": user_id_chat,
        "username": username or user_id_chat,
        "device_id": safe_text(data.get("device_id")),
        "device_name": safe_text(data.get("device_name")),
        "accounts": accounts,
        "registered_at": local_now_iso(),
        "last_seen": local_now_iso(),
        "last_seen_epoch": now,
        "raw": data,
    }
    bot_device_sessions[sid] = session

    mapped_accounts = {user_id_chat}
    mapped_accounts.update(a["user_id_chat"] for a in accounts if a.get("user_id_chat"))
    account_by_uid = {
        a.get("user_id_chat"): a
        for a in accounts
        if isinstance(a, dict) and a.get("user_id_chat")
    }
    for uid in mapped_accounts:
        acc_meta = account_by_uid.get(uid, {})
        uid_display = safe_text(
            acc_meta.get("displayName")
            or acc_meta.get("name")
            or acc_meta.get("username")
            or (username if uid == user_id_chat else "")
            or uid
        )
        bot_instances[uid] = sid
        bot_last_seen[uid] = {
            "username": uid_display,
            "device_id": session["device_id"],
            "device_name": session["device_name"],
            "last_seen": local_now_iso(),
            "last_seen_epoch": now,
            "sid": sid,
        }

    logger.info("BOT ONLINE sid=%s accounts=%s", sid, sorted(mapped_accounts))
    for uid in mapped_accounts:
        start_send_queue_for_bot(uid)
    socketio.emit("bot_status_update", {"online": True, "bot_instances": list(bot_instances.keys()), "session": session})
    return session


def build_send_message_payload(participant_url, content, message_id, conversation_id, client_message_id=None,
                               participant_name=None, account_context=None, thread_id=None):
    expected_thread_id = thread_id or extract_message_thread_id(participant_url)
    return {
        "recipient_url": participant_url,
        "message_content": content,
        "content": content,
        "message_id": message_id,
        "conversation_id": conversation_id,
        "client_message_id": client_message_id,
        "expected_thread_id": expected_thread_id,
        "thread_id": expected_thread_id,
        "participant_name": participant_name,
        "accountContext": account_context or {},
    }


def emit_send_message_command(bot_sid, participant_url, content, message_id, conversation_id, client_message_id=None,
                              participant_name=None, account_context=None, thread_id=None):
    socketio.emit("send_message_command", build_send_message_payload(
        participant_url, content, message_id, conversation_id, client_message_id, participant_name, account_context, thread_id
    ), room=bot_sid)


def call_send_message_command(bot_sid, participant_url, content, message_id, conversation_id, client_message_id=None,
                              participant_name=None, timeout=60, account_context=None, thread_id=None):
    with get_bot_send_lock(bot_sid):
        return socketio.call("send_message_command", build_send_message_payload(
            participant_url, content, message_id, conversation_id, client_message_id, participant_name, account_context, thread_id
        ), to=bot_sid, timeout=timeout)


def call_switch_account_command(bot_sid, account_context, timeout=240):
    if not account_context:
        return {"success": True, "skipped": True}
    return socketio.call("switch_account_command", {
        "accountContext": account_context,
        "account": account_context.get("account") or account_context.get("username") or account_context.get("accountKey"),
        "password": account_context.get("password"),
        "force_reset": True,
    }, to=bot_sid, timeout=timeout)


def get_default_account_context():
    if FOLDER1_DEFAULT_USER_ID_CHAT:
        return {
            "user_id_chat": FOLDER1_DEFAULT_USER_ID_CHAT,
            "username": FOLDER1_DEFAULT_USER_ID_CHAT,
            "accountKey": FOLDER1_DEFAULT_USER_ID_CHAT,
            "facebookAccountId": FOLDER1_DEFAULT_USER_ID_CHAT,
            "is_online": FOLDER1_DEFAULT_USER_ID_CHAT in bot_instances,
        }
    uid, _sid = get_any_online_bot_sid()
    if uid:
        return {"user_id_chat": uid, "accountKey": uid, "facebookAccountId": uid, "is_online": True}
    return None


def get_bot_sid_for_message(payload):
    payload = payload or {}
    candidates = [
        payload.get("userId_chat"), payload.get("user_id_chat"), payload.get("accountKey"),
        payload.get("facebookAccountId"), FOLDER1_DEFAULT_USER_ID_CHAT,
    ]
    for c in candidates:
        uid = safe_text(c)
        if uid and uid in bot_instances:
            return uid, bot_instances[uid]
    return get_any_online_bot_sid()


def get_any_online_bot_sid():
    cleanup_stale_bots()
    if not bot_instances:
        return None, None
    uid, sid = next(iter(bot_instances.items()))
    return uid, sid


def get_bot_sid_for_account_context(account_context):
    account_context = account_context if isinstance(account_context, dict) else {}
    candidates = [
        account_context.get("user_id_chat"), account_context.get("accountKey"),
        account_context.get("facebookAccountId"), account_context.get("account"), account_context.get("username"),
    ]
    for c in candidates:
        uid = safe_text(c)
        if uid and uid in bot_instances:
            return uid, bot_instances[uid]
    return get_any_online_bot_sid()


def cleanup_stale_bots():
    now = time.time()
    stale_uids = [uid for uid, info in bot_last_seen.items()
                  if info.get("last_seen_epoch") and now - float(info["last_seen_epoch"]) > BOT_STALE_SECONDS]
    for uid in stale_uids:
        sid = bot_instances.get(uid)
        if sid and bot_device_sessions.get(sid, {}).get("last_seen_epoch", now) <= now - BOT_STALE_SECONDS:
            bot_device_sessions.pop(sid, None)
        bot_instances.pop(uid, None)
        bot_last_seen.pop(uid, None)


@app.route("/api/fb_accounts", methods=["GET"])
def get_facebook_accounts():
    cleanup_stale_bots()
    accounts = {}
    for uid in set(list(get_account_metadata_map().keys()) + list(bot_instances.keys())):
        accounts[uid] = enrich_facebook_account({"user_id_chat": uid, "username": uid, "is_online": uid in bot_instances})
    if not accounts:
        with db_connect() as conn:
            for r in conn.execute("SELECT id,user_id_chat,username,is_active,last_online FROM facebook_accounts"):
                accounts[r["user_id_chat"]] = enrich_facebook_account({
                    "id": r["id"], "user_id_chat": r["user_id_chat"], "username": r["username"],
                    "is_active": bool(r["is_active"]), "last_online": r["last_online"],
                    "is_online": r["user_id_chat"] in bot_instances,
                })
    return jsonify({"accounts": list(accounts.values())})


@app.route("/api/bot_instances", methods=["GET"])
def get_bot_instances():
    cleanup_stale_bots()
    return jsonify({
        "count": len(bot_instances),
        "bot_instances": list(bot_instances.keys()),
        "details": [{
            "user_id_chat": uid, "sid": sid,
            "last_seen": bot_last_seen.get(uid, {}).get("last_seen"),
            "username": bot_last_seen.get(uid, {}).get("username"),
            "device_id": bot_last_seen.get(uid, {}).get("device_id"),
            "device_name": bot_last_seen.get(uid, {}).get("device_name"),
            "accounts": bot_device_sessions.get(sid, {}).get("accounts", []),
        } for uid, sid in bot_instances.items()],
        "sessions": bot_device_sessions,
    })


@app.route("/api/bot/heartbeat", methods=["POST"])
def bot_http_heartbeat():
    data = request.json or {}
    user_id_chat = safe_text(data.get("user_id_chat") or data.get("account") or data.get("accountKey"))
    if not user_id_chat:
        return jsonify({"success": False, "error": "user_id_chat is required"}), 400
    now = time.time()
    bot_last_seen[user_id_chat] = {
        "username": safe_text(data.get("username")) or user_id_chat,
        "device_id": safe_text(data.get("device_id")),
        "device_name": safe_text(data.get("device_name")),
        "last_seen": local_now_iso(),
        "last_seen_epoch": now,
        "http_only": user_id_chat not in bot_instances,
    }
    # HTTP heartbeat không có socket sid, nên không thể gửi lệnh. Nó chỉ dùng để debug trạng thái.
    return jsonify({"success": True, "socket_online": user_id_chat in bot_instances, "online_bots": list(bot_instances.keys())})


@app.route("/api/default_account", methods=["GET"])
def get_default_account():
    acc = get_default_account_context()
    if not acc:
        return jsonify({"error": "No default account configured"}), 404
    return jsonify({"data": acc})


@app.route("/api/current_account", methods=["GET"])
def get_current_account():
    cleanup_stale_bots()
    sid = safe_text(request.args.get("sid"))
    session = bot_device_sessions.get(sid) if sid else None
    if not session:
        device_id = safe_text(request.args.get("device_id"))
        sessions = list(bot_device_sessions.items())
        if device_id:
            sessions = [
                (item_sid, item)
                for item_sid, item in sessions
                if safe_text(item.get("device_id")) == device_id
            ]
        if sessions:
            sid, session = max(
                sessions,
                key=lambda item: float((item[1] or {}).get("last_seen_epoch") or 0),
            )
    if session:
        return jsonify({
            "success": True,
            "current_account": session.get("user_id_chat"),
            "user_id_chat": session.get("user_id_chat"),
            "username": session.get("username"),
            "displayName": session.get("username"),
            "bot_sid": sid,
            "device_id": session.get("device_id"),
        })

    uid, sid = get_any_online_bot_sid()
    if not sid:
        return jsonify({"success": False, "error": "No online bots"}), 503
    try:
        result = socketio.call("get_current_account_command", {}, to=sid, timeout=30) or {}
        current = result.get("current_account") or uid
        return jsonify({"success": True, "current_account": current, "user_id_chat": current, "bot_sid": sid})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route("/api/switch_account", methods=["POST"])
def switch_account():
    data = request.json or {}
    account_context = data.get("accountContext") or data.get("account_context") or data
    uid, sid = get_bot_sid_for_account_context(account_context)
    if not sid:
        return jsonify({"success": False, "error": "No online bot"}), 503
    try:
        result = call_switch_account_command(sid, account_context, timeout=240) or {}
        if result.get("success", True):
            switched_uid = safe_text(
                result.get("user_id_chat")
                or account_context.get("user_id_chat")
                or account_context.get("accountKey")
                or account_context.get("facebookAccountId")
                or account_context.get("account")
                or uid
            )
            switched_name = safe_text(
                account_context.get("displayName")
                or account_context.get("name")
                or account_context.get("username")
                or result.get("username")
                or switched_uid
            )
            session = bot_device_sessions.get(sid)
            if isinstance(session, dict) and switched_uid:
                session["user_id_chat"] = switched_uid
                session["username"] = switched_name or switched_uid
                session["last_seen"] = local_now_iso()
                session["last_seen_epoch"] = time.time()
                for item in session.get("accounts") or []:
                    if not isinstance(item, dict):
                        continue
                    item_uid = safe_text(
                        item.get("user_id_chat")
                        or item.get("account")
                        or item.get("accountKey")
                        or item.get("facebookAccountId")
                    )
                    item["is_online"] = item_uid == switched_uid
                    item["status"] = "Online" if item_uid == switched_uid else item.get("status", "Online")
            if switched_uid:
                bot_instances[switched_uid] = sid
                bot_last_seen[switched_uid] = {
                    "username": switched_name or switched_uid,
                    "device_id": session.get("device_id") if isinstance(session, dict) else "",
                    "device_name": session.get("device_name") if isinstance(session, dict) else "",
                    "last_seen": local_now_iso(),
                    "last_seen_epoch": time.time(),
                    "sid": sid,
                }
                socketio.emit("bot_status_update", {
                    "online": True,
                    "bot_instances": list(bot_instances.keys()),
                    "session": session,
                })
        return jsonify({**result, "runner_user_id_chat": uid}), 200 if result.get("success", True) else 400
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route("/api/commands/pending", methods=["GET"])
def commands_pending():
    accounts = [
        safe_text(item)
        for item in safe_text(request.args.get("accounts")).split(",")
        if safe_text(item)
    ]
    if not accounts:
        account = safe_text(request.args.get("user_id_chat") or request.args.get("accountKey") or request.args.get("facebookAccountId"))
        if account:
            accounts = [account]
    collection = get_mongo_commands_collection()
    if collection is None or not accounts:
        return jsonify({"pending": False, "count": 0})
    query = {
        "type": "send_message",
        "$or": [
            {"user_id_chat": {"$in": accounts}},
            {"userId_chat": {"$in": accounts}},
            {"accountKey": {"$in": accounts}},
            {"facebookAccountId": {"$in": accounts}},
            {"userId": {"$in": accounts}},
        ],
        "queue_status": {"$in": ["queued", "pending", "sending"]},
    }
    count = collection.count_documents(query)
    return jsonify({"pending": count > 0, "count": count})


@app.route("/api/sqlite_message_send_queue", methods=["POST"])
def sqlite_message_send_queue():
    data = request.json or {}
    content = safe_text(data.get("content") or data.get("message") or data.get("message_content"))
    user_id_chat = safe_text(data.get("user_id_chat") or data.get("userId_chat") or data.get("accountKey") or data.get("facebookAccountId"))
    if not user_id_chat:
        uid, _ = get_any_online_bot_sid()
        user_id_chat = uid or ""
    if not user_id_chat:
        return jsonify({"success": False, "error": "user_id_chat/accountKey is required"}), 400
    thread_id = get_message_thread_id(data.get("threadId"), data.get("thread_id"), data.get("chat_id"),
                                      data.get("recipientId"), data.get("facebookId"), data.get("participant_url"),
                                      data.get("recipient_url"))
    if not thread_id:
        return jsonify({"success": False, "error": "threadId/chat_id/recipientId is required"}), 400
    if not content:
        return jsonify({"success": False, "error": "content is required"}), 400

    command = insert_send_message_command(data, user_id_chat=user_id_chat)
    runner_uid, sid = get_bot_sid_for_message(command)
    if runner_uid:
        worker_started = start_send_queue_for_bot(runner_uid)
    else:
        worker_started = start_send_queue_for_bot(user_id_chat)
    return jsonify({
        "success": True, "queued": True, "transport": "mongo_commands",
        "message_id": safe_text(command.get("message_id")),
        "conversation_id": command.get("conversation_id"),
        "thread_id": thread_id,
        "user_id_chat": command.get("user_id_chat"),
        "bot_online": bool(sid),
        "worker_started": worker_started,
    }), 202


@app.route("/api/json_conversations/<user_id_chat>", methods=["GET"])
def get_json_conversations(user_id_chat):
    conversations = []
    for chat_id, item in load_json_chat_history().items():
        if not isinstance(item, dict) or is_ignorable_json_conversation(item):
            continue
        item_account = safe_text(item.get("user_id_chat") or item.get("account") or item.get("facebookAccountId") or item.get("accountKey"))
        if item_account != safe_text(user_id_chat):
            continue
        last_messages = item.get("last_5_messages") or []
        last = last_messages[-1] if last_messages else {}
        participant_url = item.get("participant_url") or item.get("conversation_url") or str(chat_id)
        conversation_url = item.get("conversation_url") or participant_url
        if not conversation_belongs_to_account(user_id_chat, participant_url, conversation_url, chat_id):
            continue
        conversations.append({
            "id": str(chat_id), "db_conversation_id": item.get("conversation_id") or str(chat_id),
            "participant_name": item.get("sender") or item.get("participant_name") or f"Chat {chat_id}",
            "participant_url": participant_url, "conversation_url": conversation_url,
            "unread_count": item.get("unread_count") or 0,
            "last_message_timestamp": last.get("time") or item.get("last_message_timestamp") or "",
            "last_message_content": last.get("content") or item.get("last_message_content") or "",
            "last_sender_name": last.get("sender") or item.get("sender") or "",
            "last_message_is_from_crm": (last.get("sender") in {"Tôi", "Toi", "Me"}),
            "source": "json", "user_id_chat": user_id_chat,
        })
    return jsonify({"conversations": conversations})


@app.route("/api/json_messages/<chat_id>", methods=["GET"])
def get_json_messages(chat_id):
    item = load_json_chat_history().get(str(chat_id)) or {}
    if is_ignorable_json_conversation(item):
        return jsonify({"messages": []})
    messages = []
    for i, m in enumerate(item.get("last_5_messages") or []):
        if not isinstance(m, dict):
            continue
        sender = m.get("sender") or item.get("sender") or "Không rõ"
        messages.append({
            "id": f"{chat_id}_{i}", "sender_name": sender, "content": m.get("content") or "",
            "is_from_crm": sender in {"Tôi", "Toi", "Me"}, "timestamp": m.get("time") or m.get("timestamp") or "",
            "is_read": True, "source": "json",
        })
    return jsonify({"messages": messages})


@app.route("/api/fb_accounts", methods=["POST"])
def add_facebook_account():
    data = request.json or {}
    user_id_chat = safe_text(data.get("user_id_chat"))
    username = safe_text(data.get("username"))
    password = safe_text(data.get("password"))
    if not all([user_id_chat, username, password]):
        return jsonify({"error": "Missing required fields"}), 400
    with db_connect() as conn:
        try:
            conn.execute("""
                INSERT INTO facebook_accounts (user_id_chat, username, encrypted_password, two_fa_code)
                VALUES (?, ?, ?, ?)
            """, (user_id_chat, username, password, data.get("two_fa_code")))
            conn.commit()
            return jsonify({"message": "Account added successfully"})
        except sqlite3.IntegrityError:
            return jsonify({"error": "Account already exists"}), 400


@app.route("/api/message/unanswered-statistics", methods=["POST"])
def get_unanswered_message_statistics():
    data = request.json or {}
    user_ids = [safe_text(x) for x in data.get("user_id_chats", []) if safe_text(x)]
    start = parse_stat_time(data.get("start_time") or data.get("from_time"))
    end = parse_stat_time(data.get("end_time") or data.get("to_time"))
    stats = {}
    stat_user_ids = set(user_ids)
    seen_conversations = set()
    seen_unread_keys = set()
    with db_connect() as conn:
        params = []
        where = ""
        if user_ids:
            where = "WHERE fa.user_id_chat IN (%s)" % ",".join("?" for _ in user_ids)
            params = user_ids
        rows = conn.execute(f"""
            SELECT fa.user_id_chat, c.id, c.participant_name, c.participant_url, c.conversation_url
            FROM conversations c JOIN facebook_accounts fa ON c.facebook_account_id=fa.id {where}
        """, params).fetchall()
        stat_user_ids.update(safe_text(r["user_id_chat"]) for r in rows if safe_text(r["user_id_chat"]))
        for r in rows:
            if not conversation_belongs_to_account(r["user_id_chat"], r["participant_url"], r["conversation_url"]):
                continue
            msgs = conn.execute("""
                SELECT sender_name, content, is_from_crm, timestamp FROM messages
                WHERE conversation_id=? ORDER BY timestamp DESC, id DESC
            """, (r["id"],)).fetchall()
            count = 0
            latest = ""
            latest_content = ""
            latest_sender_name = ""
            for m in msgs:
                if bool(m["is_from_crm"]):
                    break
                if is_time_in_range(m["timestamp"], start, end):
                    count += 1
                    latest = latest or m["timestamp"]
                    latest_content = latest_content or safe_text(m["content"])
                    latest_sender_name = latest_sender_name or safe_text(m["sender_name"])
            if count:
                unread_key = (
                    safe_text(r["user_id_chat"]),
                    safe_text(r["participant_name"]).lower(),
                    safe_text(latest),
                    safe_text(latest_content),
                )
                seen_conversations.add((safe_text(r["user_id_chat"]), safe_text(r["id"])))
                seen_unread_keys.add(unread_key)
                add_unanswered_stat(
                    stats,
                    r["user_id_chat"],
                    r["id"],
                    r["participant_name"],
                    count,
                    latest,
                    latest_content,
                    latest_sender_name,
                )

    for chat_id, item in load_json_chat_history().items():
        if not isinstance(item, dict) or is_ignorable_json_conversation(item):
            continue

        item_account = safe_text(
            item.get("user_id_chat") or item.get("account") or item.get("facebookAccountId") or item.get("accountKey")
        )
        if not item_account or (user_ids and item_account not in user_ids):
            continue

        stat_user_ids.add(item_account)
        conversation_id = safe_text(item.get("conversation_id") or chat_id)
        if not conversation_belongs_to_account(
            item_account,
            item.get("participant_url"),
            item.get("conversation_url"),
            conversation_id,
            chat_id,
        ):
            continue
        if (item_account, conversation_id) in seen_conversations:
            continue

        last_messages = [m for m in (item.get("last_5_messages") or []) if isinstance(m, dict)]
        try:
            unread_count = int(item.get("unread_count") or 0)
        except (TypeError, ValueError):
            unread_count = 0
        latest_content = ""
        latest_time = ""
        latest_sender_name = ""

        if last_messages:
            count_from_messages = 0
            for message in sorted(last_messages, key=message_sort_key, reverse=True):
                sender_name = safe_text(message.get("sender") or item.get("sender") or item.get("participant_name"))
                if sender_name in {"Tôi", "Toi", "Me"}:
                    break
                count_from_messages += 1
                latest_content = latest_content or safe_text(message.get("content"))
                latest_time = latest_time or safe_text(message.get("time") or message.get("timestamp"))
                latest_sender_name = latest_sender_name or sender_name
            unread_count = unread_count or count_from_messages

        latest_content = latest_content or safe_text(item.get("last_message_content") or item.get("last_message"))
        latest_time = latest_time or safe_text(item.get("last_message_timestamp") or item.get("last_message_time"))
        latest_sender_name = latest_sender_name or safe_text(item.get("sender") or item.get("participant_name"))

        if item.get("source") != "unread_conversation" and unread_count <= 0:
            continue

        unread_count = unread_count or 1
        participant_name = safe_text(item.get("participant_name") or item.get("sender") or latest_sender_name or conversation_id)
        unread_key = (item_account, participant_name.lower(), safe_text(latest_time), safe_text(latest_content))
        if unread_key in seen_unread_keys:
            continue
        seen_conversations.add((item_account, conversation_id))
        seen_unread_keys.add(unread_key)
        add_unanswered_stat(
            stats,
            item_account,
            conversation_id,
            participant_name,
            unread_count,
            latest_time,
            latest_content,
            latest_sender_name,
        )

    for uid in stat_user_ids:
        stat = stats.setdefault(safe_text(uid), {
            "user_id_chat": safe_text(uid), "unanswered_count": 0,
            "conversation_count": 0, "latest_customer_message_time": "", "conversations": [],
        })
        stat["conversations"].sort(
            key=lambda item: parse_stat_time(item.get("latest_customer_message_time")) or datetime.min,
            reverse=True,
        )
        stat["friends_status"] = "\u0110ang c\u1eadp nh\u1eadt"
    return jsonify({"results": list(stats.values()), "total": len(stats)})


def add_unanswered_stat(
    account_stats,
    user_id_chat,
    conversation_id,
    participant_name,
    unanswered_count,
    latest_customer_time,
    latest_customer_message_content="",
    latest_customer_sender_name="",
):
    stat = account_stats.setdefault(safe_text(user_id_chat), {
        "user_id_chat": safe_text(user_id_chat), "unanswered_count": 0,
        "conversation_count": 0, "latest_customer_message_time": "", "conversations": [],
    })
    stat["unanswered_count"] += unanswered_count
    stat["conversation_count"] += 1
    if latest_customer_time and latest_customer_time > (stat.get("latest_customer_message_time") or ""):
        stat["latest_customer_message_time"] = latest_customer_time
        stat["last_message_content"] = safe_text(latest_customer_message_content)
        stat["participant_name"] = safe_text(participant_name)
        stat["sender_name"] = safe_text(latest_customer_sender_name)
    stat["conversations"].append({
        "conversation_id": conversation_id, "participant_name": participant_name,
        "unanswered_count": unanswered_count,
        "latest_customer_message_time": latest_customer_time,
        "last_message_content": safe_text(latest_customer_message_content),
        "sender_name": safe_text(latest_customer_sender_name),
    })


@app.route('/api/conversations/<user_id_chat>', methods=['GET'])
def get_conversations(user_id_chat):
    """Lấy danh sách cuộc hội thoại của một tài khoản Facebook"""
    conversations = []
    with db_connect() as conn:
        rows = conn.execute('''
        SELECT c.id, c.participant_name, c.participant_url, c.conversation_url,
               c.unread_count, c.last_message_timestamp,
               (
                   SELECT m.content
                   FROM messages m
                   WHERE m.conversation_id = c.id
                   ORDER BY m.timestamp DESC, m.id DESC
                   LIMIT 1
               ) AS last_message_content,
               (
                   SELECT m.sender_name
                   FROM messages m
                   WHERE m.conversation_id = c.id
                   ORDER BY m.timestamp DESC, m.id DESC
                   LIMIT 1
               ) AS last_sender_name,
               (
                   SELECT m.is_from_crm
                   FROM messages m
                   WHERE m.conversation_id = c.id
                   ORDER BY m.timestamp DESC, m.id DESC
                   LIMIT 1
               ) AS last_message_is_from_crm
        FROM conversations c
        JOIN facebook_accounts fa ON c.facebook_account_id = fa.id
        WHERE fa.user_id_chat = ?
        ORDER BY c.last_message_timestamp DESC
    ''', (user_id_chat,)).fetchall()

    for row in rows:
        if not conversation_belongs_to_account(user_id_chat, row["participant_url"], row["conversation_url"]):
            continue
        if is_ignorable_json_conversation({
            'participant_name': row["participant_name"],
            'last_message_content': row["last_message_content"] or '',
        }):
            continue
        conversation = {
            'id': row["id"],
            'db_conversation_id': row["id"],
            'participant_name': row["participant_name"],
            'participant_url': row["participant_url"],
            'conversation_url': row["conversation_url"],
            'unread_count': row["unread_count"],
            'last_message_timestamp': row["last_message_timestamp"],
            'last_message_content': row["last_message_content"] or '',
            'last_sender_name': row["last_sender_name"] or '',
            'last_message_is_from_crm': bool(row["last_message_is_from_crm"]),
        }
        conversations.append(conversation)

    return jsonify({'conversations': conversations})

@app.route("/api/messages/<conversation_id>", methods=["GET"])
def get_messages(conversation_id):
    with db_connect() as conn:
        c = conn.cursor()
        ensure_message_delivery_columns(c)
        ensure_message_send_queue_table(c)
        rows = c.execute("""
            SELECT
                m.id,
                m.sender_name,
                m.content,
                m.is_from_crm,
                m.timestamp,
                m.is_read,
                CASE
                    WHEN m.send_status IN ('sent', 'failed') THEN m.send_status
                    ELSE COALESCE(q.status, m.send_status)
                END AS send_status,
                CASE
                    WHEN m.send_status IN ('sent', 'failed') THEN m.send_error
                    ELSE COALESCE(NULLIF(q.error, ''), m.send_error)
                END AS send_error
            FROM messages m
            LEFT JOIN message_send_queue q ON q.message_id = m.id
            WHERE m.conversation_id=?
            ORDER BY m.timestamp ASC, m.id ASC
        """, (conversation_id,)).fetchall()
        c.execute("UPDATE messages SET is_read=1 WHERE conversation_id=? AND is_from_crm=0", (conversation_id,))
        c.execute("UPDATE conversations SET unread_count=0 WHERE id=?", (conversation_id,))
        conn.commit()
    return jsonify({"messages": [{
        "id": r["id"], "sender_name": r["sender_name"], "content": r["content"],
        "is_from_crm": bool(r["is_from_crm"]), "timestamp": r["timestamp"],
        "is_read": bool(r["is_read"]), "send_status": r["send_status"], "send_error": r["send_error"],
    } for r in rows if not is_ignorable_json_conversation({"last_5_messages": [{"sender": r["sender_name"], "content": r["content"]}]})]})


@app.route("/api/send_message_from_crm", methods=["POST"])
def send_message_from_crm():
    data = request.json or {}
    user_id_chat = safe_text(data.get("user_id_chat") or data.get("userId_chat") or data.get("accountKey") or data.get("facebookAccountId"))
    thread_id = get_message_thread_id(data.get("threadId"), data.get("thread_id"), data.get("chat_id"),
                                      data.get("recipientId"), data.get("facebookId"), data.get("participant_url"), data.get("recipient_url"))
    content = safe_text(data.get("content") or data.get("message") or data.get("message_content"))
    if not content:
        return jsonify({"error": "Missing content"}), 400
    if not user_id_chat:
        uid, _ = get_any_online_bot_sid()
        user_id_chat = uid or ""
    if not user_id_chat:
        return jsonify({"error": "Missing user_id_chat and no online bot"}), 400
    if not thread_id:
        return jsonify({"error": "Missing threadId/chat_id/recipientId/participant_url"}), 400

    command = insert_send_message_command(data, user_id_chat=user_id_chat)
    runner_uid, sid = get_bot_sid_for_message(command)
    worker_started = start_send_queue_for_bot(runner_uid or user_id_chat)
    return jsonify({
        "message": "Message saved to Mongo Commands and queued for sending",
        "message_id": safe_text(command.get("message_id")),
        "conversation_id": command.get("conversation_id"),
        "client_message_id": command.get("client_message_id"),
        "send_status": "queued",
        "queued": True,
        "transport": "mongo_commands",
        "bot_online": bool(sid),
        "worker_started": worker_started,
    }), 202

@app.route("/api/post_to_facebook", methods=["POST"])
def post_to_facebook():
    data = request.json or {}
    user_id_chat = safe_text(data.get("user_id_chat"))
    content = safe_text(data.get("content"))
    if not user_id_chat or not content:
        return jsonify({"error": "Missing required fields"}), 400
    sid = bot_instances.get(user_id_chat)
    if not sid:
        return jsonify({"error": "Bot not online"}), 400
    socketio.emit("post_news_feed_command", {"content": content}, room=sid)
    return jsonify({"message": "Post command sent to bot"})


@app.route("/api/notifications/<user_id_chat>", methods=["GET"])
def get_notifications(user_id_chat):
    with db_connect() as conn:
        rows = conn.execute("""
            SELECT n.id,n.content,n.notification_type,n.timestamp,n.is_read
            FROM notifications n JOIN facebook_accounts fa ON n.facebook_account_id=fa.id
            WHERE fa.user_id_chat=? ORDER BY n.timestamp DESC
        """, (user_id_chat,)).fetchall()
    return jsonify({"notifications": [dict(r) | {"is_read": bool(r["is_read"])} for r in rows]})


@socketio.event
def connect():
    logger.info("Socket connected sid=%s", request.sid)
    emit("connected", {"success": True, "sid": request.sid, "message": "Connected to API Sender"})


@socketio.event
def disconnect():
    sid = request.sid
    logger.warning("Socket disconnected sid=%s", sid)
    session = bot_device_sessions.pop(sid, None)
    removed = []
    for uid, mapped_sid in list(bot_instances.items()):
        if mapped_sid == sid:
            removed.append(uid)
            bot_instances.pop(uid, None)
            bot_last_seen.pop(uid, None)
    if removed:
        socketio.emit("bot_status_update", {"online": False, "removed": removed, "bot_instances": list(bot_instances.keys())})


@socketio.event
def bot_register(data):
    session = register_bot_session(request.sid, data or {})
    join_room(f"bot:{session['user_id_chat']}")
    start_send_queue_for_bot(session["user_id_chat"])
    return {"success": True, "sid": request.sid, "online_bots": list(bot_instances.keys()), "session": session}


@socketio.event
def post_status_update(data):
    data = data or {}
    post_id = data.get("post_id")
    status = data.get("status")
    error = data.get("error_message") or data.get("error") or ""
    if post_id:
        with db_connect() as conn:
            conn.execute("UPDATE posts SET status=?, posted_at=CURRENT_TIMESTAMP WHERE id=?", (status, post_id))
            conn.commit()
    socketio.emit("crm_post_status", {"post_id": post_id, "status": status, "error_message": error})


@socketio.event
def send_message_result(data):
    data = data or {}
    message_id = safe_text(data.get("message_id"))
    client_message_id = safe_text(data.get("client_message_id"))
    conversation_id = safe_text(data.get("conversation_id"))
    success = bool(data.get("success"))
    error = safe_text(data.get("error"))
    collection = get_mongo_commands_collection()
    if collection is not None and (message_id or client_message_id):
        query = {"type": "send_message"}
        if client_message_id:
            query["client_message_id"] = client_message_id
        else:
            numeric_message_id = to_int_or_none(message_id)
            query["message_id"] = numeric_message_id if numeric_message_id is not None else message_id
        command = collection.find_one(query)
        if command:
            update_mongo_command_result(command, success, error)
    socketio.emit("crm_send_message_status", {
        "message_id": message_id,
        "client_message_id": client_message_id,
        "conversation_id": conversation_id,
        "success": success,
        "error": error,
    })
    return {"success": True}


@socketio.event
def get_current_account_command(data):
    sid = request.sid
    return {"success": True, "current_account": bot_device_sessions.get(sid, {}).get("user_id_chat"), "device_id": bot_device_sessions.get(sid, {}).get("device_id")}


def forward_receiver_events():
    try:
        import socketio as sio_client
        receiver = sio_client.Client(reconnection=True)

        @receiver.event
        def connect():
            logger.info("Connected to Receiver API")

        @receiver.event
        def bot_status_update(data):
            socketio.emit("bot_status_update", data)

        @receiver.event
        def crm_new_message(data):
            socketio.emit("crm_new_message", data)

        @receiver.event
        def crm_new_notification(data):
            socketio.emit("crm_new_notification", data)

        receiver.connect(RECEIVER_API_URL)
        receiver.wait()
    except Exception as exc:
        logger.warning("Cannot connect to Receiver API %s: %s", RECEIVER_API_URL, exc)


def is_port_available(host, port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, int(port)))
            return True
        except OSError:
            return False


if __name__ == "__main__":
    init_database()
    fail_stale_queued_messages()
    if not is_port_available("0.0.0.0", API_SENDER_PORT):
        logger.error("Port %s is already in use. Stop old API Sender first.", API_SENDER_PORT)
        raise SystemExit(1)
    logger.info("API Sender starting on port %s | DB=%s", API_SENDER_PORT, DATABASE_FILE)
    if os.getenv("FB_1_1_FORWARD_RECEIVER_EVENTS", "0") == "1":
        threading.Thread(target=forward_receiver_events, daemon=True).start()
    socketio.run(app, host="0.0.0.0", port=API_SENDER_PORT, debug=False, allow_unsafe_werkzeug=True)
