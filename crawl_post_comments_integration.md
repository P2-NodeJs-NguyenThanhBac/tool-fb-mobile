# Tích hợp module crawl comment vào project

## 1) Thêm file module

Copy file `crawl_post_comments.py` vào thư mục `module/` của project và đổi tên thành:

```text
module/crawl_post_comments.py
```

## 2) Sửa `module/__init__.py`

Thêm dòng:

```python
from .crawl_post_comments import *
```

## 3) Sửa `fb_task_patched.py`

### 3.1 Chuẩn hoá action

Trong block normalize action, thêm:

```python
elif action in ("crawl_post_comments", "crawl_comments", "fetch_post_comments"):
    action = "crawl_post_comments"
```

### 3.2 Nếu muốn switch đúng account trước khi cào comment

Trong block:

```python
if action in ("post_wall", "post_group", "join_group", "delete_post", "edit_post"):
```

đổi thành:

```python
if action in ("post_wall", "post_group", "join_group", "delete_post", "edit_post", "crawl_post_comments"):
```

### 3.3 Thêm nhánh action mới

Trong block xử lý action, thêm:

```python
elif action == "crawl_post_comments":
    from module.crawl_post_comments import crawl_post_comments
    result = await crawl_post_comments(
        driver,
        command_id=command_id_for_task,
        post_link=post_link,
        include_profile_link=bool(params.get("include_profile_link", True)),
        max_comments=int(params.get("max_comments", 200)),
        replace_existing=bool(params.get("replace_existing", True)),
        back_to_facebook=True,
    )
    ok = bool(result.get("ok"))
    reason = f"total_comments={result.get('total_comments', 0)}"
```

## 4) Nếu bạn dùng queue từ Mongo Commands

Trong `pymongo_management.py`, hàm `claim_pending_commands_to_json(...)`, thêm type mới:

```python
"type": {"$in": ["post_to_wall", "post_to_group", "join_group", "left_group", "crawl_post_comments"]},
```

## 5) Payload mẫu để gọi urgent job

```json
{
  "action": "crawl_post_comments",
  "user_id": "0334304530",
  "post_link": "https://www.facebook.com/groups/123456/posts/7890123456789/",
  "params": {
    "include_profile_link": true,
    "max_comments": 200,
    "replace_existing": true
  }
}
```

## 6) Mongo collections mà module sẽ dùng

- `Binh-luan-trong-bai-dang-chi-tiet`
  - 1 document / 1 comment
- `Binh-luan-trong-bai-dang-crawl`
  - log tổng của 1 lần crawl theo `Post_link`

