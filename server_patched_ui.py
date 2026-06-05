from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from typing import List, Optional, Tuple, Dict, Any, Union
import uuid, datetime, os, pathlib, shutil, subprocess, json, re
from util.get_device_info import get_all_devices_from_mongo

# UPLOAD_ROOT = pathlib.Path("./uploads")
# UPLOAD_ROOT.mkdir(exist_ok=True)
HERE = pathlib.Path(__file__).resolve().parent
UPLOAD_ROOT = HERE / "uploads"
UPLOAD_ROOT.mkdir(exist_ok=True)

print("CWD =", pathlib.Path.cwd())
print("UPLOAD_ROOT =", UPLOAD_ROOT.resolve())

app = FastAPI()
JOBS: Dict[str, Dict[str, Any]] = {}  # in-memory {id: {...}}

# serve uploaded media for devices/tools to download
app.mount("/uploads", StaticFiles(directory=UPLOAD_ROOT), name="uploads")

HERE = pathlib.Path(__file__).resolve().parent
ADB_CANDIDATES = [
    os.environ.get("ADB_PATH"),  # explicit env var
    str(HERE / "platform-tools" / ("adb.exe" if os.name == "nt" else "adb")),  # bundled adb
    "adb.exe" if os.name == "nt" else "adb",  # system PATH
]

# ===== User -> Device map (optional) =====
USER_DEVICE_MAP_REFRESH_SECONDS = 30.0
_DEVICE_MAP_CACHE: Dict[str, str] = {}
_DEVICE_MAP_REFRESHED_AT = 0.0


def _load_user_device_map() -> dict:
    """Load map user_id/email -> ADB serial from Mongo devices."""
    global _DEVICE_MAP_CACHE, _DEVICE_MAP_REFRESHED_AT
    now_ts = datetime.datetime.now().timestamp()
    if _DEVICE_MAP_CACHE and now_ts - _DEVICE_MAP_REFRESHED_AT < USER_DEVICE_MAP_REFRESH_SECONDS:
        return _DEVICE_MAP_CACHE

    try:
        result: Dict[str, str] = {}
        for device in get_all_devices_from_mongo():
            device_id = str((device or {}).get("device_id") or "").strip()
            if not device_id:
                continue
            accounts = (device or {}).get("accounts") or []
            if not isinstance(accounts, list):
                continue
            for account in accounts:
                if not isinstance(account, dict):
                    continue
                user_key = str(account.get("account") or "").strip()
                if user_key:
                    result[user_key] = device_id
        _DEVICE_MAP_CACHE = result
        _DEVICE_MAP_REFRESHED_AT = now_ts
        return _DEVICE_MAP_CACHE
    except Exception:
        return _DEVICE_MAP_CACHE


def _resolve_device_ids(
    device_ids: Optional[List[str]],
    device_serial: Optional[str],
    device_id: Optional[str],
    user_id: Optional[str],
    user_name: Optional[str],
) -> Optional[List[str]]:
    """Ưu tiên: device_ids -> device_serial/device_id -> map user_id/user_name -> None."""
    if device_ids:
        ids = [str(x).strip() for x in device_ids if str(x).strip()]
        return ids if ids else None
    if device_serial and device_serial.strip():
        return [device_serial.strip()]
    if device_id and device_id.strip():
        return [device_id.strip()]

    mp = _load_user_device_map()
    if user_id:
        k = str(user_id).strip()
        if k in mp:
            return [mp[k]]
    if user_name:
        k = str(user_name).strip()
        if k in mp:
            return [mp[k]]
    return None


# ===== ADB helpers =====
def _which(p: str | None) -> Optional[str]:
    if not p:
        return None
    if pathlib.Path(p).exists():
        return str(pathlib.Path(p))
    w = shutil.which(p)
    return w


def _find_adb() -> Optional[str]:
    for cand in ADB_CANDIDATES:
        w = _which(cand)
        if w:
            return w
    return None


def _run_adb(args: List[str], timeout: float = 6.0) -> Tuple[int, str, str, Optional[str]]:
    adb = _find_adb()
    if not adb:
        return (127, "", "adb not found", None)
    try:
        cp = subprocess.run([adb] + args, capture_output=True, text=True, timeout=timeout)
        return (cp.returncode, cp.stdout, cp.stderr, adb)
    except Exception as e:
        return (1, "", str(e), adb)


def list_connected_devices() -> dict:
    """Return info about ADB devices."""
    _run_adb(["start-server"])
    rc, out, err, adb_path = _run_adb(["devices"])
    if rc != 0:
        return {"adb_path": adb_path, "devices": [], "error": err or "failed to run adb devices"}

    lines = [l.strip() for l in out.splitlines()]
    entries = []
    for ln in lines[1:]:
        if not ln:
            continue
        if "\t" in ln:
            serial, status = ln.split("\t", 1)
        else:
            parts = ln.split()
            serial, status = parts[0], (parts[1] if len(parts) > 1 else "unknown")
        entries.append({"serial": serial, "status": status})

    return {"adb_path": adb_path, "devices": entries}


@app.get("/api/devices")
def api_devices():
    info = list_connected_devices()
    serials = [d["serial"] for d in info.get("devices", []) if d.get("status") == "device"]
    return {"devices": serials, "raw": info}


# ===== Upload media (UI uploads here; device downloads via /uploads/<name>) =====
# @app.post("/api/upload_media")
# async def upload_media(files: List[UploadFile] = File(...)):
#     """
#     UI POST multipart/form-data with key 'files' (multiple).
#     Returns stored filenames in ./uploads to be used in params.files.
#     """
#     if not files:
#         raise HTTPException(status_code=400, detail="No files uploaded")

#     stored: List[str] = []
#     for f in files:
#         ext = pathlib.Path(f.filename or "").suffix or ".bin"
#         stored_name = f"{uuid.uuid4().hex}{ext}"
#         dest_path = UPLOAD_ROOT / stored_name
#         with dest_path.open("wb") as out:
#             shutil.copyfileobj(f.file, out)
#         stored.append(stored_name)

#     return {"ok": True, "files": stored}

@app.post("/api/upload_media")
async def upload_media(files: List[UploadFile] = File(...)):
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")

    stored: List[str] = []
    for f in files:
        ext = pathlib.Path(f.filename or "").suffix or ".bin"
        # stored_name = f"{uuid.uuid4().hex}{ext}"
        stored_name = f"media-{uuid.uuid4()}{ext}"
        dest_path = UPLOAD_ROOT / stored_name

        print("UPLOAD_MEDIA called")
        print("ORIGINAL =", f.filename)
        print("SAVE TO =", dest_path.resolve())
        print("STORED NAME =", stored_name)

        with dest_path.open("wb") as out:
            shutil.copyfileobj(f.file, out)
        stored.append(stored_name)

    return {"ok": True, "files": stored}

# ===== Enqueue command giống CRM/Postman =====
class CRMCommand(BaseModel):
    type: str = Field(..., description="post_to_wall | post_to_group | join_group | delete_post | edit_post")
    crm_id: Optional[str] = None
    delay: Optional[Union[bool, str, int]] = "false"
    user_id: Optional[str] = None
    user_name: Optional[str] = None

    # optional override
    device_ids: Optional[List[str]] = None
    device_serial: Optional[str] = None
    device_id: Optional[str] = None

    params: dict = {}


@app.post("/api/command")
def enqueue_command(cmd: CRMCommand):
    """
    Receive payload:
      {type, crm_id, delay, user_id, user_name, params:{content,files,group_link}}
    -> create JOB pending for tool to pull via GET /api/next?device_id=...
    """
    t = (cmd.type or "").strip()
    if t not in {
        "post_to_wall",
        "post_to_group",
        "join_group",
        "delete_post",
        "edit_post",
        "test_clipboard_termux",
    }:
        raise HTTPException(status_code=400, detail=f"Unsupported type: {t}")

    p = cmd.params or {}
    content = p.get("content", "") or ""
    files = p.get("files") or []
    group_link = p.get("group_link") or p.get("group") or ""

    post_link = (p.get("post_link") or p.get("link") or p.get("post_url") or "").strip()

    if t in {"post_to_group", "join_group"} and not group_link:
        raise HTTPException(status_code=400, detail="params.group_link is required for post_to_group/join_group")

    if t == "delete_post":
        if not post_link:
            raise HTTPException(status_code=400, detail="params.post_link is required for delete_post")

    if t == "edit_post":
        if not post_link:
            raise HTTPException(status_code=400, detail="params.post_link is required for edit_post")
        # ít nhất phải có 1 thay đổi
        if (not str(content).strip()) and (not files):
            raise HTTPException(status_code=400, detail="edit_post requires params.new_text or params.files")

    target_device_ids = _resolve_device_ids(
        device_ids=cmd.device_ids,
        device_serial=cmd.device_serial,
        device_id=cmd.device_id,
        user_id=cmd.user_id,
        user_name=cmd.user_name,
    )

    job_id = str(uuid.uuid4())
    doc = {
        "_id": job_id,
        "crm_id": cmd.crm_id,
        "type": t,
        "action": t,  # alias
        "delay": cmd.delay,
        "user_id": cmd.user_id,
        "user_name": cmd.user_name,
        "device_ids": target_device_ids,  # None => any device
        "content": content,
        "files": files,  # IMPORTANT: tool can use this (and/or params.files)
        "group_link": group_link or None,
        "post_link": post_link or None,
        "params": p,
        "source": "private_api",
        "status": "pending",
        "created_at": datetime.datetime.utcnow().isoformat(),
    }
    JOBS[job_id] = doc
    return {"ok": True, "id": job_id, "device_ids": target_device_ids}


@app.get("/api/jobs")
def api_list_jobs(limit: int = 50):
    items = sorted(JOBS.values(), key=lambda d: d.get("created_at", ""), reverse=True)
    return {"items": items[: max(1, min(limit, 500))]}


@app.get("/api/jobs/{job_id}")
def api_get_job(job_id: str):
    j = JOBS.get(job_id)
    if not j:
        raise HTTPException(status_code=404, detail="job not found")
    return j


# ===== Tool pull job =====
@app.get("/api/next")
def next_job(device_id: Optional[str] = None):
    """
    Tool calls: GET /api/next?device_id=...
    Returns one pending job matched by device_id (if job has device_ids restriction).
    """
    for j in sorted(JOBS.values(), key=lambda d: d.get("created_at", "")):
        if j.get("status") != "pending":
            continue
        allowed = j.get("device_ids")  # None or list[str]
        if allowed is not None:
            if not device_id or device_id not in allowed:
                continue
        j["status"] = "in_progress"
        j["taken_by"] = device_id
        j["updated_at"] = datetime.datetime.utcnow().isoformat()
        return j
    raise HTTPException(status_code=204, detail="No jobs")


class DoneBody(BaseModel):
    id: str
    status: str = "done"  # done | failed
    reason: Optional[str] = None
    result: Optional[dict] = None
    link: Optional[str] = None


@app.post("/api/done")
def mark_done(body: DoneBody):
    j = JOBS.get(body.id)
    if not j:
        raise HTTPException(status_code=404, detail="job not found")
    j["status"] = body.status
    if body.reason:
        j["reason"] = body.reason
    if body.result is not None:
        j["result"] = body.result
    if body.link:
        j["link"] = body.link
    j["updated_at"] = datetime.datetime.utcnow().isoformat()
    return {"ok": True}


# ===== CORS =====
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ===== UI =====
@app.get("/", response_class=HTMLResponse)
def home():
    return """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>FB Tools — Urgent Tasks</title>
  <style>
    body{font-family:sans-serif;max-width:980px;margin:24px auto;padding:0 10px}
    h2{margin:0 0 10px}
    .hint{font-size:12px;color:#666}
    .row{margin:12px 0}
    .tabs{display:flex;gap:8px;margin:14px 0 0;border-bottom:1px solid #ddd;flex-wrap:wrap}
    .tab{padding:10px 14px;border:1px solid #ddd;border-bottom:none;border-radius:10px 10px 0 0;cursor:pointer;background:#f8f8f8}
    .tab.active{background:#fff;font-weight:700}
    .panel{display:none;border:1px solid #ddd;border-radius:0 10px 10px 10px;padding:16px;background:#fff}
    .panel.active{display:block}
    .card{border:1px solid #e5e5e5;border-radius:10px;padding:14px;background:#fff}
    .grid2{display:grid;grid-template-columns:1fr 1fr;gap:12px}
    label{display:block;margin:6px 0 4px;font-weight:600}
    input[type="text"], textarea, select{
      width:100%; padding:8px 10px; border:1px solid #ccc; border-radius:8px; box-sizing:border-box;
    }
    textarea{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
    select[multiple]{height:132px}
    .btn{padding:9px 14px;border:1px solid #ccc;border-radius:10px;background:#fafafa;cursor:pointer}
    .btn.primary{background:#111;color:#fff;border-color:#111}
    .btn:disabled{opacity:.6;cursor:not-allowed}
    .devices-box{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:6px;margin-top:6px}
    .dev-item{display:flex;align-items:center;gap:8px}
    .hr{height:1px;background:#eee;margin:14px 0}
    .pill{display:inline-block;padding:2px 8px;border:1px solid #ddd;border-radius:999px;font-size:12px;color:#555}
    .ok{color:#0a7}
    .bad{color:#c22}
  </style>
</head>
<body>
  <h2>Facebook — Urgent Tools</h2>

  <!-- Devices -->
  <div class="row">
    <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
      <div style="font-weight:700">Thiết bị (có thể chọn nhiều):</div>
      <button class="btn" type="button" onclick="loadDevices()">Làm mới</button>
      <span class="pill">Nguồn: /api/devices</span>
    </div>

    <div style="margin-top:10px">
      <select id="device_ids_select" multiple></select>
      <div class="hint">Giữ Ctrl/Shift để chọn nhiều. Tick “Thiết bị đăng” bên dưới là nguồn chính để gửi device_ids.</div>
    </div>

    <div style="margin-top:12px">
      <div style="font-weight:700">Thiết bị đăng</div>
      <div id="devices-box" class="devices-box"></div>
    </div>
  </div>

  <!-- Tabs -->
  <div class="tabs">
    <div class="tab active" data-target="panel-wall">Đăng bài TCN</div>
    <div class="tab" data-target="panel-join">Tham gia nhóm</div>
    <div class="tab" data-target="panel-group">Đăng bài nhóm</div>
    <div class="tab" data-target="panel-delete">Xoá bài</div>
    <div class="tab" data-target="panel-edit">Chỉnh sửa bài</div>
  </div>

  <!-- PANEL: post_to_wall -->
  <div id="panel-wall" class="panel active">
    <div class="card">
      <div class="grid2">
        <div>
          <label>crm_id</label>
          <input id="wall_crm_id" type="text" placeholder="VD: 22938184">
        </div>
        <div>
          <label>delay</label>
          <select id="wall_delay">
            <option value="false">false (chạy ngay)</option>
            <option value="true">true (delay/defers)</option>
          </select>
        </div>
        <div>
          <label>user_id</label>
          <input id="wall_user_id" type="text" placeholder="VD: 0988003410">
        </div>
        <div>
          <label>user_name (optional)</label>
          <input id="wall_user_name" type="text" placeholder="VD: Vũ Quỳnh Chi">
        </div>
      </div>

      <div class="row">
        <label>content</label>
        <textarea id="wall_content" rows="5" placeholder="Nội dung đăng..."></textarea>
      </div>

      <div class="row">
        <label>Ảnh/Video (tuỳ chọn)</label>
        <input id="wall_files" type="file" multiple accept="image/*,video/*">
        <div class="hint">
          Flow: Chọn file → UI upload lên <code>/api/upload_media</code> → preview có <code>params.files</code> →
          Send → POST <code>/api/command</code> → tool tải từ <code>/uploads/&lt;name&gt;</code>.
        </div>
        <div class="hint" id="wall_upload_hint"></div>
      </div>

      <div class="hr"></div>

      <div class="row">
        <label>Xem trước command sẽ gửi</label>
        <textarea id="wall_preview" rows="12" readonly></textarea>
        <div class="hint">Payload này sẽ POST tới <code>/api/command</code>.</div>
      </div>

      <div class="row" style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
        <button class="btn primary" type="button" id="wall_send">Send</button>
        <span class="hint" id="wall_send_hint"></span>
      </div>

      <div class="row">
        <label>Kết quả gửi</label>
        <textarea id="wall_result" rows="6" readonly></textarea>
      </div>

      <div class="row">
        <label>Check job (GET /api/jobs/{id})</label>
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
          <input id="wall_job_id" type="text" placeholder="job id (uuid)">
          <button class="btn" type="button" id="wall_check">Check</button>
        </div>
        <textarea id="wall_job_detail" rows="10" readonly style="margin-top:8px"></textarea>
      </div>
    </div>
  </div>

  <!-- PANEL: join_group -->
  <div id="panel-join" class="panel">
    <div class="card">
      <div class="grid2">
        <div>
          <label>crm_id</label>
          <input id="join_crm_id" type="text" placeholder="VD: 22623688">
        </div>
        <div>
          <label>delay</label>
          <select id="join_delay">
            <option value="false">false (chạy ngay)</option>
            <option value="true">true (delay/defers)</option>
          </select>
        </div>
        <div>
          <label>user_id</label>
          <input id="join_user_id" type="text" placeholder="VD: 0988003410">
        </div>
        <div>
          <label>user_name (optional)</label>
          <input id="join_user_name" type="text" placeholder="VD: Hoàng Yến">
        </div>
      </div>

      <div class="row">
        <label>group_link</label>
        <input id="join_group_link" type="text" placeholder="VD: groups/3113799865582913">
      </div>

      <div class="hr"></div>

      <div class="row">
        <label>Xem trước command sẽ gửi</label>
        <textarea id="join_preview" rows="12" readonly></textarea>
      </div>

      <div class="row" style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
        <button class="btn primary" type="button" id="join_send">Send</button>
        <span class="hint" id="join_send_hint"></span>
      </div>

      <div class="row">
        <label>Kết quả gửi</label>
        <textarea id="join_result" rows="6" readonly></textarea>
      </div>

      <div class="row">
        <label>Check job</label>
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
          <input id="join_job_id" type="text" placeholder="job id (uuid)">
          <button class="btn" type="button" id="join_check">Check</button>
        </div>
        <textarea id="join_job_detail" rows="10" readonly style="margin-top:8px"></textarea>
      </div>
    </div>
  </div>

  <!-- PANEL: post_to_group -->
  <div id="panel-group" class="panel">
    <div class="card">
      <div class="grid2">
        <div>
          <label>crm_id</label>
          <input id="group_crm_id" type="text" placeholder="VD: 22938184">
        </div>
        <div>
          <label>delay</label>
          <select id="group_delay">
            <option value="false">false (chạy ngay)</option>
            <option value="true">true (delay/defers)</option>
          </select>
        </div>
        <div>
          <label>user_id</label>
          <input id="group_user_id" type="text" placeholder="VD: 0984485936">
        </div>
        <div>
          <label>user_name (optional)</label>
          <input id="group_user_name" type="text" placeholder="VD: Lưu Thư">
        </div>
      </div>

      <div class="row">
        <label>group_link</label>
        <input id="group_group_link" type="text" placeholder="VD: groups/3113799865582913">
      </div>

      <div class="row">
        <label>content</label>
        <textarea id="group_content" rows="5" placeholder="Nội dung đăng nhóm..."></textarea>
      </div>

      <div class="row">
        <label>Ảnh/Video (tuỳ chọn) — có thể chọn nhiều</label>
        <input id="group_files" type="file" multiple accept="image/*,video/*">
        <div class="hint" id="group_upload_hint"></div>
      </div>

      <div class="hr"></div>

      <div class="row">
        <label>Xem trước command sẽ gửi</label>
        <textarea id="group_preview" rows="12" readonly></textarea>
      </div>

      <div class="row" style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
        <button class="btn primary" type="button" id="group_send">Send</button>
        <span class="hint" id="group_send_hint"></span>
      </div>

      <div class="row">
        <label>Kết quả gửi</label>
        <textarea id="group_result" rows="6" readonly></textarea>
      </div>

      <div class="row">
        <label>Check job</label>
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
          <input id="group_job_id" type="text" placeholder="job id (uuid)">
          <button class="btn" type="button" id="group_check">Check</button>
        </div>
        <textarea id="group_job_detail" rows="10" readonly style="margin-top:8px"></textarea>
      </div>
    </div>
  </div>

  <!-- PANEL: delete_post -->
  <div id="panel-delete" class="panel">
    <div class="card">
      <div class="grid2">
        <div>
          <label>crm_id</label>
          <input id="del_crm_id" type="text" placeholder="VD: 22938184">
        </div>
        <div>
          <label>delay</label>
          <select id="del_delay">
            <option value="false">false (chạy ngay)</option>
            <option value="true">true (delay/defers)</option>
          </select>
        </div>
        <div>
          <label>user_id</label>
          <input id="del_user_id" type="text" placeholder="VD: 0988003410">
        </div>
        <div>
          <label>user_name (optional)</label>
          <input id="del_user_name" type="text" placeholder="VD: Vũ Quỳnh Chi">
        </div>
      </div>

      <div class="row">
        <label>post_link</label>
        <input id="del_post_link" type="text" placeholder="VD: https://www.facebook.com/... hoặc /share/p/...">
        <div class="hint">Tool sẽ mở link bài viết rồi bấm “...” → “Xoá/Delete”.</div>
      </div>

      <div class="hr"></div>

      <div class="row">
        <label>Xem trước command sẽ gửi</label>
        <textarea id="del_preview" rows="12" readonly></textarea>
      </div>

      <div class="row" style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
        <button class="btn primary" type="button" id="del_send">Send</button>
        <span class="hint" id="del_send_hint"></span>
      </div>

      <div class="row">
        <label>Kết quả gửi</label>
        <textarea id="del_result" rows="6" readonly></textarea>
      </div>

      <div class="row">
        <label>Check job</label>
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
          <input id="del_job_id" type="text" placeholder="job id (uuid)">
          <button class="btn" type="button" id="del_check">Check</button>
        </div>
        <textarea id="del_job_detail" rows="10" readonly style="margin-top:8px"></textarea>
      </div>
    </div>
  </div>

  <!-- PANEL: edit_post -->
  <div id="panel-edit" class="panel">
    <div class="card">
      <div class="grid2">
        <div>
          <label>crm_id</label>
          <input id="edit_crm_id" type="text" placeholder="VD: 22938184">
        </div>
        <div>
          <label>delay</label>
          <select id="edit_delay">
            <option value="false">false (chạy ngay)</option>
            <option value="true">true (delay/defers)</option>
          </select>
        </div>
        <div>
          <label>user_id</label>
          <input id="edit_user_id" type="text" placeholder="VD: 0988003410">
        </div>
        <div>
          <label>user_name (optional)</label>
          <input id="edit_user_name" type="text" placeholder="VD: Vũ Quỳnh Chi">
        </div>
      </div>

      <div class="row">
        <label>post_link</label>
        <input id="edit_post_link" type="text" placeholder="VD: https://www.facebook.com/... hoặc /share/p/...">
      </div>

      <div class="row">
        <label>new_text (nếu rỗng = không đổi text)</label>
        <textarea id="edit_content" rows="5" placeholder="Nội dung mới... (tool sẽ xoá toàn bộ text cũ và thay bằng text này)"></textarea>
        <div class="hint">Lưu ý: bản edit_post hiện tại đang ưu tiên chỉnh sửa text. Media sẽ bổ sung sau.</div>
      </div>

      <div class="row">
        <label>Ảnh/Video mới (tuỳ chọn)</label>
        <input id="edit_files" type="file" multiple accept="image/*,video/*">
        <div class="hint" id="edit_upload_hint"></div>
      </div>

      <div class="hr"></div>

      <div class="row">
        <label>Xem trước command sẽ gửi</label>
        <textarea id="edit_preview" rows="12" readonly></textarea>
      </div>

      <div class="row" style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
        <button class="btn primary" type="button" id="edit_send">Send</button>
        <span class="hint" id="edit_send_hint"></span>
      </div>

      <div class="row">
        <label>Kết quả gửi</label>
        <textarea id="edit_result" rows="6" readonly></textarea>
      </div>

      <div class="row">
        <label>Check job</label>
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
          <input id="edit_job_id" type="text" placeholder="job id (uuid)">
          <button class="btn" type="button" id="edit_check">Check</button>
        </div>
        <textarea id="edit_job_detail" rows="10" readonly style="margin-top:8px"></textarea>
      </div>
    </div>
  </div>


<script>
  // ===== Tabs switch =====
  const tabs = document.querySelectorAll('.tab');
  const panels = document.querySelectorAll('.panel');
  tabs.forEach(tab => {
    tab.addEventListener('click', () => {
      tabs.forEach(t => t.classList.remove('active'));
      panels.forEach(p => p.classList.remove('active'));
      tab.classList.add('active');
      document.getElementById(tab.dataset.target).classList.add('active');
      refreshAllPreviews();
    });
  });

  // ===== Devices =====
  async function fetchDevices() {
    try {
      const res = await fetch('/api/devices', { cache: 'no-store' });
      const js = await res.json();
      if (js && Array.isArray(js.devices)) return js.devices.map(s => ({ serial: s, label: s }));
      if (Array.isArray(js)) return js.map(d => ({ serial: d.serial, label: d.label || d.serial }));
      return [];
    } catch (e) {
      console.warn(e);
      return [];
    }
  }

  function renderDevices(devs) {
    const sel = document.getElementById('device_ids_select');
    const box = document.getElementById('devices-box');
    sel.innerHTML = '';
    box.innerHTML = '';

    for (const d of devs) {
      const opt = document.createElement('option');
      opt.value = d.serial;
      opt.textContent = d.label || d.serial;
      sel.appendChild(opt);
    }

    for (const d of devs) {
      const wrap = document.createElement('label');
      wrap.className = 'dev-item';
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.value = d.serial;
      const span = document.createElement('span');
      span.textContent = d.label || d.serial;
      wrap.appendChild(cb);
      wrap.appendChild(span);
      box.appendChild(wrap);
    }

    sel.addEventListener('change', () => {
      const selected = new Set([...sel.selectedOptions].map(o => o.value));
      box.querySelectorAll('input[type="checkbox"]').forEach(cb => cb.checked = selected.has(cb.value));
      refreshAllPreviews();
    });
    box.querySelectorAll('input[type="checkbox"]').forEach(cb => {
      cb.addEventListener('change', () => {
        [...sel.options].forEach(o => { if (o.value === cb.value) o.selected = cb.checked; });
        refreshAllPreviews();
      });
    });
  }

  async function loadDevices() {
    const devs = await fetchDevices();
    renderDevices(devs);
  }

  function getSelectedDeviceIds() {
    return [...document.querySelectorAll('#devices-box input[type="checkbox"]:checked')].map(x => x.value);
  }

  // ===== Helpers =====
  function pretty(obj){ return JSON.stringify(obj, null, 2); }

  function buildBase(type) {
    const device_ids = getSelectedDeviceIds();
    return {
      type,
      crm_id: null,
      delay: "false",
      user_id: "",
      user_name: "",
      device_ids: device_ids.length ? device_ids : null,
      params: {}
    };
  }

  function setText(id, msg){ document.getElementById(id).textContent = msg || ""; }

  async function apiCheckJob(id, outElId) {
    const out = document.getElementById(outElId);
    out.value = "";
    try {
      if (!id) { out.value = "ERROR: thiếu job id"; return; }
      const res = await fetch('/api/jobs/' + encodeURIComponent(id), { cache: 'no-store' });
      const js = await res.json().catch(()=> ({}));
      if (!res.ok) throw new Error(js.detail || ("HTTP " + res.status));
      out.value = pretty(js);
    } catch (e) {
      out.value = "ERROR: " + e.message;
    }
  }

  // ===== Upload media =====
  async function uploadMediaFiles(fileInput) {
    const files = fileInput && fileInput.files ? [...fileInput.files] : [];
    if (!files.length) return [];

    const fd = new FormData();
    files.forEach(f => fd.append('files', f));

    const res = await fetch('/api/upload_media', { method:'POST', body: fd });
    const js = await res.json().catch(()=> ({}));
    if (!res.ok) throw new Error(js.detail || ("upload failed HTTP " + res.status));
    if (!js || !js.ok || !Array.isArray(js.files)) throw new Error("upload response invalid");
    return js.files; // stored filenames
  }

  // ===== Upload-on-select state =====
  const uploadedFiles = { wall: [], group: [], edit: [] };
  const uploadPromises = { wall: null, group: null, edit: null };
  const uploadTokens = { wall: 0, group: 0, edit: 0 };

  function setUploadHint(kind, msg, ok=null){
    const id = (kind === 'wall') ? 'wall_upload_hint' : (kind === 'group') ? 'group_upload_hint' : 'edit_upload_hint';
    if (ok === true) setText(id, "✅ " + msg);
    else if (ok === false) setText(id, "❌ " + msg);
    else setText(id, msg);
  }

  async function startUpload(kind) {
    const inputId = (kind === 'wall') ? 'wall_files' : (kind === 'group') ? 'group_files' : 'edit_files';
    const fileInput = document.getElementById(inputId);
    const localCount = fileInput.files ? fileInput.files.length : 0;

    // bump token to cancel previous results
    const token = ++uploadTokens[kind];

    if (!localCount) {
      uploadedFiles[kind] = [];
      uploadPromises[kind] = null;
      setUploadHint(kind, "", null);
      refreshAllPreviews();
      return;
    }

    setUploadHint(kind, `Đang upload ${localCount} file...`, null);
    const p = uploadMediaFiles(fileInput);
    uploadPromises[kind] = p;

    try {
      const stored = await p;
      if (token !== uploadTokens[kind]) return; // outdated
      uploadedFiles[kind] = stored;
      setUploadHint(kind, `Upload OK → params.files đã sẵn sàng (${stored.length} file).`, true);
      refreshAllPreviews();
    } catch (e) {
      if (token !== uploadTokens[kind]) return;
      uploadedFiles[kind] = [];
      setUploadHint(kind, `Upload lỗi: ${e.message}`, false);
      refreshAllPreviews();
    }
  }

  async function ensureUploadDone(kind) {
    const p = uploadPromises[kind];
    if (p) {
      try { await p; } catch (_) {}
    }
  }

  // ===== Preview builders =====
  function refreshWallPreview() {
    const cmd = buildBase("post_to_wall");
    cmd.crm_id = document.getElementById('wall_crm_id').value.trim() || null;
    cmd.delay = document.getElementById('wall_delay').value;
    cmd.user_id = document.getElementById('wall_user_id').value.trim();
    cmd.user_name = document.getElementById('wall_user_name').value.trim();
    cmd.params = {
      content: document.getElementById('wall_content').value || "",
      files: uploadedFiles.wall.slice()
    };
    document.getElementById('wall_preview').value = pretty(cmd);
  }

  function refreshJoinPreview() {
    const cmd = buildBase("join_group");
    cmd.crm_id = document.getElementById('join_crm_id').value.trim() || null;
    cmd.delay = document.getElementById('join_delay').value;
    cmd.user_id = document.getElementById('join_user_id').value.trim();
    cmd.user_name = document.getElementById('join_user_name').value.trim();
    cmd.params = {
      group_link: document.getElementById('join_group_link').value.trim(),
    };
    document.getElementById('join_preview').value = pretty(cmd);
  }

  function refreshGroupPreview() {
    const cmd = buildBase("post_to_group");
    cmd.crm_id = document.getElementById('group_crm_id').value.trim() || null;
    cmd.delay = document.getElementById('group_delay').value;
    cmd.user_id = document.getElementById('group_user_id').value.trim();
    cmd.user_name = document.getElementById('group_user_name').value.trim();
    cmd.params = {
      group_link: document.getElementById('group_group_link').value.trim(),
      content: document.getElementById('group_content').value || "",
      files: uploadedFiles.group.slice()
    };
    document.getElementById('group_preview').value = pretty(cmd);
  }

  function refreshEditPreview() {
    const cmd = buildBase("edit_post");
    cmd.crm_id = document.getElementById('edit_crm_id').value.trim() || null;
    cmd.delay = document.getElementById('edit_delay').value;
    cmd.user_id = document.getElementById('edit_user_id').value.trim();
    cmd.user_name = document.getElementById('edit_user_name').value.trim();
    cmd.params = {
      post_link: document.getElementById('edit_post_link').value.trim(),
      content: document.getElementById('edit_content').value || "",
      files: uploadedFiles.edit.slice(),
    };
    document.getElementById('edit_preview').value = pretty(cmd);
  }

  

  function refreshDeletePreview() {
    const cmd = buildBase("delete_post");
    cmd.crm_id = document.getElementById('del_crm_id').value.trim() || null;
    cmd.delay = document.getElementById('del_delay').value;
    cmd.user_id = document.getElementById('del_user_id').value.trim();
    cmd.user_name = document.getElementById('del_user_name').value.trim();
    cmd.params = {
      post_link: document.getElementById('del_post_link').value.trim(),
    };
    document.getElementById('del_preview').value = pretty(cmd);
  }

function refreshAllPreviews(){
    refreshWallPreview();
    refreshJoinPreview();
    refreshGroupPreview();
    refreshDeletePreview();
    refreshEditPreview();
  }

  // Watch inputs
  const watchIds = [
    'wall_crm_id','wall_delay','wall_user_id','wall_user_name','wall_content',
    'join_crm_id','join_delay','join_user_id','join_user_name','join_group_link',
    'group_crm_id','group_delay','group_user_id','group_user_name','group_group_link','group_content',
    'del_crm_id','del_delay','del_user_id','del_user_name','del_post_link',
    'edit_crm_id','edit_delay','edit_user_id','edit_user_name','edit_post_link','edit_content'
  ];
  watchIds.forEach(id => {
    const el = document.getElementById(id);
    el.addEventListener('input', refreshAllPreviews);
    el.addEventListener('change', refreshAllPreviews);
  });

  // File inputs: upload-on-select
  document.getElementById('wall_files').addEventListener('change', () => startUpload('wall'));
  document.getElementById('group_files').addEventListener('change', () => startUpload('group'));
  document.getElementById('edit_files').addEventListener('change', () => startUpload('edit'));

  // ===== Send handlers =====
  async function sendWall() {
    const btn = document.getElementById('wall_send');
    btn.disabled = true;
    setText('wall_send_hint', 'Đang gửi...');
    document.getElementById('wall_result').value = "";
    document.getElementById('wall_job_detail').value = "";
    try {
      // ensure upload finished so preview already has params.files
      await ensureUploadDone('wall');

      const cmd = JSON.parse(document.getElementById('wall_preview').value);

      const res = await fetch('/api/command', {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify(cmd)
      });
      const js = await res.json().catch(()=> ({}));
      if (!res.ok) throw new Error(js.detail || ("HTTP " + res.status));
      document.getElementById('wall_result').value = pretty(js);
      if (js && js.id) document.getElementById('wall_job_id').value = js.id;
      setText('wall_send_hint', 'OK');
    } catch (e) {
      document.getElementById('wall_result').value = "ERROR: " + e.message;
      setText('wall_send_hint', 'Lỗi');
    } finally {
      btn.disabled = false;
    }
  }

  async function sendJoin() {
    const btn = document.getElementById('join_send');
    btn.disabled = true;
    setText('join_send_hint', 'Đang gửi...');
    document.getElementById('join_result').value = "";
    document.getElementById('join_job_detail').value = "";
    try {
      const cmd = JSON.parse(document.getElementById('join_preview').value);

      const res = await fetch('/api/command', {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify(cmd)
      });
      const js = await res.json().catch(()=> ({}));
      if (!res.ok) throw new Error(js.detail || ("HTTP " + res.status));
      document.getElementById('join_result').value = pretty(js);
      if (js && js.id) document.getElementById('join_job_id').value = js.id;
      setText('join_send_hint', 'OK');
    } catch (e) {
      document.getElementById('join_result').value = "ERROR: " + e.message;
      setText('join_send_hint', 'Lỗi');
    } finally {
      btn.disabled = false;
    }
  }

  

  async function sendDelete() {
    const btn = document.getElementById('del_send');
    btn.disabled = true;
    setText('del_send_hint', 'Đang gửi...');
    document.getElementById('del_result').value = "";
    document.getElementById('del_job_detail').value = "";
    try {
      const cmd = JSON.parse(document.getElementById('del_preview').value);

      const res = await fetch('/api/command', {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify(cmd)
      });
      const js = await res.json().catch(()=> ({}));
      if (!res.ok) throw new Error(js.detail || ("HTTP " + res.status));
      document.getElementById('del_result').value = pretty(js);
      if (js && js.id) document.getElementById('del_job_id').value = js.id;
      setText('del_send_hint', 'OK');
    } catch (e) {
      document.getElementById('del_result').value = "ERROR: " + e.message;
      setText('del_send_hint', 'Lỗi');
    } finally {
      btn.disabled = false;
    }
  }

  async function sendEdit() {
    const btn = document.getElementById('edit_send');
    btn.disabled = true;
    setText('edit_send_hint', 'Đang gửi...');
    document.getElementById('edit_result').value = "";
    document.getElementById('edit_job_detail').value = "";
    try {
      await ensureUploadDone('edit');

      const cmd = JSON.parse(document.getElementById('edit_preview').value);
      const res = await fetch('/api/command', {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify(cmd)
      });
      const js = await res.json().catch(()=> ({}));
      if (!res.ok) throw new Error(js.detail || ("HTTP " + res.status));
      document.getElementById('edit_result').value = pretty(js);
      if (js && js.id) document.getElementById('edit_job_id').value = js.id;
      setText('edit_send_hint', 'OK');
    } catch (e) {
      document.getElementById('edit_result').value = "ERROR: " + e.message;
      setText('edit_send_hint', 'Lỗi');
    } finally {
      btn.disabled = false;
    }
  }

async function sendGroup() {
    const btn = document.getElementById('group_send');
    btn.disabled = true;
    setText('group_send_hint', 'Đang gửi...');
    document.getElementById('group_result').value = "";
    document.getElementById('group_job_detail').value = "";
    try {
      await ensureUploadDone('group');

      const cmd = JSON.parse(document.getElementById('group_preview').value);

      const res = await fetch('/api/command', {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify(cmd)
      });
      const js = await res.json().catch(()=> ({}));
      if (!res.ok) throw new Error(js.detail || ("HTTP " + res.status));
      document.getElementById('group_result').value = pretty(js);
      if (js && js.id) document.getElementById('group_job_id').value = js.id;
      setText('group_send_hint', 'OK');
    } catch (e) {
      document.getElementById('group_result').value = "ERROR: " + e.message;
      setText('group_send_hint', 'Lỗi');
    } finally {
      btn.disabled = false;
    }
  }

  document.getElementById('wall_send').addEventListener('click', sendWall);
  document.getElementById('join_send').addEventListener('click', sendJoin);
  document.getElementById('group_send').addEventListener('click', sendGroup);
  document.getElementById('del_send').addEventListener('click', sendDelete);
  document.getElementById('edit_send').addEventListener('click', sendEdit);

  // check buttons
  document.getElementById('wall_check').addEventListener('click', () => apiCheckJob(
    document.getElementById('wall_job_id').value.trim(),
    'wall_job_detail'
  ));
  document.getElementById('join_check').addEventListener('click', () => apiCheckJob(
    document.getElementById('join_job_id').value.trim(),
    'join_job_detail'
  ));
  document.getElementById('group_check').addEventListener('click', () => apiCheckJob(
    document.getElementById('group_job_id').value.trim(),
    'group_job_detail'
  ));

  document.getElementById('del_check').addEventListener('click', () => apiCheckJob(
    document.getElementById('del_job_id').value.trim(),
    'del_job_detail'
  ));

  document.getElementById('edit_check').addEventListener('click', () => apiCheckJob(
    document.getElementById('edit_job_id').value.trim(),
    'edit_job_detail'
  ));

  // init
  loadDevices();
  refreshAllPreviews();
</script>
</body>
</html>
"""


if __name__ == '__main__':
    import uvicorn
    host = os.getenv('HOST', '0.0.0.0')
    port = int(os.getenv('PORT', '5000'))
    uvicorn.run(app, host=host, port=port, reload=False)

