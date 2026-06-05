import argparse
import json
import os
import re
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import pymongo
from bson import ObjectId


DEVICE_FILE_RE = re.compile(r"^Zalo_data_login_path_(?P<device_id>.+)\.json$", re.IGNORECASE)
VALID_DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9]+$")
DEFAULT_DB_NAME = "Facebook"
DEFAULT_COLLECTION = "devices"


def load_mongo_uri(env_path: Path, account_type: str = "Base") -> str:
    with env_path.open("r", encoding="utf-8") as f:
        data = json.load(f).get(account_type, {})

    username = urllib.parse.quote_plus(str(data["username"]))
    password = urllib.parse.quote_plus(str(data["pwd"]))
    return f"mongodb://{username}:{password}@123.24.206.25:27017/?authSource=admin"


def normalize_status(value: Any) -> str:
    if isinstance(value, bool):
        return "Online" if value else "Offline"

    text = str(value or "").strip().lower()
    if text in {"online", "true", "1", "active", "on"}:
        return "Online"
    return "Offline"


def normalize_phone(value: Any) -> str:
    return str(value or "").strip()


def normalize_name(value: Any) -> str:
    return str(value or "").strip()


def normalize_device_id(value: Any) -> Optional[str]:
    device_id = str(value or "").strip()
    if not device_id or device_id.lower() in {"id", "id_device"}:
        return None
    if not VALID_DEVICE_ID_RE.match(device_id):
        return None
    return device_id


def iter_zalo_files(zalo_base: Path) -> Iterable[Path]:
    for path in sorted(zalo_base.glob("Zalo_data_login_path_*.json")):
        if DEVICE_FILE_RE.match(path.name):
            yield path


def device_id_from_path(path: Path) -> str:
    match = DEVICE_FILE_RE.match(path.name)
    if not match:
        raise ValueError(f"Invalid Zalo data filename: {path.name}")
    return match.group("device_id")


def load_json_entries(path: Path) -> List[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        raw = json.load(f)

    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]

    if isinstance(raw, dict):
        for key in ("accounts_zalo", "accounts", "data", "results"):
            value = raw.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return [raw]

    return []


def build_accounts_zalo(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    accounts: List[Dict[str, Any]] = []
    seen_phones = set()

    for entry in entries:
        phone = normalize_phone(
            entry.get("num_phone_zalo")
            or entry.get("account")
            or entry.get("phone")
            or entry.get("phone_number")
        )
        if not phone or phone in seen_phones:
            continue

        seen_phones.add(phone)
        accounts.append(
            {
                "_id": ObjectId(),
                "account": phone,
                "num_phone_zalo": phone,
                "name": normalize_name(entry.get("name")),
                "status": normalize_status(entry.get("status")),
            }
        )

    return accounts


def merge_with_existing_ids(
    existing_accounts: Optional[List[Dict[str, Any]]],
    source_accounts: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    existing_by_phone: Dict[str, Dict[str, Any]] = {}
    for account in existing_accounts or []:
        if not isinstance(account, dict):
            continue
        phone = normalize_phone(account.get("num_phone_zalo") or account.get("account"))
        if phone:
            existing_by_phone[phone] = account

    merged_accounts = []
    for source in source_accounts:
        phone = source["num_phone_zalo"]
        existing = existing_by_phone.get(phone, {})
        merged = dict(existing)
        merged["_id"] = existing.get("_id") or source["_id"]
        merged["account"] = phone
        merged["num_phone_zalo"] = phone
        merged["name"] = source["name"]
        merged["status"] = source["status"]
        merged_accounts.append(merged)

    return merged_accounts


def collect_zalo_accounts(zalo_base: Path, only_device: Optional[str] = None) -> Dict[str, List[Dict[str, Any]]]:
    raw_by_device: Dict[str, List[Dict[str, Any]]] = {}
    for path in iter_zalo_files(zalo_base):
        fallback_device_id = normalize_device_id(device_id_from_path(path))
        try:
            entries = load_json_entries(path)
        except json.JSONDecodeError as e:
            print(f"[SKIP] {path}: invalid JSON at line {e.lineno}, column {e.colno}: {e.msg}")
        except Exception as e:
            print(f"[SKIP] {path}: cannot parse file: {e}")
        else:
            for entry in entries:
                device_id = normalize_device_id(entry.get("id_device")) or fallback_device_id
                if not device_id:
                    print(f"[SKIP] {path}: cannot determine valid device_id")
                    continue
                if only_device and device_id != only_device:
                    continue
                raw_by_device.setdefault(device_id, []).append(entry)

    return {
        device_id: build_accounts_zalo(entries)
        for device_id, entries in sorted(raw_by_device.items())
    }


def print_parse_summary(accounts_by_device: Dict[str, List[Dict[str, Any]]]) -> None:
    total_accounts = sum(len(accounts) for accounts in accounts_by_device.values())
    print(f"Found {len(accounts_by_device)} Zalo devices, {total_accounts} Zalo accounts.")
    for device_id, accounts in accounts_by_device.items():
        online = sum(1 for account in accounts if account.get("status") == "Online")
        offline = len(accounts) - online
        print(f"- {device_id}: {len(accounts)} accounts ({online} Online, {offline} Offline)")


def format_device_label(device_id: str, device: Optional[Dict[str, Any]] = None) -> str:
    device_name = str((device or {}).get("device_name") or "").strip()
    if device_name:
        return f"{device_name} ({device_id})"
    return device_id


def sync_to_mongo(
    accounts_by_device: Dict[str, List[Dict[str, Any]]],
    mongo_uri: str,
    db_name: str,
    collection_name: str,
    apply: bool,
    create_missing: bool,
) -> None:
    client = pymongo.MongoClient(mongo_uri, serverSelectionTimeoutMS=10000)
    collection = client[db_name][collection_name]

    matched = 0
    modified = 0
    missing = 0
    created = 0
    now = datetime.now(timezone.utc)

    for device_id, source_accounts in accounts_by_device.items():
        device = collection.find_one(
            {"device_id": device_id},
            {"accounts_zalo": 1, "device_id": 1, "device_name": 1},
        )
        device_label = format_device_label(device_id, device)

        if not device:
            missing += 1
            if not create_missing:
                print(f"[SKIP] {device_label}: device document not found.")
                continue

            update_doc = {
                "device_id": device_id,
                "accounts_zalo": source_accounts,
                "created_at": now,
                "updated_at": now,
            }
            print(f"[CREATE]{' apply' if apply else ' dry-run'} {device_label}: {len(source_accounts)} accounts_zalo")
            if apply:
                collection.insert_one(update_doc)
                created += 1
            continue

        matched += 1
        merged_accounts = merge_with_existing_ids(device.get("accounts_zalo"), source_accounts)
        print(f"[UPDATE]{' apply' if apply else ' dry-run'} {device_label}: {len(merged_accounts)} accounts_zalo")

        if apply:
            result = collection.update_one(
                {"_id": device["_id"]},
                {
                    "$set": {
                        "accounts_zalo": merged_accounts,
                        "updated_at": now,
                    }
                },
            )
            modified += result.modified_count

    print(
        "Summary: "
        f"matched={matched}, modified={modified}, missing={missing}, created={created}, "
        f"mode={'apply' if apply else 'dry-run'}"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sync Zalo_base/Zalo_data_login_path_<device_id>.json into Mongo devices.accounts_zalo."
    )
    parser.add_argument("--zalo-base", default="Zalo_base", help="Folder containing Zalo_data_login_path_*.json")
    parser.add_argument("--env", default="DatabaseAccounts.env", help="Mongo credential JSON file")
    parser.add_argument("--mongo-type", default="Base", help="Top-level credential key in DatabaseAccounts.env")
    parser.add_argument("--db", default=os.getenv("MONGO_DB_NAME", DEFAULT_DB_NAME), help="Mongo database name")
    parser.add_argument("--collection", default=DEFAULT_COLLECTION, help="Mongo collection name")
    parser.add_argument("--only-device", help="Sync only one device_id")
    parser.add_argument("--parse-only", action="store_true", help="Only parse local files; do not connect to Mongo")
    parser.add_argument("--apply", action="store_true", help="Write changes to Mongo. Without this flag, dry-run only.")
    parser.add_argument("--create-missing", action="store_true", help="Create minimal device docs when device_id is missing")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    zalo_base = Path(args.zalo_base)
    env_path = Path(args.env)

    if not zalo_base.exists():
        raise FileNotFoundError(f"Zalo base folder not found: {zalo_base}")

    accounts_by_device = collect_zalo_accounts(zalo_base, only_device=args.only_device)
    print_parse_summary(accounts_by_device)

    if args.parse_only:
        return 0

    mongo_uri = load_mongo_uri(env_path, args.mongo_type)
    sync_to_mongo(
        accounts_by_device=accounts_by_device,
        mongo_uri=mongo_uri,
        db_name=args.db,
        collection_name=args.collection,
        apply=args.apply,
        create_missing=args.create_missing,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
