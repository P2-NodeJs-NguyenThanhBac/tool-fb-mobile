import json
import logging
import os
import urllib.parse
from pathlib import Path

import pymongo

from util.log import log_message

MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "Facebook")
_MONGO_CLIENT = None


def _get_mongo_uri(Type="Base"):
    env_path = Path(__file__).resolve().parents[1] / "DatabaseAccounts.env"
    with open(env_path, "r", encoding="utf-8") as file:
        data = json.load(file).get(Type, {})

    username = urllib.parse.quote_plus(data["username"])
    password = urllib.parse.quote_plus(data["pwd"])
    return f"mongodb://{username}:{password}@123.24.206.25:27017/?authSource=admin"


def _get_mongo_client():
    global _MONGO_CLIENT
    if _MONGO_CLIENT is None:
        _MONGO_CLIENT = pymongo.MongoClient(
            _get_mongo_uri(),
            serverSelectionTimeoutMS=10000,
        )
    return _MONGO_CLIENT


def _get_devices_collection():
    return _get_mongo_client()[MONGO_DB_NAME]["devices"]


def load_device_account(device_id):
    """Return one device document from Mongo collection `devices`."""
    device_id = str(device_id or "").strip()
    if not device_id:
        return {}

    try:
        return _get_devices_collection().find_one({"device_id": device_id}) or {}
    except Exception as e:
        log_message(f"Loi doc device {device_id} tu Mongo devices: {e}", logging.ERROR)
        return {}


def get_all_devices_from_mongo():
    """Return all device documents from Mongo collection `devices`."""
    try:
        devices = list(_get_devices_collection().find({}))
        log_message(f"Da lay {len(devices)} devices tu Mongo collection devices", logging.INFO)
        return devices
    except Exception as e:
        log_message(f"Loi doc danh sach devices tu Mongo: {e}", logging.ERROR)
        return []


def get_all_devices_from_api():
    """Compatibility wrapper. Existing callers now read Mongo, not the old API."""
    return get_all_devices_from_mongo()


def get_device():
    """Return device_id values from Mongo collection `devices`."""
    device_list = []
    for device in get_all_devices_from_mongo():
        device_id = str(device.get("device_id") or "").strip()
        if device_id:
            device_list.append(device_id)
    return device_list
