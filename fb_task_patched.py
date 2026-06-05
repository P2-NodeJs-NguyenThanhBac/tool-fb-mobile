import asyncio, random, logging, contextlib, inspect, json, os, re, unicodedata
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from module import *
from util import *
from module import *
from module.android_automation_common import normalize_text as _common_normalize_text
from ws_client import notify_ack
import pymongo_management
from pymongo_management import *
import urgent_queue
from module.crawl_comments_api_client import call_crawl_comments_api
from module.mobile_dump_automation_rebuilt import attach_dump_automation
from rabbitmq_result_client import publish_rabbitmq_result
import requests

DEVICE_URGENT_EVENT: dict[str, asyncio.Event] = {}
DEVICE_URGENT_QUEUE: dict[str, asyncio.Queue] = {}
# DEVICE_ACTION_LOCK = asyncio.Lock()
CURRENT_ACTION_TASK = {} 
# ==== Runtime feature flags ====
ALLOW_AUTO_SWITCH = True       # auto switch account after each fb_natural_task cycle
AUTO_SWITCH_AFTER_POST_GROUP_WHEN_IDLE = True  # đăng group xong + hết việc thì chuyển sang account kế tiếp
AUTO_SWITCH_PRIORITIZE_NO_POST_TODAY = True  # ưu tiên account chưa có bài trong Bai-dang hôm nay
STARTUP_SWITCH_TO_LOWEST_POST_TODAY = True  # startup Facebook: ưu tiên account ít bài Bai-dang nhất hôm nay
DISABLE_LOGOUT_ACTION = True   # vô hiệu hóa lệnh logout từ CRM (nếu có)
POST_GROUP_SUCCESS_COOLDOWN_SECONDS = 5  # wait after successful post_group before scanning/running the next job
POST_GROUP_NOT_APPROVED_STATUSES = {"rejected", "removed"}
ENABLE_MONGO_COMMAND_SCAN = True  # False = chỉ nhận lệnh RabbitMQ, không quét Mongo/urgent_queue.json
# ===============================
# global default
PASSIVE_WAIT_ONLY = False   # chỉ check trạng thái + chờ RabbitMQ, không nuôi FB/Zalo
_RUNTIME_PASSIVE_WAIT_ONLY = False

# per-device override: {device_id: bool}
_DEVICE_PASSIVE_WAIT_ONLY = {}

ACCOUNT_MANUAL_LOGIN_REASON = "Tài khoản bị đăng xuất cần hỗ trợ đăng nhập bằng tay"
ENSURE_ACCOUNT_LAST_REASON: dict[str, str] = {}


def set_mongo_command_scan_enabled(value: bool):
    global ENABLE_MONGO_COMMAND_SCAN
    ENABLE_MONGO_COMMAND_SCAN = bool(value)
    try:
        log_message(f"[MONGO_SCAN] Set global: {ENABLE_MONGO_COMMAND_SCAN}", logging.INFO)
    except Exception:
        pass


def set_passive_wait_only(value: bool, device_id: str = None):
    global PASSIVE_WAIT_ONLY, _RUNTIME_PASSIVE_WAIT_ONLY, _DEVICE_PASSIVE_WAIT_ONLY

    v = bool(value)

    if device_id:
        _DEVICE_PASSIVE_WAIT_ONLY[device_id] = v
        try:
            log_message(f"[PASSIVE] Set cho device {device_id}: {v}", logging.INFO)
        except Exception:
            pass
        return

    PASSIVE_WAIT_ONLY = v
    _RUNTIME_PASSIVE_WAIT_ONLY = v
    _DEVICE_PASSIVE_WAIT_ONLY.clear()   # reset override riêng khi set global
    try:
        log_message(f"[PASSIVE] Set global: {v}", logging.INFO)
    except Exception:
        pass


def get_passive_wait_only(device_id: str = None) -> bool:
    if device_id and device_id in _DEVICE_PASSIVE_WAIT_ONLY:
        return _DEVICE_PASSIVE_WAIT_ONLY[device_id]
    return _RUNTIME_PASSIVE_WAIT_ONLY


async def set_passive_wait_only_now(value: bool, device_id: str = None):
    set_passive_wait_only(value, device_id=device_id)

    if value:
        # nếu bật passive, hủy action hiện tại của đúng device để nhường tài nguyên ngay
        if device_id:
            task = CURRENT_ACTION_TASK.get(device_id)
            if task and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
                CURRENT_ACTION_TASK.pop(device_id, None)


def _is_valid_objectid(s: str) -> bool:
    """Return True nếu s là chuỗi ObjectId 24-hex."""
    return bool(s and re.fullmatch(r"[0-9a-fA-F]{24}", str(s).strip()))

def _dummy_objectid() -> str:
    """ObjectId hợp lệ dùng cho job test (không có _id thật)."""
    return "000000000000000000000000"

def get_urgent_objects(device_id: str) -> tuple[asyncio.Event, asyncio.Queue]:
    """Lấy (event, queue) dành riêng cho 1 device."""
    ev = DEVICE_URGENT_EVENT.get(device_id)
    q = DEVICE_URGENT_QUEUE.get(device_id)
    if ev is None:
        ev = DEVICE_URGENT_EVENT[device_id] = asyncio.Event()
    if q is None:
        q = DEVICE_URGENT_QUEUE[device_id] = asyncio.Queue()
    return ev, q

async def cancel_current_action(driver):
    """Huỷ action tự nhiên đang chạy trên device này (nếu có)."""
    task = CURRENT_ACTION_TASK.get(driver.serial)
    if task and not task.done():
        task.cancel()
        try:
            await task   # đợi nó kết thúc hẳn
        except asyncio.CancelledError:
            # log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Đã huỷ action hiện tại để chạy urgent task")
            log_message(
                f"[{DEVICE_LIST_NAME.get(driver.serial, driver.serial)}] Đã huỷ action hiện tại để chạy urgent task",
                logging.INFO
            )
    CURRENT_ACTION_TASK.pop(driver.serial, None)

def _runtime_state_path_from_device_runtime_state(device_id: str) -> Path:
    """
    Trả về path util/runtime_state/device_{id}.json.
    Ưu tiên lấy theo vị trí module util.device_runtime_state để không phụ thuộc CWD.
    """
    base: Path | None = None
    # 1) Nếu project có module util.device_runtime_state thì bám theo __file__ của nó (ổn định nhất).
    try:
        from importlib import import_module
        drs = import_module("util.device_runtime_state")
        drs_file = getattr(drs, "__file__", None)
        if drs_file:
            base = Path(drs_file).resolve().parent / "runtime_state"
    except Exception:
        base = None

    # 2) Fallback: theo cấu trúc repo (util/runtime_state)
    if base is None:
        base = Path("util") / "runtime_state"

    base.mkdir(parents=True, exist_ok=True)
    return base / f"device_{device_id}.json"


def _persist_current_account(device_id: str, account: str, username: str) -> None:
    """
    Lưu thông tin account hiện tại sau khi switch.
    Ưu tiên dùng set_online_account (nếu có) để đồng bộ đúng format/file mà hệ thống đang dùng.
    Nếu không có thì fallback update trực tiếp file JSON.
    """
    # 1) Ưu tiên dùng helper chuẩn của project (nếu đã import từ util).
    try:
        fn = globals().get("set_online_account")
        if callable(fn):
            fn(device_id, account=str(account).strip(), username=str(username).strip())
            return
    except Exception:
        pass

    # 2) Fallback: update file JSON trực tiếp (giữ nguyên device_id/status).
    p = _runtime_state_path_from_device_runtime_state(device_id)
    try:
        st = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(st, dict):
            st = {}
    except FileNotFoundError:
        st = {}
    except Exception:
        st = {}

    st.setdefault("device_id", device_id)
    st.setdefault("status", "Online")
    st["current_account"] = str(account).strip()
    st["current_username"] = str(username).strip()
    st["updated_at"] = int(time.time())

    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)

async def sync_device_accounts_status(
    device_id: str,
    current_account: str,
    current_username: str | None = None,
    number_of_friends: int | None = None,
):
    try:
        current_account = str(current_account or "").strip()
        current_username = str(current_username or current_account).strip()

        if not device_id or not current_account:
            return False

        try:
            set_offline(device_id)
        except Exception:
            pass

        try:
            set_online_account(device_id, account=current_account, username=current_username)
        except Exception:
            pass

        await pymongo_management.switch_online_account_for_device(
            device_id=device_id,
            current_account=current_account,
            number_of_friends=number_of_friends,
        )

        log_message(
            f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] Đồng bộ account status OK | device_id={device_id} | current_account={current_account}",
            logging.INFO,
        )
        return True

    except Exception as e:
        log_message(
            f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] sync_device_accounts_status lỗi: {type(e).__name__} | {e}\n{traceback.format_exc()}",
            logging.ERROR,
        )
        return False
   
def _normalize_account_value(value: str | None) -> str:
    return str(value or "").strip()


def _normalize_username_value(value: str | None) -> str:
    normalized = str(value or "").replace("_", " ").strip()
    if not normalized or normalized == "unknown_user":
        return ""
    return normalized


def _empty_rabbit_error_meta() -> dict:
    return {
        "error_code": None,
        "error_group": None,
        "error_message": None,
        "retryable": None,
        "step": None,
    }


def _build_rabbit_error_meta(
    action: str,
    *,
    ok: bool,
    reason: str | None = None,
    join_status: str | None = None,
    post_status: str | None = None,
) -> dict:
    if ok:
        return _empty_rabbit_error_meta()

    reason_text = str(reason or "").strip()
    reason_low = reason_text.lower()
    join_status_low = str(join_status or "").strip().lower()
    post_status_low = str(post_status or "").strip().lower()

    def meta(code: str, group: str, message: str, retryable: bool, step: str) -> dict:
        return {
            "error_code": code,
            "error_group": group,
            "error_message": message,
            "retryable": bool(retryable),
            "step": step,
        }

    if (
        "không thể chuyển sang tài khoản" in reason_low
        or "cannot switch" in reason_low
        or "missing target_account" in reason_low
    ):
        return meta(
            "ACCOUNT_SWITCH_FAILED",
            "ACCOUNT",
            "Không thể chuyển sang tài khoản cần chạy",
            False,
            "switch_account",
        )

    if "group_link is required" in reason_low:
        return meta(
            "GROUP_LINK_REQUIRED",
            "VALIDATION",
            "Thiếu group_link",
            False,
            "validate_input",
        )

    if action == "join_group":
        if join_status_low == "question_required" or "trả lời câu hỏi" in reason_low or "question" in reason_low:
            return meta(
                "JOIN_QUESTION_REQUIRED",
                "APPROVAL",
                "Nhóm yêu cầu trả lời câu hỏi",
                False,
                "join_questionnaire",
            )
        if "pending approval" in reason_low or "admin approval" in reason_low:
            return meta(
                "JOIN_PENDING_APPROVAL",
                "APPROVAL",
                "Yêu cầu tham gia đang chờ duyệt",
                False,
                "wait_approval",
            )
        if "could not complete join flow" in reason_low or "join_group failed" in reason_low:
            return meta(
                "JOIN_FLOW_INCOMPLETE",
                "JOIN",
                "Không thể hoàn tất luồng tham gia nhóm",
                True,
                "join_group",
            )
        return meta(
            "JOIN_GROUP_FAILED",
            "JOIN",
            reason_text or "Tham gia nhóm thất bại",
            True,
            "join_group",
        )

    if "fb không lên foreground" in reason_low or "foreground" in reason_low:
        return meta(
            "FB_FOREGROUND_FAILED",
            "APP",
            "Không đưa được Facebook lên foreground",
            True,
            "open_facebook",
        )

    if (
        "pending_approval" in reason_low
        or "waiting for admin approval" in reason_low
        or "admin approval" in reason_low
        or "chờ admin duyệt" in reason_low
        or "cho admin duyet" in reason_low
    ):
        return meta(
            "JOIN_PENDING_APPROVAL",
            "APPROVAL",
            "Yêu cầu tham gia đang chờ duyệt",
            False,
            "wait_approval",
        )

    if "chưa là thành viên" in reason_low or "not a member" in reason_low:
        return meta(
            "GROUP_NOT_MEMBER",
            "MEMBERSHIP",
            "Tài khoản chưa là thành viên nhóm",
            False,
            "verify_membership",
        )

    if "không tìm thấy nút mở ô soạn bài" in reason_low or "không thấy ô soạn bài" in reason_low:
        return meta(
            "COMPOSER_NOT_FOUND",
            "UI",
            "Không tìm thấy nút mở ô soạn bài",
            True,
            "open_composer",
        )

    if "click không được" in reason_low and ("composer" in reason_low or "bạn viết gì đi" in reason_low):
        return meta(
            "COMPOSER_OPEN_FAILED",
            "UI",
            "Không mở được ô soạn bài",
            True,
            "open_composer",
        )

    if "không mở được màn soạn bài" in reason_low:
        return meta(
            "COMPOSER_SCREEN_FAILED",
            "UI",
            "Không mở được màn soạn bài",
            True,
            "open_composer",
        )

    if "không nhập được nội dung" in reason_low:
        return meta(
            "CONTENT_INPUT_FAILED",
            "CONTENT",
            "Không nhập được nội dung bài viết",
            True,
            "input_content",
        )

    if "không thấy nút ảnh/video" in reason_low:
        return meta(
            "MEDIA_BUTTON_NOT_FOUND",
            "MEDIA",
            "Không tìm thấy nút Ảnh/Video",
            True,
            "attach_media",
        )

    if "nút đăng chưa sáng" in reason_low:
        return meta(
            "POST_BUTTON_DISABLED",
            "PUBLISH",
            "Nút đăng chưa sẵn sàng",
            True,
            "submit_post",
        )

    if "không tìm thấy nút đăng" in reason_low:
        return meta(
            "POST_BUTTON_NOT_FOUND",
            "PUBLISH",
            "Không tìm thấy nút đăng",
            True,
            "submit_post",
        )

    if "không mở được màn 'nội dung của bạn'" in reason_low or "không mở được màn 'nội dung" in reason_low:
        return meta(
            "CONTENT_SCREEN_OPEN_FAILED",
            "UI",
            "Không mở được màn quản lý nội dung bài viết",
            True,
            "open_post_status",
        )

    if "lỗi detect theo content" in reason_low:
        return meta(
            "POST_STATUS_DETECT_FAILED",
            "STATUS",
            "Không xác định được trạng thái bài viết",
            True,
            "detect_post_status",
        )

    if "không được duyệt" in reason_low or post_status_low in ("rejected", "removed", "unknown"):
        return meta(
            "POST_REJECTED",
            "REVIEW",
            "Bài viết không được duyệt hoặc đã bị gỡ",
            False,
            "detect_post_status",
        )

    if "link trả về không hợp lệ" in reason_low:
        return meta(
            "POST_LINK_INVALID",
            "LINK",
            "Link bài viết trả về không hợp lệ",
            True,
            "copy_post_link",
        )

    if "không lấy được link bài viết" in reason_low:
        return meta(
            "POST_LINK_NOT_FOUND",
            "LINK",
            "Không lấy được link bài viết sau khi đăng",
            True,
            "copy_post_link",
        )

    return meta(
        "UNKNOWN_ERROR",
        "SYSTEM",
        reason_text or "Lỗi không xác định",
        True,
        "unknown",
    )


def _attach_rabbit_error_meta(
    payload: dict,
    *,
    action: str,
    ok: bool,
    reason: str | None = None,
    join_status: str | None = None,
    post_status: str | None = None,
) -> dict:
    meta = _build_rabbit_error_meta(
        action,
        ok=ok,
        reason=reason,
        join_status=join_status,
        post_status=post_status,
    )
    payload.update(meta)
    payload["error"] = None if meta["error_code"] is None else dict(meta)

    for key in ("result", "data"):
        if isinstance(payload.get(key), dict):
            payload[key].setdefault("error_code", meta["error_code"])
            payload[key].setdefault("error_group", meta["error_group"])
            payload[key].setdefault("error_message", meta["error_message"])
            payload[key].setdefault("retryable", meta["retryable"])
            payload[key].setdefault("step", meta["step"])

    return payload


def _rabbit_result_type(action: str) -> str:
    return {
        "post_wall": "post_to_wall",
        "post_group": "post_to_group",
    }.get(action, action)


def _extract_post_status_from_reason(reason: str | None) -> str:
    match = re.search(r"status\s*=\s*([a-z_]+)", str(reason or ""), flags=re.IGNORECASE)
    return match.group(1).strip().lower() if match else ""


def _post_status_from_result_or_reason(post_result, reason: str | None = None) -> str:
    if isinstance(post_result, dict):
        post_status = str(post_result.get("post_status") or "").strip().lower()
        if post_status:
            return post_status
    return _extract_post_status_from_reason(reason)


def _post_group_should_rotate_account(ok: bool, post_result=None, reason: str | None = None) -> bool:
    if ok:
        return True
    return _post_status_from_result_or_reason(post_result, reason) in POST_GROUP_NOT_APPROVED_STATUSES


def _build_post_group_failure_result(
    *,
    reason: str | None,
    post_link: str | None,
    group_link: str | None,
    content: str,
    user_id: str | None,
):
    post_status = _extract_post_status_from_reason(reason)
    if post_status not in POST_GROUP_NOT_APPROVED_STATUSES:
        return None
    return {
        "ok": False,
        "reason": reason or "",
        "post_link": post_link,
        "post_status": post_status,
        "group_link": group_link,
        "content": content or "",
        "user_id": user_id,
    }


def _build_rabbit_result_payload(
    *,
    job_id: str,
    driver,
    action: str,
    ok: bool,
    reason: str | None,
    user_id: str | None,
    user_name: str | None,
    content: str,
    files,
    group_link: str | None,
    post_link: str | None,
    post_result: dict | None = None,
    join_result=None,
) -> dict:
    join_status = getattr(join_result, "status", None)
    post_status = post_result.get("post_status") if isinstance(post_result, dict) else None
    result_data = dict(post_result) if isinstance(post_result, dict) else {
        "ok": bool(ok),
        "reason": reason or "",
    }
    result_data.setdefault("group_link", group_link)
    result_data.setdefault("post_link", post_link)
    result_data.setdefault("post_status", post_status)
    result_data.setdefault("join_status", join_status)
    result_data.setdefault("user_id", user_id)

    payload = {
        "command_id": job_id,
        "device_id": getattr(driver, "serial", ""),
        "type": _rabbit_result_type(action),
        "ok": bool(ok),
        "status": "SUCCESS" if ok else "FAILED",
        "reason": reason or "",
        "user_id": user_id,
        "user_name": user_name,
        "content": content,
        "files": files,
        "group_link": group_link,
        "post_link": post_link,
        "post_status": post_status,
        "join_status": join_status,
        "result": result_data,
        "data": dict(result_data),
    }
    return _attach_rabbit_error_meta(
        payload,
        action=action,
        ok=ok,
        reason=reason,
        join_status=join_status,
        post_status=post_status,
    )


def _load_device_account_with_fallback(device_id: str) -> dict:
    """
    Ưu tiên lấy cấu hình device từ API. Nếu API lỗi/rỗng thì fallback file local.
    """
    try:
        device = load_device_account(device_id)
        if isinstance(device, dict) and device:
            return device
    except Exception as e:
        log_message(
            f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] load_device_account lỗi: {type(e).__name__} | {e}",
            logging.WARNING,
        )

    return {}

def _extract_crm_id_from_device(device: dict | None) -> str:
    if not isinstance(device, dict):
        return ""

    user = device.get("user")
    if not isinstance(user, dict):
        return ""

    return str(user.get("crm_id") or "").strip()


def _find_username_by_account(device: dict | None, account: str | None) -> str:
    if not isinstance(device, dict):
        return ""

    target_account = _normalize_account_value(account)
    if not target_account:
        return ""

    accounts = device.get("accounts")
    if not isinstance(accounts, list):
        return ""

    for item in accounts:
        if not isinstance(item, dict):
            continue
        if _normalize_account_value(item.get("account")) != target_account:
            continue
        username = _normalize_username_value(item.get("name"))
        if username:
            return username

    return ""


def _normalize_name_for_lookup(value: str | None) -> str:
    value = _normalize_username_value(value) or _normalize_account_value(value)
    value = value.split(",")[0].replace("_", " ").strip().lower()
    if not value:
        return ""
    value = unicodedata.normalize("NFD", value)
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    value = re.sub(r"[^0-9a-zA-Z]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _names_match_for_lookup(left: str | None, right: str | None) -> bool:
    left_norm = _normalize_name_for_lookup(left)
    right_norm = _normalize_name_for_lookup(right)
    if not left_norm or not right_norm:
        return False
    if left_norm == right_norm:
        return True

    left_tokens = left_norm.split()
    right_tokens = right_norm.split()
    if len(left_tokens) >= 2 and len(left_tokens) == len(right_tokens):
        return sorted(left_tokens) == sorted(right_tokens)
    return False


def _find_account_candidate_from_device(
    device: dict | None,
    account: str | None = None,
    name: str | None = None,
) -> dict:
    if not isinstance(device, dict):
        return {}

    accounts = device.get("accounts")
    if not isinstance(accounts, list):
        return {}

    target_account = _normalize_account_value(account)
    target_name = _normalize_username_value(name) or _normalize_account_value(name)

    for item in accounts:
        if not isinstance(item, dict):
            continue
        item_account = _normalize_account_value(item.get("account"))
        if target_account and item_account == target_account:
            return {
                "account": item_account,
                "name": _normalize_username_value(item.get("name")) or _normalize_account_value(item.get("name")) or item_account,
                "password": str(item.get("password") or ""),
            }

    if target_name:
        for item in accounts:
            if not isinstance(item, dict):
                continue
            item_account = _normalize_account_value(item.get("account"))
            item_name = _normalize_username_value(item.get("name")) or _normalize_account_value(item.get("name")) or item_account
            if _names_match_for_lookup(item_name, target_name) or _names_match_for_lookup(item_account, target_name):
                return {
                    "account": item_account,
                    "name": item_name,
                    "password": str(item.get("password") or ""),
                }

    return {}


async def _mark_facebook_account_crash(
    device_id: str,
    account: str | None = None,
    username: str | None = None,
    reason: str | None = None,
) -> bool:
    account = _normalize_account_value(account)
    username = _normalize_username_value(username) or _normalize_account_value(username)
    if not device_id or (not account and not username):
        return False

    try:
        await pymongo_management.update_statusFB(statusFB="Offline", device_id=device_id)
    except Exception as e:
        log_message(
            f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] update_statusFB Offline before Crash loi: {type(e).__name__} | {e}",
            logging.WARNING,
        )

    try:
        update_status = await pymongo_management.update_statusFB(
            username=username or account,
            account=account or None,
            statusFB="Crash",
            device_id=device_id,
        )
        log_message(
            (
                f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] Mark account Crash "
                f"account={account or ''} username={username or ''} "
                f"reason={reason or ''} result={update_status}"
            ),
            logging.ERROR,
        )
        return True
    except Exception as e:
        log_message(
            f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] update_statusFB Crash loi: {type(e).__name__} | {e}\n{traceback.format_exc()}",
            logging.ERROR,
        )
        return False


async def _facebook_home_visible(driver, max_retries: int = 4) -> bool:
    return bool(await my_find_element(
        driver,
        {
            ("xpath", '//*[contains(@content-desc,"Trang chủ") and contains(@content-desc,"Tab")]'),
            ("xpath", '//*[contains(@content-desc,"Home") and contains(@content-desc,"Tab")]'),
        },
        max_retries,
        back_if_not_found=False,
    ))


def _find_account_by_username_in_device(device: dict | None, username: str | None) -> str:
    target = _normalize_username_value(username)
    if not target or not isinstance(device, dict):
        return ""

    def _name_key(value: str) -> str:
        normalized = _common_normalize_text(value, remove_accents=True)
        normalized = normalized.replace("đ", "d").replace("Đ", "d")
        return re.sub(r"\s+", " ", normalized).strip().casefold()

    target_norm = _name_key(target)
    accounts = device.get("accounts")
    if not isinstance(accounts, list):
        return ""

    for item in accounts:
        if not isinstance(item, dict):
            continue
        account = _normalize_account_value(item.get("account"))
        name = _normalize_username_value(item.get("name"))
        if not account or not name:
            continue
        if name == target or _name_key(name) == target_norm:
            return account

    return ""


def _load_cached_active_account_payload(device_id: str) -> dict:
    """Read the Messenger profile-detection cache if it belongs to this device."""
    candidates = [
        Path(os.getenv("FB_1_1_ACTIVE_ACCOUNT_FILE", "active_facebook_account.json")),
        Path(__file__).resolve().parent / "active_facebook_account.json",
    ]
    for path in candidates:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        cached_device_id = _normalize_account_value(payload.get("device_id"))
        if cached_device_id and cached_device_id != _normalize_account_value(device_id):
            continue
        return payload
    return {}


def _get_cached_current_account_identity(device_id: str, device: dict | None) -> tuple[str, str]:
    """Return (account, username) from runtime/cache without visiting profile.

    This prevents the startup flow from opening the Facebook profile twice: the
    Messenger priority flow already performs a profile-based detection and saves
    the result into runtime state/cache.
    """
    account = ""
    username = ""

    try:
        state = load_state(device_id)
    except Exception:
        state = {}
    if isinstance(state, dict):
        account = _normalize_account_value(state.get("current_account"))
        username = _normalize_username_value(state.get("current_username"))

    if not account:
        try:
            account = _normalize_account_value(get_current_account(device_id))
        except Exception:
            account = ""

    if not username and account:
        username = _find_username_by_account(device, account)

    if not account or not username:
        payload = _load_cached_active_account_payload(device_id)
        if payload:
            account = account or _normalize_account_value(payload.get("user_id_chat") or payload.get("account"))
            username = username or _normalize_username_value(
                payload.get("displayName") or payload.get("username") or payload.get("name")
            )

    if account == "default":
        account = ""
    return account, username


def _account_candidates_from_device(device: dict | None) -> list[dict]:
    if not isinstance(device, dict):
        return []

    raw_accounts = device.get("accounts")
    if not isinstance(raw_accounts, list):
        return []

    accounts = []
    seen = set()
    for item in raw_accounts:
        if not isinstance(item, dict):
            continue
        acc = _normalize_account_value(item.get("account"))
        if not acc or acc in seen:
            continue
        seen.add(acc)
        accounts.append({
            "account": acc,
            "name": _normalize_account_value(item.get("name")) or acc,
            "password": str(item.get("password") or ""),
        })
    return accounts


def _ordered_account_candidates(accounts: list[dict], current_account: str | None) -> list[dict]:
    current = _normalize_account_value(current_account)
    if not accounts:
        return []

    current_index = -1
    if current:
        for idx, item in enumerate(accounts):
            if item.get("account") == current:
                current_index = idx
                break

    if current_index < 0:
        ordered = list(accounts)
    else:
        ordered = accounts[current_index + 1:] + accounts[:current_index]

    if current:
        ordered = [item for item in ordered if item.get("account") != current]
    return ordered


def _today_vietnam_bounds():
    vietnam_tz = timezone(timedelta(hours=7))
    now_vn = datetime.now(vietnam_tz)
    start_vn = now_vn.replace(hour=0, minute=0, second=0, microsecond=0)
    end_vn = start_vn + timedelta(days=1)
    start_utc = start_vn.astimezone(timezone.utc).replace(tzinfo=None)
    end_utc = end_vn.astimezone(timezone.utc).replace(tzinfo=None)
    return start_utc, end_utc, int(start_vn.timestamp()), int(end_vn.timestamp())


async def _count_group_posts_today_by_account(account_ids: list[str]) -> dict[str, int]:
    account_ids = [str(x).strip() for x in account_ids if str(x).strip()]
    if not account_ids:
        return {}

    start_utc, end_utc, start_epoch, end_epoch = _today_vietnam_bounds()
    posted_statuses = [
        "Đã đăng thành công",
        "Đang chờ duyệt",
        "Đã đăng - Lấy link bài viết lỗi",
        "Đã Đăng - đang kiểm tra kết quả",
        "Bài viết không được duyệt",
    ]
    col = pymongo_management.get_async_collection("Bai-dang")
    pipeline = [
        {
            "$match": {
                "user_id": {"$in": account_ids},
                "status": {"$in": posted_statuses},
                "$or": [
                    {"posted_at": {"$gte": start_utc, "$lt": end_utc}},
                    {"updated_at": {"$gte": start_utc, "$lt": end_utc}},
                    {"time": {"$gte": start_epoch, "$lt": end_epoch}},
                ],
            }
        },
        {"$group": {"_id": "$user_id", "count": {"$sum": 1}}},
    ]
    rows = await col.aggregate(pipeline).to_list(length=None)
    return {str(row.get("_id")): int(row.get("count") or 0) for row in rows}


async def _pick_next_account_prioritize_no_post_today(
    device: dict | None,
    current_account: str | None,
    device_id: str | None = None,
) -> tuple[str | None, str | None]:
    if not AUTO_SWITCH_PRIORITIZE_NO_POST_TODAY:
        return _pick_next_account_from_device(device, current_account)

    accounts = _account_candidates_from_device(device)
    if len(accounts) < 2:
        return None, None

    current = _normalize_account_value(current_account) or _normalize_account_value((device or {}).get("current_account"))
    ordered = _ordered_account_candidates(accounts, current)
    if not ordered:
        return None, None

    try:
        counts = await _count_group_posts_today_by_account([item["account"] for item in accounts])
    except Exception as e:
        log_message(
            f"[{DEVICE_LIST_NAME.get(device_id or '', device_id or '')}] Không đếm được Bai-dang hôm nay để ưu tiên account: {type(e).__name__} | {e}",
            logging.WARNING,
        )
        return _pick_next_account_from_device(device, current)

    best = min(ordered, key=lambda item: counts.get(item["account"], 0))
    try:
        summary = ", ".join(f"{item['account']}={counts.get(item['account'], 0)}" for item in accounts)
        log_message(
            f"[{DEVICE_LIST_NAME.get(device_id or '', device_id or '')}] Ưu tiên switch account theo số bài Bai-dang hôm nay: {summary} -> chọn {best['account']}",
            logging.INFO,
        )
    except Exception:
        pass
    return best["account"], best["name"]


async def _pick_lowest_post_account_today(
    device: dict | None,
    current_account: str | None,
    device_id: str | None = None,
) -> tuple[str | None, str | None, dict[str, int]]:
    accounts = _account_candidates_from_device(device)
    if not accounts:
        return None, None, {}

    current = _normalize_account_value(current_account) or _normalize_account_value((device or {}).get("current_account"))
    try:
        counts = await _count_group_posts_today_by_account([item["account"] for item in accounts])
    except Exception as e:
        log_message(
            f"[{DEVICE_LIST_NAME.get(device_id or '', device_id or '')}] Startup account rebalance: cannot count Bai-dang today: {type(e).__name__} | {e}",
            logging.WARNING,
        )
        return None, None, {}

    min_count = min(counts.get(item["account"], 0) for item in accounts)
    tied = [item for item in accounts if counts.get(item["account"], 0) == min_count]
    best = None
    if current:
        best = next((item for item in tied if item["account"] == current), None)
    if best is None:
        best = tied[0] if tied else accounts[0]

    try:
        summary = ", ".join(f"{item['account']}={counts.get(item['account'], 0)}" for item in accounts)
        log_message(
            f"[{DEVICE_LIST_NAME.get(device_id or '', device_id or '')}] Startup account rebalance counts: {summary} -> selected {best['account']}",
            logging.INFO,
        )
    except Exception:
        pass

    return best["account"], best["name"], counts


def _pick_next_account_from_device(device: dict | None, current_account: str | None) -> tuple[str | None, str | None]:
    """
    Lấy account kế tiếp theo thứ tự trong device['accounts'] và bỏ qua chính account hiện tại.
    """
    accounts = _account_candidates_from_device(device)
    if len(accounts) < 2:
        return None, None

    current = _normalize_account_value(current_account) or _normalize_account_value(device.get("current_account"))
    if not current:
        first = accounts[0]
        return first["account"], first["name"]

    current_index = -1
    for idx, item in enumerate(accounts):
        if item["account"] == current:
            current_index = idx
            break

    if current_index < 0:
        for item in accounts:
            if item["account"] != current:
                return item["account"], item["name"]
        return None, None

    total = len(accounts)
    for step in range(1, total + 1):
        item = accounts[(current_index + step) % total]
        if item["account"] != current:
            return item["account"], item["name"]

    return None, None


def _account_names_in_priority_order(
    accounts: list[dict],
    preferred_account: str | None = None,
    preferred_name: str | None = None,
) -> list[str]:
    names = []

    preferred_account = _normalize_account_value(preferred_account)
    preferred_name = _normalize_username_value(preferred_name) or _normalize_account_value(preferred_name)
    if preferred_name:
        names.append(preferred_name)

    if preferred_account:
        for item in accounts:
            if _normalize_account_value(item.get("account")) != preferred_account:
                continue
            name = _normalize_username_value(item.get("name")) or _normalize_account_value(item.get("name"))
            if name and name not in names:
                names.append(name)
            break

    for item in accounts:
        name = _normalize_username_value(item.get("name")) or _normalize_account_value(item.get("name"))
        if name and name not in names:
            names.append(name)

    return names


async def _select_normal_account_from_login_picker(driver, device: dict | None) -> bool | None:
    """
    Chon mot account dang co trong base neu Facebook dang o man login picker.
    Return None neu khong phai login picker.
    """
    device_id = getattr(driver, "serial", "") or ""
    if not await is_facebook_account_picker_visible(driver):
        return None

    accounts = _account_candidates_from_device(device)
    if not accounts:
        log_message(
            f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] Facebook login picker dang hien nhung base khong co account de chon",
            logging.WARNING,
        )
        return False

    current_account = ""
    try:
        current_account = _normalize_account_value(get_current_account(device_id))
    except Exception:
        current_account = ""
    if not current_account and isinstance(device, dict):
        current_account = _normalize_account_value(device.get("current_account"))

    preferred_account, preferred_name, _ = await _pick_lowest_post_account_today(
        device,
        current_account,
        device_id=device_id,
    )

    names = _account_names_in_priority_order(
        accounts,
        preferred_account=preferred_account,
        preferred_name=preferred_name,
    )
    selected_name = await select_facebook_account_from_picker(driver, names, max_scrolls=4)
    if selected_name is None:
        return None

    if selected_name:
        selected_account = _find_account_candidate_from_device(
            device,
            account=selected_name,
            name=selected_name,
        )
        password_result = await submit_facebook_password_if_prompted(
            driver,
            (selected_account or {}).get("password") or "",
            account_name=selected_name,
            timeout=6.0,
        )
        if password_result is False:
            await _mark_facebook_account_crash(
                device_id,
                account=(selected_account or {}).get("account") or "",
                username=(selected_account or {}).get("name") or selected_name,
                reason="password_prompt_submit_failed",
            )
            return False
        if password_result is True and not await wait_logged_in(driver, timeout=20.0, poll=1.0):
            await _mark_facebook_account_crash(
                device_id,
                account=(selected_account or {}).get("account") or "",
                username=(selected_account or {}).get("name") or selected_name,
                reason="password_prompt_submitted_but_not_logged_in",
            )
            return False

        log_message(
            f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] Login picker: da chon account '{selected_name}' de online binh thuong",
            logging.INFO,
        )
        return True

    log_message(
        f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] Login picker: khong tim thay account nao trong base sau khi luot",
        logging.WARNING,
    )
    return False


async def maybe_auto_switch_account_after_natural_task(driver, current_account: str | None) -> bool:
    """
    After one fb_natural_task cycle finishes, switch to the next account on the same device.
    """
    if not ALLOW_AUTO_SWITCH:
        return False

    device_id = getattr(driver, "serial", "") or ""
    if not device_id:
        return False

    current_account = _normalize_account_value(current_account)
    if not current_account:
        try:
            current_account = _normalize_account_value(get_current_account(device_id))
        except Exception:
            current_account = ""

    try:
        device = await asyncio.to_thread(_load_device_account_with_fallback, device_id)
    except Exception as e:
        log_message(
            f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] Cannot load device accounts for auto switch after natural task: {type(e).__name__} | {e}",
            logging.WARNING,
        )
        return False

    target_account, target_name = await _pick_next_account_prioritize_no_post_today(
        device,
        current_account,
        device_id=device_id,
    )
    if not target_account:
        log_message(
            f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] Skip auto switch after natural task: no valid next account",
            logging.INFO,
        )
        return False

    if target_account == current_account:
        log_message(
            f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] Skip auto switch after natural task: next account equals current account ({current_account})",
            logging.INFO,
        )
        return False

    log_message(
        f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] Auto switch after natural task: {current_account} -> {target_account} ({target_name})",
        logging.INFO,
    )

    ok = await ensure_account_for_user(driver, target_account, target_name)
    if ok:
        log_message(
            f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] Auto switch after natural task succeeded -> {target_account}",
            logging.INFO,
        )
        await run_messenger_priority_after_account_switch(
            driver,
            target_account,
            reason="auto switch after natural task",
        )
    else:
        log_message(
            f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] Auto switch after natural task failed -> {target_account}",
            logging.WARNING,
        )
    return bool(ok)


async def maybe_auto_switch_account_after_post_group(driver, current_account: str | None) -> bool:
    """
    Sau khi post_group thành công và device đang rảnh, đổi sang account kế tiếp trên cùng device.
    """
    if not AUTO_SWITCH_AFTER_POST_GROUP_WHEN_IDLE:
        return False

    device_id = getattr(driver, "serial", "") or ""
    if not device_id:
        return False

    current_account = _normalize_account_value(current_account)
    if not current_account:
        try:
            current_account = _normalize_account_value(get_current_account(device_id))
        except Exception:
            current_account = ""

    try:
        device = await asyncio.to_thread(load_device_account, device_id)
    except Exception as e:
        log_message(
            f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] Không tải được danh sách account để auto switch: {type(e).__name__} | {e}",
            logging.WARNING,
        )
        return False

    target_account, target_name = await _pick_next_account_prioritize_no_post_today(
        device,
        current_account,
        device_id=device_id,
    )
    if not target_account:
        log_message(
            f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] Bỏ qua auto switch sau post_group: không tìm được account kế tiếp hợp lệ",
            logging.INFO,
        )
        return False

    if target_account == current_account:
        log_message(
            f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] Bỏ qua auto switch sau post_group: account kế tiếp trùng account hiện tại ({current_account})",
            logging.INFO,
        )
        return False

    log_message(
        f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] Device đã rảnh sau post_group -> chuyển account {current_account} -> {target_account} ({target_name})",
        logging.INFO,
    )

    ok = await ensure_account_for_user(driver, target_account, target_name)
    if ok:
        log_message(
            f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] Auto switch sau post_group thành công -> {target_account}",
            logging.INFO,
        )
    else:
        log_message(
            f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] Auto switch sau post_group thất bại -> {target_account}",
            logging.WARNING,
        )
    if ok:
        await run_messenger_priority_after_account_switch(
            driver,
            target_account,
            reason="auto switch after post_group",
        )
    return bool(ok)


async def maybe_switch_to_lowest_post_account_on_startup(
    driver,
    device: dict | None,
    current_account: str | None,
    current_username: str | None = None,
) -> tuple[str | None, str | None, bool]:
    if not STARTUP_SWITCH_TO_LOWEST_POST_TODAY:
        return current_account, current_username, False

    device_id = getattr(driver, "serial", "") or ""
    if not device_id:
        return current_account, current_username, False

    current_account = _normalize_account_value(current_account)
    current_username = _normalize_username_value(current_username) or _normalize_account_value(current_username)

    if not isinstance(device, dict) or not device:
        try:
            device = await asyncio.to_thread(_load_device_account_with_fallback, device_id)
        except Exception as e:
            log_message(
                f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] Startup account rebalance: cannot load device accounts: {type(e).__name__} | {e}",
                logging.WARNING,
            )
            return current_account, current_username, False

    target_account, target_name, counts = await _pick_lowest_post_account_today(
        device,
        current_account,
        device_id=device_id,
    )
    if not target_account:
        log_message(
            f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] Startup account rebalance skipped: no account candidate/count data",
            logging.INFO,
        )
        return current_account, current_username, False

    current_count = counts.get(current_account, 0) if current_account else None
    target_count = counts.get(target_account, 0)
    if current_account and target_account == current_account:
        log_message(
            f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] Startup account rebalance: current account already has lowest Bai-dang count today ({current_account}={target_count})",
            logging.INFO,
        )
        return current_account, current_username or target_name, False

    log_message(
        f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] Startup account rebalance: switch {current_account or 'unknown'}({current_count}) -> {target_account}({target_count})",
        logging.INFO,
    )
    ok = await ensure_account_for_user(driver, target_account, target_name)
    if ok:
        return target_account, target_name, True

    log_message(
        f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] Startup account rebalance failed -> keep {current_account or 'unknown'}",
        logging.WARNING,
    )
    return current_account, current_username, False


async def ensure_account_for_user(driver, target_account: str, target_name: str | None = None) -> bool:
    """
    Local runtime_state (device_{id}.json):
    - chỉ lưu account hiện đang online trên device
    - khi switch thành công thì cập nhật current_account/current_username (GIỮ NGUYÊN status/device_id)
    """
    try:
        # normalize input
        if not target_account:
            return True

        device_id = getattr(driver, "serial", None) or ""
        if not device_id:
            log_message("[ensure_account_for_user] driver.serial is empty", level="ERROR")
            return False

        target_account = str(target_account).strip()
        if not target_account:
            return True

        ENSURE_ACCOUNT_LAST_REASON.pop(device_id, None)

        try:
            device = await asyncio.to_thread(_load_device_account_with_fallback, device_id)
        except Exception:
            device = {}

        resolved_name_from_base = _find_username_by_account(device, target_account)
        target_username = (target_name or resolved_name_from_base or target_account).strip()
        if target_username == target_account and resolved_name_from_base:
            target_username = resolved_name_from_base

        picker_names = []
        for item in (target_name, resolved_name_from_base, target_username):
            item = _normalize_username_value(item) or _normalize_account_value(item)
            if item and item not in picker_names:
                picker_names.append(item)

        picker_result = await select_facebook_account_from_picker(
            driver,
            picker_names,
            max_scrolls=4,
        )
        if picker_result is False:
            ENSURE_ACCOUNT_LAST_REASON[device_id] = ACCOUNT_MANUAL_LOGIN_REASON
            log_message(
                f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] {ACCOUNT_MANUAL_LOGIN_REASON}: khong thay account {target_account}/{target_username} tren login picker",
                logging.ERROR,
            )
            try:
                log_note_acc(driver, target_account, ACCOUNT_MANUAL_LOGIN_REASON)
            except Exception:
                pass
            return False
        if picker_result:
            target_username = str(picker_result).strip() or target_username
            selected_account = _find_account_candidate_from_device(
                device,
                account=target_account,
                name=target_username,
            )
            password_result = await submit_facebook_password_if_prompted(
                driver,
                (selected_account or {}).get("password") or "",
                account_name=target_username,
                timeout=6.0,
            )
            if password_result is False:
                await _mark_facebook_account_crash(
                    device_id,
                    account=(selected_account or {}).get("account") or target_account,
                    username=(selected_account or {}).get("name") or target_username,
                    reason="ensure_account_password_prompt_submit_failed",
                )
                ENSURE_ACCOUNT_LAST_REASON[device_id] = ACCOUNT_MANUAL_LOGIN_REASON
                log_message(
                    f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] {ACCOUNT_MANUAL_LOGIN_REASON}: khong nhap duoc mat khau cho account {target_account}/{target_username}",
                    logging.ERROR,
                )
                try:
                    log_note_acc(driver, target_account, ACCOUNT_MANUAL_LOGIN_REASON)
                except Exception:
                    pass
                return False

            if await wait_logged_in(driver, timeout=20.0):
                _persist_current_account(device_id, target_account, target_username)
                await sync_device_accounts_status(
                    device_id=device_id,
                    current_account=target_account,
                    current_username=target_username,
                )
                return True

            await _mark_facebook_account_crash(
                device_id,
                account=(selected_account or {}).get("account") or target_account,
                username=(selected_account or {}).get("name") or target_username,
                reason="ensure_account_picker_selected_but_not_logged_in",
            )
            ENSURE_ACCOUNT_LAST_REASON[device_id] = ACCOUNT_MANUAL_LOGIN_REASON
            log_message(
                f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] {ACCOUNT_MANUAL_LOGIN_REASON}: da click account nhung Facebook chua vao home",
                logging.ERROR,
            )
            return False

        # 1) nếu đã đúng account rồi thì chỉ "touch" updated_at (optional nhưng hữu ích)
        current = None
        try:
            current = get_current_account(device_id)
        except Exception:
            current = None

        if current and str(current).strip() == target_account:
            _persist_current_account(device_id, target_account, target_username)
            await sync_device_accounts_status(
                device_id=device_id,
                current_account=target_account,
                current_username=target_username,
            )
            return True

        # 2) đổi account qua UI
        await go_to_home_page(driver)

        # tạo acc dict “an toàn” (thêm alias keys để tránh KeyError trong swap_account_new)
        target_account_doc = _find_account_candidate_from_device(
            device,
            account=target_account,
            name=target_username,
        )
        acc_for_swap = {
            "account": target_account,
            "name": target_username,
            "password": (target_account_doc or {}).get("password") or "",
            # alias keys (nếu swap_account_new dùng tên khác)
            "user_id": target_account,
            "username": target_username,
        }

        ok = await swap_account_new(driver, acc_for_swap)
        if not ok:
            return False

        # 3) swap OK -> cập nhật local runtime
        _persist_current_account(device_id, target_account, target_username)

        # 4) đồng bộ toàn bộ account status cho device hiện tại
        await sync_device_accounts_status(
            device_id=device_id,
            current_account=target_account,
            current_username=target_username,
        )

        return True

    except Exception as e:
        dev_name = None
        try:
            dev_name = DEVICE_LIST_NAME.get(driver.serial)
        except Exception:
            dev_name = None
        prefix = f"[{dev_name or getattr(driver, 'serial', '?')}]"
        log_message(f"{prefix} ensure_account_for_user lỗi: {e}\n{traceback.format_exc()}", logging.ERROR)
        return False

async def pump_mongo_pending_to_json(driver, limit: int = 50) -> int:
    if not ENABLE_MONGO_COMMAND_SCAN:
        return 0

    device = load_device_account(driver.serial)
    allowed_user_ids = []
    if device and isinstance(device.get("accounts"), list):
        allowed_user_ids = [acc.get("account") for acc in device["accounts"] if acc.get("account")]

    link_retry_docs = await pymongo_management.claim_failed_group_post_link_commands_to_json(
        limit=min(limit, 10),
        queued_by=driver.serial,
        allowed_user_ids=allowed_user_ids,
        device_id=driver.serial,
        min_age_minutes=15,
        max_age_minutes=20,
    )

    remaining_limit = max(0, limit - len(link_retry_docs))
    today_missing_link_docs = await pymongo_management.claim_today_success_group_posts_missing_link_to_json(
        limit=min(remaining_limit, 10),
        queued_by=driver.serial,
        allowed_user_ids=allowed_user_ids,
        device_id=driver.serial,
        max_retry=5,
        stuck_minutes=10,
    ) if remaining_limit else []
    if today_missing_link_docs:
        log_message(
            (
                f"[{DEVICE_LIST_NAME.get(driver.serial, driver.serial)}] "
                f"POST_LINK_MISSING_SCAN claimed {len(today_missing_link_docs)} command(s) từ Bai-dang hôm nay thiếu link"
            ),
            logging.INFO,
        )

    remaining_limit = max(0, remaining_limit - len(today_missing_link_docs))
    post_error_retry_docs = await pymongo_management.claim_retryable_group_post_errors_to_json(
        limit=min(remaining_limit, 10),
        queued_by=driver.serial,
        allowed_user_ids=allowed_user_ids,
        device_id=driver.serial,
        max_retry=10,
        stuck_minutes=10,
        lookback_minutes=30,
    ) if remaining_limit else []
    if post_error_retry_docs:
        log_message(
            (
                f"[{DEVICE_LIST_NAME.get(driver.serial, driver.serial)}] "
                f"POST_ERROR_RETRY claimed {len(post_error_retry_docs)} command(s) từ Bai-dang lỗi"
            ),
            logging.INFO,
        )

    remaining_limit = max(0, remaining_limit - len(post_error_retry_docs))
    command_error_retry_docs = await pymongo_management.claim_retryable_command_errors_to_json(
        limit=min(remaining_limit, 10),
        queued_by=driver.serial,
        allowed_user_ids=allowed_user_ids,
        device_id=driver.serial,
        max_retry=10,
        lookback_minutes=24 * 60,
    ) if remaining_limit else []
    if command_error_retry_docs:
        log_message(
            (
                f"[{DEVICE_LIST_NAME.get(driver.serial, driver.serial)}] "
                f"COMMAND_ERROR_RETRY claimed {len(command_error_retry_docs)} retryable command error(s)"
            ),
            logging.INFO,
        )

    remaining_limit = max(0, remaining_limit - len(command_error_retry_docs))
    docs = await pymongo_management.claim_pending_commands_to_json(
        limit=remaining_limit,
        queued_by=driver.serial,
        allowed_user_ids=allowed_user_ids,
        max_retry=10,
        stuck_minutes=10,
        device_id=driver.serial,
    ) if remaining_limit else []

    docs = (
        list(link_retry_docs)
        + list(today_missing_link_docs)
        + list(post_error_retry_docs)
        + list(command_error_retry_docs)
        + list(docs)
    )
    if not docs:
        return 0

    pushed = 0
    for cmd in docs:
        job = pymongo_management.command_doc_to_job(cmd)
        urgent_queue.enqueue_urgent_task(job)
        pushed += 1
    return pushed

async def scheduler_tick_json_to_device_queue(driver) -> bool:
    """
    Lấy 1 job 'đến hạn' từ urgent_queue.json -> đưa vào queue RAM của device -> set event.
    Chỉ lấy job thuộc các account trên device này.
    """
    if not ENABLE_MONGO_COMMAND_SCAN:
        return False

    device = load_device_account(driver.serial)
    allowed_user_ids = []
    if device and isinstance(device.get("accounts"), list):
        allowed_user_ids = [acc.get("account") for acc in device["accounts"] if acc.get("account")]

    task = urgent_queue.get_next_task_if_due(allowed_user_ids=allowed_user_ids)
    if not task:
        return False

    event, queue = get_urgent_objects(driver.serial)
    queue.put_nowait(task)
    event.set()
    return True

async def smart_sleep(driver, seconds: float):
    event, _ = get_urgent_objects(driver.serial)
    step = 0.5
    elapsed = 0.0
    while elapsed < seconds:
        if event.is_set():
            return  # có urgent cho device này thì thoát ngủ
        await asyncio.sleep(step)
        elapsed += step

async def wait_for_messenger_queue_pending(account, *, device_id: str = "", interval: float = 1.0):
    while True:
        if _messenger_queue_pending_sync(account, device_id=device_id):
            return True
        await asyncio.sleep(interval)

async def passive_wait_for_commands(driver, crm_id: str, account: str, restart_event=None):
    event, _ = get_urgent_objects(driver.serial)

    while True:
        if not get_passive_wait_only(driver.serial):
            log_message(
                f"[{DEVICE_LIST_NAME.get(driver.serial, driver.serial)}] PASSIVE mode OFF -> thoát chế độ chờ để quay lại nuôi",
                logging.INFO,
            )
            return

        if restart_event and restart_event.get(driver.serial):
            log_message(
                f"[{DEVICE_LIST_NAME[driver.serial]}] PASSIVE mode: phát hiện restart event, dừng chờ",
                logging.WARNING
            )
            break

        try:
            if event.is_set():
                await handle_urgent_if_any(driver, crm_id, account)
                event.clear()
                continue

            pushed = await pump_mongo_pending_to_json(driver, limit=20)
            if pushed:
                log_message(
                    f"[{DEVICE_LIST_NAME.get(driver.serial, driver.serial)}] PASSIVE mode: pumped {pushed} command(s) từ Mongo -> urgent_queue.json",
                    logging.INFO,
                )

            await scheduler_tick_json_to_device_queue(driver)

            if event.is_set():
                await handle_urgent_if_any(driver, crm_id, account)
                event.clear()
                continue

            await asyncio.sleep(1.0)

        except asyncio.CancelledError:
            raise
        except Exception as e:
            log_message(
                f"[{DEVICE_LIST_NAME.get(driver.serial, driver.serial)}] passive_wait_for_commands lỗi: {type(e).__name__} | {e}",
                logging.ERROR,
            )
            await asyncio.sleep(2.0)

# run urgent jobs as soon as URGENT_EVENT is set
# async def urgent_worker(driver, crm_id, account):
#     event, _ = get_urgent_objects(driver.serial)
#     while True:
#         # chờ job cho đúng device
#         await event.wait()
#         await handle_urgent_if_any(driver, crm_id, account)
#         event.clear()
DEVICE_ACTION_LOCKS: dict[str, asyncio.Lock] = {}

def get_device_action_lock(device_id: str) -> asyncio.Lock:
    lock = DEVICE_ACTION_LOCKS.get(device_id)
    if lock is None:
        lock = DEVICE_ACTION_LOCKS[device_id] = asyncio.Lock()
    return lock

async def handle_urgent_if_any(driver, crm_id, account):
    event, queue = get_urgent_objects(driver.serial)
    if not event.is_set():
        return {"processed": 0, "failed": 0, "ok": True, "reason": ""}
    if queue.empty():
        event.clear()
        return {"processed": 0, "failed": 0, "ok": True, "reason": ""}
    await cancel_current_action(driver)
    
    processed = 0
    failed = 0
    last_reason = ""
    
    try:
        # async with DEVICE_ACTION_LOCK:
        async with get_device_action_lock(driver.serial):
            while True:
                if queue.empty():
                    pulled_any = False
                    while await scheduler_tick_json_to_device_queue(driver):
                        pulled_any = True
                    if not pulled_any and queue.empty():
                        break
                    if queue.empty():
                        continue

                job = await queue.get()
                processed += 1

                ok = False
                reason: str | None = None

                # Hợp nhất id: WS dùng 'id', private API dùng '_id'
                job_id = str(job.get("command_id") or job.get("id") or job.get("_id") or "").strip()
                # Phân biệt source
                source = (job.get("source") or "").strip().lower()
                is_private_api_job = (source == "private_api")
                is_rabbitmq_job = (source == "rabbitmq")
                if is_rabbitmq_job and job_id:
                    try:
                        inbox_status = await pymongo_management.get_rabbitmq_inbox_status(job_id)
                        if inbox_status in {"SUPERSEDED_BY_MONGO", "FINISHED"}:
                            log_message(
                                f"[{DEVICE_LIST_NAME.get(driver.serial, driver.serial)}] skip RabbitMQ job "
                                f"command_id={job_id} vi inbox_status={inbox_status}",
                                logging.WARNING,
                            )
                            continue
                        await pymongo_management.mark_rabbitmq_inbox_running(job_id)
                    except Exception as inbox_err:
                        log_message(
                            f"[{DEVICE_LIST_NAME.get(driver.serial, driver.serial)}] mark RabbitMQ inbox RUNNING failed | command_id={job_id} | error={inbox_err}",
                            logging.WARNING,
                        )

                # job_id của private_api thường là UUID => KHÔNG phải ObjectId.
                # Một số tác vụ (join_group/post_*) gọi pymongo_management.execute_command(command_id)
                # nên cần truyền ObjectId hợp lệ để tránh crash.
                # Với Mongo/ObjectId: chỉ dùng khi action cần và id hợp lệ.
                # Không tự dummy ObjectId cho rabbitmq/crm nữa (tránh update nhầm DB).
                command_id_for_task = job_id
                if is_private_api_job and not _is_valid_objectid(command_id_for_task):
                    command_id_for_task = _dummy_objectid()
                    
                # 🔹 Lấy params (CRM gửi content, files, group_link nằm trong đây)
                params = job.get("params") or {}
                user_id = job.get("user_id") or crm_id
                # 🔹 Lấy action từ job hoặc từ params, rồi chuẩn hoá
                # Lấy action: NodeJS gửi type, WS/private_api có action
                raw_action = job.get("action") or job.get("type") or params.get("action") or "post_wall"
                action = raw_action.strip().lower()

                # Chuẩn hoá tên action để mọi nguồn dùng chung 1 chuẩn
                if action in ("post_to_wall", "post_wall"):
                    action = "post_wall"
                elif action in ("post_to_group", "post_group"):
                    action = "post_group"
                elif action in ("join_group", "join_fb_group"):
                    action = "join_group"
                elif action in ("post_video", "post_blog_video", "post_video_wall"):
                    action = "post_video"
                elif action in ("test_clipboard_termux", "termux_clipboard_test", "test_termux_clipboard"):
                    action = "test_clipboard_termux"
                elif action in ("crawl_post_comments", "crawl_comments", "fetch_post_comments"):
                    action = "crawl_post_comments"
                elif action in ("fetch_group_post_link", "retry_group_post_link", "copy_group_post_link"):
                    action = "fetch_group_post_link"
                # nếu có thêm action khác thì mapping tiếp ở đây

                rabbit_result_type = _rabbit_result_type(action)

                # 🔹 Hợp nhất content từ job phẳng và params
                content = (job.get("content") or params.get("content") or "").strip()
                files = job.get("files")
                if files is None:
                    files = params.get("files")
                group_link = job.get("group_link") or params.get("group_link")
                post_link = job.get("post_link") or params.get("post_link")

                auto_join_default = True if action == "post_group" else False
                auto_join = bool(job.get("auto_join_if_needed", params.get("auto_join_if_needed", auto_join_default)))
                join_timeout_sec = int(job.get("join_timeout_sec", params.get("join_timeout_sec", 15)))


                # Đưa FB về trạng thái chuẩn
                # Cái phần này đang làm cho app nhảy về Home rồi vào lại FB liên tục nếu poller có nhiều job
                # đổi sang bring to foreground thôi cho nhẹ nhàng
                #CŨ
                # await asyncio.to_thread(driver.press, "home")
                # await asyncio.sleep(1.2)
                # await asyncio.to_thread(driver.app_start, "com.facebook.katana")
                # await asyncio.sleep(2.5)
                #MỚI thay cho press home + app_start
                info = await asyncio.to_thread(driver.app_current)
                pkg = (info or {}).get("package")

                if pkg != "com.facebook.katana":
                    await asyncio.to_thread(driver.app_start, "com.facebook.katana")
                    await asyncio.sleep(2.0)
                # 🔹 Đảm bảo đang ở đúng tài khoản FB cho 3 loại action nhạy cảm
                # user_id ở đây là số điện thoại FB mà CRM gửi xuống (nếu có),
                # nếu không có thì fallback về crm_id hoặc account hiện tại.
                target_account = user_id or account

                if action in ("post_wall", "post_group", "join_group", "delete_post", "edit_post", "fetch_group_post_link"):
                    # ensure_ok = await ensure_account_for_user(driver, target_account)
                    target_name = (job.get("user_name") or params.get("user_name") or "").strip() or None
                    ensure_ok = await ensure_account_for_user(driver, target_account, target_name)
                    if not ensure_ok:
                        # Không switch được -> báo lỗi và bỏ qua job này
                        reason = (
                            ENSURE_ACCOUNT_LAST_REASON.pop(driver.serial, None)
                            or f"Không thể chuyển sang tài khoản {target_account}"
                        )
                        last_reason = reason
                        failed += 1
                        log_message(
                            f"[{DEVICE_LIST_NAME[driver.serial]}] {reason} cho job {job_id}",
                            logging.ERROR,
                        )
                        if job_id and is_rabbitmq_job:
                            failed_switch_payload = _build_rabbit_result_payload(
                                job_id=job_id,
                                driver=driver,
                                action=action,
                                ok=False,
                                reason=reason,
                                user_id=user_id,
                                user_name=job.get("user_name") or params.get("user_name"),
                                content=content,
                                files=files,
                                group_link=group_link,
                                post_link=None,
                            )
                            pub_ok, pub_err = await publish_rabbitmq_result(failed_switch_payload)
                            if not pub_ok:
                                log_message(
                                    f"[{DEVICE_LIST_NAME.get(driver.serial, driver.serial)}] publish account-switch failed result failed | command_id={job_id} | error={pub_err}",
                                    logging.WARNING,
                                )
                        if job_id and (not is_private_api_job) and (not is_rabbitmq_job):
                            notify_ack(job_id, "failed", reason=reason)
                        if job_id and is_private_api_job:
                            await private_api_client_patched.mark_done(job_id, ok=False, reason=reason)
                        # bỏ qua job hiện tại, đi tiếp job sau trong queue
                        continue
                    account = target_account

                ok, reason = False, None
                join_result = None
                post_result = None
                rabbit_result_published = False
                # ACK: đã bắt đầu chạy (chỉ cho job từ CRM/WS)
                # if job_id and (not is_private_api_job):
                #     notify_ack(job_id, "started")

                if job_id and (not is_private_api_job) and (not is_rabbitmq_job):
                    notify_ack(job_id, "started")

                try:
                    if action == "post_wall":
                        post_result = await post_to_wall(
                            driver,
                            command_id_for_task,
                            account,
                            content,
                            files,
                            return_result=is_rabbitmq_job,
                        )
                        if is_rabbitmq_job and isinstance(post_result, dict):
                            ok = bool(post_result.get("ok"))
                            reason = post_result.get("reason") or reason
                            wall_payload = {
                                "command_id": job_id,
                                "device_id": getattr(driver, "serial", ""),
                                "type": rabbit_result_type,
                                "ok": ok,
                                "status": "SUCCESS" if ok else "FAILED",
                                "reason": reason or "",
                                "user_id": user_id,
                                "user_name": job.get("user_name") or params.get("user_name"),
                                "content": content,
                                "files": files,
                                "post_link": post_result.get("post_link"),
                                "post_status": post_result.get("post_status"),
                                "result": post_result,
                                "data": post_result,
                            }
                            _attach_rabbit_error_meta(
                                wall_payload,
                                action=action,
                                ok=ok,
                                reason=reason,
                                post_status=post_result.get("post_status"),
                            )
                            pub_ok, pub_err = await publish_rabbitmq_result(wall_payload)
                            rabbit_result_published = bool(pub_ok)
                            if not pub_ok:
                                log_message(
                                    f"[{DEVICE_LIST_NAME.get(driver.serial, driver.serial)}] publish post_wall result failed | command_id={job_id} | error={pub_err}",
                                    logging.WARNING,
                                )
                        else:
                            ok = True
                    elif action == "logout_facebook" and DISABLE_LOGOUT_ACTION:
                         reason = "logout is disabled by policy"
                         ok = False
                    elif action == "join_group":
                        join_result = await join_group(
                            driver,
                            command_id=command_id_for_task,         # patched: avoid invalid ObjectId for private_api
                            user_id=user_id,            # hoặc None
                            group_link=group_link,
                            back_to_facebook=True
                        )
                        ok = join_result.status in ("joined", "pending_approval")
                        reason = join_result.reason or reason
                        if not ok and not reason:
                            reason = "join_group failed"
                        if is_rabbitmq_job:
                            join_payload = _build_rabbit_result_payload(
                                job_id=job_id,
                                driver=driver,
                                action=action,
                                ok=ok,
                                reason=reason,
                                user_id=user_id,
                                user_name=job.get("user_name") or params.get("user_name"),
                                content=content,
                                files=files,
                                group_link=group_link,
                                post_link=post_link,
                                join_result=join_result,
                            )
                            pub_ok, pub_err = await publish_rabbitmq_result(join_payload)
                            rabbit_result_published = bool(pub_ok)
                            if not pub_ok:
                                log_message(
                                    f"[{DEVICE_LIST_NAME.get(driver.serial, driver.serial)}] publish join_group result failed | command_id={job_id} | error={pub_err}",
                                    logging.WARNING,
                                )
                    # elif action == "post_group":
                    #     log_message(
                    #         f"[{DEVICE_LIST_NAME[driver.serial]}] START post_group | command_id={job_id} | group_link={group_link}",
                    #         logging.INFO,
                    #     )
                    #     ok = await post_to_group(
                    #         driver, command_id_for_task, account, content, files,
                    #         group_link=group_link,
                    #         auto_join_if_needed=auto_join,
                    #         join_timeout_sec=join_timeout_sec
                    #     )
                    elif action == "post_group":
                        log_message(
                            f"[{DEVICE_LIST_NAME[driver.serial]}] CALL post_to_group | command_id={job_id} | source={source or 'unknown'}",
                            logging.INFO,
                        )
                        post_result = await post_to_group(
                            driver, command_id_for_task, account, content, files,
                            group_link=group_link,
                            auto_join_if_needed=auto_join,
                            join_timeout_sec=join_timeout_sec,
                            return_result=is_rabbitmq_job,
                        )
                        if is_rabbitmq_job and isinstance(post_result, dict):
                            ok = bool(post_result.get("ok"))
                            reason = post_result.get("reason") or reason
                            group_payload = {
                                "command_id": job_id,
                                "device_id": getattr(driver, "serial", ""),
                                "type": rabbit_result_type,
                                "ok": ok,
                                "status": "SUCCESS" if ok else "FAILED",
                                "reason": reason or "",
                                "group_link": group_link,
                                "user_id": user_id,
                                "user_name": job.get("user_name") or params.get("user_name"),
                                "content": content,
                                "files": files,
                                "post_link": post_result.get("post_link"),
                                "post_status": post_result.get("post_status"),
                                "result": post_result,
                                "data": post_result,
                            }
                            _attach_rabbit_error_meta(
                                group_payload,
                                action=action,
                                ok=ok,
                                reason=reason,
                                post_status=post_result.get("post_status"),
                            )
                            pub_ok, pub_err = await publish_rabbitmq_result(group_payload)
                            rabbit_result_published = bool(pub_ok)
                            if not pub_ok:
                                log_message(
                                    f"[{DEVICE_LIST_NAME.get(driver.serial, driver.serial)}] publish post_group result failed | command_id={job_id} | error={pub_err}",
                                    logging.WARNING,
                                )
                        else:
                            ok = bool(post_result)
                        log_message(
                            f"[{DEVICE_LIST_NAME[driver.serial]}] RETURN post_to_group | command_id={job_id} | source={source or 'unknown'} | ok={ok}",
                            logging.INFO,
                        )
                    elif action == "fetch_group_post_link":
                        log_message(
                            f"[{DEVICE_LIST_NAME[driver.serial]}] CALL retry_fetch_group_post_link | command_id={job_id} | group_link={group_link}",
                            logging.INFO,
                        )
                        post_result = await retry_fetch_group_post_link(
                            driver,
                            command_id_for_task,
                            account,
                            content,
                            group_link=group_link,
                            return_result=True,
                        )
                        ok = bool(post_result.get("ok")) if isinstance(post_result, dict) else bool(post_result)
                        reason = post_result.get("reason") if isinstance(post_result, dict) else reason
                        log_message(
                            f"[{DEVICE_LIST_NAME[driver.serial]}] RETURN retry_fetch_group_post_link | command_id={job_id} | ok={ok}",
                            logging.INFO,
                        )
                    elif action == "post_video":
                        await post_video(driver, command_id_for_task, account, content, files)
                        ok = True
                    elif action == "switch_account":
                        target = (params.get("target_account") or job.get("target_account") or "").strip()
                        target_name = (params.get("target_name") or job.get("target_name") or target).strip()
                        if not target:
                            ok = False
                            reason = "missing target_account"
                        else:
                            ensure_ok = await ensure_account_for_user(driver, target, target_name)
                            ok = bool(ensure_ok)
                            if ok:
                                account = target
                                await run_messenger_priority_after_account_switch(
                                    driver,
                                    account,
                                    reason="urgent switch_account command",
                                )
                            else:
                                reason = (
                                    ENSURE_ACCOUNT_LAST_REASON.pop(driver.serial, None)
                                    or f"Không thể chuyển sang tài khoản {target}"
                                )
                    elif action == "delete_post":
                        ok = await delete_post(driver, command_id_for_task, post_link)
                    elif action == "test_clipboard_termux":
                        from toolfacebook_lib import test_termux_clipboard_once
                        result = await test_termux_clipboard_once(driver)
                        ok = bool(result.get("ok"))
                        reason = json.dumps(result, ensure_ascii=False)
                    elif action == "edit_post":
                        if not edit_post:
                            ok = False
                            reason = "edit_post module not available"
                        else:
                            ok = await edit_post(
                                driver,
                                command_id_for_task,
                                post_link,
                                new_text=content,
                                files=files,
                            )
                    elif action == "crawl_post_comments":
                        link = (
                            post_link
                            or job.get("link")
                            or params.get("link")
                            or ""
                        ).strip()

                        group_link_value = (
                            group_link
                            or job.get("group_link")
                            or params.get("group_link")
                            or ""
                        ).strip()

                        if not link:
                            ok = False
                            reason = "missing post_link/link"
                        elif not group_link_value:
                            ok = False
                            reason = "missing group_link"
                        else:
                            api_result = await call_crawl_comments_api(
                                group_link=group_link_value,
                                link=link,
                                timeout=int(params.get("crawl_api_timeout", 180)),
                            )

                            ok = bool(api_result.get("ok"))
                            reason = api_result.get("reason") or ""

                            # RabbitMQ job: publish data crawl về cho NodeJS
                            if is_rabbitmq_job:
                                result_payload = {
                                    "command_id": job_id,
                                    "device_id": getattr(driver, "serial", ""),
                                    "type": action,
                                    "ok": ok,
                                    "status": "SUCCESS" if ok else "FAILED",
                                    "reason": reason,
                                    "group_link": group_link_value,
                                    "link": link,
                                    "result": api_result.get("data"),
                                    "data": api_result.get("data"),
                                }

                                pub_ok, pub_err = await publish_rabbitmq_result(result_payload)
                                rabbit_result_published = bool(pub_ok)
                                if not pub_ok:
                                    ok = False
                                    reason = f"publish result failed: {pub_err}"
                    elif action == "crawl_messenger_notifications":
                        crawl_result = await crawl_messenger_notifications(
                            driver,
                            command_id=job_id,
                            params=params,
                        )
                        ok = bool(crawl_result.get("ok"))
                        reason = crawl_result.get("reason") or ""

                        if is_rabbitmq_job:
                            result_payload = {
                                "command_id": job_id,
                                "device_id": getattr(driver, "serial", ""),
                                "type": action,
                                "ok": ok,
                                "status": "SUCCESS" if ok else "FAILED",
                                "reason": reason,
                                "sidecar_only": True,
                                "affects_crm_message_flow": False,
                                "result": crawl_result,
                                "data": crawl_result,
                            }
                            pub_ok, pub_err = await publish_rabbitmq_result(result_payload)
                            rabbit_result_published = bool(pub_ok)
                            if not pub_ok:
                                ok = False
                                reason = f"publish result failed: {pub_err}"
                    elif action == "crawl_mobile_fb_1_1_farming":
                        farming_event = await crawl_mobile_fb_1_1_farming(
                            driver,
                            command_id=job_id,
                            params={
                                **params,
                                "account": account,
                                "user_id": user_id,
                            },
                        )
                        crawl_result = farming_event.get("result") or {}
                        ok = bool(crawl_result.get("ok"))
                        reason = crawl_result.get("reason") or ""

                        if is_rabbitmq_job:
                            result_payload = {
                                "command_id": job_id,
                                "device_id": getattr(driver, "serial", ""),
                                "type": action,
                                "ok": ok,
                                "status": "SUCCESS" if ok else "FAILED",
                                "reason": reason,
                                "sidecar_only": True,
                                "affects_crm_message_flow": False,
                                "result": farming_event,
                                "data": farming_event,
                            }
                            pub_ok, pub_err = await publish_rabbitmq_result(result_payload)
                            if not pub_ok:
                                ok = False
                                reason = f"publish sidecar result failed: {pub_err}"
                    else:
                        reason = f"Unknown action: {action}"
                except Exception as e:
                    ok = False
                    reason = str(e)
                    if action == "post_group" and not isinstance(post_result, dict):
                        post_result = _build_post_group_failure_result(
                            reason=reason,
                            post_link=post_link,
                            group_link=group_link,
                            content=content,
                            user_id=user_id,
                        )
                    if is_rabbitmq_job:
                        failed_payload = _build_rabbit_result_payload(
                            job_id=job_id,
                            driver=driver,
                            action=action,
                            ok=False,
                            reason=reason,
                            user_id=user_id,
                            user_name=job.get("user_name") or params.get("user_name"),
                            content=content,
                            files=files,
                            group_link=group_link,
                            post_link=post_result.get("post_link") if isinstance(post_result, dict) else post_link,
                            post_result=post_result if isinstance(post_result, dict) else None,
                            join_result=join_result,
                        )
                        pub_ok, pub_err = await publish_rabbitmq_result(failed_payload)
                        rabbit_result_published = bool(pub_ok)
                        if not pub_ok:
                            log_message(
                                f"[{DEVICE_LIST_NAME.get(driver.serial, driver.serial)}] publish {action} failed-result error | command_id={job_id} | error={pub_err}",
                                logging.WARNING,
                            )
                    log_message(
                        f"[{DEVICE_LIST_NAME[driver.serial]}] post urgent action='{action}' FAILED | command_id={job_id} | reason={reason}",
                        logging.ERROR,
                    )
                    import traceback
                    traceback.print_exc()

                if is_rabbitmq_job and not rabbit_result_published:
                    fallback_payload = _build_rabbit_result_payload(
                        job_id=job_id,
                        driver=driver,
                        action=action,
                        ok=ok,
                        reason=reason,
                        user_id=user_id,
                        user_name=job.get("user_name") or params.get("user_name"),
                        content=content,
                        files=files,
                        group_link=group_link,
                        post_link=post_result.get("post_link") if isinstance(post_result, dict) else post_link,
                        post_result=post_result if isinstance(post_result, dict) else None,
                        join_result=join_result,
                    )
                    pub_ok, pub_err = await publish_rabbitmq_result(fallback_payload)
                    rabbit_result_published = bool(pub_ok)
                    if not pub_ok:
                        log_message(
                            f"[{DEVICE_LIST_NAME.get(driver.serial, driver.serial)}] publish fallback result failed | action={action} | command_id={job_id} | error={pub_err}",
                            logging.WARNING,
                        )

                if action == "post_group" and _post_group_should_rotate_account(ok, post_result, reason):
                    post_status_for_rotation = _post_status_from_result_or_reason(post_result, reason)
                    try:
                        switched = await maybe_auto_switch_account_after_post_group(driver, account)
                        if switched:
                            try:
                                account = _normalize_account_value(get_current_account(driver.serial)) or account
                            except Exception:
                                pass
                    except Exception as switch_err:
                        log_message(
                            f"[{DEVICE_LIST_NAME.get(driver.serial, driver.serial)}] Auto switch after post_group failed: {type(switch_err).__name__} | {switch_err}",
                            logging.WARNING,
                        )
                    if POST_GROUP_SUCCESS_COOLDOWN_SECONDS > 0:
                        log_message(
                            f"[{DEVICE_LIST_NAME.get(driver.serial, driver.serial)}] post_group counted for account rotation (ok={ok}, post_status={post_status_for_rotation or 'unknown'}) -> sleep {POST_GROUP_SUCCESS_COOLDOWN_SECONDS}s before next queue scan",
                            logging.INFO,
                        )
                        await asyncio.sleep(POST_GROUP_SUCCESS_COOLDOWN_SECONDS)
                    
                if not ok:
                    failed += 1
                    last_reason = (reason or last_reason or "")
                    
                # ACK (WS) - chỉ cho job từ CRM/WS
                # if job_id and (not is_private_api_job):
                #     notify_ack(job_id, "finished" if ok else "failed", reason=reason or "")

                if job_id and (not is_private_api_job) and (not is_rabbitmq_job):
                    notify_ack(job_id, "finished" if ok else "failed", reason=reason or "")

                # DONE (Private API server) - chỉ cho job từ UI/Postman (/api/next)
                if job_id and is_private_api_job:
                    await private_api_client_patched.mark_done(job_id, ok=ok, reason=reason or "")
                if _messenger_queue_pending_sync(account, device_id=driver.serial):
                    log_message(
                        f"[{DEVICE_LIST_NAME.get(driver.serial, driver.serial)}] Messenger queue xuat hien sau urgent action='{action}' -> uu tien gui 1-1",
                        logging.INFO,
                    )
                    await send_messenger_queue_from_facebook_home(
                        driver,
                        account,
                        reason=f"queue after urgent action {action}",
                    )
                    return {"processed": processed, "failed": failed, "ok": failed == 0, "reason": last_reason}

            # Sau khi chạy urgent, nên về Home trước khi thả khoá
            try:
                await go_to_home_page(driver)
            except Exception:
                await asyncio.to_thread(driver.press, "home")
    finally:
        event.clear()
        
        
async def clear_app(driver):
    driver.press("recent")
    await asyncio.sleep(2)

    size = driver.window_size()
    width, height = size[0], size[1]

    start_x = width / 2
    end_x = start_x
    start_y = height * 0.7
    end_y = height * 0.2
    duration = 0.04

    driver.swipe(start_x, start_y, end_x, end_y, duration=duration)
    await asyncio.sleep(3)
    driver.press("home")
    driver.press("back")

async def run_messenger_priority_after_account_switch(driver, account, *, reason: str = "") -> None:
    account = _normalize_account_value(account)
    if not account or account == "default":
        return

    device_id = getattr(driver, "serial", "") or ""
    label = DEVICE_LIST_NAME.get(device_id, device_id)
    reason_text = f" sau {reason}" if reason else ""
    log_message(
        f"[{label}] Sau khi chuyen account {reason_text} -> chi xu ly Messenger neu co queue cho {account}",
        logging.INFO,
    )

    try:
        if _messenger_queue_pending_sync(account, device_id=driver.serial):
            await send_messenger_queue_from_facebook_home(
                driver,
                account,
                reason="queue after account switch",
            )
            return
    except Exception as exc:
        log_message(
            f"[{label}] Lỗi check_and_send_messenger_queue_priority sau switch account {account}: {type(exc).__name__} | {exc}",
            logging.WARNING,
        )

    log_message(f"[{label}] Account {account} khong co Messenger queue sau switch -> tiep tuc tac vu binh thuong", logging.INFO)

async def ensure_facebook_home_after_messenger(driver, *, timeout: int = 12) -> bool:
    device_id = getattr(driver, "serial", "")
    try:
        await asyncio.to_thread(driver.app_start, "com.facebook.katana")
        await asyncio.sleep(2)
        deadline = time.time() + max(2, timeout)
        home_locator = {
            ("xpath", '//*[contains(@content-desc,"Trang chủ") and contains(@content-desc,"Tab")]'),
            ("xpath", '//*[contains(@content-desc,"Home") and contains(@content-desc,"Tab")]'),
        }
        while time.time() < deadline:
            if await my_find_element(driver, home_locator, 2, back_if_not_found=False):
                return True
            try:
                pkg = await asyncio.to_thread(driver.app_current)
                current_pkg = pkg.get("package") if isinstance(pkg, dict) else str(pkg)
            except Exception:
                current_pkg = ""
            if current_pkg != "com.facebook.katana":
                await asyncio.to_thread(driver.app_start, "com.facebook.katana")
                await asyncio.sleep(1.5)
                continue
            try:
                await asyncio.to_thread(driver.press, "back")
            except Exception:
                pass
            await asyncio.sleep(1.2)
        log_message(
            f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] Khong xac nhan duoc Facebook Home sau Messenger crawl",
            logging.WARNING,
        )
        return False
    except Exception as exc:
        log_message(
            f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] Loi khi quay ve Facebook sau Messenger crawl: {type(exc).__name__} | {exc}",
            logging.WARNING,
        )
        return False

async def check_and_crawl_messenger_priority(driver, account, *, force: bool = False):
    """
    Tác vụ ưu tiên: Chuyển sang Messenger để kiểm tra và cào tin nhắn mới trước khi farming.
    """
    if not account or account == "default":
        return

    device_id = getattr(driver, "serial", "")
    account = _normalize_account_value(account)
    if _messenger_queue_pending_sync(account, device_id=device_id):
        log_message(
            f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] Messenger crawl bị chặn vì message_send_queue còn queued/sending; chuyển sang ưu tiên gửi trước cho account {account}",
            logging.INFO,
        )
        await send_messenger_queue_from_facebook_home(
            driver,
            account,
            reason="queue pending blocked crawl",
        )
        return
    ensure_messenger_command_bot_online(driver, account)
    now = time.time()
    last_priority = getattr(driver, "_fb_messenger_priority_last", None)
    if (not force) and isinstance(last_priority, dict):
        last_device = last_priority.get("device_id")
        last_account = last_priority.get("account")
        last_done_at = float(last_priority.get("done_at") or 0)
        dedupe_seconds = int(os.getenv("FB_1_1_PRIORITY_DEDUPE_SECONDS", "180"))
        if last_device == device_id and last_account == account and now - last_done_at < dedupe_seconds:
            log_message(
                f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] Skip Messenger priority: account {account} was just scanned",
                logging.INFO,
            )
            return

    log_message(f"[{DEVICE_LIST_NAME.get(driver.serial, driver.serial)}] ⭐ ƯU TIÊN: Kiểm tra tin nhắn Messenger", logging.INFO)
    try:
        from module.mobile_1_1_messenger import MobileOneToOneMessenger
        
        # Khởi tạo messenger tool cho account hiện tại
        messenger = MobileOneToOneMessenger(user_id_chat=account)
        # Ép dùng driver và device hiện tại
        messenger.ui_driver = driver
        messenger = attach_dump_automation(messenger, patch_crawl=True) # Áp dụng bản vá
        messenger.active_device = driver.serial
        
        def run_sync_logic():
            if _messenger_queue_pending_sync(account, device_id=driver.serial):
                print("[SEND] Queue pending before Messenger crawl; skip opening/crawling Messenger.")
                return False
            # Mở Messenger chỉ để đưa app vào trạng thái sẵn sàng. Tắt auto crawl
            # trong open_messenger để không cào Message Requests hai lần; phần
            # cào idle bên dưới là nguồn duy nhất cho lượt priority này.
            previous_skip = getattr(messenger, "_skip_open_messenger_initial_request_crawl", False)
            messenger._skip_open_messenger_initial_request_crawl = True
            try:
                opened = messenger.open_messenger()
            finally:
                messenger._skip_open_messenger_initial_request_crawl = previous_skip
            if opened:
                if _messenger_queue_pending_sync(account, device_id=driver.serial):
                    print("[SEND] Queue appeared after opening Messenger; skip all crawl steps.")
                    return False
                if hasattr(messenger, "run_idle_message_crawls_if_no_send_queue"):
                    messenger.run_idle_message_crawls_if_no_send_queue()
                else:
                    messenger.crawl_message_requests()
                    messenger.crawl_visible_messenger_messages()
                    messenger.scan_local_chat_history_for_new_messages()
                return True
            return False

        scan_ok = await asyncio.to_thread(run_sync_logic)
        if scan_ok:
            setattr(driver, "_fb_messenger_priority_last", {
                "device_id": device_id,
                "account": account,
                "done_at": time.time(),
            })
        
        # Keep Messenger foreground after priority crawl/send. The caller can
        # explicitly return to Facebook only when no 1-1 CRM work is active.
            
    except Exception as e:
        log_message(f"[{DEVICE_LIST_NAME.get(driver.serial, driver.serial)}] Lỗi khi ưu tiên Messenger: {e}", logging.ERROR)
        await ensure_facebook_home_after_messenger(driver, timeout=6)

async def check_and_send_messenger_queue_priority(driver, account):
    account = _normalize_account_value(account)
    if not account or account == "default":
        return False
    pending_account = _next_messenger_queue_account_for_device(getattr(driver, "serial", ""), account)
    if pending_account:
        log_message(
            f"[{DEVICE_LIST_NAME.get(driver.serial, driver.serial)}] Mongo Commands co tin 1-1 dang cho; sender se emit len bot account={pending_account}",
            logging.INFO,
        )
        return True
    return False

    max_attempts = int(os.getenv("FB_1_1_MAX_QUEUE_ATTEMPTS", "3"))
    sending_stale_seconds = int(os.getenv("FB_1_1_SENDING_STALE_SECONDS", "240"))
    conn = sqlite3.connect("crm_facebook.db")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        UPDATE message_send_queue
        SET status='queued',
            error='',
            updated_at=CURRENT_TIMESTAMP
        WHERE id IN (
            SELECT q.id
            FROM message_send_queue q
            JOIN messages m ON m.id = q.message_id
            WHERE q.user_id_chat = ?
              AND q.status = 'sent'
              AND m.send_status IN ('queued', 'sending', 'failed')
              AND q.attempts < ?
        )
    """, (account, max_attempts))
    restored_sent = cur.rowcount or 0
    if restored_sent:
        log_message(
            f"[{DEVICE_LIST_NAME.get(driver.serial, driver.serial)}] Dua lai {restored_sent} queue sent lech trang thai ve queued de gui lai",
            logging.WARNING,
        )
    cur.execute("""
        UPDATE messages
        SET send_status='queued',
            send_error=''
        WHERE id IN (
            SELECT message_id
            FROM message_send_queue
            WHERE user_id_chat = ?
              AND status = 'queued'
              AND attempts < ?
        )
          AND send_status <> 'queued'
    """, (account, max_attempts))
    normalized_queued_messages = cur.rowcount or 0
    if normalized_queued_messages:
        log_message(
            f"[{DEVICE_LIST_NAME.get(driver.serial, driver.serial)}] Đồng bộ {normalized_queued_messages} messages lệch trạng thái về queued theo message_send_queue",
            logging.WARNING,
        )
    cur.execute("""
        SELECT q.id, q.message_id, q.conversation_id, q.participant_name, q.participant_url, q.content, q.attempts, q.updated_at
        FROM message_send_queue q
        WHERE q.user_id_chat = ?
          AND q.status = 'sending'
          AND q.attempts < ?
        ORDER BY q.id ASC
        LIMIT 1
    """, (account, max_attempts))
    sending_row = cur.fetchone()
    sending_is_stale = False
    if sending_row:
        active_snapshot_account = _active_facebook_account_snapshot_account(getattr(driver, "serial", ""))
        sending_is_for_inactive_account = bool(active_snapshot_account and active_snapshot_account != account)
        cur.execute("""
            SELECT (
                strftime('%s','now') - strftime('%s', updated_at)
            ) >= ?
            FROM message_send_queue
            WHERE id=?
        """, (sending_stale_seconds, sending_row["id"]))
        sending_is_stale = bool((cur.fetchone() or [0])[0])
        current_pkg = ""
        try:
            pkg = await asyncio.to_thread(driver.app_current)
            current_pkg = pkg.get("package") if isinstance(pkg, dict) else str(pkg)
        except Exception:
            current_pkg = ""
        sending_on_facebook = current_pkg != "com.facebook.orca"

        if sending_is_stale or sending_is_for_inactive_account or sending_on_facebook:
            if sending_is_for_inactive_account:
                recover_reason = f"Recovered sending state because active account is {active_snapshot_account}"
            elif sending_is_stale:
                recover_reason = "Recovered stale sending state"
            else:
                recover_reason = "Recovered sending state because Messenger is not foreground"
            cur.execute("""
                UPDATE message_send_queue
                SET status='queued',
                    error=?,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                  AND status='sending'
            """, (recover_reason, sending_row["id"]))
            cur.execute("""
                UPDATE messages
                SET send_status='queued',
                    send_error=?
                WHERE id=?
            """, (recover_reason, sending_row["message_id"]))
            log_message(
                f"[{DEVICE_LIST_NAME.get(driver.serial, driver.serial)}] Queue id={sending_row['id']} dang sending nhung active={active_snapshot_account or 'unknown'}, target={account}, current_package={current_pkg} -> dua ve queued de switch/gửi ngay",
                logging.WARNING,
            )
            sending_row = None
        else:
            cur.execute("""
                UPDATE messages
                SET send_status='sending',
                    send_error=''
                WHERE id=?
                  AND send_status <> 'sending'
            """, (sending_row["message_id"],))
    cur.execute("""
        SELECT q.id, q.message_id, q.conversation_id, q.participant_name, q.participant_url, q.content, q.attempts, q.account_context
        FROM message_send_queue q
        WHERE q.user_id_chat = ?
          AND q.status = 'queued'
          AND q.attempts < ?
        ORDER BY q.id ASC
        LIMIT 1
    """, (account, max_attempts))
    row = cur.fetchone()
    if row:
        cur.execute("""
            UPDATE message_send_queue
            SET status='sending',
                bot_sid=?,
                attempts=attempts+1,
                error='',
                updated_at=CURRENT_TIMESTAMP
            WHERE id=?
              AND status = 'queued'
              AND attempts < ?
        """, (f"mobile_priority:{driver.serial}", row["id"], max_attempts))
        if cur.rowcount != 1:
            row = None
        else:
            cur.execute("""
                UPDATE messages
                SET send_status='sending',
                    send_error=''
                WHERE id=?
            """, (row["message_id"],))
    conn.commit()
    conn.close()

    if not row:
        if sending_row:
            log_message(
                f"[{DEVICE_LIST_NAME.get(driver.serial, driver.serial)}] Queue id={sending_row['id']} dang sending, chan crawl Messenger de tranh gui/cao chen ngang",
                logging.INFO,
            )
            return True
        return False

    log_message(
        f"[{DEVICE_LIST_NAME.get(driver.serial, driver.serial)}] ƯU TIÊN GỬI MESSENGER QUEUE: {row['participant_name']}",
        logging.INFO,
    )

    await cancel_current_action(driver)

    messenger = MobileOneToOneMessenger(user_id_chat=account)
    messenger.ui_driver = driver
    messenger.active_device = driver.serial
    messenger = attach_dump_automation(messenger, patch_crawl=False)

    try:
        account_context = json.loads(row["account_context"] or "{}")
    except Exception:
        account_context = {}
    account_context = {
        **_account_context_for_queue_target(device_id, account),
        **(account_context if isinstance(account_context, dict) else {}),
    }
    switch_result = messenger.ensure_account_from_payload({"accountContext": account_context, "force_reset": True})
    if not switch_result.get("success"):
        error = switch_result.get("error") or f"Khong the doi sang account {account} de gui Messenger queue"
        next_attempts = int(row["attempts"] or 0) + 1
        final_status = "queued" if next_attempts < max_attempts else "failed"
        conn = sqlite3.connect("crm_facebook.db")
        cur = conn.cursor()
        cur.execute("""
            UPDATE message_send_queue
            SET status=?, error=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
        """, (final_status, error, row["id"]))
        cur.execute("""
            UPDATE messages
            SET send_status=?, send_error=?
            WHERE id=?
        """, (final_status, error, row["message_id"]))
        conn.commit()
        conn.close()
        log_message(
            f"[{DEVICE_LIST_NAME.get(driver.serial, driver.serial)}] Khong doi duoc account truoc khi gui Messenger queue: {error}",
            logging.WARNING,
        )
        return True

    def run_send():
        return messenger.send_queued_message_by_participant_name(
            participant_name=row["participant_name"],
            content=row["content"],
            participant_url=row["participant_url"],
        )

    try:
        success = bool(await asyncio.to_thread(run_send))
        if success:
            setattr(driver, "_fb_1_1_last_active_thread_participant_name", row["participant_name"])
        error = "" if success else (
            getattr(messenger, "_last_send_failure_reason", "")
            or "Không gửi được tin nhắn Messenger theo participant_name"
        )
    except Exception as exc:
        success = False
        error = f"Lỗi gửi Messenger queue: {type(exc).__name__} | {exc}"
    if not success:
        try:
            success = bool(messenger.visible_sent_message_confirmed(driver, row["content"]))
            if success:
                error = ""
        except Exception:
            pass
    next_attempts = int(row["attempts"] or 0) + 1
    final_status = "sent" if success else ("queued" if next_attempts < max_attempts else "failed")

    conn = sqlite3.connect("crm_facebook.db")
    cur = conn.cursor()
    # if success:
    #     cur.execute("DELETE FROM message_send_queue WHERE id=?", (row["id"],))
    if success:
        cur.execute("""
            UPDATE message_send_queue
            SET status='sent',
                error='',
                updated_at=CURRENT_TIMESTAMP
            WHERE id=?
        """, (row["id"],))
        try:
            messenger._update_db_tin_nhan_after_send(
                row["participant_url"],
                row["content"],
                message_id=row["message_id"],
            )
        except Exception as exc:
            log_message(
                f"[{DEVICE_LIST_NAME.get(driver.serial, driver.serial)}] Khong cap nhat duoc db_tin_nhan sau khi gui queue id={row['id']}: {type(exc).__name__} | {exc}",
                logging.WARNING,
            )
    else:
        cur.execute("""
            UPDATE message_send_queue
            SET status=?, error=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
        """, (final_status, error, row["id"]))
    cur.execute("""
        UPDATE messages
        SET send_status=?, send_error=?
        WHERE id=?
    """, (final_status, error, row["message_id"]))
    conn.commit()
    conn.close()

    log_message(
        f"[{DEVICE_LIST_NAME.get(driver.serial, driver.serial)}] Kết quả gửi Messenger queue id={row['id']}: {final_status}",
        logging.INFO if success else logging.WARNING,
    )
    return True

def _messenger_queue_accounts_for_device(device_id: str = "", seed_account: str = "") -> list[str]:
    accounts: list[str] = []
    seed_account = _normalize_account_value(seed_account)
    if seed_account and seed_account != "default":
        accounts.append(seed_account)

    device_id = str(device_id or "").strip()
    if device_id:
        try:
            device = _load_device_account_with_fallback(device_id)
        except Exception:
            device = {}
        for item in (device.get("accounts") or []) if isinstance(device, dict) else []:
            if not isinstance(item, dict):
                continue
            account = _normalize_account_value(item.get("account") or item.get("user_id_chat") or item.get("accountKey"))
            if account and account != "default" and account not in accounts:
                accounts.append(account)

    return accounts

def _next_messenger_queue_account_for_device(device_id: str = "", seed_account: str = "") -> str:
    accounts = _messenger_queue_accounts_for_device(device_id, seed_account)
    if not accounts:
        return ""
    try:
        url = os.getenv("FB_1_1_CRM_SENDER_URL", "http://123.24.206.25:8024").rstrip("/") + "/api/commands/pending"
        for account in accounts:
            response = requests.get(url, params={"accounts": account}, timeout=3)
            response.raise_for_status()
            if (response.json() or {}).get("pending"):
                return account
    except Exception as exc:
        log_message(f"Khong kiem tra duoc Mongo Commands pending: {exc}", logging.WARNING)
    return ""
def _account_context_for_queue_target(device_id: str, account: str) -> dict:
    account = _normalize_account_value(account)
    if not account:
        return {}
    try:
        device = _load_device_account_with_fallback(device_id)
    except Exception:
        device = {}
    account_info = {}
    if isinstance(device, dict):
        account_info = next(
            (
                item for item in (device.get("accounts") or [])
                if isinstance(item, dict) and _normalize_account_value(item.get("account")) == account
            ),
            {},
        )
    display_name = (
        _normalize_username_value(account_info.get("name") if isinstance(account_info, dict) else "")
        or _normalize_username_value(account_info.get("username") if isinstance(account_info, dict) else "")
        or _find_username_by_account(device, account)
        or account
    )
    return {
        "user_id_chat": account,
        "account": account,
        "accountKey": account,
        "facebookAccountId": account,
        "username": display_name,
        "displayName": display_name,
        "name": display_name,
        "password": str(account_info.get("password") or "") if isinstance(account_info, dict) else "",
        "device_id": device_id,
        "device_name": DEVICE_LIST_NAME.get(device_id, device_id),
    }

def _active_facebook_account_snapshot_account(device_id: str = "") -> str:
    try:
        snapshot_file = Path(os.getenv("FB_1_1_ACTIVE_ACCOUNT_FILE", "active_facebook_account.json"))
        payload = json.loads(snapshot_file.read_text(encoding="utf-8"))
    except Exception:
        return ""
    if not isinstance(payload, dict):
        return ""
    snapshot_device = str(payload.get("device_id") or "").strip()
    if device_id and snapshot_device and snapshot_device != str(device_id).strip():
        return ""
    return _normalize_account_value(payload.get("user_id_chat") or payload.get("account"))

def _messenger_queue_pending_sync(account, *, device_id: str = ""):
    if device_id:
        return bool(_next_messenger_queue_account_for_device(device_id, account))
    import sqlite3

    account = _normalize_account_value(account)
    if not account or account == "default":
        return False
    try:
        conn = sqlite3.connect("crm_facebook.db")
        cur = conn.cursor()
        cur.execute("""
            SELECT 1
            FROM message_send_queue
            WHERE user_id_chat = ?
              AND status IN ('queued', 'sending')
            LIMIT 1
        """, (account,))
        found = cur.fetchone() is not None
        conn.close()
        return found
    except Exception as exc:
        log_message(f"Không kiểm tra được message_send_queue: {exc}", logging.WARNING)
        return False

async def has_messenger_queue_pending(account):
    return _messenger_queue_pending_sync(account)

async def stay_in_messenger_1_1_mode(driver, account, *, reason: str = "") -> None:
    """Keep the device in Messenger after CRM 1-1 queue activity.

    When CRM sends a queued 1-1 message, Messenger becomes the highest-priority
    surface. Do not return to Facebook farming; keep the command bot registered
    and leave the UI where the send flow ended so incoming customer messages can
    be crawled/forwarded and new CRM messages can be sent immediately.
    """
    account = _normalize_account_value(account)
    if not account or account == "default":
        return
    ensure_messenger_command_bot_online(driver, account)
    setattr(driver, "_fb_1_1_priority_active", True)
    setattr(driver, "_fb_1_1_priority_account", account)
    setattr(driver, "_fb_1_1_priority_last_activity", time.time())
    device_id = getattr(driver, "serial", "") or ""
    try:
        set_messenger_priority_hold(device_id, account=account)
    except Exception as exc:
        log_message(
            f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] Khong ghi duoc Messenger 1-1 runtime hold: {type(exc).__name__} | {exc}",
            logging.WARNING,
        )
    label = DEVICE_LIST_NAME.get(device_id, device_id)
    suffix = f" ({reason})" if reason else ""
    log_message(
        f"[{label}] Messenger 1-1 priority active{suffix}: giu nguyen Messenger/chat, dung cac tac vu Facebook con lai",
        logging.INFO,
    )

def is_messenger_1_1_priority_active(driver, account=None) -> bool:
    device_id = getattr(driver, "serial", "") or ""
    if device_id:
        try:
            if is_messenger_priority_hold_active(device_id, account):
                return True
        except Exception:
            pass

    if not getattr(driver, "_fb_1_1_priority_active", False):
        return False
    if account:
        active_account = _normalize_account_value(getattr(driver, "_fb_1_1_priority_account", ""))
        if active_account and active_account != _normalize_account_value(account):
            return False
    hold_seconds = int(os.getenv("FB_1_1_HOLD_AFTER_SEND_SECONDS", "900"))
    if hold_seconds <= 0:
        return True
    last_activity = float(getattr(driver, "_fb_1_1_priority_last_activity", 0) or 0)
    if last_activity and time.time() - last_activity <= hold_seconds:
        return True
    setattr(driver, "_fb_1_1_priority_active", False)
    if device_id:
        try:
            clear_messenger_priority_hold(device_id)
        except Exception:
            pass
    return False

async def run_messenger_1_1_priority_session(driver, account, *, restart_event=None, reason: str = "") -> None:
    account = _normalize_account_value(account)
    if not account or account == "default":
        return

    await stay_in_messenger_1_1_mode(driver, account, reason=reason or "priority session")
    crawl_interval = int(os.getenv("FB_1_1_PRIORITY_CURRENT_CHAT_SCAN_SECONDS", "5"))
    last_scan = 0.0
    label = DEVICE_LIST_NAME.get(getattr(driver, "serial", ""), getattr(driver, "serial", ""))

    while is_messenger_1_1_priority_active(driver, account):
        if restart_event and restart_event.get(getattr(driver, "serial", "")):
            log_message(f"[{label}] Messenger 1-1 priority session dung do restart_event", logging.WARNING)
            return

        sent = await check_and_send_messenger_queue_priority(driver, account)
        if sent:
            await stay_in_messenger_1_1_mode(driver, account, reason="sent queue in priority session")
            last_scan = 0.0

        ensure_messenger_command_bot_online(driver, account)
        now = time.time()
        if now - last_scan >= max(2, crawl_interval):
            messenger = getattr(driver, "_fb_1_1_messenger_command_bot", None)
            if messenger is not None and getattr(messenger, "ui_driver", None) is driver:
                try:
                    current_thread_messages = 0
                    if hasattr(messenger, "crawl_current_open_thread_messages_to_crm"):
                        current_thread_messages = await asyncio.to_thread(
                            messenger.crawl_current_open_thread_messages_to_crm,
                            allow_when_send_queue_pending=True,
                        )
                    local_messages = await asyncio.to_thread(messenger.scan_local_chat_history_for_new_messages)
                    new_messages = int(current_thread_messages or 0) + int(local_messages or 0)
                    if new_messages:
                        setattr(driver, "_fb_1_1_priority_last_activity", time.time())
                except Exception as exc:
                    log_message(
                        f"[{label}] Loi scan chat hien tai trong Messenger 1-1 priority: {type(exc).__name__} | {exc}",
                        logging.WARNING,
                    )
            last_scan = now

        await asyncio.sleep(1.0)

    log_message(
        f"[{label}] Messenger 1-1 idle qua {int(os.getenv('FB_1_1_HOLD_AFTER_SEND_SECONDS', '900'))}s -> quay lai crawl/nuoi Facebook",
        logging.INFO,
    )

async def send_messenger_queue_from_facebook_home(driver, account, *, reason: str = "", restart_event=None) -> bool:
    from module.mobile_1_1_messenger import MESSENGER_PKG, MobileOneToOneMessenger

    account = _normalize_account_value(account)
    device_id = getattr(driver, "serial", "") or ""
    target_account = _next_messenger_queue_account_for_device(device_id, account)
    if target_account:
        account = target_account
    if not account or account == "default":
        return False
    label = DEVICE_LIST_NAME.get(getattr(driver, "serial", ""), getattr(driver, "serial", ""))
    messenger_probe = MobileOneToOneMessenger(user_id_chat=account)
    messenger_probe.ui_driver = driver
    messenger_probe.active_device = getattr(driver, "serial", "")
    try:
        current_package = messenger_probe.get_current_package_name(driver)
    except Exception:
        current_package = ""

    if current_package == MESSENGER_PKG or messenger_probe.is_current_package(MESSENGER_PKG):
        log_message(
            f"[{label}] Messenger queue pending{f' ({reason})' if reason else ''}: dang o Messenger, xu ly noi bo roi gui tin",
            logging.INFO,
        )
    else:
        log_message(
            f"[{label}] Messenger queue pending{f' ({reason})' if reason else ''}: ve Facebook Home roi vao Messenger gui tin",
            logging.INFO,
        )
        try:
            await go_to_home_page(driver)
        except Exception as exc:
            log_message(f"[{label}] Khong ve duoc Facebook Home truoc khi gui Messenger queue: {exc}", logging.WARNING)
    if await check_and_send_messenger_queue_priority(driver, account):
        await run_messenger_1_1_priority_session(
            driver,
            account,
            restart_event=restart_event,
            reason=reason or "sent queue from Facebook task",
        )
        return True
    return False

def ensure_messenger_command_bot_online(driver, account):
    """
    Keep the Socket.IO 1-1 Messenger bot registered while the main Facebook tool runs.

    Without this persistent registration, messages inserted into
    crm_facebook.db.message_send_queue stay queued because fb_1_1_api_sender.py
    has no online bot_sid to emit send_message_command to.
    """
    account = _normalize_account_value(account)
    if not account or account == "default":
        return None

    try:
        messenger = getattr(driver, "_fb_1_1_messenger_command_bot", None)
        current = _normalize_account_value(getattr(messenger, "user_id_chat", "")) if messenger else ""
        if messenger is None or current != account:
            from module.mobile_1_1_messenger import MobileOneToOneMessenger

            messenger = MobileOneToOneMessenger(user_id_chat=account)
            messenger.ui_driver = driver
            messenger = attach_dump_automation(messenger, patch_crawl=True)
            messenger.active_device = driver.serial
            setattr(driver, "_fb_1_1_messenger_command_bot", messenger)

        messenger.active_device = driver.serial
        messenger.ui_driver = driver
        # Do not write the queue/request account as the active Facebook account.
        # The active snapshot must come from profile/UI verification; otherwise a
        # pending send for one account can overwrite the real account on screen.
        try:
            current_account = (
                messenger.current_account_id()
                if hasattr(messenger, "current_account_id")
                else _normalize_account_value(getattr(messenger, "user_id_chat", ""))
            )
            if not current_account and hasattr(messenger, "snapshot_facebook_account_before_messenger"):
                messenger.snapshot_facebook_account_before_messenger()
        except Exception as exc:
            log_message(
                f"[{DEVICE_LIST_NAME.get(driver.serial, driver.serial)}] Khong xac minh duoc active Facebook account truoc khi register bot: {exc}",
                "WARNING",
            )
        try:
            messenger.load_db()
        except Exception:
            pass
        last_thread_name = getattr(driver, "_fb_1_1_last_active_thread_participant_name", "")
        if last_thread_name:
            setattr(messenger, "_last_active_thread_participant_name", last_thread_name)
        if messenger.connect_to_crm():
            messenger.start_socket_heartbeat(
                interval=int(os.getenv("FB_1_1_SOCKET_REGISTER_HEARTBEAT_SECONDS", "10"))
            )
            registered_account = (
                messenger.current_account_id()
                if hasattr(messenger, "current_account_id")
                else _normalize_account_value(getattr(messenger, "user_id_chat", ""))
            )
            log_message(
                f"[{DEVICE_LIST_NAME.get(driver.serial, driver.serial)}] Messenger command bot online | account={registered_account or account}",
                logging.INFO,
            )
        return messenger
    except Exception as e:
        device_id = getattr(driver, "serial", "")
        log_message(
            f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] Không đưa được Messenger command bot online: {type(e).__name__} | {e}",
            logging.WARNING,
        )
        return None

async def fb_natural_task(driver, crm_id:str, account: str, current_username: str | None = None, screen_standing_event=None, restart_event=None):
    # 1. Thực hiện kiểm tra tin nhắn Messenger ngay lập tức (Ưu tiên cao nhất)
    device_id = getattr(driver, "serial", "") or ""
    current_username = _normalize_username_value(current_username)
    ensure_messenger_command_bot_online(driver, account)

    if _messenger_queue_pending_sync(account, device_id=device_id):
        await send_messenger_queue_from_facebook_home(
            driver,
            account,
            reason="queue before fb_natural_task",
            restart_event=restart_event,
        )
        return
    
    await check_and_crawl_messenger_priority(driver, account)
    if not current_username:
        try:
            state = load_state(driver.serial)
        except Exception:
            state = {}
        if isinstance(state, dict):
            current_username = _normalize_username_value(state.get("current_username"))
    if not current_username:
        current_username = _normalize_username_value(account)

    can_run_friend_actions = bool(str(crm_id or "").strip().isdigit() and current_username)
    actions = [
        # ("Bình luận thương hiệu", lambda: comment_recruitment_post(driver, account)), 
        ("Xem story", lambda: watch_story(driver, action_name="Xem story",screen_standing_event=screen_standing_event, restart_event=restart_event)),
        ("Xem reels", lambda: watch_reels(driver, action_name="Xem reels",screen_standing_event=screen_standing_event, restart_event=restart_event)),
        ("Lướt tin tuyển dụng", lambda: surf_fb(driver, action_name="Lướt tin tuyển dụng", screen_standing_event=screen_standing_event, restart_event=restart_event)),  
        ("Đồng ý lời mời kết bạn", lambda: accept_facebook_friend_requests(driver, device_id=driver.serial, action_name="Đồng ý lời mời kết bạn", screen_standing_event=screen_standing_event, restart_event=restart_event)),
        # ("Thăm tường bạn bè", lambda: load_facebook_friends_list_advanced(driver, device_id=driver.serial, action_name="Thăm tường bạn bè", visit_friends=True, accept_requests=False, screen_standing_event=screen_standing_event, restart_event=restart_event)), 
        ("Thăm trang cá nhân", lambda: tham_trang_ca_nhan(driver, action_name="Thăm trang cá nhân", screen_standing_event=screen_standing_event, restart_event=restart_event)),
        ("Xem reels2", lambda: watch_reels(driver, action_name="Xem reels",screen_standing_event=screen_standing_event, restart_event=restart_event)), 
        ("Lướt tin tuyển dụng2", lambda: surf_fb(driver, action_name="Lướt tin tuyển dụng", screen_standing_event=screen_standing_event, restart_event=restart_event)), 
        # ("Thăm tường bạn bè2", lambda: load_facebook_friends_list_advanced(driver, device_id=driver.serial, action_name="Thăm tường bạn bè", visit_friends=True, accept_requests=False, screen_standing_event=screen_standing_event, restart_event=restart_event)),  
        ("Thăm trang cá nhân2", lambda: tham_trang_ca_nhan(driver, action_name="Thăm trang cá nhân", screen_standing_event=screen_standing_event, restart_event=restart_event)),
        ("Xem reels3", lambda: watch_reels(driver, action_name="Xem reels",screen_standing_event=screen_standing_event, restart_event=restart_event)), 
        ("Lướt tin tuyển dụng3", lambda: surf_fb(driver, action_name="Lướt tin tuyển dụng", screen_standing_event=screen_standing_event, restart_event=restart_event)), 
        # ("Thăm tường bạn bè3", lambda: load_facebook_friends_list_advanced(driver, device_id=driver.serial, action_name="Thăm tường bạn bè", visit_friends=True, accept_requests=False, screen_standing_event=screen_standing_event, restart_event=restart_event)),  
        ("Thăm trang cá nhân3", lambda: tham_trang_ca_nhan(driver, action_name="Thăm trang cá nhân", screen_standing_event=screen_standing_event, restart_event=restart_event)) 
    ]
    if can_run_friend_actions:
        actions.extend([
            # ("Kết bạn", lambda: add_friend(driver, crm_id, current_username)),
            # ("Kiểm tra trạng thái kết bạn", lambda: clean_expired_requests_fb(driver, current_username)),
        ])
    else:
        log_message(
            f"[{DEVICE_LIST_NAME.get(driver.serial, driver.serial)}] Bỏ qua tác vụ kết bạn do thiếu crm_id/current_username hợp lệ | crm_id={crm_id} | current_username={current_username or 'missing'}",
            logging.WARNING,
        )
    # Random hóa thứ tự các hành động
    event, _ = get_urgent_objects(driver.serial)
    random.shuffle(actions)
    protected_queue_action_terms = ("join", "post", "nhom", "nhóm", "dang bai", "đăng bài", "tham gia")
    for name, action in actions:
        if _messenger_queue_pending_sync(account, device_id=driver.serial):
            await send_messenger_queue_from_facebook_home(
                driver,
                account,
                reason=f"queue before action {name}",
                restart_event=restart_event,
            )
            return
        if get_passive_wait_only(driver.serial):
            log_message(
                f"[{DEVICE_LIST_NAME.get(driver.serial, driver.serial)}] PASSIVE mode ON -> dừng chuỗi nuôi hiện tại",
                logging.INFO,
            )
            return
        if restart_event and restart_event.get(driver.serial):
            log_message(f"[{DEVICE_LIST_NAME[driver.serial]}]Phát hiện restart event, dừng task", logging.WARNING)
            break
        pushed = await pump_mongo_pending_to_json(driver, limit=20)
        if pushed:
            log_message(
                f"[{DEVICE_LIST_NAME[driver.serial]}] Pumped {pushed} command(s) từ Mongo -> urgent_queue.json",
                logging.INFO,
            )

        while await scheduler_tick_json_to_device_queue(driver):
            pass
        if event.is_set():
            await handle_urgent_if_any(driver, crm_id, account)
            if get_passive_wait_only(driver.serial):
                log_message(
                    f"[{DEVICE_LIST_NAME.get(driver.serial, driver.serial)}] PASSIVE mode ON sau urgent -> thoát fb_natural_task",
                    logging.INFO,
                )
                return

        log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Thực hiện tác vụ: {name}", logging.INFO)
        action_task = asyncio.create_task(action())
        CURRENT_ACTION_TASK[driver.serial] = action_task

        # Tạo task "chờ urgent"
        urgent_wait = asyncio.create_task(event.wait())
        queue_wait = asyncio.create_task(wait_for_messenger_queue_pending(account, device_id=driver.serial))

        done, pending = await asyncio.wait(
            {action_task, urgent_wait, queue_wait},
            return_when=asyncio.FIRST_COMPLETED,
        )

        queue_wait_triggered = queue_wait in done and _messenger_queue_pending_sync(account, device_id=driver.serial)
        protected_from_queue_interrupt = any(term in name.lower() for term in protected_queue_action_terms)

        if queue_wait_triggered and action_task not in done:
            if protected_from_queue_interrupt:
                log_message(
                    f"[{DEVICE_LIST_NAME[driver.serial]}] Messenger queue xuất hiện trong '{name}', đây là tác vụ join/post nên chờ chạy xong rồi mới gửi",
                    logging.INFO,
                )
                with contextlib.suppress(asyncio.CancelledError):
                    if not urgent_wait.done():
                        urgent_wait.cancel()
                        await urgent_wait
                try:
                    await action_task
                except asyncio.CancelledError:
                    log_message(
                        f"[{DEVICE_LIST_NAME[driver.serial]}] Action '{name}' bị huỷ trong lúc chờ hoàn tất trước khi gửi Messenger queue",
                        logging.WARNING,
                    )
                finally:
                    CURRENT_ACTION_TASK.pop(driver.serial, None)
                await send_messenger_queue_from_facebook_home(
                    driver,
                    account,
                    reason=f"queue after protected action {name}",
                    restart_event=restart_event,
                )
                return

            log_message(
                f"[{DEVICE_LIST_NAME[driver.serial]}] Messenger queue xuất hiện trong '{name}', huỷ tác vụ nuôi để gửi tin 1-1",
                logging.INFO,
            )
            await cancel_current_action(driver)
            with contextlib.suppress(asyncio.CancelledError):
                await action_task
            if not urgent_wait.done():
                urgent_wait.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await urgent_wait
            CURRENT_ACTION_TASK.pop(driver.serial, None)
            await send_messenger_queue_from_facebook_home(
                driver,
                account,
                reason=f"queue interrupted action {name}",
                restart_event=restart_event,
            )
            return

        if urgent_wait in done and event.is_set():
            # 👉 Có urgent trong lúc action đang chạy
            log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Phát hiện urgent trong khi đang chạy '{name}', huỷ action và xử lý urgent", logging.INFO)

            # Huỷ action hiện tại
            await cancel_current_action(driver)  # dùng hàm bạn đã có

            # Xử lý các job urgent
            await handle_urgent_if_any(driver, crm_id, account)
            # Clear event để vòng sau chỉ chờ urgent mới
            event.clear()
    
            # Huỷ task urgent_wait nếu cần
            urgent_wait.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await urgent_wait
            if not queue_wait.done():
                queue_wait.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await queue_wait

            # Chuyển sang action FB kế tiếp (không chạy tiếp action cũ nữa)
            CURRENT_ACTION_TASK.pop(driver.serial, None)
            continue

        # Nếu action FB chạy xong trước
        if action_task in done:
            try:
                await action_task
            except asyncio.CancelledError:
                log_message(
                    f"[{DEVICE_LIST_NAME[driver.serial]}] Action '{name}' bị huỷ (có thể do urgent hoặc restart)",
                    logging.WARNING,
                )
                if get_passive_wait_only(driver.serial):
                    log_message(
                        f"[{DEVICE_LIST_NAME.get(driver.serial, driver.serial)}] PASSIVE mode ON sau khi huỷ action -> thoát fb_natural_task",
                        logging.INFO,
                    )
                    return
            finally:
                CURRENT_ACTION_TASK.pop(driver.serial, None)

        # Dọn task chờ urgent
        if not urgent_wait.done():
            urgent_wait.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await urgent_wait
        if not queue_wait.done():
            queue_wait.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await queue_wait

        if _messenger_queue_pending_sync(account, device_id=driver.serial):
            await send_messenger_queue_from_facebook_home(
                driver,
                account,
                reason=f"queue after action {name}",
                restart_event=restart_event,
            )
            return

        # Thời gian chờ có thể bị cắt bởi urgent
        await smart_sleep(driver, random.uniform(4, 6))
        if _messenger_queue_pending_sync(account, device_id=driver.serial):
            await send_messenger_queue_from_facebook_home(
                driver,
                account,
                reason=f"queue after sleep {name}",
                restart_event=restart_event,
            )
            return
        if get_passive_wait_only(driver.serial):
            log_message(
                f"[{DEVICE_LIST_NAME.get(driver.serial, driver.serial)}] PASSIVE mode ON sau smart_sleep -> thoát fb_natural_task",
                logging.INFO,
            )
            return

        await scheduler_tick_json_to_device_queue(driver)
        # Check urgent sau khi chạy xong action
        if event.is_set():
            await handle_urgent_if_any(driver, crm_id, account)
            if get_passive_wait_only(driver.serial):
                log_message(
                    f"[{DEVICE_LIST_NAME.get(driver.serial, driver.serial)}] PASSIVE mode ON sau urgent -> thoát fb_natural_task",
                    logging.INFO,
                )
                return
        await go_to_home_page(driver)


    log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Hoàn thành 1 chuỗi task")

# Luồng chạy chính của facebook
async def run_on_device_original(driver, screen_standing_event=None, restart_event=None):
    try:
        device_id = driver.serial
        if is_messenger_1_1_priority_active(driver):
            active_account = _normalize_account_value(
                getattr(driver, "_fb_1_1_priority_account", "")
                or get_current_account(device_id)
            )
            log_message(
                f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] Messenger 1-1 priority hold dang active"
                f"{f' cho account {active_account}' if active_account else ''} -> khong ve Home/Facebook profile",
                logging.INFO,
            )
            return
        device = await asyncio.to_thread(_load_device_account_with_fallback, device_id)
        resolved_crm_id = _extract_crm_id_from_device(device) or "default"
        current_username_for_tasks = ""

        # await clear_app(driver)
        await asyncio.to_thread(driver.press, "home")
        await asyncio.to_thread(driver.app_start, "com.facebook.katana", ".LoginActivity")
        await asyncio.sleep(random.uniform(4, 6))
        
        # Xác định account hiện tại
        raw_acc = await asyncio.to_thread(get_current_account, device_id)
        account = _normalize_account_value(raw_acc or device.get("current_account") or "default")

        # # ⭐ ƯU TIÊN: Kiểm tra tin nhắn ngay khi khởi động app
        # await check_and_crawl_messenger_priority(driver, account)

        check_current_acc = False
        current_username_for_tasks = ""

        picker_selected = await _select_normal_account_from_login_picker(driver, device)
        if picker_selected:
            await asyncio.sleep(random.uniform(5, 8))

        is_logged_in = await wait_logged_in(driver, timeout=20.0, poll=1.0)
        if not is_logged_in:
            late_picker_selected = await _select_normal_account_from_login_picker(driver, device)
            if late_picker_selected:
                await asyncio.sleep(random.uniform(5, 8))
                is_logged_in = await wait_logged_in(driver, timeout=20.0, poll=1.0)

        if not is_logged_in and await _facebook_home_visible(driver, max_retries=4):
            is_logged_in = True
            log_message(
                f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] check_logged_in timeout nhung Facebook Home da hien -> van sync Online",
                logging.WARNING,
            )

        if is_logged_in:
            check_current_acc = True
            current_username = await get_facebook_account_name(driver)
            number_of_friends = None
            try:
                number_of_friends = get_last_profile_friend_count(device_id)
            except Exception:
                number_of_friends = None
            log_message(
                f"[{DEVICE_LIST_NAME[device_id]}] current_username raw = {current_username}",
                logging.INFO
            )
            # cached_account, cached_username = _get_cached_current_account_identity(device_id, device)
            # current_account = cached_account or None
            current_account = None
            # current_username_clean = cached_username
            current_account_mapped_from_ui = False
            current_username_clean = _normalize_username_value(current_username)
            current_username_for_tasks = current_username_clean

            # if current_account and current_username_clean:
            if current_username_clean:
                log_message(
                    f"[{DEVICE_LIST_NAME[device_id]}] current_username_clean = {current_username_clean}", logging.INFO
                )
                current_account = await get_account_by_name(
                    current_username_clean,
                    device_id=device_id
                )
                log_message(
                    f"[{DEVICE_LIST_NAME[device_id]}] mapped current_account = {current_account}",
                    logging.INFO
                )
            # else:
            #     current_username = await get_facebook_account_name(driver)
            #     log_message(
            #         f"[{DEVICE_LIST_NAME[device_id]}] current_username raw = {current_username}",
            #         logging.INFO
            #     )

            #     current_username_clean = _normalize_username_value(current_username)
            #     current_username_for_tasks = current_username_clean

            #     if current_username_clean:
            #         log_message(
            #             f"[{DEVICE_LIST_NAME[device_id]}] current_username_clean = {current_username_clean}",
            #             logging.INFO
            #         )

            #         current_account = await get_account_by_name(
            #             current_username_clean,
            #             device_id=device_id
            #         )
            #         log_message(
            #             f"[{DEVICE_LIST_NAME[device_id]}] mapped current_account = {current_account}",
            #             logging.INFO
            #         )
            #     else:
            #         log_message(
            #             f"[{DEVICE_LIST_NAME[device_id]}] get_facebook_account_name trả về unknown_user",
            #             logging.WARNING
            #         )

            if current_username_clean and not current_account:
                current_account = _find_account_by_username_in_device(device, current_username_clean)
                if current_account:
                    log_message(
                        f"[{DEVICE_LIST_NAME[device_id]}] mapped current_account from device.accounts = {current_account}",
                        logging.INFO,
                    )
                else:
                    try:
                        current_account = _normalize_account_value(
                            await pymongo_management.get_account_by_name(
                                current_username_clean,
                                device_id=device_id,
                            )
                        )
                    except Exception as map_err:
                        current_account = ""
                        log_message(
                            f"[{DEVICE_LIST_NAME[device_id]}] get_account_by_name lỗi: {type(map_err).__name__} | {map_err}",
                            logging.WARNING,
                        )
                    if current_account:
                        log_message(
                            f"[{DEVICE_LIST_NAME[device_id]}] mapped current_account from Mongo = {current_account}",
                            logging.INFO,
                        )

            if current_username_clean:
                if current_account:
                    current_account_mapped_from_ui = True
                    await sync_device_accounts_status(
                        device_id=device_id,
                        current_account=current_account,
                        current_username=current_username_clean,
                        number_of_friends=number_of_friends,
                    )
                else:
                    log_message(
                        f"[{DEVICE_LIST_NAME[device_id]}] KHÔNG map được account từ username='{current_username_clean}'",
                        logging.WARNING
                    )

                # try:
                #     update_status = await pymongo_management.update_statusFB(
                #         username=current_username_clean,
                #         account=current_account,
                #         statusFB="Online",
                #         device_id=device_id
                #     )
                #     log_message(
                #         f"[{DEVICE_LIST_NAME[device_id]}] update_statusFB Online result = {update_status}",
                #         logging.INFO
                #     )
                # except Exception as e:
                #     log_message(
                #         f"[{DEVICE_LIST_NAME[device_id]}] update_statusFB Online lỗi: {type(e).__name__} | {e}\n{traceback.format_exc()}",
                #         logging.ERROR
                #     )

            else:
                log_message(
                    f"[{DEVICE_LIST_NAME[device_id]}] get_facebook_account_name trả về unknown_user",
                    logging.WARNING
                )

            if not current_username_for_tasks:
                current_username_for_tasks = _find_username_by_account(device, current_account)

            if not current_account:
                fallback_account = (
                    _normalize_account_value(get_current_account(device_id))
                    or _normalize_account_value(device.get("current_account") if isinstance(device, dict) else "")
                )
                if fallback_account:
                    current_account = fallback_account
                    if not current_username_for_tasks:
                        current_username_for_tasks = _find_username_by_account(device, current_account)
                    log_message(
                        f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] fallback current_account khi khong map duoc username: {current_account}",
                        logging.WARNING,
                    )
                    await sync_device_accounts_status(
                        device_id=device_id,
                        current_account=current_account,
                        current_username=current_username_for_tasks or current_account,
                        number_of_friends=number_of_friends,
                    )

            account = (
                current_account
                or _normalize_account_value(get_current_account(device_id))
                or _normalize_account_value(device.get("current_account") if isinstance(device, dict) else "")
                or "default"
            )
            crm_id = resolved_crm_id

            if current_account_mapped_from_ui:
                try:
                    selected_account, selected_username, startup_switched = await maybe_switch_to_lowest_post_account_on_startup(
                        driver,
                        device,
                        account,
                        current_username_for_tasks,
                    )
                    if selected_account:
                        account = _normalize_account_value(selected_account) or account
                    if selected_username:
                        current_username_for_tasks = selected_username
                    if startup_switched:
                        try:
                            account = _normalize_account_value(get_current_account(device_id)) or account
                        except Exception:
                            pass
                        current_username_for_tasks = _find_username_by_account(device, account) or current_username_for_tasks
                except Exception as startup_switch_err:
                    log_message(
                        f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] Startup account rebalance error: {type(startup_switch_err).__name__} | {startup_switch_err}",
                        logging.WARNING,
                    )
            else:
                log_message(
                    f"[{DEVICE_LIST_NAME.get(device_id, device_id)}] Startup account rebalance skipped: current Facebook account was not mapped from UI",
                    logging.WARNING,
                )
        else:
            set_offline(device_id)
            log_message(
                f"[{DEVICE_LIST_NAME[device_id]}] set_offline local runtime state OK | device_id={device_id}",
                logging.INFO
            )

            try:
                update_status = await pymongo_management.update_statusFB(
                    statusFB="Offline",
                    device_id=device_id
                )
                log_message(
                    f"[{DEVICE_LIST_NAME[device_id]}] update_statusFB Offline result = {update_status}",
                    logging.INFO
                )
            except Exception as e:
                log_message(
                    f"[{DEVICE_LIST_NAME[device_id]}] update_statusFB Offline lỗi: {type(e).__name__} | {e}\n{traceback.format_exc()}",
                    logging.ERROR
                )

            account = _normalize_account_value(device.get("current_account") if isinstance(device, dict) else "") or "default"
            current_username_for_tasks = _find_username_by_account(device, account)
            crm_id = resolved_crm_id

        # if await check_logged_in(driver):
        #     current_username = await get_facebook_account_name(driver)
        #     if current_username != "unknown_user":
        #         current_username = current_username.replace('_',' ')
        #         current_account = await get_account_by_name(current_username)
        #         update_status = await pymongo_management.update_statusFB(username=current_account, statusFB="Online")
        # elif device_id:
        #     update_status = await pymongo_management.update_statusFB(device_id, "Offline")

        # device = load_device_account(device_id)

        # if device == {}:
        #     log_message(f"[{DEVICE_LIST_NAME[device_id]}] Không tìm thấy dữ liệu cho thiết bị", logging.WARNING)
        #     crm_id = "22615833"
        #     account = "default"
        # else:
        #     crm_id = device['user']['crm_id']
        #     # Chuyển tài khoản
        #     # last_time = device['time_logged_in']
        #     # if (last_time != '0') and (datetime.fromisoformat(last_time) + timedelta(hours=random.randint(4,6))) < datetime.now():
        #     #     # Đủ thời gian, chuyển tài khoản

        #     account_count = device['accountCount']
        #     current_account_index=0
        #     for acc in device['accounts']:
        #         if acc['account'] == device['current_account']:
        #             break
        #         current_account_index+=1
        #     next_account_index = current_account_index
        #     swap_time = time.time()
        #     # check_current_acc = True
        #     # log_message(f"{DEVICE_LIST_NAME[device_id]} Bắt đầu chuyển tài khoản", logging.INFO)
        #     while time.time() - swap_time <= 600:
        #         next_account_index = (next_account_index + 1) % account_count
        #         next_account = device['accounts'][next_account_index]

        #         check_current_acc = await swap_account_new(driver, next_account)
        #         if check_current_acc == True:
        #             device['current_account'] = next_account['account']
        #             break
        #         if next_account_index == current_account_index:
        #             log_message(f"{DEVICE_LIST_NAME[device_id]} Không chuyển được tài khoản nào", logging.INFO)
        #             break
        #     await asyncio.sleep(10)
        #     account = device['current_account']
        # Kiểm tra trạng thái màn hình để phòng trường hợp lỗi mạng khi đăng xuất 
        if screen_standing_event.get(driver.serial):
            screen_standing_event[driver.serial] = False
        if account and account != "default":
            if _messenger_queue_pending_sync(account, device_id=device_id):
                await send_messenger_queue_from_facebook_home(
                    driver,
                    account,
                    reason="queue in main loop",
                    restart_event=restart_event,
                )
                return
            ensure_messenger_command_bot_online(driver, account)
            await check_and_crawl_messenger_priority(driver, account)
            if _messenger_queue_pending_sync(account, device_id=device_id):
                await send_messenger_queue_from_facebook_home(
                    driver,
                    account,
                    reason="queue after Messenger priority",
                    restart_event=restart_event,
                )
                return
            if is_messenger_1_1_priority_active(driver, account):
                await run_messenger_1_1_priority_session(
                    driver,
                    account,
                    restart_event=restart_event,
                    reason="CRM socket send hold",
                )
                return
        facebook_home_ready = await ensure_facebook_home_after_messenger(driver, timeout=8)
        # tasks nuôi fb
        if check_current_acc or facebook_home_ready or (await my_find_element(driver, {("xpath", '//*[contains(@content-desc,"Trang chủ") and contains(@content-desc,"Tab")]')}, 4, back_if_not_found=True)):
            if get_passive_wait_only(driver.serial):
                log_message(
                    f"[{DEVICE_LIST_NAME[driver.serial]}] PASSIVE_WAIT_ONLY=True -> không chạy nuôi Facebook, chỉ chờ lệnh RabbitMQ",
                    logging.INFO
                )
                await passive_wait_for_commands(
                    driver,
                    crm_id,
                    account,
                    restart_event=restart_event
                )
            else:
                await fb_natural_task(
                    driver,
                    crm_id,
                    account,
                    current_username=current_username_for_tasks,
                    screen_standing_event=screen_standing_event,
                    restart_event=restart_event
                )
                if (not get_passive_wait_only(driver.serial)) and not (restart_event and restart_event.get(driver.serial)):
                    try:
                        switched = await maybe_auto_switch_account_after_natural_task(driver, account)
                        if switched:
                            try:
                                account = _normalize_account_value(get_current_account(driver.serial)) or account
                            except Exception:
                                pass
                    except Exception as switch_err:
                        log_message(
                            f"[{DEVICE_LIST_NAME.get(driver.serial, driver.serial)}] Auto switch after natural task error: {type(switch_err).__name__} | {switch_err}",
                            logging.WARNING,
                        )
        else:
            log_message(f"[{DEVICE_LIST_NAME[driver.serial]}] Không thực hiện được tác vụ do không vào được tài khoản nào", logging.ERROR)
            await asyncio.to_thread(driver.app_start, "com.facebook.katana")
        # await share_post(driver, text=random.choice(SHARES))
    except Exception as e:
        log_message(f"[{DEVICE_LIST_NAME[driver.serial]}]❌ Lỗi khi chạy luồng Facebook: {type(e).__name__}\n {e}", logging.ERROR)
