# Gợi ý tối ưu cho project FB mobile (command-only qua RabbitMQ)

## Kết luận nhanh

1. Nút thắt lớn nhất hiện tại là `DEVICE_ACTION_LOCK = asyncio.Lock()` trong `fb_task_patched.py`.
   - Đây là **lock toàn cục**, khiến urgent task của nhiều máy bị chạy **nối đuôi nhau**.
   - Cần đổi sang **lock theo từng device**.

2. Nếu muốn hệ thống chỉ nhận lệnh RabbitMQ để:
   - tham gia nhóm
   - đăng bài nhóm
   - lấy link bài viết

   thì nên tắt tạm:
   - toàn bộ `fb_natural_task(...)`
   - toàn bộ pha Zalo trong `main_merged.py`
   - `global_urgent_scheduler()` nếu không còn dùng `urgent_queue.json`

3. Tính năng **lấy link bài viết** thực ra đã có sẵn trong `module/post_to_group.py`:
   - sau khi đăng bài, code gọi `copy_post_link_for_group_by_content_text(...)`
   - sau đó gọi `update_post_link(command_id, post_link)` để lưu link
   - nghĩa là **không cần action RabbitMQ riêng** nếu bạn chỉ cần link ngay sau khi post.

## File đã chuẩn bị sẵn

- `main_merged_command_only.py`
- `fb_task_patched_command_only.py`

Đây là 2 bản đã chỉnh theo hướng command-only.

## Những chỗ cần sửa trong code gốc

### 1) Đổi lock toàn cục thành lock theo máy

Trong `fb_task_patched.py`, thay:

```python
DEVICE_ACTION_LOCK = asyncio.Lock()
```

thành:

```python
DEVICE_ACTION_LOCKS: dict[str, asyncio.Lock] = {}


def get_device_action_lock(device_id: str) -> asyncio.Lock:
    lock = DEVICE_ACTION_LOCKS.get(device_id)
    if lock is None:
        lock = DEVICE_ACTION_LOCKS[device_id] = asyncio.Lock()
    return lock
```

và thay:

```python
async with DEVICE_ACTION_LOCK:
```

thành:

```python
async with get_device_action_lock(driver.serial):
```

---

### 2) Bật chế độ command-only

Trong `fb_task_patched.py`, thêm:

```python
COMMAND_ONLY_MODE = True
ALLOWED_COMMAND_ACTIONS = {"join_group", "post_group"}
```

Lưu ý:
- `post_group` đã bao gồm bước lấy link bài viết.
- Nếu sau này bạn muốn action riêng kiểu `get_post_link`, có thể thêm tiếp.

---

### 3) Chặn các action ngoài phạm vi command-only

Trong `handle_urgent_if_any(...)`, sau khi normalize action, thêm kiểm tra:

```python
if COMMAND_ONLY_MODE and action not in ALLOWED_COMMAND_ACTIONS:
    reason = f"Action '{action}' bị chặn trong COMMAND_ONLY_MODE"
    failed += 1
    last_reason = reason
    log_message(
        f"[{DEVICE_LIST_NAME.get(driver.serial, driver.serial)}] {reason}",
        logging.WARNING,
    )
    if job_id and (not is_private_api_job):
        notify_ack(job_id, "failed", reason=reason)
    if job_id and is_private_api_job:
        await private_api_client_patched.mark_done(job_id, ok=False, reason=reason)
    continue
```

---

### 4) Tắt task nuôi Facebook tự động

Trong `fb_task_patched.py`, thay chỗ gọi:

```python
await fb_natural_task(driver, crm_id, account, ...)
```

bằng loop chờ RabbitMQ:

```python
async def fb_command_only_loop(driver, crm_id: str, account: str, restart_event=None):
    event, _ = get_urgent_objects(driver.serial)

    while True:
        if restart_event and restart_event.get(driver.serial):
            break
        await event.wait()
        await handle_urgent_if_any(driver, crm_id, account)
        await asyncio.sleep(0.2)
```

và trong `run_on_device_original(...)`:

```python
if COMMAND_ONLY_MODE:
    await fb_command_only_loop(driver, crm_id, account, restart_event=restart_event)
else:
    await fb_natural_task(driver, crm_id, account, screen_standing_event=screen_standing_event, restart_event=restart_event)
```

---

### 5) Tắt pha Zalo trong `main_merged.py`

Bạn có thể comment tạm thời các dòng:

```python
handler = DeviceHandler(driver, device_id)
await handler.connect()
```

và toàn bộ block pha Zalo:

```python
await asyncio.to_thread(driver.app_stop, FACEBOOK_PKG)
current_phase["value"] = "zalo"
logging.info("[%s] BẮT ĐẦU pha Zalo", DEVICE_LIST_NAME.get(device_id, device_id))

await asyncio.to_thread(driver.app_start, ZALO_PKG)
await asyncio.sleep(2.0)

logging.info("[%s] GỌI handler.run(Zalo)", DEVICE_LIST_NAME.get(device_id, device_id))
await asyncio.to_thread(handler.run, screen_standing_event)
logging.info("[%s] KẾT THÚC pha Zalo", DEVICE_LIST_NAME.get(device_id, device_id))
```

Nếu làm kiểu cờ:

```python
if COMMAND_ONLY_MODE:
    return
```

ngay sau pha Facebook thì gọn hơn.

---

### 6) Tắt scheduler file-based nếu chỉ dùng RabbitMQ

Trong `main_merged.py`, thay:

```python
await asyncio.gather(
    run_all_devices(),
    global_urgent_scheduler(),
)
```

bằng:

```python
await run_all_devices()
```

nếu bạn **không còn dùng** `urgent_queue.json` nữa.

---

### 7) Giảm poll/log để nhẹ hơn khi nhiều máy

Khuyến nghị:

- `InactivityWatchdog.run()`:
  - từ `time.sleep(0.5)` -> `1.5` hoặc `2.0`
- `run_all_devices()`:
  - chỉ log danh sách thiết bị khi có `added/removed`
  - tăng chu kỳ scan ADB từ `10s` lên `20s`
- `device_supervisor()`:
  - khi command-only có thể tăng nhịp `await asyncio.sleep(...)` từ `2s` lên `5s`

## Ghi chú về "lấy link bài viết"

Đã có sẵn trong `module/post_to_group.py` đoạn cuối:

```python
post_link = await toolfacebook_lib.copy_post_link_for_group_by_content_text(...)
await pymongo_management.update_post_link(command_id, post_link)
```

Nghĩa là pipeline hiện tại là:

`RabbitMQ post_group -> post bài -> copy link -> update Mongo`

## File đính kèm

- `main_merged_command_only.py`: bản main nhẹ hơn
- `fb_task_patched_command_only.py`: bản fb_task command-only + lock theo device
