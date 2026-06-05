from __future__ import annotations

import json
import os
import pathlib
import shutil
import uuid
from datetime import datetime
from typing import Any

from bson import ObjectId
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from pymongo_management import get_async_collection

HERE = pathlib.Path(__file__).resolve().parent
UPLOAD_ROOT = pathlib.Path(
    os.getenv("MEDIA_UPLOAD_ROOT", str(HERE / "uploads"))
).resolve()
UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)

PUBLIC_MEDIA_BASE_URL = (os.getenv("PUBLIC_MEDIA_BASE_URL") or "").rstrip("/")
DEFAULT_DB_NAME = os.getenv("MEDIA_GATEWAY_DB_NAME", "Facebook")
COMMANDS_COLLECTION_NAME = os.getenv("MEDIA_GATEWAY_COMMANDS_COLLECTION", "Commands")
ALLOWED_COMMAND_TYPES = {"post_to_wall", "post_to_group"}

app = FastAPI(title="Shared Media Gateway", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/uploads", StaticFiles(directory=UPLOAD_ROOT), name="uploads")


class JsonEnqueueBody(BaseModel):
    type: str = Field(..., description="post_to_wall | post_to_group")
    user_id: str
    user_name: str | None = None
    content: str = ""
    files: list[str] = Field(default_factory=list)
    device_id: str | None = None
    device_ids: list[str] | None = None
    group_link: str | None = None
    crm_id: str | None = None
    oid: str | None = None
    delay: str | int | bool | None = "false"
    auto_join_if_needed: bool = False
    join_timeout_sec: int = 15


def _commands_collection():
    return get_async_collection(COMMANDS_COLLECTION_NAME, db_name=DEFAULT_DB_NAME)


def _public_root(request: Request) -> str:
    if PUBLIC_MEDIA_BASE_URL:
        return PUBLIC_MEDIA_BASE_URL
    return str(request.base_url).rstrip("/")


def _parse_string_list(raw: str | None) -> list[str]:
    if not raw:
        return []

    value = raw.strip()
    if not value:
        return []

    if value.startswith("["):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid JSON list: {exc}") from exc
        if not isinstance(parsed, list):
            raise HTTPException(status_code=400, detail="device_ids/existing_files must be a JSON list")
        return [str(item).strip() for item in parsed if str(item).strip()]

    return [item.strip() for item in value.replace("\n", ",").split(",") if item.strip()]


def _coerce_bool(raw: str | bool | None, *, default: bool = False) -> bool:
    if isinstance(raw, bool):
        return raw
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


def _normalize_media_ref(request: Request, ref: str) -> str:
    cleaned = str(ref or "").strip()
    if not cleaned:
        return ""

    lowered = cleaned.lower()
    if lowered.startswith("http://") or lowered.startswith("https://"):
        return cleaned

    root = _public_root(request)
    if cleaned.startswith("/uploads/"):
        return f"{root}{cleaned}"
    if cleaned.startswith("uploads/"):
        return f"{root}/{cleaned}"
    return f"{root}/uploads/{cleaned}"


def _merge_target_devices(device_id: str | None, device_ids: list[str] | None) -> list[str] | None:
    merged: list[str] = []
    if device_ids:
        merged.extend(str(item).strip() for item in device_ids if str(item).strip())
    if device_id and str(device_id).strip():
        merged.append(str(device_id).strip())

    deduped: list[str] = []
    for item in merged:
        if item not in deduped:
            deduped.append(item)

    return deduped or None


def _serialize_doc(doc: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in doc.items():
        if isinstance(value, ObjectId):
            out[key] = str(value)
        elif isinstance(value, datetime):
            out[key] = value.isoformat()
        elif isinstance(value, list):
            out[key] = [
                item.isoformat() if isinstance(item, datetime) else str(item) if isinstance(item, ObjectId) else item
                for item in value
            ]
        else:
            out[key] = value
    return out


async def _save_upload(request: Request, upload: UploadFile) -> dict[str, str]:
    ext = pathlib.Path(upload.filename or "").suffix or ".bin"
    stored_name = f"media-{uuid.uuid4()}{ext}"
    destination = UPLOAD_ROOT / stored_name

    with destination.open("wb") as output:
        shutil.copyfileobj(upload.file, output)

    return {
        "original_name": upload.filename or stored_name,
        "stored_name": stored_name,
        "url": f"{_public_root(request)}/uploads/{stored_name}",
    }


def _validate_command_input(command_type: str, user_id: str, content: str, media_refs: list[str], group_link: str | None) -> None:
    if command_type not in ALLOWED_COMMAND_TYPES:
        raise HTTPException(status_code=400, detail=f"Unsupported type: {command_type}")

    if not str(user_id or "").strip():
        raise HTTPException(status_code=400, detail="user_id is required")

    if command_type == "post_to_group" and not str(group_link or "").strip():
        raise HTTPException(status_code=400, detail="group_link is required for post_to_group")

    if not str(content or "").strip() and not media_refs:
        raise HTTPException(status_code=400, detail="content or files is required")


async def _insert_command(
    *,
    command_type: str,
    user_id: str,
    user_name: str | None,
    content: str,
    media_refs: list[str],
    target_device_ids: list[str] | None,
    group_link: str | None,
    crm_id: str | None,
    oid: str | None,
    delay: str | int | bool | None,
    auto_join_if_needed: bool,
    join_timeout_sec: int,
) -> dict[str, Any]:
    now = datetime.utcnow()
    params: dict[str, Any] = {
        "content": content,
        "files": media_refs,
    }
    if user_name:
        params["user_name"] = user_name
    if group_link:
        params["group_link"] = group_link
    if oid:
        params["oid"] = oid
    if command_type == "post_to_group":
        params["auto_join_if_needed"] = auto_join_if_needed
        params["join_timeout_sec"] = int(join_timeout_sec)

    doc: dict[str, Any] = {
        "type": command_type,
        "user_id": user_id,
        "user_name": user_name or "",
        "Status": "Chưa xử lý",
        "Created_at": now,
        "created_at": now,
        "Executed_at": None,
        "time": now,
        "delay": delay if delay is not None else "false",
        "retry_count": 0,
        "source": "media_gateway_api",
        "content": content,
        "files": media_refs,
        "group_link": group_link or None,
        "params": params,
    }
    if crm_id:
        doc["crm_id"] = crm_id
    if target_device_ids:
        doc["device_ids"] = target_device_ids
        if len(target_device_ids) == 1:
            doc["device_id"] = target_device_ids[0]

    result = await _commands_collection().insert_one(doc)
    doc["_id"] = result.inserted_id
    return doc


@app.get("/api/health")
async def healthcheck():
    return {
        "ok": True,
        "upload_root": str(UPLOAD_ROOT),
        "public_media_base_url": PUBLIC_MEDIA_BASE_URL or None,
        "db_name": DEFAULT_DB_NAME,
        "commands_collection": COMMANDS_COLLECTION_NAME,
    }


@app.post("/api/upload_media")
async def upload_media(request: Request, files: list[UploadFile] = File(...)):
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")

    items = [await _save_upload(request, upload) for upload in files]
    return {
        "ok": True,
        "files": [item["stored_name"] for item in items],
        "urls": [item["url"] for item in items],
        "items": items,
    }


@app.post("/api/enqueue_command")
async def enqueue_command(request: Request, body: JsonEnqueueBody):
    normalized_refs = [
        normalized
        for normalized in (_normalize_media_ref(request, ref) for ref in body.files)
        if normalized
    ]
    target_device_ids = _merge_target_devices(body.device_id, body.device_ids)
    _validate_command_input(body.type, body.user_id, body.content, normalized_refs, body.group_link)

    doc = await _insert_command(
        command_type=body.type,
        user_id=body.user_id.strip(),
        user_name=(body.user_name or "").strip() or None,
        content=body.content or "",
        media_refs=normalized_refs,
        target_device_ids=target_device_ids,
        group_link=(body.group_link or "").strip() or None,
        crm_id=(body.crm_id or "").strip() or None,
        oid=(body.oid or "").strip() or None,
        delay=body.delay,
        auto_join_if_needed=bool(body.auto_join_if_needed),
        join_timeout_sec=int(body.join_timeout_sec),
    )
    return {"ok": True, "command": _serialize_doc(doc)}


@app.post("/api/upload_and_enqueue")
async def upload_and_enqueue(
    request: Request,
    type: str = Form(...),
    user_id: str = Form(...),
    user_name: str | None = Form(None),
    content: str = Form(""),
    device_id: str | None = Form(None),
    device_ids: str | None = Form(None),
    group_link: str | None = Form(None),
    crm_id: str | None = Form(None),
    oid: str | None = Form(None),
    delay: str | None = Form("false"),
    auto_join_if_needed: str | None = Form("false"),
    join_timeout_sec: int = Form(15),
    existing_files: str | None = Form(None),
    files: list[UploadFile] | None = File(None),
):
    parsed_device_ids = _parse_string_list(device_ids)
    target_device_ids = _merge_target_devices(device_id, parsed_device_ids)

    normalized_existing_refs = [
        normalized
        for normalized in (_normalize_media_ref(request, ref) for ref in _parse_string_list(existing_files))
        if normalized
    ]
    uploaded_items = [await _save_upload(request, upload) for upload in (files or [])]
    media_refs = normalized_existing_refs + [item["url"] for item in uploaded_items]

    _validate_command_input(type, user_id, content, media_refs, group_link)

    doc = await _insert_command(
        command_type=type.strip(),
        user_id=user_id.strip(),
        user_name=(user_name or "").strip() or None,
        content=content or "",
        media_refs=media_refs,
        target_device_ids=target_device_ids,
        group_link=(group_link or "").strip() or None,
        crm_id=(crm_id or "").strip() or None,
        oid=(oid or "").strip() or None,
        delay=delay,
        auto_join_if_needed=_coerce_bool(auto_join_if_needed, default=False),
        join_timeout_sec=int(join_timeout_sec),
    )
    return {
        "ok": True,
        "uploaded": uploaded_items,
        "command": _serialize_doc(doc),
    }


@app.get("/api/jobs/{command_id}")
async def get_job(command_id: str):
    if not ObjectId.is_valid(command_id):
        raise HTTPException(status_code=400, detail="Invalid command_id")

    doc = await _commands_collection().find_one({"_id": ObjectId(command_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Command not found")
    return {"ok": True, "command": _serialize_doc(doc)}
