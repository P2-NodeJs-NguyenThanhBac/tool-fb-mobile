import json
import os
import time
from typing import Optional, Dict, Any, List

QUEUE_FILE = "urgent_queue.json"

# Delay cho từng action (tính bằng giây)
ACTION_INTERVALS = {
    "post_wall": 20 * 60,   # 20 phút
    "post_group": 15 * 60,  # 15 phút
    "fetch_group_post_link": 60,
    "join_group": 5 * 60,
    "left_group": 60,
    # nếu sau này muốn thêm: "post_video": 10 * 60, ...
}

def _load_state() -> Dict[str, Any]:
    if not os.path.exists(QUEUE_FILE):
        return {"last_run_at": {}, "queue": []}
    try:
        with open(QUEUE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if "last_run_at" not in data or not isinstance(data["last_run_at"], dict):
            data["last_run_at"] = {}
        if "queue" not in data or not isinstance(data["queue"], list):
            data["queue"] = []
        return data
    except Exception:
        return {"last_run_at": {}, "queue": []}


def _save_state(state: Dict[str, Any]) -> None:
    tmp_file = QUEUE_FILE + ".tmp"
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp_file, QUEUE_FILE)


def _job_signature(job: Dict[str, Any]) -> str:
    user_id = job.get("user_id")
    user_name = job.get("user_name")
    action = job.get("action")
    params = job.get("params", {})
    params_str = json.dumps(params, sort_keys=True, ensure_ascii=False)
    return f"{user_id}|{user_name}|{action}|{params_str}"


def _norm_action(raw_action: str) -> str:
    a = (raw_action or "").strip().lower()
    if a in ("post_to_wall", "post_wall"):
        return "post_wall"
    if a in ("post_to_group", "post_group"):
        return "post_group"
    if a in ("fetch_group_post_link", "retry_group_post_link", "copy_group_post_link"):
        return "fetch_group_post_link"
    if a in ("join_group", "join_fb_group"):
        return "join_group"
    if a in ("left_group", "leave_group"):
        return "left_group"
    return a


def enqueue_urgent_task(job: Dict[str, Any]) -> None:
    """
    Thêm job vào file queue.
    Dùng cho các action CẦN delay (post_wall, post_group).
    Nếu trùng toàn bộ (user_id + action + params) thì bỏ qua.
    """
    state = _load_state()
    queue: List[Dict[str, Any]] = state.get("queue", [])

    action = _norm_action(job.get("action") or "")
    job["action"] = action  # chuẩn hoá luôn cho đồng nhất
        # Chỉ lưu các action có delay
    if ACTION_INTERVALS.get(action, 0) <= 0:
        return

    sig_new = _job_signature(job)
    for existing in queue:
        if _job_signature(existing) == sig_new:
            # đã có job y hệt trong file -> không thêm nữa
            return

    job.setdefault("created_at", time.time())
    queue.append(job)
    state["queue"] = queue
    _save_state(state)


def get_next_task_if_due(allowed_user_ids: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
    """
    Mỗi lần scheduler gọi:
    - Duyệt queue từ đầu đến cuối
    - Tìm job đầu tiên có action thuộc ACTION_INTERVALS
    - Delay được tính THEO TỪNG (user_id, action):
        now - last_run_at[f"{user_id}|{action}"] >= ACTION_INTERVALS[action]
    - Nếu đủ:
        + Lấy job đó
        + Xoá tất cả job trùng nội dung (signature) khỏi queue
        + Cập nhật last_run_at[key] = now
        + Lưu file và return job
    - Nếu không job nào đủ điều kiện -> trả về None
    """
    now = time.time()
    state = _load_state()
    last_run_at: Dict[str, float] = state.get("last_run_at", {})
    queue: List[Dict[str, Any]] = state.get("queue", [])
    allowed = None
    if allowed_user_ids:
        allowed = set(str(x).strip() for x in allowed_user_ids if str(x).strip())

    if not queue:
        return None

    for idx, job in enumerate(queue):
        action = _norm_action(job.get("action") or "")
        job["action"] = action
        interval = ACTION_INTERVALS.get(action, 0)
        if interval <= 0:
            # action này không quản lý delay bằng file -> bỏ qua
            continue

        # 👉 Lấy user_id của job
        user_id = str(job.get("user_id") or "").strip()
        # ✅ Nếu device chỉ được phép chạy một số user_id
        if allowed is not None and user_id not in allowed:
            continue

        # Nếu không có user_id, fallback về delay chung theo action (tránh crash)
        if user_id:
            key = f"{user_id}|{action}"
        else:
            key = action  # fallback kiểu cũ

        last = float(last_run_at.get(key, 0))
        if now - last < interval:
            # chưa đủ thời gian delay -> bỏ qua, thử job tiếp theo
            continue

        # ✅ Job này đủ điều kiện chạy
        task = job
        sig = _job_signature(task)

        # Lọc queue, loại bỏ job này + tất cả job trùng signature
        remaining: List[Dict[str, Any]] = []
        for j in queue:
            if _job_signature(j) == sig:
                continue
            remaining.append(j)

        # Cập nhật theo (user_id, action)
        last_run_at[key] = now
        state["last_run_at"] = last_run_at
        state["queue"] = remaining
        _save_state(state)
        return task

    # Không có job nào đủ điều kiện chạy
    return None


def mark_task_done(job_id: str) -> None:
    """Xoá job theo job_id nếu nó còn trong file queue (ít dùng)."""
    state = _load_state()
    queue: List[Dict[str, Any]] = state.get("queue", [])
    new_queue = [job for job in queue if str(job.get("id") or job.get("_id") or "") != str(job_id)]
    state["queue"] = new_queue
    _save_state(state)
