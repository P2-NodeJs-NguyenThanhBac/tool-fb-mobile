#!/usr/bin/env python3
"""
API Server để nhận tin nhắn và thông báo từ Facebook Bot
Chạy độc lập với CRM Frontend API
"""

import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent
_ORIGINAL_SYS_PATH = list(sys.path)
sys.path = [
    item
    for item in sys.path
    if item and Path(item).resolve() != _PROJECT_ROOT
]
try:
    from flask import Flask, request, jsonify
    from flask_socketio import SocketIO, emit
    from flask_cors import CORS
finally:
    sys.path = _ORIGINAL_SYS_PATH

import sqlite3
import logging
from datetime import datetime
import json
import unicodedata
import re
import threading
import time # Added import for time module
import requests
import pymongo
from pymongo.errors import DuplicateKeyError
import hashlib
import urllib.parse

# Cấu hình logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

FB_INBOUND_FALLBACK_URL = os.getenv('FB_1_1_INBOUND_FALLBACK_URL', '')
DATABASE_ACCOUNTS_FILE = os.getenv("DATABASE_ACCOUNTS_FILE", str(_PROJECT_ROOT / "DatabaseAccounts.env"))
MONGODB_DB_NAME = os.getenv("MONGODB_DB_NAME", "Facebook")
_MONGO_CLIENT = None

def local_now_iso():
    return datetime.now().astimezone().isoformat(timespec='seconds')

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

def get_mongo_usermessages_collection():
    global _MONGO_CLIENT
    url = get_mongodb_url()
    if not url:
        return None
    db_name = os.getenv("MONGODB_DB_NAME") or MONGODB_DB_NAME
    if _MONGO_CLIENT is None:
        _MONGO_CLIENT = pymongo.MongoClient(url, serverSelectionTimeoutMS=5000)
    return _MONGO_CLIENT[db_name]["usermessages"]

def normalize_sender_name(sender_name):
    text = unicodedata.normalize('NFKD', str(sender_name or '').strip().lower())
    return text.encode('ascii', 'ignore').decode('ascii')

def normalize_message_filter_text(value):
    text = unicodedata.normalize('NFKD', str(value or '').strip().lower())
    text = ''.join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r'\s+', ' ', text)

def parse_message_timestamp(value):
    if not value:
        return None

    raw = str(value).strip()
    if raw.endswith('Z'):
        raw = f'{raw[:-1]}+00:00'

    try:
        parsed = datetime.fromisoformat(raw)
        return parsed.astimezone().replace(tzinfo=None) if parsed.tzinfo else parsed
    except ValueError:
        pass

    for fmt in ('%Y-%m-%d %H:%M:%S', '%H:%M:%S %d/%m/%Y', '%H:%M:%S %#d/%#m/%Y'):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue

    return None

def is_self_sender(sender_name):
    return normalize_sender_name(sender_name) in {'toi', 'me'}

def is_ignorable_message_content(content):
    text = str(content or '').strip()
    if not text:
        return True

    lower_text = text.lower()
    normalized_text = normalize_message_filter_text(text)
    if any(fragment in normalized_text for fragment in (
        'chua xem', 'ban moi', 'tin chua doc', 'dang hoat dong', 'thong bao',
        'nhan dup de tao', 'trong so', 'active now', 'delivered', 'seen',
        'end-to-end encrypted', 'ma hoa dau cuoi', 'changed the emoji',
        'thay doi bieu tuong cam xuc', 'missed call', 'cuoc goi nho',
        'you replied to', 'replied to you', 'original message:', 'replying to',
        'tab 1/', 'tab 2/', 'tab 3/', 'tab 4/', 'tab 5/', 'tab 6/',
        'trang chu', 'nhom', 'cong dong', 'kho luu tru', 'loi moi ket ban',
        'facebook reels', 'su kien tren facebook', 'nhap bang giong noi',
        'phim', 'chi tiet chuoi bai', 'khoi phuc lich su chat',
        'de xem tin nhan bi thieu',
    )):
        return True
    system_fragments = [
        'end-to-end encryption',
        'end-to-end encrypted',
        'mã hóa đầu cuối',
        'only people in this chat',
        'chỉ những người trong đoạn chat',
        'learn more',
        'tìm hiểu thêm',
        'this message was deleted',
        'tin nhắn này đã bị xóa',
        'you unsent a message',
        'bạn đã thu hồi một tin nhắn',
        'missed call',
        'cuộc gọi nhỡ',
        'the call ended',
        'cuộc gọi đã kết thúc',
        'changed the chat theme',
        'đã đặt chủ đề',
        'changed the emoji',
        'thay đổi biểu tượng cảm xúc',
        'delivered',
        'seen',
        'đã gửi',
        'đã xem',
        'thông báo',
        'tin chưa đọc',
        'tin chua doc',
        'chưa xem',
        'chua xem',
        'bạn mới',
        'ban moi',
        'đang hoạt động',
        'active now',
        'unread message',
        'vừa xong',
        'nhấn đúp để tạo',
        'nhan dup de tao',
        'trong số',
        'trong so',
    ]
    reply_fragments = [
        'you replied to',
        'replied to you',
        'replied to your note',
        'replied to their note',
        'original message:',
        'replying to',
        'đã trả lời bạn',
        'đã trả lời ghi chú',
        'đã trả lời tin nhắn',
        'bạn đã trả lời',
        'đang trả lời',
    ]
    if any(fragment in lower_text for fragment in system_fragments + reply_fragments):
        return True

    system_patterns = [
        r'^active\s+(now|\d+\s*[mhdw]\s*ago|\d+\s+(minutes?|hours?|days?|weeks?)\s+ago)$',
        r'^sent\s+(\d+\s*[smhdw]\s+ago|now|just now)$',
        r'^delivered\s+(\d+\s*[smhdw]\s+ago|now|just now)?$',
        r'^seen\s+(\d+\s*[smhdw]\s+ago|now|just now)?$',
        r'^đã gửi\s+.*$',
        r'^đã xem\s+.*$',
        r'^đang hoạt động\s+.*$',
        r'^hoạt động\s+.*$',
        r'^tin chưa đọc\s*.*$',
        r'^chưa xem\s*.*$',
        r'^bạn mới\s*.*$',
        r'^thông báo\s*.*$',
        r'^\d+\s*trong số\s*\d+$',
        r'^\d+\s*(m|h|d|w|mo)$',
        r'^\d+:\d{2}\s*(am|pm)?$',
        r'^\d{1,2}/\d{1,2}/\d{2,4}$',
    ]
    normalized_patterns = [
        r'^active\s+(now|\d+\s*[mhdw]\s*ago|\d+\s+(minutes?|hours?|days?|weeks?)\s+ago)$',
        r'^\d+\s*trong so\s*\d+$',
        r'^\d+/\d+$',
        r'^tab\s+\d+/\d+$',
        r'^\d+\s*(m|h|d|w|mo)$',
        r'^\d{1,2}:\d{2}\s*(am|pm)?$',
        r'^\d{1,2}/\d{1,2}/\d{2,4}$',
    ]
    return (
        any(re.search(pattern, lower_text, re.IGNORECASE) for pattern in system_patterns)
        or any(re.search(pattern, normalized_text, re.IGNORECASE) for pattern in normalized_patterns)
    )


def is_ignorable_conversation_row(participant_name, sender_name, content):
    participant = normalize_sender_name(participant_name)
    sender = normalize_sender_name(sender_name)
    raw_participant = str(participant_name or '').strip().lower()
    raw_sender = str(sender_name or '').strip().lower()
    raw_content = str(content or '').strip().lower()
    normalized_participant = normalize_message_filter_text(participant_name)
    normalized_sender = normalize_message_filter_text(sender_name)

    blocked_exact = {
        'tat ca', 'anh', 'reels', 'vi tri', 'que quan', 'ngay sinh', 'tao',
        'all', 'photos', 'photo', 'create', 'location', 'hometown', 'birthday',
    }
    if participant in blocked_exact or sender in blocked_exact or normalized_participant in blocked_exact or normalized_sender in blocked_exact:
        return True
    if normalized_participant.startswith('tin cua ') or normalized_sender.startswith('tin cua '):
        return True
    if raw_participant.startswith(('tin của ', 'tin cua ')) or raw_sender.startswith(('tin của ', 'tin cua ')):
        return True
    if is_ignorable_message_content(raw_content):
        return True
    return False

def is_recent_crm_echo(cursor, conversation_id, content, timestamp, window_seconds=600):
    target_time = parse_message_timestamp(timestamp)
    cursor.execute('''
        SELECT timestamp FROM messages
        WHERE conversation_id = ? AND content = ? AND is_from_crm = 1
        ORDER BY id DESC
        LIMIT 10
    ''', (conversation_id, content))

    for row in cursor.fetchall():
        existing_time = parse_message_timestamp(row[0])
        if not target_time or not existing_time:
            return True
        if abs((target_time - existing_time).total_seconds()) <= window_seconds:
            return True

    return False

def extract_message_thread_id(url):
    value = str(url or '').strip()
    match = re.search(r'/messages/t/([^/?#]+)', value)
    if match:
        return match.group(1)
    if value and not value.startswith(('http://', 'https://')):
        return value
    return None

def get_message_thread_id(message_data):
    for key in ('threadId', 'thread_id', 'chat_id', 'recipientId', 'facebookId', 'participant_url', 'conversation_url'):
        thread_id = extract_message_thread_id(message_data.get(key))
        if thread_id:
            return thread_id
    return None

def extract_mobile_visible_account_key(*values):
    for value in values:
        text = str(value or '').strip()
        match = re.match(r'^([^:]+):mobile_visible:', text)
        if match:
            return match.group(1)
    return ''

def conversation_belongs_to_account(user_id_chat, *conversation_refs):
    owner_key = extract_mobile_visible_account_key(*conversation_refs)
    return not owner_key or owner_key == str(user_id_chat or '').strip()

def build_fb_inbound_payload(user_id_chat, message):
    thread_id = get_message_thread_id(message)
    timestamp = message.get('timestamp')
    try:
        timestamp_ms = int((parse_message_timestamp(timestamp) or datetime.now()).timestamp() * 1000)
    except Exception:
        timestamp_ms = int(time.time() * 1000)
    return {
        'content': message.get('content') or '',
        'message': message.get('content') or '',
        'facebookId': thread_id or message.get('participant_url') or message.get('participant_name'),
        'sender': message.get('sender_name') or message.get('participant_name'),
        'fbName': message.get('participant_name') or message.get('sender_name'),
        'sender_name': message.get('sender_name') or message.get('participant_name'),
        'userId': user_id_chat,
        'facebookAccountId': user_id_chat,
        'accountKey': user_id_chat,
        'threadId': thread_id,
        'messageId': thread_id,
        'conversation_id': message.get('conversation_id'),
        'crm_message_id': message.get('id') or message.get('crm_message_id'),
        'is_from_crm': False,
        'participant_url': message.get('participant_url'),
        'conversation_url': message.get('conversation_url'),
        'timestamp': timestamp,
        'createdAt': timestamp_ms,
        'updatedAt': timestamp_ms,
        'message_fingerprint': message.get('message_fingerprint'),
        'source': message.get('source') or 'tool-fb-mobile',
        'previous_message_content': message.get('previous_message_content'),
        'previous_message_is_from_crm': bool(message.get('previous_message_is_from_crm')),
        'next_message_content': message.get('next_message_content'),
        'next_message_is_from_crm': bool(message.get('next_message_is_from_crm')),
    }

def build_inbound_message_fingerprint(user_id_chat, conversation_ref, sender_name, content, timestamp=None, source=None):
    parts = [
        str(user_id_chat or '').strip(),
        str(conversation_ref or '').strip(),
        normalize_message_filter_text(content),
    ]
    raw = "\x1f".join(parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()

def build_stable_inbound_crm_message_id(user_id_chat, conversation_ref, sender_name, content, timestamp=None, source=None):
    return f"in_{build_inbound_message_fingerprint(user_id_chat, conversation_ref, sender_name, content, timestamp, source)[:20]}"

def build_inbound_dedupe_key(user_id_chat, conversation_ref, content, previous_message_content=None, previous_message_is_from_crm=False):
    parts = [
        str(user_id_chat or '').strip(),
        str(conversation_ref or '').strip(),
        normalize_message_filter_text(content),
        normalize_message_filter_text(previous_message_content),
        'prev_crm' if previous_message_is_from_crm else 'prev_customer',
    ]
    raw = "\x1f".join(parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()

def find_existing_inbound_usermessage_by_content(collection, user_id, conversation_id, sender_name, last_message, inbound_dedupe_key=None):
    if collection is None or not user_id or not conversation_id or not last_message:
        return None

    target_content = normalize_message_filter_text(last_message)
    if not target_content:
        return None

    try:
        if inbound_dedupe_key:
            existing = collection.find_one(
                {
                    "userId": user_id,
                    "conversation_id": conversation_id,
                    "is_from_crm": False,
                    "inbound_dedupe_key": inbound_dedupe_key,
                },
                {"_id": 1},
            )
            if existing:
                return existing

        candidates = collection.find(
            {
                "userId": user_id,
                "conversation_id": conversation_id,
                "is_from_crm": False,
            },
            {
                "_id": 1,
                "sender_name": 1,
                "sender": 1,
                "lastMessage": 1,
                "previous_message_content": 1,
                "previous_message_is_from_crm": 1,
            },
        ).sort("updatedAt", -1).limit(100)
        for candidate in candidates:
            candidate_content = normalize_message_filter_text(candidate.get("lastMessage"))
            candidate_dedupe_key = candidate.get("inbound_dedupe_key") or build_inbound_dedupe_key(
                user_id,
                conversation_id,
                candidate.get("lastMessage"),
                candidate.get("previous_message_content"),
                bool(candidate.get("previous_message_is_from_crm")),
            )
            if candidate_content == target_content and (not inbound_dedupe_key or candidate_dedupe_key == inbound_dedupe_key):
                return candidate
    except Exception as exc:
        logger.warning("Cannot check existing inbound usermessage duplicate: %s", exc)
    return None

def upsert_inbound_message_to_usermessages(user_id_chat, message):
    payload = build_fb_inbound_payload(user_id_chat, message)
    crm_message_id = payload.get('crm_message_id')
    if not crm_message_id:
        return False

    facebook_id = str(payload.get('facebookId') or payload.get('messageId') or '').strip()
    user_id = str(payload.get('userId') or user_id_chat or '').strip()
    if user_id and facebook_id and not facebook_id.startswith(user_id + ":"):
        facebook_id = f"{user_id}:{facebook_id}"
    elif not user_id and facebook_id:
        user_id = facebook_id.split(":", 1)[0]

    conversation_id = payload.get('conversation_id')
    last_message = payload.get('content') or payload.get('message') or ''
    sender_name = payload.get('sender_name') or payload.get('sender')
    timestamp = payload.get('timestamp') or local_now_iso()
    message_fingerprint = payload.get('message_fingerprint') or build_inbound_message_fingerprint(
        user_id, conversation_id or facebook_id, sender_name, last_message, payload.get('timestamp'), payload.get('source')
    )
    inbound_dedupe_key = build_inbound_dedupe_key(
        user_id,
        conversation_id or facebook_id,
        last_message,
        payload.get('previous_message_content'),
        bool(payload.get('previous_message_is_from_crm')),
    )
    deterministic_id = f"inbound:{message_fingerprint}"

    doc = {
        "messageId": facebook_id,
        "userId": user_id,
        "facebookId": facebook_id,
        "sender": payload.get('fbName') or payload.get('sender') or facebook_id,
        "lastMessage": last_message,
        "conversation_id": conversation_id,
        "crm_message_id": crm_message_id,
        "message_fingerprint": message_fingerprint,
        "inbound_dedupe_key": inbound_dedupe_key,
        "sender_name": sender_name,
        "is_from_crm": False,
        "source": payload.get('source'),
        "timestamp": timestamp,
        "is_read": False,
        "send_status": "received",
        "send_error": "",
        "createdAt": payload.get('createdAt') or int(time.time() * 1000),
        "updatedAt": payload.get('updatedAt') or int(time.time() * 1000),
        "previous_message_content": payload.get('previous_message_content') or "",
        "previous_message_is_from_crm": bool(payload.get('previous_message_is_from_crm')),
        "next_message_content": payload.get('next_message_content') or "",
        "next_message_is_from_crm": bool(payload.get('next_message_is_from_crm')),
        "__v": 0,
    }

    try:
        collection = get_mongo_usermessages_collection()
        if collection is None:
            logger.warning("Cannot upsert inbound message to usermessages: MongoDB URL not configured")
            return False
        existing_by_content = find_existing_inbound_usermessage_by_content(
            collection,
            user_id,
            conversation_id,
            sender_name,
            last_message,
            inbound_dedupe_key,
        )
        query_terms = [
            {"_id": deterministic_id},
            {"message_fingerprint": message_fingerprint},
            {"inbound_dedupe_key": inbound_dedupe_key},
            {"crm_message_id": crm_message_id},
        ]
        if str(payload.get('source') or '').strip() != 'conversation_detail':
            query_terms.append({
                "userId": user_id,
                "conversation_id": conversation_id,
                "sender_name": sender_name,
                "lastMessage": last_message,
                "is_from_crm": False,
            })
        query = {"_id": existing_by_content["_id"]} if existing_by_content else {"$or": query_terms}
        update = {
            "$set": doc,
            "$setOnInsert": {"_id": existing_by_content["_id"] if existing_by_content else deterministic_id},
        }
        try:
            collection.update_one(query, update, upsert=True)
        except DuplicateKeyError:
            collection.update_one({"_id": deterministic_id}, {"$set": doc}, upsert=False)
        return True
    except Exception as exc:
        logger.error("Cannot upsert inbound message to usermessages crm_message_id=%s: %s", crm_message_id, exc)
        return False

def post_fb_inbound_message_fallback(payload):
    fallback_url = str(FB_INBOUND_FALLBACK_URL or '').strip()
    if not fallback_url:
        return False

    response = requests.post(fallback_url, json=payload, timeout=10)
    response.raise_for_status()
    return True

def process_inbound_messages_mongo_only(user_id_chat, messages):
    processed_messages = []
    for message_data in dedupe_incoming_messages(messages or []):
        message_source = str(message_data.get('source') or '').strip()
        if message_source in {'messenger_home', 'mobile_visible', 'window_dump', 'window_dump_button'}:
            logger.info(
                "Skip preview/list inbound row source=%s participant=%s content=%s",
                message_source,
                message_data.get('participant_name'),
                message_data.get('content'),
            )
            continue
        participant_name = message_data.get('participant_name')
        thread_id = get_message_thread_id(message_data)
        participant_url = message_data.get('participant_url') or thread_id
        conversation_url = message_data.get('conversation_url') or participant_url
        content = message_data.get('content')
        sender_name = message_data.get('sender_name') or participant_name
        raw_timestamp = message_data.get('timestamp')
        timestamp = raw_timestamp or local_now_iso()
        if not participant_url:
            logger.warning("Skip inbound message without thread id or participant key: %s", message_data)
            continue
        if not conversation_belongs_to_account(user_id_chat, participant_url, conversation_url):
            logger.warning(
                "Skip inbound message with mismatched account: user_id_chat=%s participant_url=%s conversation_url=%s",
                user_id_chat, participant_url, conversation_url
            )
            continue
        if is_ignorable_conversation_row(participant_name, sender_name, content) or is_ignorable_message_content(content):
            continue
        crm_message_id = message_data.get('id') or message_data.get('crm_message_id')
        message_fingerprint = build_inbound_message_fingerprint(
            user_id_chat,
            message_data.get('conversation_id') or thread_id or participant_url,
            sender_name,
            content,
            raw_timestamp,
            message_data.get('source'),
        )
        if not crm_message_id:
            crm_message_id = build_stable_inbound_crm_message_id(
                user_id_chat,
                message_data.get('conversation_id') or thread_id or participant_url,
                sender_name,
                content,
                raw_timestamp,
                message_data.get('source'),
            )
        processed = {
            'id': crm_message_id,
            'conversation_id': message_data.get('conversation_id') or thread_id or participant_url,
            'participant_name': participant_name,
            'participant_url': participant_url,
            'conversation_url': conversation_url,
            'content': content,
            'sender_name': sender_name,
            'timestamp': timestamp,
            'message_fingerprint': message_fingerprint,
            'source': message_data.get('source'),
            'previous_message_content': message_data.get('previous_message_content'),
            'previous_message_is_from_crm': message_data.get('previous_message_is_from_crm'),
            'next_message_content': message_data.get('next_message_content'),
            'next_message_is_from_crm': message_data.get('next_message_is_from_crm'),
        }
        if not upsert_inbound_message_to_usermessages(user_id_chat, processed):
            logger.error(
                "Inbound message was processed but not saved to usermessages: user_id_chat=%s sender=%s content=%s source=%s",
                user_id_chat,
                sender_name,
                content,
                message_data.get('source'),
            )
            continue
        payload = build_fb_inbound_payload(user_id_chat, processed)
        try:
            post_fb_inbound_message_fallback(payload)
        except Exception as exc:
            logger.error("Failed to post inbound message to backend fallback: %s", exc)
        processed_messages.append(processed)
    if processed_messages:
        socketio.emit('crm_new_message', {'user_id_chat': user_id_chat, 'messages': processed_messages})
    return processed_messages

def normalized_message_content(value):
    return normalize_message_filter_text(value)

def normalized_message_sender(value):
    return normalize_sender_name(value)

def is_duplicate_incoming_message(existing_message, new_message, window_seconds=600):
    existing_content = normalized_message_content(existing_message.get('content'))
    new_content = normalized_message_content(new_message.get('content'))
    if not existing_content or existing_content != new_content:
        return False

    existing_sender = normalized_message_sender(existing_message.get('sender_name') or existing_message.get('sender'))
    new_sender = normalized_message_sender(new_message.get('sender_name') or new_message.get('sender'))
    if existing_sender != new_sender:
        return False

    existing_time = existing_message.get('timestamp') or existing_message.get('time')
    new_time = new_message.get('timestamp') or new_message.get('time')
    if not existing_time or not new_time:
        return True

    parsed_existing = parse_message_timestamp(existing_time)
    parsed_new = parse_message_timestamp(new_time)
    if not parsed_existing or not parsed_new:
        return existing_time == new_time

    return abs((parsed_new - parsed_existing).total_seconds()) <= window_seconds

def should_replace_incoming_message(existing_message, new_message):
    existing_sender = existing_message.get('sender_name') or existing_message.get('sender')
    new_sender = new_message.get('sender_name') or new_message.get('sender')
    existing_time = existing_message.get('timestamp') or existing_message.get('time')
    new_time = new_message.get('timestamp') or new_message.get('time')

    if is_self_sender(existing_sender) and not is_self_sender(new_sender):
        return True
    if not existing_time and new_time:
        return True

    return False

def dedupe_incoming_messages(messages):
    deduped = []
    for message in messages:
        duplicate_index = next((
            index for index, existing in enumerate(deduped)
            if is_duplicate_incoming_message(existing, message)
        ), None)

        if duplicate_index is None:
            deduped.append(message)
            continue

        if should_replace_incoming_message(deduped[duplicate_index], message):
            deduped[duplicate_index] = message

    return deduped

def is_existing_duplicate_message(cursor, conversation_id, content, timestamp, sender_name='', window_seconds=600):
    normalized_content = normalized_message_content(content)
    normalized_sender = normalized_message_sender(sender_name)
    if normalized_content and normalized_sender:
        cursor.execute('''
            SELECT 1 FROM messages
            WHERE conversation_id = ?
              AND is_from_crm = 0
              AND lower(trim(content)) = lower(trim(?))
              AND lower(trim(sender_name)) = lower(trim(?))
            LIMIT 1
        ''', (conversation_id, content, sender_name))
        if cursor.fetchone():
            return True

    cursor.execute('''
        SELECT timestamp, sender_name FROM messages
        WHERE conversation_id = ? AND content = ? AND is_from_crm = 0
        ORDER BY id DESC
        LIMIT 20
    ''', (conversation_id, content))

    target_time = parse_message_timestamp(timestamp)
    for row in cursor.fetchall():
        if normalized_sender and normalized_message_sender(row[1]) != normalized_sender:
            continue
        existing_time = parse_message_timestamp(row[0])
        if not target_time or not existing_time:
            return True
        if abs((target_time - existing_time).total_seconds()) <= window_seconds:
            return True

    return False

app = Flask(__name__)
app.config['SECRET_KEY'] = 'receiver-secret-key'
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*", port=8023)

# Khởi tạo database
def init_database():
    conn = sqlite3.connect('crm_facebook.db')
    cursor = conn.cursor()
    
    # Bảng tài khoản Facebook
    cursor.execute('''
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
    ''')
    
    # Bảng cuộc hội thoại
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            facebook_account_id INTEGER,
            participant_name TEXT NOT NULL,
            participant_url TEXT NOT NULL,
            conversation_url TEXT NOT NULL,
            unread_count INTEGER DEFAULT 0,
            last_message_timestamp TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (facebook_account_id) REFERENCES facebook_accounts (id)
        )
    ''')
    
    # Bảng tin nhắn
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER,
            sender_name TEXT NOT NULL,
            content TEXT NOT NULL,
            is_from_crm BOOLEAN DEFAULT 0,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_read BOOLEAN DEFAULT 0,
            FOREIGN KEY (conversation_id) REFERENCES conversations (id)
        )
    ''')
    
    # Bảng thông báo
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            facebook_account_id INTEGER,
            content TEXT NOT NULL,
            notification_type TEXT DEFAULT 'notification',
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_read BOOLEAN DEFAULT 0,
            FOREIGN KEY (facebook_account_id) REFERENCES facebook_accounts (id)
        )
    ''')
    
    # Bảng hàng đợi gửi tin nhắn inbound (tới cổng 3000)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS inbound_send_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id_chat TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            status TEXT DEFAULT 'queued',
            attempts INTEGER DEFAULT 0,
            error TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    conn.commit()
    conn.close()

# Lưu trữ thông tin bot instances
bot_instances = {}

def ensure_facebook_account(cursor, user_id_chat, username=None):
    display_name = username or f"Account_{user_id_chat}"

    cursor.execute('''
        INSERT OR IGNORE INTO facebook_accounts (user_id_chat, username, encrypted_password, last_online)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
    ''', (user_id_chat, display_name, "bot_registered"))

    cursor.execute('''
        UPDATE facebook_accounts
        SET username = COALESCE(?, username), last_online = CURRENT_TIMESTAMP
        WHERE user_id_chat = ?
    ''', (username, user_id_chat))

    cursor.execute('''
        -- Ensure facebook_accounts table has an id column
        SELECT id FROM facebook_accounts WHERE user_id_chat = ?
    ''', (user_id_chat,))
    return cursor.fetchone()[0]

# REST API Endpoints cho việc nhận dữ liệu
@app.route('/api/bot/register', methods=['POST'])
def register_bot():
    """Bot đăng ký với hệ thống"""
    data = request.json
    user_id_chat = data.get('user_id_chat')
    username = data.get('username')
    
    if not user_id_chat or not username:
        return jsonify({'error': 'Missing required fields'}), 400
    
    
    # Lưu hoặc cập nhật thông tin bot
    conn = sqlite3.connect('crm_facebook.db')
    cursor = conn.cursor()
    
    ensure_facebook_account(cursor, user_id_chat, username)
    
    conn.commit()
    conn.close()
    
    # Thông báo cho CRM Frontend
    socketio.emit('bot_status_update', {
        'user_id_chat': user_id_chat,
        'status': 'online',
        'username': username
    })
    
    return jsonify({'message': 'Bot registered successfully'})

def ensure_inbound_send_queue_table(cursor):
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS inbound_send_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id_chat TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            status TEXT DEFAULT 'queued',
            attempts INTEGER DEFAULT 0,
            error TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

def enqueue_inbound_message_for_send(user_id_chat, payload):
    conn = sqlite3.connect('crm_facebook.db')
    cursor = conn.cursor()
    ensure_inbound_send_queue_table(cursor)
    cursor.execute('''
        INSERT INTO inbound_send_queue (user_id_chat, payload_json, status, attempts)
        VALUES (?, ?, 'queued', 0)
    ''', (user_id_chat, json.dumps(payload, ensure_ascii=False)))
    conn.commit()
    conn.close()
    logger.info("Enqueued inbound message for user %s to send to port 3000", user_id_chat)

def claim_next_inbound_message_for_send():
    conn = sqlite3.connect('crm_facebook.db')
    cursor = conn.cursor()
    ensure_inbound_send_queue_table(cursor)
    cursor.execute('''
        SELECT id, user_id_chat, payload_json, attempts
        FROM inbound_send_queue
        WHERE status = 'queued' AND attempts < 3
        ORDER BY created_at ASC
        LIMIT 1
    ''')
    row = cursor.fetchone()
    conn.close()
    return row

@app.route('/api/bot/new_messages', methods=['POST'])
def receive_new_messages():
    """Nhận tin nhắn mới từ Facebook Bot"""
    data = request.json
    user_id_chat = data.get('user_id_chat')
    messages = dedupe_incoming_messages(data.get('messages', []))
    
    if not user_id_chat or not messages:
        return jsonify({'error': 'Missing required fields'}), 400
    processed_messages = process_inbound_messages_mongo_only(user_id_chat, messages)
    return jsonify({'message': f'Processed {len(processed_messages)} messages', 'messages': processed_messages})

# Legacy sqlite inbound handling removed. MongoDB `usermessages` is the single source of truth.
# The function `process_inbound_messages_mongo_only` performs upsert into the `usermessages` collection
# and emits `crm_new_message` to the frontend.


    socketio.emit('crm_new_message', {
        'user_id_chat': user_id_chat,
        'messages': processed_messages
    })
    
    return jsonify({'message': f'Processed {len(processed_messages)} messages'})

@app.route('/api/bot/new_notifications', methods=['POST'])
def receive_new_notifications():
    """Nhận thông báo mới từ Facebook Bot"""
    data = request.json
    user_id_chat = data.get('user_id_chat')
    notifications = data.get('notifications', [])
    
    if not user_id_chat or not notifications:
        return jsonify({'error': 'Missing required fields'}), 400
    
    conn = sqlite3.connect('crm_facebook.db')
    cursor = conn.cursor()
    
    processed_notifications = []
    
    for notification_data in notifications:
        content = notification_data.get('content')
        notification_type = notification_data.get('type', 'notification')
        timestamp = notification_data.get('timestamp', datetime.now().isoformat())
        
        # Lưu thông báo vào database
        cursor.execute('''
            INSERT INTO notifications (facebook_account_id, content, notification_type, timestamp)
            VALUES ((SELECT id FROM facebook_accounts WHERE user_id_chat = ?), ?, ?, ?)
        ''', (user_id_chat, content, notification_type, timestamp))
        
        notification_id = cursor.lastrowid
        processed_notifications.append({
            'id': notification_id,
            'content': content,
            'notification_type': notification_type,
            'timestamp': timestamp
        })
    
    conn.commit()
    conn.close()
    
    # Thông báo cho CRM Frontend
    socketio.emit('crm_new_notification', {
        'user_id_chat': user_id_chat,
        'notifications': processed_notifications
    })
    
    return jsonify({'message': f'Processed {len(processed_notifications)} notifications'})

@app.route('/api/bot/status_update', methods=['POST'])
def update_bot_status():
    """Cập nhật trạng thái bot"""
    data = request.json
    user_id_chat = data.get('user_id_chat')
    status = data.get('status')  # 'online' hoặc 'offline'
    
    if not user_id_chat or not status:
        return jsonify({'error': 'Missing required fields'}), 400
    
    conn = sqlite3.connect('crm_facebook.db')
    cursor = conn.cursor()
    
    if status == 'online':
        cursor.execute('''
            UPDATE facebook_accounts SET last_online = CURRENT_TIMESTAMP
            WHERE user_id_chat = ?
        ''', (user_id_chat,))
    else:
        cursor.execute('''
            UPDATE facebook_accounts SET last_online = NULL
            WHERE user_id_chat = ?
        ''', (user_id_chat,))
    
    conn.commit()
    conn.close()
    
    # Thông báo cho CRM Frontend
    socketio.emit('bot_status_update', {
        'user_id_chat': user_id_chat,
        'status': status
    })
    
    return jsonify({'message': f'Bot status updated to {status}'})

# Socket.IO Events
@socketio.event
def connect():
    """Client kết nối"""
    logger.info(f"Client connected: {request.sid}")

@socketio.event
def disconnect():
    """Client ngắt kết nối"""
    logger.info(f"Client disconnected: {request.sid}")

@socketio.event
def bot_register(data):
    """Bot đăng ký qua Socket.IO"""
    user_id_chat = data.get('user_id_chat')
    username = data.get('username')
    
    if user_id_chat and username:
        bot_instances[user_id_chat] = request.sid
        socketio.emit('bot_status_update', {
            'user_id_chat': user_id_chat,
            'status': 'online',
            'username': username
        })
        logger.info(f"Bot registered via Socket.IO: {user_id_chat}")

@socketio.event
def new_messages(data):
    """Nhận tin nhắn mới từ bot qua Socket.IO"""
    user_id_chat = data.get('user_id_chat')
    messages = dedupe_incoming_messages(data.get('messages', []))
    
    if not user_id_chat or not messages:
        return
    process_inbound_messages_mongo_only(user_id_chat, messages)
    return

    # Legacy sqlite inbound handling removed here as well. Using MongoDB `usermessages` only.
    # `process_inbound_messages_mongo_only` already performs persistence and emits CRM events.

def finish_inbound_message_send(queue_id, success, error=''):
    conn = sqlite3.connect('crm_facebook.db')
    cursor = conn.cursor()
    ensure_inbound_send_queue_table(cursor)
    if success:
        cursor.execute('''
            DELETE FROM inbound_send_queue
            WHERE id = ?
        ''', (queue_id,))
        logger.info("Successfully sent inbound message from queue (id=%s)", queue_id)
    else:
        cursor.execute('''
            UPDATE inbound_send_queue
            SET status = 'queued', error = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (error or '', queue_id))
        logger.warning("Failed to send inbound message from queue (id=%s), retrying. Error: %s", queue_id, error)
    conn.commit()
    conn.close()

def process_inbound_send_queue():
    logger.info("Starting inbound message send queue worker...")
    while True:
        conn = sqlite3.connect('crm_facebook.db')
        cursor = conn.cursor()
        ensure_inbound_send_queue_table(cursor)
        cursor.execute('''
            UPDATE inbound_send_queue
            SET status = 'failed', error = COALESCE(NULLIF(error, ''), 'Max send attempts reached'), updated_at = CURRENT_TIMESTAMP
            WHERE status = 'queued' AND attempts >= 3
        ''')
        conn.commit()
        conn.close()

        row = claim_next_inbound_message_for_send()
        if row:
            queue_id, user_id_chat, payload_json, attempts = row
            payload = json.loads(payload_json)
            success, error = _post_fb_inbound_message_fallback_actual(payload)
            finish_inbound_message_send(queue_id, success, error)
        time.sleep(1) # Check queue every second

def _post_fb_inbound_message_fallback_actual(payload):
    """Actual HTTP POST logic for inbound messages."""
    fallback_url = str(FB_INBOUND_FALLBACK_URL or '').strip()
    if not fallback_url:
        logger.warning("FB_INBOUND_FALLBACK_URL is not configured, cannot send inbound message.")
        return False, "FB_INBOUND_FALLBACK_URL not configured"

    try:
        response = requests.post(fallback_url, json=payload, timeout=10)
        response.raise_for_status()
        logger.info("Successfully posted inbound message to backend: user_id_chat=%s", payload.get('user_id_chat'))
        return True, ""
    except requests.exceptions.RequestException as exc:
        error_msg = f"Failed to post inbound message to backend: {exc}"
        logger.error(error_msg)
        return False, error_msg
    except Exception as exc:
        error_msg = f"Unexpected error when posting inbound message: {exc}"
        logger.error(error_msg)
        return False, error_msg

@socketio.event
def new_notifications(data):
    """Nhận thông báo mới từ bot qua Socket.IO"""
    user_id_chat = data.get('user_id_chat')
    notifications = data.get('notifications', [])
    
    conn = sqlite3.connect('crm_facebook.db')
    cursor = conn.cursor()
    
    for notification_data in notifications:
        content = notification_data.get('content')
        notification_type = notification_data.get('type', 'notification')
        timestamp = notification_data.get('timestamp')
        
        cursor.execute('''
            INSERT INTO notifications (facebook_account_id, content, notification_type, timestamp)
            VALUES ((SELECT id FROM facebook_accounts WHERE user_id_chat = ?), ?, ?, ?)
        ''', (user_id_chat, content, notification_type, timestamp))
    
    conn.commit()
    conn.close()
    
    # Gửi thông báo mới tới Frontend
    socketio.emit('crm_new_notification', {
        'user_id_chat': user_id_chat,
        'notifications': notifications
    })

if __name__ == '__main__':
    print("API Receiver Server is starting...")
    print("Port: 8023")
    print("Role: receive messages and notifications from Facebook Bot")
    logger.info("Mongo-only inbound receiver started.")
    socketio.run(app, host='0.0.0.0', port=8023, debug=False, use_reloader=False, allow_unsafe_werkzeug=True)
