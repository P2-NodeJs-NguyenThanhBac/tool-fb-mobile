from datetime import datetime, timedelta
from bson import ObjectId
import motor.motor_asyncio
import logging
import pymongo
import os
import urllib
import json
import logging
from util import log_message
RETRY_TYPES = ["post_to_wall", "post_to_group", "join_group", "left_group"]
RABBITMQ_INBOX_COLLECTION = "RabbitMQ-inbox"
RABBITMQ_OUTBOX_COLLECTION = "RabbitMQ-outbox"
RABBITMQ_ACTIVE_COMMAND_STATUS = "Đang thực hiện qua RabbitMQ"
POST_LINK_FETCH_FAILED_STATUS = "Đã đăng - Lấy link bài viết lỗi"
POST_LINK_CHECKING_STATUS = "Đã Đăng - đang kiểm tra kết quả"
POST_LINK_DEVICE_DISCONNECTED_STATUS = "Đã Đăng - lấy link lỗi do mất kết nối thiết bị"
POST_LINK_RETRY_QUEUE_STATUS = "Đã cho vào hàng chờ để lấy lại link"
POST_LINK_RETRY_ACTION = "fetch_group_post_link"
GROUP_POST_RETRY_QUEUE_STATUS = "Đã cho vào hàng chờ để đăng lại"
POST_LINK_ERROR_STATUSES = [
    POST_LINK_FETCH_FAILED_STATUS,
    POST_LINK_CHECKING_STATUS,
    POST_LINK_DEVICE_DISCONNECTED_STATUS,
    POST_LINK_RETRY_QUEUE_STATUS,
]
MONGO_SCAN_FALLBACK_GRACE_SECONDS = max(
    0,
    int(os.getenv("MONGO_SCAN_FALLBACK_GRACE_SECONDS", "60")),
)
RABBITMQ_TO_MONGO_FALLBACK_SECONDS = max(
    30,
    int(os.getenv("RABBITMQ_TO_MONGO_FALLBACK_SECONDS", "120")),
)
#-------------------------------------------------------------------------------------------------------------------------------

def _normalize_publish_status(cmd_type, post_status=None):
    s = (post_status or "").strip().lower()

    if cmd_type == "post_to_group":
        if s in (
            "pending",
            "đang chờ",
            "dang cho",
            "chờ duyệt",
            "cho duyet",
            "đang chờ duyệt",
            "dang cho duyet",
        ):
            return "Đang chờ duyệt"

        if s in (
            "posted",
            "đã đăng",
            "da dang",
            "đăng thành công",
            "dang thanh cong",
        ):
            return "Đã đăng thành công"

        # mặc định nếu không rõ nhưng đã lấy được link
        return "Đã đăng thành công"

    if cmd_type == "post_to_wall":
        return "Đã đăng thành công"

    return None

def _epoch_seconds(now=None):
    return int((now or datetime.now()).timestamp())

def _has_valid_epoch_seconds(value):
    return isinstance(value, (int, float)) and value > 0

async def _ensure_post_time_if_missing(collection, post_obj_id, update_fields, now=None):
    existing = await collection.find_one({"_id": post_obj_id}, {"time": 1})
    if not _has_valid_epoch_seconds((existing or {}).get("time")):
        update_fields["time"] = _epoch_seconds(now)

# Lệnh chung
def _safe_objectid(x: str):
    try:
        return ObjectId(x)
    except Exception:
        return None
    
def get_client_name(Type="Base"):
    with open("DatabaseAccounts.env", "r") as file:
        data = json.load(file).get(Type, {})
    username = urllib.parse.quote_plus(data["username"])
    password = urllib.parse.quote_plus(data["pwd"])
    return f"mongodb://{username}:{password}@123.24.206.25:27017/?authSource=admin"

def get_async_collection(collection_name, client_name=get_client_name(), db_name="Facebook"):
    """Tạo async collection client"""
    client = motor.motor_asyncio.AsyncIOMotorClient(client_name)
    db = client[db_name]
    collection = db[collection_name]
    return collection

#-------------------------------------------------------------------------------------------------------------------------------
# Lệnh liên quan đến collection "Link-groups"
async def get_groups_links():
    """Lấy danh sách các liên kết nhóm từ cơ sở dữ liệu."""
    collection = get_async_collection("Link-groups")
    return await collection.find().to_list(length=None)

async def get_group_to_join(FB_id):
    """Lấy liên kết nhóm từ cơ sở dữ liệu để tham gia."""
    collection = get_async_collection("Link-groups")
    cursor = collection.aggregate([
        {
            "$match": {
                "Status": "Hoạt động",
                "Joined_Accounts": {
                    "$not": { "$elemMatch": { "$eq": FB_id } }
                },
                "Temp_Joined_Accounts": {
                    "$not": { "$elemMatch": { "$eq": FB_id } }
                }
            }
        },
        {
            "$addFields": {
                "Joined_Count": { 
                    "$add": [
                        { "$size": { "$ifNull": ["$Joined_Accounts", []] } },
                        { "$size": { "$ifNull": ["$Temp_Joined_Accounts", []] } }
                    ]
                }
            }
        },
        {
            "$sort": { 
                "Joined_Count": 1,
                "Number_Of_Posts": -1
            }  
        },
        {
            "$limit": 1
        }
    ])
    
    groups = await cursor.to_list(length=1)
    if groups:
        group = groups[0]
        await collection.update_one(
            {"_id": group["_id"]}, 
            {"$addToSet": {"Temp_Joined_Accounts": FB_id}}
        )
        return group['Link']
    return None

async def update_group_name(group_link, new_name):
    """Cập nhật tên nhóm trong cơ sở dữ liệu."""
    collection = get_async_collection("Link-groups")
    result = await collection.update_one(
        {"Link": group_link}, 
        {"$set": {"Name": new_name}}
    )
    if result.matched_count == 0:  
        return {'message': '❌ Nhóm không tồn tại'}, logging.ERROR
    if result.modified_count == 0:
        return {'message': '⚠️ Tên nhóm không thay đổi'}, logging.WARNING
    return {'message': '✅ Cập nhật thành công'}, logging.INFO

async def update_group_status(group_link, status, number_of_posts):
    """Cập nhật trạng thái nhóm trong cơ sở dữ liệu."""
    collection = get_async_collection("Link-groups")
    result = await collection.update_one(
        {"Link": group_link}, 
        {"$set": {"Status": status, "Number_Of_Posts": number_of_posts}}
    )
    if result.matched_count == 0:
        return {'message': '❌ Nhóm không tồn tại'}, logging.ERROR
    if result.modified_count == 0:
        return {'message': '⚠️ Trạng thái nhóm không thay đổi'}, logging.WARNING
    return {'message': '✅ Cập nhật thành công'}, logging.INFO

async def update_temp_joined_accounts(user_id, group_link):
    """Thêm user_name vào danh sách các tài khoản tạm thời tham gia nhóm"""
    collection = get_async_collection("Link-groups")
    result = await collection.update_one(
        {"Link": group_link},
        {"$addToSet": {"Temp_Joined_Accounts": user_id}}
    )
    if result.matched_count == 0:
        return {'message': f'❌ Cập nhật tài khoản gửi yêu cầu tham gia nhóm: Nhóm {group_link} không tồn tại'}, logging.ERROR
    if result.modified_count == 0:
        return {'message': f'⚠️ Cập nhật tài khoản gửi yêu cầu tham gia nhóm: Nhóm {group_link} không có thay đổi nào'}, logging.WARNING
    return {'message': f'✅ Cập nhật tài khoản gửi yêu cầu tham gia nhóm: Nhóm {group_link} đã gửi yêu cầu tham gia thành công'}, logging.INFO

async def update_joined_accounts(user_id, group_link):
    """Thêm user_name vào danh sách các tài khoản đã tham gia nhóm"""
    collection = get_async_collection("Link-groups")
    result = await collection.update_one(
        {"Link": group_link},
        {
            "$addToSet": {"Joined_Accounts": user_id},
            "$pull": {"Temp_Joined_Accounts": user_id}
        }
    )
    print("[DEBUG-MONGO] update_joined_accounts CALLED:",
      "user_id=", user_id,
      "group_link=", group_link,
      "matched=", result.matched_count,
      "modified=", result.modified_count)
    
    if result.matched_count == 0:
        return {'message': f'❌ Cập nhật tài khoản đã tham gia nhóm: Nhóm {group_link} không tồn tại'}, logging.ERROR
    if result.modified_count == 0:
        return {'message': f'⚠️ Cập nhật tài khoản đã tham gia nhóm: Nhóm {group_link} không có thay đổi nào'}, logging.WARNING
    return {'message': f'✅ Cập nhật tài khoản đã tham gia nhóm: Nhóm {group_link} đã tham gia thành công'}, logging.INFO

async def get_unapproved_groups(user_id):
    """Lấy danh sách các nhóm chưa được phê duyệt."""
    collection = get_async_collection("Link-groups")
    groups = await collection.find({"Temp_Joined_Accounts": user_id}).to_list(length=None)
    return groups

async def remove_joined_account(user_id, group_link):
    """
    Xóa user_id khỏi danh sách tài khoản đã tham gia (Joined_Accounts) 
    và tài khoản tạm (Temp_Joined_Accounts) khi user rời nhóm.
    """
    collection = get_async_collection("Link-groups")
    
    # Sử dụng $pull để lấy user_id ra khỏi các mảng
    result = await collection.update_one(
        {"Link": group_link},
        {
            "$pull": {
                "Joined_Accounts": user_id,
                "Temp_Joined_Accounts": user_id
            }
        }
    )

    log_message("[DEBUG-MONGO] remove_joined_account CALLED:",
      "user_id=", user_id,
      "group_link=", group_link,
      "matched=", result.matched_count,
      "modified=", result.modified_count)

    # Kiểm tra xem nhóm có tồn tại trong DB không
    if result.matched_count == 0:
        return {'message': f'Cập nhật rời nhóm: Nhóm {group_link} không tồn tại trong DB'}, logging.ERROR
    
    # Kiểm tra xem có dữ liệu nào thay đổi không (nếu modified = 0 nghĩa là user không có trong nhóm từ trước)
    if result.modified_count == 0:
        return {'message': f'Cập nhật rời nhóm: User {user_id} không có trong danh sách thành viên của nhóm {group_link}'}, logging.WARNING
        
    return {'message': f'Cập nhật rời nhóm: User {user_id} đã được xóa khỏi nhóm {group_link} thành công'}, logging.INFO

#-------------------------------------------------------------------------------------------------------------------------------
# Lệnh liên quan tới collection "Binh-luan"
async def save_comment(group_name, post_link, content, comment, post_type, post_keywords):
    """Lưu bình luận vào cơ sở dữ liệu."""
    collection = get_async_collection("Binh-luan")
    await collection.insert_one({
        "Group_name": group_name,
        "Link-post": post_link,
        "Content": content,
        "Comment": comment,
        "Type": post_type,
        "Keywords": post_keywords,
        "Status": "Chưa xử lý",
        "Time": datetime.now()
    })

async def get_comment(user_id):
    """Lấy bình luận để xử lý"""
    # Kiểm tra KPI
    if not await check_kpi(user_id, "Bình luận"): 
        return {'message': '⚠️ Bình luận thương hiệu: Đã đạt KPI, không thể lấy bình luận'}, logging.WARNING

    # Tìm bình luận chưa xử lý mới nhất
    binh_luan_collection = get_async_collection("Binh-luan")
    binh_luan_moi_nhat = await binh_luan_collection.find_one(
        {"Status": "Chưa xử lý"},
        sort=[("Time", -1)]
    )

    if not binh_luan_moi_nhat:
        return {'message': '⚠️ Bình luận thương hiệu: Không có bình luận nào chưa xử lý'}, logging.WARNING

    # Lấy dữ liệu từ bản ghi mới nhất
    group_name = binh_luan_moi_nhat.get("Group_name")
    post_link = binh_luan_moi_nhat.get("Link-post")
    Content = binh_luan_moi_nhat.get("Content")
    Comment = binh_luan_moi_nhat.get("Comment")

    # Thêm bản ghi vào bảng thống kê
    thong_ke_collection = get_async_collection("Thong-ke-binh-luan")
    await thong_ke_collection.insert_one({
        "Group_name": group_name,
        "Link-post": post_link,
        "Content": Content,
        "Comment": Comment,
        "Time": datetime.now().timestamp(),
        "Commented_by": user_id
    })

    # Gọi hàm cập nhật trạng thái bài viết
    await update_crawled_post_status(post_link, "Đã bình luận")

    # Cập nhật trạng thái bình luận
    await binh_luan_collection.update_one(
        {"_id": binh_luan_moi_nhat["_id"]},
        {"$set": {"Status": "Đã bình luận"}}
    )

    return {
        'message': f'✅ Bình luận thương hiệu: Đã bình luận "{Comment}" vào bài viết {post_link}',
        'comment': Comment,
        'link': post_link
    }, logging.INFO

#-------------------------------------------------------------------------------------------------------------------------------
# Lệnh liên quan tới collection "Users"
async def get_account(user_id):
    collection = get_async_collection("Users")
    user = await collection.find_one({"user_id": user_id})
    if not user:
        comment_count = await get_async_collection("Thong-ke-binh-luan").count_documents({"Commented_by": user_id})
        group_count = await get_async_collection("Link-groups").count_documents(
            {
                "$or": [
                    {"Joined_Accounts": user_id},
                    {"Temp_Joined_Accounts": user_id}
                ]
            }
        )
        result = await collection.insert_one({"user_id": user_id, "kpi": {"Bình luận": comment_count + 1, "Tham gia nhóm": group_count + 5}, "kpi_per_day": {"Bình luận": 1, "Tham gia nhóm": 5}})
        if result.inserted_id:
            user = await collection.find_one({"_id": result.inserted_id})
    return user

async def check_kpi(user_id, kpi_type):
    """Kiểm tra KPI của người dùng."""
    user = await get_account(user_id)
    if not user:
        return False
    kpi = user.get("kpi", {}).get(kpi_type, 0)
    
    if kpi_type == "Bình luận":
        done = await get_async_collection("Thong-ke-binh-luan").count_documents({"Commented_by": user_id})
        return done < kpi
    if kpi_type == "Tham gia nhóm":
        done = await get_async_collection("Link-groups").count_documents({"Joined_Accounts": user_id})
        return done < kpi
    return False

async def update_daily_kpi():
    users_collection = get_async_collection("Users")
    comments_collection = get_async_collection("Thong-ke-binh-luan")
    groups_collection = get_async_collection("Link-groups")

    users = await users_collection.find({}, {"_id": 1, "user_id": 1, "kpi_per_day": 1}).to_list(length=None)
    updates = []

    for user in users:
        user_id = user.get("user_id")
        kpi_per_day = user.get("kpi_per_day", {})
        if not user_id:
            continue

        # Đếm số bình luận
        comment_count = await comments_collection.count_documents({ "Commented_by": user_id })

        # Đếm số nhóm đã tham gia
        group_join_count = await groups_collection.count_documents({
            "$or": [
                { "Joined_Accounts": user_id },
                { "Temp_Joined_Accounts": user_id }
            ]
        })

        # Tạo kpi mới
        new_kpi = {
            "Bình luận": comment_count + kpi_per_day.get("Bình luận", 0),
            "Tham gia nhóm": group_join_count + kpi_per_day.get("Tham gia nhóm", 0)
        }

        updates.append(pymongo.UpdateOne(
            { "_id": user["_id"] },
            { "$set": { "kpi": new_kpi } }
        ))

    if updates:
        await users_collection.bulk_write(updates)
#-------------------------------------------------------------------------------------------------------------------------------
# Lệnh liên quan tới collection "Questions"
async def upload_question(group_link, question, how_to_answer, answers):
    """Tải lên câu hỏi vào cơ sở dữ liệu."""
    collection = get_async_collection("Questions")
    # Kiểm tra câu hỏi đã tồn tại chưa
    existing_question = await collection.find_one({
        "Group_link": group_link,
        "Question": question,
    })
    if existing_question:
        return {'message': '⚠️ Tải lên câu hỏi: Câu hỏi đã tồn tại'}, logging.WARNING
    
    await collection.insert_one({
        "Group_link": group_link,
        "Question": question,
        "How_to_answer": how_to_answer,
        "Answers": answers,
        "Time": datetime.now(),
        "Status": "Chưa xử lý",
        "Answer": None
    })
    return {'message': '✅ Tải lên câu hỏi: Thành công'}, logging.INFO

async def get_answer(group_link, question):
    """Lấy câu trả lời cho câu hỏi từ cơ sở dữ liệu."""
    collection = get_async_collection("Questions")
    existing_question = await collection.find_one({
        "Group_link": group_link,
        "Question": question
    })
    if existing_question:
        if existing_question["Status"] == "Đã trả lời":
            return {'message': f'✅ Lấy câu trả lời: Câu hỏi "{question}": Thành công', 'answer': existing_question["Answer"]}, logging.INFO
    return {'message': f'⚠️ Lấy câu trả lời: Câu hỏi "{question}": Chưa được trả lời'}, logging.WARNING

#-------------------------------------------------------------------------------------------------------------------------------
# Lệnh liên quan tới collection Commands
async def get_commands(user_id, status="Chưa xử lý"):
    """Lấy thông tin lệnh từ cơ sở dữ liệu."""
    collection = get_async_collection("Commands")
    commands = await collection.find({"user_id": user_id, "Status": status}).to_list(length=None)
    # if status == "Chưa xử lý":
    #     collection.update_many(
    #         {"user_id": user_id, "Status": status},
    #         {"$set": {"Status": "Đang thực hiện"}}
    #     )
    return commands

# async def execute_command(command_id, status):
async def execute_command(command_id, status, post_status=None):
    """Thực hiện lệnh cho người dùng."""
    try:
        command_obj_id = ObjectId(command_id)
    except Exception:
        return {
            "message": f"❌ command_id không hợp lệ: {command_id}",
            "command_updated": False,
        }, logging.WARNING

    collection = get_async_collection("Commands")
    now = datetime.now()
    result = await collection.update_one(
        {"_id": command_obj_id},
        {"$set": {"Status": status, "Executed_at": now}}
    )
    if result.matched_count == 0:
        return {"message": "❌ Cập nhật trạng thái lệnh: Lệnh không tồn tại"}, logging.ERROR
    if result.modified_count == 0:
        return {"message": "⚠️ Cập nhật trạng thái lệnh: Lệnh không có thay đổi nào"}, logging.WARNING
    
    # Lấy thông tin lệnh đã thực hiện
    command = await collection.find_one({"_id": command_obj_id})
    type = command.get("type")
    if type == "post_to_group":
        params = command.get("params") if command else None
        bai_dang_collection = get_async_collection("Bai-dang")

        if status == "Đã thực hiện":
            final_post_status = _normalize_publish_status("post_to_group", post_status)
            update_fields = {"status": final_post_status, "updated_at": now}
            await _ensure_post_time_if_missing(
                bai_dang_collection,
                ObjectId(params["oid"]),
                update_fields,
                now,
            )
            await bai_dang_collection.update_one(
                {"_id": ObjectId(params["oid"])},
                {"$set": update_fields}
            )
        else:
            update_fields = {"status": status, "updated_at": now}
            if status == POST_LINK_CHECKING_STATUS:
                update_fields["time"] = _epoch_seconds(now)
            elif status == POST_LINK_FETCH_FAILED_STATUS and post_status:
                await _ensure_post_time_if_missing(
                    bai_dang_collection,
                    ObjectId(params["oid"]),
                    update_fields,
                    now,
                )
            await bai_dang_collection.update_one(
                {"_id": ObjectId(params["oid"])},
                {"$set": update_fields}
            )

    if type == "post_to_wall":
        params = command.get("params") if command else None
        bai_dang_tuong_collection = get_async_collection("Bai-dang-tuong")

        if status == "Đã thực hiện":
            final_post_status = _normalize_publish_status("post_to_wall", post_status)
            await bai_dang_tuong_collection.update_one(
                {"_id": ObjectId(params["oid"])},
                {"$set": {"status": final_post_status}}
            )
        else:
            await bai_dang_tuong_collection.update_one(
                {"_id": ObjectId(params["oid"])},
                {"$set": {"status": status}}
            )
    # if type == "post_to_group":
    #     params = command.get("params") if command else None
    #     bai_dang_collection = get_async_collection("Bai-dang")
    #     if status == "Đã thực hiện":
    #         await bai_dang_collection.update_one(
    #             {"_id": ObjectId(params["oid"])},
    #             {"$set": {"status": "Đã đăng thành công"}}
    #         )
    #     else:
    #         await bai_dang_collection.update_one(
    #             {"_id": ObjectId(params["oid"])},
    #             {"$set": {"status": status}}
    #         )
    # if type == "post_to_wall":
    #     params = command.get("params") if command else None
    #     bai_dang_tuong_collection = get_async_collection("Bai-dang-tuong")
    #     if status == "Đã thực hiện":
    #         await bai_dang_tuong_collection.update_one(
    #             {"_id": ObjectId(params["oid"])},
    #             {"$set": {"status": "Đã đăng thành công"}}
    #         )
    #     else:
    #         await bai_dang_tuong_collection.update_one(
    #             {"_id": ObjectId(params["oid"])},
    #             {"$set": {"status": status}}
    #         )
    return {"message": "✅ Cập nhật trạng thái lệnh: Lệnh đã được thực hiện"}, logging.INFO


async def register_rabbitmq_inbox_job(machine_code: str, job: dict):
    command_id = str((job or {}).get("command_id") or (job or {}).get("id") or "").strip()
    if not command_id:
        return None, False

    now = datetime.now()
    collection = get_async_collection(RABBITMQ_INBOX_COLLECTION)
    result = await collection.update_one(
        {"command_id": command_id},
        {
            "$setOnInsert": {
                "command_id": command_id,
                "job": job,
                "status": "RECEIVED",
                "received_at": now,
            },
            "$set": {
                "machine_code": machine_code,
                "latest_job": job,
                "last_seen_at": now,
                "updated_at": now,
            },
        },
        upsert=True,
    )
    doc = await collection.find_one({"command_id": command_id})
    return doc, result.upserted_id is not None


async def claim_command_for_rabbitmq(command_id: str, machine_code: str | None = None):
    """
    Cho RabbitMQ claim command truoc khi dua vao queue RAM.
    Neu command_id khong phai ObjectId hoac command khong ton tai trong Commands,
    coi nhu job RabbitMQ doc lap va van cho phep chay.
    """
    command_id = str(command_id or "").strip()
    try:
        command_obj_id = ObjectId(command_id)
    except Exception:
        return None, True, "rabbitmq_only"

    collection = get_async_collection("Commands")
    now = datetime.now()
    update_doc = {
        "$set": {
            "Status": RABBITMQ_ACTIVE_COMMAND_STATUS,
            "Execution_source": "rabbitmq",
            "Rabbitmq_received_at": now,
            "Rabbitmq_device_id": machine_code,
        },
        "$unset": {
            "Rabbitmq_started_at": "",
        },
    }
    doc = await collection.find_one_and_update(
        {
            "_id": command_obj_id,
            "Status": {
                "$in": [
                    "Chưa xử lý",
                    "Timeout",
                ]
            },
        },
        update_doc,
        return_document=pymongo.ReturnDocument.AFTER,
    )
    if doc:
        return doc, True, "claimed"

    existing = await collection.find_one(
        {"_id": command_obj_id},
        {"Status": 1, "Execution_source": 1},
    )
    if not existing:
        return None, True, "command_not_found"

    if str(existing.get("Status") or "") == RABBITMQ_ACTIVE_COMMAND_STATUS:
        return existing, True, "already_claimed"

    return existing, False, str(existing.get("Status") or "")


async def mark_rabbitmq_inbox_queued(command_id: str):
    command_id = str(command_id or "").strip()
    if not command_id:
        return

    await get_async_collection(RABBITMQ_INBOX_COLLECTION).update_one(
        {"command_id": command_id},
        {"$set": {"status": "QUEUED", "queued_at": datetime.now(), "updated_at": datetime.now()}},
    )


async def mark_rabbitmq_inbox_running(command_id: str):
    command_id = str(command_id or "").strip()
    if not command_id:
        return

    now = datetime.now()
    await get_async_collection(RABBITMQ_INBOX_COLLECTION).update_one(
        {"command_id": command_id},
        {"$set": {"status": "RUNNING", "started_at": now, "updated_at": now}},
    )
    try:
        command_obj_id = ObjectId(command_id)
    except Exception:
        return

    await get_async_collection("Commands").update_one(
        {"_id": command_obj_id},
        {"$set": {"Rabbitmq_started_at": now}},
    )


async def get_rabbitmq_inbox_status(command_id: str) -> str:
    command_id = str(command_id or "").strip()
    if not command_id:
        return ""

    doc = await get_async_collection(RABBITMQ_INBOX_COLLECTION).find_one(
        {"command_id": command_id},
        {"status": 1},
    )
    return str((doc or {}).get("status") or "").upper()


async def save_rabbitmq_result_outbox(payload: dict):
    payload = payload or {}
    command_id = str(payload.get("command_id") or "").strip()
    if not command_id:
        return

    now = datetime.now()
    outbox = get_async_collection(RABBITMQ_OUTBOX_COLLECTION)
    existing = await outbox.find_one({"command_id": command_id}, {"publish_status": 1})
    publish_status = (existing or {}).get("publish_status") or "PENDING"

    await outbox.update_one(
        {"command_id": command_id},
        {
            "$setOnInsert": {
                "command_id": command_id,
                "created_at": now,
                "publish_attempts": 0,
            },
            "$set": {
                "payload": payload,
                "publish_status": publish_status if publish_status == "PUBLISHED" else "PENDING",
                "updated_at": now,
            },
        },
        upsert=True,
    )

    await get_async_collection(RABBITMQ_INBOX_COLLECTION).update_one(
        {"command_id": command_id},
        {
            "$set": {
                "status": "FINISHED",
                "final_result": payload,
                "finished_at": now,
                "updated_at": now,
            }
        },
    )


async def mark_rabbitmq_result_publish_attempt(command_id: str, ok: bool, error: str | None = None):
    command_id = str(command_id or "").strip()
    if not command_id:
        return

    now = datetime.now()
    update_doc = {
        "$inc": {"publish_attempts": 1},
        "$set": {
            "last_publish_attempt_at": now,
            "updated_at": now,
        },
    }
    if ok:
        update_doc["$set"].update(
            {
                "publish_status": "PUBLISHED",
                "published_at": now,
                "last_publish_error": None,
            }
        )
    else:
        update_doc["$set"].update(
            {
                "publish_status": "PENDING",
                "last_publish_error": error or "",
            }
        )

    await get_async_collection(RABBITMQ_OUTBOX_COLLECTION).update_one(
        {"command_id": command_id},
        update_doc,
    )


async def get_pending_rabbitmq_outbox(limit: int = 100):
    cursor = (
        get_async_collection(RABBITMQ_OUTBOX_COLLECTION)
        .find({"publish_status": {"$ne": "PUBLISHED"}})
        .sort([("created_at", 1)])
        .limit(limit)
    )
    return await cursor.to_list(length=limit)


async def claim_recoverable_rabbitmq_jobs(
    machine_code: str,
    limit: int = 50,
    running_stale_minutes: int = 30,
    include_fresh_queued: bool = False,
):
    now = datetime.now()
    stale_before = now - timedelta(minutes=running_stale_minutes)
    collection = get_async_collection(RABBITMQ_INBOX_COLLECTION)
    queued_filter = {"status": "QUEUED"}
    if not include_fresh_queued:
        queued_filter["updated_at"] = {"$lte": stale_before}

    cursor = (
        collection.find(
            {
                "machine_code": machine_code,
                "$or": [
                    {"status": "RECEIVED"},
                    queued_filter,
                    {"status": "RUNNING", "updated_at": {"$lte": stale_before}},
                ],
            }
        )
        .sort([("received_at", 1)])
        .limit(limit)
    )
    docs = await cursor.to_list(length=limit)
    if not docs:
        return []

    command_ids = [doc.get("command_id") for doc in docs if doc.get("command_id")]
    if command_ids:
        await collection.update_many(
            {"command_id": {"$in": command_ids}},
            {"$set": {"status": "QUEUED", "queued_at": now, "updated_at": now}},
        )
    return docs


async def claim_pending_commands_to_json(
    limit: int = 50,
    queued_by: str = "",
    allowed_user_ids: list[str] | None = None,
    max_retry: int =10,
    stuck_minutes: int = 10,
    device_id: str | None = None,
):
    col = get_async_collection("Commands")
    out = []
    now = datetime.now()
    stuck_before = now - timedelta(minutes=stuck_minutes)
    rabbitmq_fallback_before = now - timedelta(seconds=MONGO_SCAN_FALLBACK_GRACE_SECONDS)
    rabbitmq_stalled_before = now - timedelta(seconds=RABBITMQ_TO_MONGO_FALLBACK_SECONDS)
    mongo_fallback_ready_filter = {
        "Mongo_scan_first_seen_at": {"$lte": rabbitmq_fallback_before}
    }

    base_filter = {
        "type": {"$in": ["post_to_wall", "post_to_group", "join_group", "left_group", "crawl_post_comments"]},
        "$and": [
            {
                "$or": [
                    {"retry_count": {"$exists": False}},
                    {"retry_count": {"$lt": max_retry}},
                ]
            }
        ],
        "$or": [
            # 🟡 Lệnh mới chỉ cho Mongo nhặt sau 1 khoảng grace để RabbitMQ có quyền chạy trước.
            {
                "$and": [
                    {"Status": "Chưa xử lý"},
                    mongo_fallback_ready_filter,
                ]
            },
            {"Status": "Timeout"},
            {
                "$and": [
                    {"Status": RABBITMQ_ACTIVE_COMMAND_STATUS},
                    {"Rabbitmq_received_at": {"$lte": rabbitmq_stalled_before}},
                    {
                        "$or": [
                            {"Rabbitmq_started_at": {"$exists": False}},
                            {"Rabbitmq_started_at": None},
                        ]
                    },
                ]
            },
            # {"Status": {"$regex": "lỗi", "$options": "i"}},

            # # 🔴 kẹt quá lâu
            # {
            #     "Status": "Đang thực hiện",
            #     "Executed_at": {"$lte": stuck_before},
            # },

            # 🟠 đã queue nhưng chưa chạy xong
            {
                "Status": GROUP_POST_RETRY_QUEUE_STATUS,
                "Queued_to_json_at": {"$lte": stuck_before},
            },
        ],
    }

    if allowed_user_ids:
        base_filter["user_id"] = {
            "$in": [str(x).strip() for x in allowed_user_ids if str(x).strip()]
        }

    normalized_device_id = str(device_id or "").strip()
    device_scope_filter = None
    if normalized_device_id:
        device_scope_filter = {
            "$or": [
                {"device_ids": normalized_device_id},
                {"device_id": normalized_device_id},
                {
                    "$and": [
                        {
                            "$or": [
                                {"device_ids": {"$exists": False}},
                                {"device_ids": None},
                                {"device_ids": []},
                            ]
                        },
                        {
                            "$or": [
                                {"device_id": {"$exists": False}},
                                {"device_id": None},
                                {"device_id": ""},
                            ]
                        },
                    ]
                },
            ]
        }
        base_filter["$and"].append(device_scope_filter)

    first_seen_filter = {
        "type": base_filter["type"],
        "Status": "Chưa xử lý",
        "Mongo_scan_first_seen_at": {"$exists": False},
    }
    if allowed_user_ids:
        first_seen_filter["user_id"] = base_filter["user_id"]
    if device_scope_filter:
        first_seen_filter["$and"] = [device_scope_filter]
    await col.update_many(
        first_seen_filter,
        {"$set": {"Mongo_scan_first_seen_at": now}},
    )

    for _ in range(limit):
        doc = await col.find_one_and_update(
            base_filter,
            {
                "$set": {
                    "Status": GROUP_POST_RETRY_QUEUE_STATUS,
                    "Queued_to_json_at": now,
                    "Queued_to_json_by": queued_by,
                    "Last_retry_at": now,
                    "Execution_source": "mongo_scan",
                },
                "$inc": {"retry_count": 1},
            },
            sort=[("_id", 1)],
            return_document=True,
        )
        if not doc:
            break
        out.append(doc)
        await get_async_collection(RABBITMQ_INBOX_COLLECTION).update_one(
            {
                "command_id": str(doc.get("_id") or ""),
                "status": {"$in": ["RECEIVED", "QUEUED"]},
            },
            {
                "$set": {
                    "status": "SUPERSEDED_BY_MONGO",
                    "superseded_at": now,
                    "updated_at": now,
                }
            },
        )

    return out


async def claim_retryable_group_post_errors_to_json(
    limit: int = 10,
    queued_by: str = "",
    allowed_user_ids: list[str] | None = None,
    device_id: str | None = None,
    max_retry: int = 5,
    stuck_minutes: int = 10,
    lookback_minutes: int = 30,
):
    """
    Claim post_to_group commands whose linked Bai-dang document has a retryable
    posting error. Link-fetch failures are handled by the dedicated link-retry
    flow, so they are intentionally excluded here.
    """
    if limit <= 0:
        return []

    command_col = get_async_collection("Commands")
    post_col = get_async_collection("Bai-dang")
    out = []
    now = datetime.now()
    stuck_before = now - timedelta(minutes=stuck_minutes)
    lookback_before = now - timedelta(minutes=max(1, int(lookback_minutes or 30)))
    lookback_before_epoch = int(lookback_before.timestamp())
    normalized_device_id = str(device_id or "").strip()
    normalized_allowed_user_ids = [
        str(x).strip() for x in (allowed_user_ids or []) if str(x).strip()
    ]

    retryable_status_filter = {
        "$and": [
            {
                "$or": [
                    {"status": {"$regex": "^\\s*Lỗi", "$options": "i"}},
                    {"status": {"$regex": "^\\s*Không", "$options": "i"}},
                ]
            },
            {"status": {"$nin": POST_LINK_ERROR_STATUSES}},
            {
                "$or": [
                    {"updated_at": {"$gte": lookback_before}},
                    {"created_at": {"$gte": lookback_before}},
                    {"time": {"$gte": lookback_before_epoch}},
                    {"_id": {"$gte": ObjectId.from_datetime(lookback_before)}},
                ]
            },
        ]
    }

    if normalized_allowed_user_ids:
        retryable_status_filter["$and"].append(
            {"user_id": {"$in": normalized_allowed_user_ids}}
        )

    if normalized_device_id:
        retryable_status_filter["$and"].append({
            "$or": [
                {"device_ids": normalized_device_id},
                {"device_id": normalized_device_id},
                {
                    "$and": [
                        {
                            "$or": [
                                {"device_ids": {"$exists": False}},
                                {"device_ids": None},
                                {"device_ids": []},
                            ]
                        },
                        {
                            "$or": [
                                {"device_id": {"$exists": False}},
                                {"device_id": None},
                                {"device_id": ""},
                            ]
                        },
                    ]
                },
            ]
        })

    failed_posts = await post_col.find(
        retryable_status_filter,
        {"_id": 1, "status": 1, "user_id": 1, "device_id": 1, "device_ids": 1},
    ).sort([("updated_at", 1), ("_id", 1)]).limit(max(limit * 5, limit)).to_list(
        length=max(limit * 5, limit)
    )
    failed_post_ids = [str(doc.get("_id")) for doc in failed_posts if doc.get("_id")]
    if not failed_post_ids:
        return []

    blocked_statuses = [
        "Đang thực hiện",
        "Bắt đầu thực hiện",
        RABBITMQ_ACTIVE_COMMAND_STATUS,
        GROUP_POST_RETRY_QUEUE_STATUS,
        *POST_LINK_ERROR_STATUSES,
    ]
    no_link_filter = {
        "$and": [
            {
                "$or": [
                    {"post_link": {"$exists": False}},
                    {"post_link": None},
                    {"post_link": ""},
                ]
            },
            {
                "$or": [
                    {"link": {"$exists": False}},
                    {"link": None},
                    {"link": ""},
                ]
            },
        ]
    }

    base_filter = {
        "type": "post_to_group",
        "params.oid": {"$in": failed_post_ids},
        "$and": [
            {
                "$or": [
                    {"retry_count": {"$exists": False}},
                    {"retry_count": {"$lt": max_retry}},
                ]
            }
        ],
        "$or": [
            {
                "$and": [
                    {
                        "$or": [
                            {"Status": {"$regex": "^\\s*Lỗi", "$options": "i"}},
                            {"Status": {"$regex": "^\\s*Không", "$options": "i"}},
                        ]
                    },
                    {"Status": {"$nin": POST_LINK_ERROR_STATUSES}},
                ]
            },
            {
                "$and": [
                    {"Status": GROUP_POST_RETRY_QUEUE_STATUS},
                    {"Queued_to_json_at": {"$lte": stuck_before}},
                ]
            },
            {
                "$and": [
                    {"Status": {"$nin": blocked_statuses}},
                    no_link_filter,
                ]
            },
        ],
    }

    if normalized_allowed_user_ids:
        base_filter["user_id"] = {
            "$in": normalized_allowed_user_ids
        }

    if normalized_device_id:
        base_filter["$and"].append({
            "$or": [
                {"device_ids": normalized_device_id},
                {"device_id": normalized_device_id},
                {
                    "$and": [
                        {
                            "$or": [
                                {"device_ids": {"$exists": False}},
                                {"device_ids": None},
                                {"device_ids": []},
                            ]
                        },
                        {
                            "$or": [
                                {"device_id": {"$exists": False}},
                                {"device_id": None},
                                {"device_id": ""},
                            ]
                        },
                    ]
                },
            ]
        })

    claimed_post_ids = set()
    for _ in range(limit):
        doc = await command_col.find_one_and_update(
            base_filter,
            {
                "$set": {
                    "Status": GROUP_POST_RETRY_QUEUE_STATUS,
                    "Queued_to_json_at": now,
                    "Queued_to_json_by": queued_by,
                    "Last_retry_at": now,
                    "Execution_source": "mongo_scan_post_error_retry",
                },
                "$unset": {"Retry_action": ""},
                "$inc": {"retry_count": 1},
            },
            sort=[("Last_retry_at", 1), ("_id", 1)],
            return_document=True,
        )
        if not doc:
            break

        out.append(doc)
        post_oid = str((doc.get("params") or {}).get("oid") or "").strip()
        post_status = next(
            (
                str(post.get("status") or "")
                for post in failed_posts
                if str(post.get("_id") or "") == post_oid
            ),
            "",
        )
        if post_oid:
            claimed_post_ids.add(post_oid)
            remaining_post_ids = [
                oid for oid in failed_post_ids if oid not in claimed_post_ids
            ]
            base_filter["params.oid"] = {"$in": remaining_post_ids}
        if post_oid:
            try:
                await post_col.update_one(
                    {
                        "_id": ObjectId(post_oid),
                        **retryable_status_filter,
                    },
                    {
                        "$set": {
                            "status": GROUP_POST_RETRY_QUEUE_STATUS,
                            "updated_at": now,
                        }
                    },
                )
            except Exception as e:
                log_message(
                    f"[POST_ERROR_RETRY] Không cập nhật được Bai-dang {post_oid}: {e}",
                    logging.WARNING,
                )

        log_message(
            (
                f"[POST_ERROR_RETRY] Claimed command={doc.get('_id')} "
                f"post_oid={post_oid or 'missing'} Bai-dang.status={post_status or 'unknown'} "
                f"user_id={doc.get('user_id') or (doc.get('params') or {}).get('user_id') or ''} "
                f"device={normalized_device_id or 'any'}"
            ),
            logging.INFO,
        )

        await get_async_collection(RABBITMQ_INBOX_COLLECTION).update_one(
            {
                "command_id": str(doc.get("_id") or ""),
                "status": {"$in": ["RECEIVED", "QUEUED"]},
            },
            {
                "$set": {
                    "status": "SUPERSEDED_BY_MONGO",
                    "superseded_at": now,
                    "updated_at": now,
                }
            },
        )

        if not base_filter["params.oid"]["$in"]:
            break

    if failed_posts and not out:
        sample = [
            {
                "_id": str(post.get("_id") or ""),
                "status": str(post.get("status") or ""),
                "user_id": str(post.get("user_id") or ""),
                "device_id": str(post.get("device_id") or post.get("device_ids") or ""),
            }
            for post in failed_posts[:3]
        ]
        log_message(
            (
                f"[POST_ERROR_RETRY] Found {len(failed_posts)} Bai-dang retry candidate(s) "
                f"but no matching Commands claimed | device={normalized_device_id or 'any'} "
                f"allowed_user_ids={len(allowed_user_ids or [])} sample={sample}. "
                "Check Commands.params.oid, user_id, device_id/device_ids, retry_count, Status, post_link/link."
            ),
            logging.INFO,
        )

    return out


async def claim_retryable_command_errors_to_json(
    limit: int = 10,
    queued_by: str = "",
    allowed_user_ids: list[str] | None = None,
    device_id: str | None = None,
    max_retry: int = 5,
    lookback_minutes: int = 24 * 60,
):
    """
    Claim post_to_group Commands that failed before a post/link was produced but
    are explicitly marked retryable. Post rejection/not-approved failures are
    intentionally excluded because they are handled as final post results.
    """
    if limit <= 0:
        return []

    command_col = get_async_collection("Commands")
    post_col = get_async_collection("Bai-dang")
    out = []
    now = datetime.now()
    lookback_before = now - timedelta(minutes=max(1, int(lookback_minutes or 24 * 60)))
    lookback_before_epoch = int(lookback_before.timestamp())
    normalized_device_id = str(device_id or "").strip()
    normalized_allowed_user_ids = [
        str(x).strip() for x in (allowed_user_ids or []) if str(x).strip()
    ]

    command_error_status_filter = {
        "$or": [
            {"Status": {"$regex": "^\\s*Lỗi", "$options": "i"}},
            {"Status": {"$regex": "^\\s*Không", "$options": "i"}},
        ]
    }
    retryable_error_filter = {
        "$or": [
            {"last_error.retryable": True},
            {"result.retryable": True},
            {"result.error.retryable": True},
            {"result.result.retryable": True},
            {"result.data.retryable": True},
        ]
    }
    no_link_filter = {
        "$and": [
            {
                "$or": [
                    {"post_link": {"$exists": False}},
                    {"post_link": None},
                    {"post_link": ""},
                ]
            },
            {
                "$or": [
                    {"link": {"$exists": False}},
                    {"link": None},
                    {"link": ""},
                ]
            },
            {
                "$or": [
                    {"result.post_link": {"$exists": False}},
                    {"result.post_link": None},
                    {"result.post_link": ""},
                ]
            },
            {
                "$or": [
                    {"result.result.post_link": {"$exists": False}},
                    {"result.result.post_link": None},
                    {"result.result.post_link": ""},
                ]
            },
            {
                "$or": [
                    {"result.data.post_link": {"$exists": False}},
                    {"result.data.post_link": None},
                    {"result.data.post_link": ""},
                ]
            },
        ]
    }
    recent_filter = {
        "$or": [
            {"finished_at": {"$gte": lookback_before}},
            {"Finished_at": {"$gte": lookback_before}},
            {"executed_at": {"$gte": lookback_before}},
            {"Executed_at": {"$gte": lookback_before}},
            {"published_at": {"$gte": lookback_before}},
            {"updated_at": {"$gte": lookback_before}},
            {"created_at": {"$gte": lookback_before}},
            {"Rabbitmq_received_at": {"$gte": lookback_before}},
            {"Rabbitmq_started_at": {"$gte": lookback_before}},
            {"time": {"$gte": lookback_before_epoch}},
            {"_id": {"$gte": ObjectId.from_datetime(lookback_before)}},
        ]
    }
    rejected_text_pattern = (
        "không được duyệt|khong duoc duyet|bị từ chối|bi tu choi|"
        "rejected|removed|not approved|declined"
    )
    not_post_rejected_filter = {
        "$nor": [
            {"last_error.error_code": "POST_REJECTED"},
            {"result.error_code": "POST_REJECTED"},
            {"result.error.error_code": "POST_REJECTED"},
            {"result.result.error_code": "POST_REJECTED"},
            {"result.data.error_code": "POST_REJECTED"},
            {
                "last_error.error_message": {
                    "$regex": rejected_text_pattern,
                    "$options": "i",
                }
            },
            {
                "result.reason": {
                    "$regex": rejected_text_pattern,
                    "$options": "i",
                }
            },
            {
                "result.error_message": {
                    "$regex": rejected_text_pattern,
                    "$options": "i",
                }
            },
            {
                "result.error.error_message": {
                    "$regex": rejected_text_pattern,
                    "$options": "i",
                }
            },
            {
                "result.result.reason": {
                    "$regex": rejected_text_pattern,
                    "$options": "i",
                }
            },
            {
                "result.result.error_message": {
                    "$regex": rejected_text_pattern,
                    "$options": "i",
                }
            },
            {
                "result.data.reason": {
                    "$regex": rejected_text_pattern,
                    "$options": "i",
                }
            },
            {
                "result.data.error_message": {
                    "$regex": rejected_text_pattern,
                    "$options": "i",
                }
            },
            {"result.post_status": {"$regex": "^(rejected|removed)$", "$options": "i"}},
            {"result.result.post_status": {"$regex": "^(rejected|removed)$", "$options": "i"}},
            {"result.data.post_status": {"$regex": "^(rejected|removed)$", "$options": "i"}},
        ]
    }

    base_filter = {
        "type": "post_to_group",
        "$and": [
            {
                "$or": [
                    {"retry_count": {"$exists": False}},
                    {"retry_count": {"$lt": max_retry}},
                ]
            },
            command_error_status_filter,
            retryable_error_filter,
            no_link_filter,
            recent_filter,
            not_post_rejected_filter,
        ],
    }

    if normalized_allowed_user_ids:
        base_filter["user_id"] = {"$in": normalized_allowed_user_ids}

    if normalized_device_id:
        base_filter["$and"].append({
            "$or": [
                {"device_ids": normalized_device_id},
                {"device_id": normalized_device_id},
                {
                    "$and": [
                        {
                            "$or": [
                                {"device_ids": {"$exists": False}},
                                {"device_ids": None},
                                {"device_ids": []},
                            ]
                        },
                        {
                            "$or": [
                                {"device_id": {"$exists": False}},
                                {"device_id": None},
                                {"device_id": ""},
                            ]
                        },
                    ]
                },
            ]
        })

    for _ in range(limit):
        doc = await command_col.find_one_and_update(
            base_filter,
            {
                "$set": {
                    "Status": GROUP_POST_RETRY_QUEUE_STATUS,
                    "Queued_to_json_at": now,
                    "Queued_to_json_by": queued_by,
                    "Last_retry_at": now,
                    "Execution_source": "mongo_scan_retryable_command_error",
                },
                "$unset": {"Retry_action": ""},
                "$inc": {"retry_count": 1},
            },
            sort=[("Last_retry_at", 1), ("finished_at", 1), ("_id", 1)],
            return_document=True,
        )
        if not doc:
            break

        out.append(doc)
        post_oid = str((doc.get("params") or {}).get("oid") or "").strip()
        if post_oid:
            try:
                await post_col.update_one(
                    {"_id": ObjectId(post_oid)},
                    {
                        "$set": {
                            "status": GROUP_POST_RETRY_QUEUE_STATUS,
                            "updated_at": now,
                        }
                    },
                )
            except Exception as e:
                log_message(
                    f"[COMMAND_ERROR_RETRY] Cannot update Bai-dang {post_oid}: {e}",
                    logging.WARNING,
                )

        error_payload = doc.get("last_error") or (doc.get("result") or {}).get("error") or {}
        log_message(
            (
                f"[COMMAND_ERROR_RETRY] Claimed command={doc.get('_id')} "
                f"post_oid={post_oid or 'missing'} "
                f"user_id={doc.get('user_id') or (doc.get('params') or {}).get('user_id') or ''} "
                f"device={normalized_device_id or 'any'} "
                f"reason={error_payload.get('error_message') or (doc.get('result') or {}).get('reason') or ''}"
            ),
            logging.INFO,
        )

        await get_async_collection(RABBITMQ_INBOX_COLLECTION).update_one(
            {
                "command_id": str(doc.get("_id") or ""),
                "status": {"$in": ["RECEIVED", "QUEUED"]},
            },
            {
                "$set": {
                    "status": "SUPERSEDED_BY_MONGO",
                    "superseded_at": now,
                    "updated_at": now,
                }
            },
        )

    return out


async def claim_failed_group_post_link_commands_to_json(
    limit: int = 10,
    queued_by: str = "",
    allowed_user_ids: list[str] | None = None,
    device_id: str | None = None,
    min_age_minutes: int = 15,
    max_age_minutes: int = 20,
    max_retry: int = 5,
    stuck_minutes: int = 10,
):
    """
    Claim post_to_group commands that already posted but failed to fetch link.
    Only commands whose last failed-status update is 15-20 minutes old are claimed.
    """
    col = get_async_collection("Commands")
    out = []
    now = datetime.now()
    oldest = now - timedelta(minutes=max_age_minutes)
    newest = now - timedelta(minutes=min_age_minutes)
    stuck_before = now - timedelta(minutes=stuck_minutes)
    oldest_epoch = int(oldest.timestamp())
    newest_epoch = int(newest.timestamp())

    age_filters = [
        {"Executed_at": {"$gte": oldest, "$lte": newest}},
        {"executed_at": {"$gte": oldest, "$lte": newest}},
        {"published_at": {"$gte": oldest, "$lte": newest}},
        {"updated_at": {"$gte": oldest, "$lte": newest}},
        {"time": {"$gte": oldest_epoch, "$lte": newest_epoch}},
    ]
    retry_source_statuses = [
        POST_LINK_FETCH_FAILED_STATUS,
        POST_LINK_CHECKING_STATUS,
    ]

    base_filter = {
        "type": "post_to_group",
        "$and": [
            {
                "$or": [
                    {"link_retry_count": {"$exists": False}},
                    {"link_retry_count": {"$lt": max_retry}},
                ]
            },
            {
                "$and": [
                    {
                        "$or": [
                            {"post_link": {"$exists": False}},
                            {"post_link": None},
                            {"post_link": ""},
                        ]
                    },
                    {
                        "$or": [
                            {"link": {"$exists": False}},
                            {"link": None},
                            {"link": ""},
                        ]
                    },
                ]
            },
        ],
        "$or": [
            {
                "$and": [
                    {"Status": {"$in": retry_source_statuses}},
                    {"$or": age_filters},
                ]
            },
            {
                "$and": [
                    {"Status": POST_LINK_RETRY_QUEUE_STATUS},
                    {"Queued_to_json_at": {"$lte": stuck_before}},
                ]
            },
        ],
    }

    if allowed_user_ids:
        base_filter["user_id"] = {
            "$in": [str(x).strip() for x in allowed_user_ids if str(x).strip()]
        }

    normalized_device_id = str(device_id or "").strip()
    if normalized_device_id:
        device_doc = await get_async_collection("devices").find_one(
            {"device_id": normalized_device_id},
            {"status": 1, "adb_connected": 1},
        )
        if device_doc and (
            device_doc.get("status") is False
            or device_doc.get("adb_connected") is False
        ):
            await mark_group_post_link_retry_device_disconnected(
                normalized_device_id,
                min_age_minutes=min_age_minutes,
                max_age_minutes=max_age_minutes,
            )
            return []

        base_filter["$and"].append({
            "$or": [
                {"device_ids": normalized_device_id},
                {"device_id": normalized_device_id},
                {
                    "$and": [
                        {
                            "$or": [
                                {"device_ids": {"$exists": False}},
                                {"device_ids": None},
                                {"device_ids": []},
                            ]
                        },
                        {
                            "$or": [
                                {"device_id": {"$exists": False}},
                                {"device_id": None},
                                {"device_id": ""},
                            ]
                        },
                    ]
                },
            ]
        })

    for _ in range(limit):
        doc = await col.find_one_and_update(
            base_filter,
            {
                "$set": {
                    "Status": POST_LINK_RETRY_QUEUE_STATUS,
                    "Retry_action": POST_LINK_RETRY_ACTION,
                    "Queued_to_json_at": now,
                    "Queued_to_json_by": queued_by,
                    "Last_retry_at": now,
                    "Execution_source": "mongo_scan_link_retry",
                },
                "$inc": {"link_retry_count": 1},
            },
            sort=[("Executed_at", 1), ("_id", 1)],
            return_document=True,
        )
        if not doc:
            break
        out.append(doc)

    return out


async def mark_group_post_link_retry_device_disconnected(
    device_id: str,
    *,
    min_age_minutes: int = 15,
    max_age_minutes: int = 20,
    limit: int = 100,
) -> int:
    """
    Mark link-retry candidates for a disconnected device so they are not claimed forever.
    """
    device_id = str(device_id or "").strip()
    if not device_id:
        return 0

    col = get_async_collection("Commands")
    now = datetime.now()
    oldest = now - timedelta(minutes=max_age_minutes)
    newest = now - timedelta(minutes=min_age_minutes)
    oldest_epoch = int(oldest.timestamp())
    newest_epoch = int(newest.timestamp())

    age_filters = [
        {"Executed_at": {"$gte": oldest, "$lte": newest}},
        {"executed_at": {"$gte": oldest, "$lte": newest}},
        {"published_at": {"$gte": oldest, "$lte": newest}},
        {"updated_at": {"$gte": oldest, "$lte": newest}},
        {"time": {"$gte": oldest_epoch, "$lte": newest_epoch}},
    ]

    query = {
        "type": "post_to_group",
        "Status": {
            "$in": [
                POST_LINK_FETCH_FAILED_STATUS,
                POST_LINK_CHECKING_STATUS,
                POST_LINK_RETRY_QUEUE_STATUS,
            ]
        },
        "$and": [
            {
                "$or": [
                    {"device_id": device_id},
                    {"device_ids": device_id},
                ]
            },
            {"$or": age_filters},
            {
                "$and": [
                    {
                        "$or": [
                            {"post_link": {"$exists": False}},
                            {"post_link": None},
                            {"post_link": ""},
                        ]
                    },
                    {
                        "$or": [
                            {"link": {"$exists": False}},
                            {"link": None},
                            {"link": ""},
                        ]
                    },
                ]
            },
        ],
    }

    docs = await col.find(query, {"_id": 1}).limit(limit).to_list(length=limit)
    updated = 0
    for doc in docs:
        command_id = str(doc.get("_id") or "")
        if not command_id:
            continue
        try:
            await execute_command(command_id, POST_LINK_DEVICE_DISCONNECTED_STATUS)
            await col.update_one(
                {"_id": doc["_id"]},
                {"$set": {"Device_disconnected_at": now}},
            )
            updated += 1
        except Exception as e:
            log_message(
                f"[LINK_RETRY] Không cập nhật được command bị rút thiết bị {command_id}: {e}",
                logging.WARNING,
            )

    return updated


def _missing_link_filter() -> dict:
    return {
        "$and": [
            {
                "$or": [
                    {"link": {"$exists": False}},
                    {"link": None},
                    {"link": ""},
                ]
            },
            {
                "$or": [
                    {"post_link": {"$exists": False}},
                    {"post_link": None},
                    {"post_link": ""},
                ]
            },
        ]
    }


def _today_local_bounds(now=None):
    now = now or datetime.now()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return start, end, int(start.timestamp()), int(end.timestamp())


def _first_non_empty(doc: dict, *keys: str) -> str:
    for key in keys:
        value = doc.get(key)
        if value is None:
            continue
        if isinstance(value, str):
            value = value.strip()
        if value:
            return value
    return ""


async def claim_today_success_group_posts_missing_link_to_json(
    limit: int = 10,
    queued_by: str = "",
    allowed_user_ids: list[str] | None = None,
    device_id: str | None = None,
    max_retry: int = 5,
    stuck_minutes: int = 10,
):
    """
    Claim Bai-dang documents posted today that are marked successful but still
    have no link. They are converted to fetch_group_post_link commands so the
    device reopens the group, finds the post by content, and updates the link.
    """
    if limit <= 0:
        return []

    post_col = get_async_collection("Bai-dang")
    command_col = get_async_collection("Commands")
    now = datetime.now()
    start, end, start_epoch, end_epoch = _today_local_bounds(now)
    stuck_before = now - timedelta(minutes=stuck_minutes)
    normalized_device_id = str(device_id or "").strip()
    normalized_allowed_user_ids = [
        str(x).strip() for x in (allowed_user_ids or []) if str(x).strip()
    ]

    date_filter = {
        "$or": [
            {"posted_at": {"$gte": start, "$lt": end}},
            {"updated_at": {"$gte": start, "$lt": end}},
            {"created_at": {"$gte": start, "$lt": end}},
            {"time": {"$gte": start_epoch, "$lt": end_epoch}},
            {"_id": {"$gte": ObjectId.from_datetime(start), "$lt": ObjectId.from_datetime(end)}},
        ]
    }
    post_filter = {
        "status": "Đã đăng thành công",
        "$and": [
            _missing_link_filter(),
            date_filter,
            {
                "$or": [
                    {"link_fetch_retry_count": {"$exists": False}},
                    {"link_fetch_retry_count": {"$lt": max_retry}},
                ]
            },
        ],
    }

    if normalized_allowed_user_ids:
        post_filter["user_id"] = {"$in": normalized_allowed_user_ids}

    if normalized_device_id:
        post_filter["$and"].append({
            "$or": [
                {"device_ids": normalized_device_id},
                {"device_id": normalized_device_id},
                {
                    "$and": [
                        {
                            "$or": [
                                {"device_ids": {"$exists": False}},
                                {"device_ids": None},
                                {"device_ids": []},
                            ]
                        },
                        {
                            "$or": [
                                {"device_id": {"$exists": False}},
                                {"device_id": None},
                                {"device_id": ""},
                            ]
                        },
                    ]
                },
            ]
        })

    projection = {
        "_id": 1,
        "user_id": 1,
        "user_name": 1,
        "content": 1,
        "Content": 1,
        "group_link": 1,
        "Group_link": 1,
        "Link": 1,
        "files": 1,
        "device_id": 1,
        "device_ids": 1,
    }
    posts = await post_col.find(
        post_filter,
        projection,
    ).sort([("updated_at", 1), ("_id", 1)]).limit(max(limit * 3, limit)).to_list(
        length=max(limit * 3, limit)
    )
    if not posts:
        return []

    out = []
    for post in posts:
        if len(out) >= limit:
            break

        post_oid = str(post.get("_id") or "").strip()
        user_id = str(post.get("user_id") or "").strip()
        content = _first_non_empty(post, "content", "Content")
        group_link = _first_non_empty(post, "group_link", "Group_link", "Link")
        if not post_oid or not user_id or not content or not group_link:
            log_message(
                (
                    f"[POST_LINK_MISSING_SCAN] Bỏ qua Bai-dang {post_oid or 'missing'} "
                    f"do thiếu user_id/content/group_link"
                ),
                logging.WARNING,
            )
            continue

        command_lookup_filter = {
            "type": "post_to_group",
            "params.oid": post_oid,
        }

        existing_command = await command_col.find_one(
            command_lookup_filter,
            sort=[("Executed_at", 1), ("_id", 1)],
        )
        if existing_command:
            retry_count = int(existing_command.get("link_retry_count") or 0)
            queued_at = existing_command.get("Queued_to_json_at")
            if retry_count >= max_retry:
                continue
            if (
                existing_command.get("Status") == POST_LINK_RETRY_QUEUE_STATUS
                and queued_at
                and queued_at > stuck_before
            ):
                continue

        set_fields = {
            "type": "post_to_group",
            "user_id": user_id,
            "Status": POST_LINK_RETRY_QUEUE_STATUS,
            "Retry_action": POST_LINK_RETRY_ACTION,
            "Queued_to_json_at": now,
            "Queued_to_json_by": queued_by,
            "Last_retry_at": now,
            "Execution_source": "bai_dang_success_missing_link_scan",
            "params.oid": post_oid,
            "params.content": content,
            "params.group_link": group_link,
            "params.auto_join_if_needed": False,
            "params.join_timeout_sec": 15,
        }
        if post.get("user_name"):
            set_fields["user_name"] = post.get("user_name")
        if post.get("files") is not None:
            set_fields["params.files"] = post.get("files")
        if post.get("device_ids"):
            set_fields["device_ids"] = post.get("device_ids")
        if post.get("device_id"):
            set_fields["device_id"] = post.get("device_id")

        if existing_command:
            doc = await command_col.find_one_and_update(
                {"_id": existing_command["_id"]},
                {
                    "$set": set_fields,
                    "$inc": {"link_retry_count": 1},
                },
                return_document=True,
            )
        else:
            insert_doc = {
                "type": "post_to_group",
                "user_id": user_id,
                "Status": POST_LINK_RETRY_QUEUE_STATUS,
                "Retry_action": POST_LINK_RETRY_ACTION,
                "Queued_to_json_at": now,
                "Queued_to_json_by": queued_by,
                "Last_retry_at": now,
                "Execution_source": "bai_dang_success_missing_link_scan",
                "Created_at": now,
                "created_at": now,
                "link_retry_count": 1,
                "params": {
                    "oid": post_oid,
                    "content": content,
                    "group_link": group_link,
                    "auto_join_if_needed": False,
                    "join_timeout_sec": 15,
                    "files": post.get("files") or [],
                },
            }
            if post.get("user_name"):
                insert_doc["user_name"] = post.get("user_name")
            if post.get("device_ids"):
                insert_doc["device_ids"] = post.get("device_ids")
            if post.get("device_id"):
                insert_doc["device_id"] = post.get("device_id")
            insert_result = await command_col.insert_one(insert_doc)
            doc = dict(insert_doc)
            doc["_id"] = insert_result.inserted_id

        if not doc:
            continue

        out.append(doc)
        await post_col.update_one(
            {"_id": post["_id"], "status": "Đã đăng thành công"},
            {
                "$set": {
                    "status": POST_LINK_RETRY_QUEUE_STATUS,
                    "updated_at": now,
                    "Link_retry_queued_at": now,
                    "Link_retry_queued_by": queued_by,
                    "Link_retry_command_id": str(doc.get("_id") or ""),
                },
                "$inc": {"link_fetch_retry_count": 1},
            },
        )

        log_message(
            (
                f"[POST_LINK_MISSING_SCAN] Claimed Bai-dang {post_oid} "
                f"command={doc.get('_id')} user_id={user_id} group_link={group_link}"
            ),
            logging.INFO,
        )

    return out


async def get_deferred_commands(user_id: str, deferred_by: str):
    """
    Lấy các command đã bị hoãn bởi 1 command_id cụ thể.
    Sort theo time để chạy theo thứ tự tạo.
    """
    collection = get_async_collection("Commands")
    cursor = collection.find(
        {"user_id": user_id, "Status": "Đã cho vào hàng chờ", "Deferred_by": str(deferred_by)}
    ).sort([("time", 1)])
    return await cursor.to_list(length=None)

async def mark_deferred_as_running(command_id: str):
    collection = get_async_collection("Commands")
    await collection.update_one(
        {"_id": ObjectId(command_id)},
        {"$set": {"Status": "Đang thực hiện", "Deferred_started_at": datetime.now()}}
    )
def command_doc_to_job(cmd: dict) -> dict:
    """
    Convert 1 document trong collection Commands -> job dict chuẩn cho fb_task.handle_urgent_if_any
    """
    cmd = cmd or {}
    cid = str(cmd.get("_id") or "")

    ctype = (cmd.get("type") or "").strip()
    params = cmd.get("params") or {}
    user_id = cmd.get("user_id") or params.get("user_id")
    retry_action = (cmd.get("Retry_action") or params.get("Retry_action") or "").strip()

    # map type -> action chuẩn của tool
    if retry_action == POST_LINK_RETRY_ACTION:
        action = POST_LINK_RETRY_ACTION
    elif ctype == "post_to_wall":
        action = "post_wall"
    elif ctype == "post_to_group":
        action = "post_group"
    elif ctype == "join_group":
        action = "join_group"
    elif ctype == "left_group":
        action = "left_group"
    else:
        action = (ctype or params.get("action") or "").strip()

    job = {
        "id": cid,
        "source": "mongo_scan",
        "action": action,
        "user_id": user_id,
        "user_name": cmd.get("user_name") or params.get("user_name") or "",
        "device_ids": cmd.get("device_ids") or cmd.get("device_id"),
        "params": params,
        # flatten cho tiện
        "content": params.get("content") or cmd.get("content") or "",
        "files": params.get("files") or cmd.get("files"),
        "group_link": params.get("group_link") or cmd.get("group_link"),
        "join_timeout_sec": params.get("join_timeout_sec") or 15,
        "auto_join_if_needed": params.get("auto_join_if_needed", True if action == "post_group" else False),
    }
    return job
#-------------------------------------------------------------------------------------------------------------------------------
# Lệnh liên quan tới collection Bai-dang và Bai-dang-tuong
async def get_unapproved_posts(user_id):
    """Lấy danh sách bài viết chưa được phê duyệt của người dùng."""
    collection = get_async_collection("Bai-dang")
    posts = await collection.find({"user_id": user_id, "status": "Đang chờ duyệt"}).to_list(length=None)
    return posts

async def get_unapproved_wall_posts(user_id):
    """Lấy danh sách bài viết chưa được phê duyệt trên tường của người dùng."""
    collection = get_async_collection("Bai-dang-tuong")
    posts = await collection.find({"user_id": user_id, "status": "Đang đăng"}).to_list(length=None)
    return posts

async def update_post_status(post_id, status, link):
    """Cập nhật trạng thái bài viết."""
    collection = get_async_collection("Bai-dang")
    result = await collection.update_one(
        {"_id": ObjectId(post_id)},
        {"$set": {"status": status, "updated_at": datetime.now(), "link": link}}
    )
    if result.matched_count == 0:
        return {"message": "❌ Cập nhật trạng thái bài viết: Bài viết không tồn tại"}, logging.ERROR
    if result.modified_count == 0:
        return {"message": "⚠️ Cập nhật trạng thái bài viết: Trạng thái bài viết không thay đổi"}, logging.WARNING
    return {"message": "✅ Cập nhật trạng thái bài viết: Thành công, link: " + link}, logging.INFO

async def get_account_by_name(name, device_id=None, device_name=None):
    """
    Tìm account theo tên Facebook, bắt buộc ưu tiên đúng thiết bị.
    Vì trong mỗi device chỉ có duy nhất 1 accounts.name trùng khớp.
    """
    collection = get_async_collection("devices")

    resolved_device_name = device_name
    if device_id and not resolved_device_name:
        resolved_device_name = await get_device_name_by_id(device_id)
        if resolved_device_name == "Unknown Device":
            resolved_device_name = None

    device = None

    if device_id:
        device = await collection.find_one(
            {
                "device_id": device_id,
                "accounts.name": name
            },
            {
                "accounts": {"$elemMatch": {"name": name}},
                "device_id": 1,
                "device_name": 1
            }
        )

    if not device and resolved_device_name:
        device = await collection.find_one(
            {
                "device_name": resolved_device_name,
                "accounts.name": name
            },
            {
                "accounts": {"$elemMatch": {"name": name}},
                "device_id": 1,
                "device_name": 1
            }
        )

    if not device or not device.get("accounts"):
        return None

    return device["accounts"][0].get("account")

async def update_wall_post_status(post_id, status, link):
    """Cập nhật trạng thái bài viết trên tường."""
    collection = get_async_collection("Bai-dang-tuong")
    result = await collection.update_one(
        {"_id": ObjectId(post_id)},
        {"$set": {"status": status, "updated_at": datetime.now(), "link": link}}
    )
    if result.matched_count == 0:
        return {"message": "❌ Cập nhật trạng thái bài viết trên tường: Bài viết không tồn tại"}, logging.ERROR
    if result.modified_count == 0:
        return {"message": "⚠️ Cập nhật trạng thái bài viết trên tường: Trạng thái bài viết không thay đổi"}, logging.WARNING
    return {"message": "✅ Cập nhật trạng thái bài viết trên tường: Thành công, link: " + link}, logging.INFO

async def ensure_group_post_tracking_record(
    command_id=None,
    *,
    user_id=None,
    user_name=None,
    content=None,
    files=None,
    group_link=None,
    link=None,
    post_status=None,
    device_id=None,
    source="post_to_group",
):
    """
    Ensure a posted group item exists in Bai-dang even when the command came
    from RabbitMQ/old sources and has no matching Bai-dang document.
    """
    if not link:
        return {
            "ok": False,
            "message": "⚠️ Không tạo Bai-dang tracking vì thiếu link bài viết",
            "inserted": False,
            "updated": False,
            "target_id": None,
        }, logging.WARNING

    now = datetime.now()
    final_status = _normalize_publish_status("post_to_group", post_status)
    post_col = get_async_collection("Bai-dang")
    command_col = get_async_collection("Commands")
    command = None
    command_obj_id = None
    post_oid = None

    try:
        command_obj_id = ObjectId(str(command_id))
        command = await command_col.find_one({"_id": command_obj_id})
    except Exception:
        command_obj_id = None
        command = None

    if command:
        params = command.get("params") or {}
        post_oid = params.get("oid")
        user_id = user_id or command.get("user_id") or params.get("user_id")
        user_name = user_name or command.get("user_name") or params.get("user_name")
        content = content or params.get("content") or command.get("content")
        files = files if files is not None else params.get("files") or command.get("files")
        group_link = group_link or params.get("group_link") or command.get("group_link")
        device_id = device_id or command.get("device_id") or command.get("device_ids")

    update_fields = {
        "status": final_status,
        "updated_at": now,
        "posted_at": now,
        "link": link,
        "post_link": link,
        "Tracking_source": source,
    }
    if user_id:
        update_fields["user_id"] = str(user_id)
    if user_name:
        update_fields["user_name"] = user_name
    if content:
        update_fields["content"] = content
    if files is not None:
        update_fields["files"] = files
    if group_link:
        update_fields["group_link"] = group_link
        update_fields["Group_link"] = group_link
    if device_id:
        update_fields["device_id"] = device_id
        update_fields["device_ids"] = device_id
    if command_id:
        update_fields["command_id"] = str(command_id)
    if command_obj_id:
        update_fields["command_oid"] = command_obj_id

    post_obj_id = None
    if post_oid:
        try:
            post_obj_id = ObjectId(str(post_oid))
        except Exception:
            post_obj_id = None

    if post_obj_id:
        existing_update = await post_col.update_one(
            {"_id": post_obj_id},
            {"$set": update_fields},
        )
        if existing_update.matched_count > 0:
            return {
                "ok": True,
                "message": f"✅ Đã cập nhật Bai-dang tracking theo oid: {post_obj_id}",
                "inserted": False,
                "updated": True,
                "target_id": str(post_obj_id),
            }, logging.INFO

        insert_doc = {
            "_id": post_obj_id,
            **update_fields,
            "created_at": now,
            "time": _epoch_seconds(now),
            "Auto_created_by_tool": True,
        }
        try:
            await post_col.insert_one(insert_doc)
            return {
                "ok": True,
                "message": f"✅ Đã tạo mới Bai-dang tracking theo oid: {post_obj_id}",
                "inserted": True,
                "updated": False,
                "target_id": str(post_obj_id),
            }, logging.INFO
        except Exception as e:
            if "duplicate" not in str(e).lower():
                return {
                    "ok": False,
                    "message": f"❌ Không tạo được Bai-dang tracking theo oid: {e}",
                    "inserted": False,
                    "updated": False,
                    "target_id": str(post_obj_id),
                }, logging.ERROR
            await post_col.update_one({"_id": post_obj_id}, {"$set": update_fields})
            return {
                "ok": True,
                "message": f"✅ Đã cập nhật Bai-dang tracking sau duplicate oid: {post_obj_id}",
                "inserted": False,
                "updated": True,
                "target_id": str(post_obj_id),
            }, logging.INFO

    link_query = {"$or": [{"link": link}, {"post_link": link}]}
    existing_by_link = await post_col.find_one(link_query, {"_id": 1})
    if existing_by_link:
        await post_col.update_one(
            {"_id": existing_by_link["_id"]},
            {"$set": update_fields},
        )
        return {
            "ok": True,
            "message": f"✅ Đã cập nhật Bai-dang tracking theo link: {existing_by_link['_id']}",
            "inserted": False,
            "updated": True,
            "target_id": str(existing_by_link["_id"]),
        }, logging.INFO

    insert_doc = {
        **update_fields,
        "created_at": now,
        "time": _epoch_seconds(now),
        "Auto_created_by_tool": True,
    }
    result = await post_col.insert_one(insert_doc)
    return {
        "ok": True,
        "message": f"✅ Đã tạo mới Bai-dang tracking theo link: {result.inserted_id}",
        "inserted": True,
        "updated": False,
        "target_id": str(result.inserted_id),
    }, logging.INFO

# async def update_post_link(command_id, link):
async def update_post_link(command_id, link, post_status=None):
    if not link:
        return {
            "ok": False,
            "message": "⚠️ Cập nhật link bài viết: Không có link để cập nhật",
            "command_updated": False,
            "target_updated": False,
            "target_collection": None,
            "target_id": None,
        }, logging.WARNING

    commands_collection = get_async_collection("Commands")

    # 1) Lấy command
    try:
        command_obj_id = ObjectId(command_id)
    except Exception:
        return {
            "ok": False,
            "message": f"❌ command_id không hợp lệ: {command_id}",
            "command_updated": False,
            "target_updated": False,
            "target_collection": None,
            "target_id": None,
        }, logging.ERROR

    command = await commands_collection.find_one({"_id": command_obj_id})
    if not command:
        return {
            "ok": False,
            "message": "❌ Cập nhật link bài viết: Lệnh không tồn tại",
            "command_updated": False,
            "target_updated": False,
            "target_collection": None,
            "target_id": None,
        }, logging.ERROR

    cmd_type = command.get("type")
    params = command.get("params") or {}
    post_oid = params.get("oid")

    command_updated = False
    target_updated = False
    target_collection = None
    now = datetime.now()

    # 2) Luôn lưu vào Commands để dễ quản lý/debug
    try:
        cmd_update = await commands_collection.update_one(
            {"_id": command_obj_id},
            {"$set": {
                "post_link": link,
                "updated_at": now,
            }}
        )
        command_updated = cmd_update.matched_count > 0
    except Exception:
        command_updated = False

    # 3) Nếu có oid thì update thêm collection đích
    if post_oid:
        try:
            post_obj_id = ObjectId(post_oid)

            # if cmd_type == "post_to_group":
            #     target_collection = "Bai-dang"
            #     collection = get_async_collection("Bai-dang")
            #     result = await collection.update_one(
            #         {"_id": post_obj_id},
            #         {"$set": {
            #             "status": "Đã đăng thành công",
            #             "updated_at": datetime.now(),
            #             "link": link,
            #         }}
            #     )
            #     target_updated = result.matched_count > 0
            
            if cmd_type == "post_to_group":
                target_collection = "Bai-dang"
                collection = get_async_collection("Bai-dang")
                final_post_status = _normalize_publish_status("post_to_group", post_status)
                update_fields = {
                    "status": final_post_status,
                    "updated_at": now,
                    "link": link,
                }
                await _ensure_post_time_if_missing(
                    collection,
                    post_obj_id,
                    update_fields,
                    now,
                )

                result = await collection.update_one(
                    {"_id": post_obj_id},
                    {"$set": update_fields}
                )
                target_updated = result.matched_count > 0
                
            # elif cmd_type == "post_to_wall":
            #     target_collection = "Bai-dang-tuong"
            #     collection = get_async_collection("Bai-dang-tuong")
            #     result = await collection.update_one(
            #         {"_id": post_obj_id},
            #         {"$set": {
            #             "status": "Đã đăng thành công",
            #             "updated_at": datetime.now(),
            #             "link": link,
            #         }}
            #     )
            #     target_updated = result.matched_count > 0
            elif cmd_type == "post_to_wall":
                target_collection = "Bai-dang-tuong"
                collection = get_async_collection("Bai-dang-tuong")
                final_post_status = _normalize_publish_status("post_to_wall", post_status)

                result = await collection.update_one(
                    {"_id": post_obj_id},
                    {"$set": {
                        "status": final_post_status,
                        "updated_at": now,
                        "link": link,
                    }}
                )
                target_updated = result.matched_count > 0
        except Exception as e:
            return {
                "ok": False,
                "message": f"❌ Lỗi update collection đích: {e}",
                "command_updated": command_updated,
                "target_updated": False,
                "target_collection": target_collection,
                "target_id": post_oid,
            }, logging.ERROR

    ok = command_updated or target_updated

    if target_collection:
        return {
            "ok": ok,
            "message": (
                f"✅ Đã lưu link. Commands={command_updated}, "
                f"{target_collection}={target_updated}, link={link}"
            ),
            "command_updated": command_updated,
            "target_updated": target_updated,
            "target_collection": target_collection,
            "target_id": post_oid,
        }, logging.INFO if ok else logging.WARNING

    return {
        "ok": command_updated,
        "message": f"✅ Đã lưu link vào Commands: {link}" if command_updated else "⚠️ Không lưu được link vào Commands",
        "command_updated": command_updated,
        "target_updated": False,
        "target_collection": None,
        "target_id": post_oid,
    }, logging.INFO if command_updated else logging.WARNING

#-------------------------------------------------------------------------------------------------------------------------------
# Lệnh liên quan tới collection "Danh-sach-bai-dang"
async def update_crawled_post_status(post_link, status):
    """Cập nhật trạng thái bài viết trong cơ sở dữ liệu."""
    collection = get_async_collection("Danh-sach-bai-dang")
    result = await collection.update_one(
        {"Link-post": post_link}, 
        {"$set": {"Status": status}}
    )
    if result.matched_count == 0:
        return {'message': '❌ Bài viết không tồn tại'}, logging.ERROR
    if result.modified_count == 0:
        return {'message': '⚠️ Trạng thái bài viết không thay đổi'}, logging.WARNING
    return {'message': '✅ Cập nhật thành công'}, logging.INFO
#-------------------------------------------------------------------------------------------------------------------------------
# Lệnh liên quan tới collection Binh-luan-trong-bai-dang
async def save_post_comment(post_link, commenter, comment, time, level=0, parent_commenter=None, parent_comment=None):
    """Lưu bình luận trong bài đăng vào cơ sở dữ liệu."""
    collection = get_async_collection("Binh-luan-trong-bai-dang")
    parent_id = None
    
    if level > 0:
        if not parent_commenter or not parent_comment:
            return {'message': '❌ Lưu bình luận trong bài đăng: Thiếu thông tin bình luận cha'}, logging.ERROR
        # Lấy id của bình luận cha
        parent = await collection.find_one({
            "Post_link": post_link,
            "Commenter": parent_commenter,
            "Comment": parent_comment,
            "Level": level - 1
        })
        if not parent:
            return {'message': '❌ Lưu bình luận trong bài đăng: Không tìm thấy bình luận cha'}, logging.ERROR
        parent_id = parent.get("_id")
    
    # Lưu bình luận mới
    result = await collection.insert_one({
        "Post_link": post_link,
        "Commenter": commenter,
        "Comment": comment,
        "Time": time,
        "Level": level,
        "Parent_id": parent_id
    })
    
    if result.inserted_id:
        return {'message': '✅ Lưu bình luận trong bài đăng: Thành công'}, logging.INFO
    return {'message': '❌ Lưu bình luận trong bài đăng: Thất bại'}, logging.ERROR

#-------------------------------------------------------------------------------------------------------------------------------
# Lệnh liên quan tới collection "devices"
async def get_device_name_by_id(device_id):
    """Lấy tên thiết bị theo device_id."""
    collection = get_async_collection("devices")
    device = await collection.find_one({"device_id": device_id})
    if device:
        return device.get("device_name")
    return "Unknown Device"

# async def get_account_by_username(username):
#     """Lấy tài khoản theo username."""
#     collection = get_async_collection("devices")
#     device = await collection.find_one({"accounts.account": username})
#     if not device:
#         return None
#     account = next(
#         (acc for acc in device["accounts"] if acc["account"] == username),
#             None
#         )
#     if not account:
#         device_id = None
#     else:
#         device_id = device.get("device_id")
#     return account, device_id

async def get_account_by_username(username, device_id=None, device_name=None):
    """
    Lấy account theo username, có ưu tiên match đúng thiết bị.

    Ưu tiên:
    1. account + device_id
    2. account + device_name
    3. fallback: account đang Online/current_account
    4. cuối cùng mới fallback theo account thuần để tránh vỡ luồng cũ

    Trả về:
    - (account_dict, device_id) nếu tìm thấy
    - (None, None) nếu không tìm thấy
    """
    collection = get_async_collection("devices")

    resolved_device_name = device_name
    if device_id and not resolved_device_name:
        resolved_device_name = await get_device_name_by_id(device_id)
        if resolved_device_name == "Unknown Device":
            resolved_device_name = None

    device = None

    # 1) Ưu tiên match chính xác bằng device_id
    if device_id:
        device = await collection.find_one({
            "device_id": device_id,
            "accounts.account": username
        })

    # 2) Nếu chưa thấy thì match theo device_name
    if not device and resolved_device_name:
        device = await collection.find_one({
            "device_name": resolved_device_name,
            "accounts.account": username
        })

    # 3) Fallback cho luồng cũ chưa truyền device_id/device_name:
    #    ưu tiên document đang login account này
    if not device:
        device = await collection.find_one({
            "accounts": {
                "$elemMatch": {
                    "account": username,
                    "status": "Online"
                }
            },
            "current_account": username
        })

    # 4) Fallback cuối cùng để không làm vỡ code cũ
    if not device:
        device = await collection.find_one({"accounts.account": username})

    if not device:
        return None, None

    account = next(
        (acc for acc in device.get("accounts", []) if acc.get("account") == username),
        None
    )

    if not account:
        return None, device.get("device_id")

    return account, device.get("device_id")

# async def update_statusFB(username, statusFB):
#     """Cập nhật trạng thái Facebook của thiết bị."""
#     collection = get_async_collection("devices")
#     if statusFB == "Online":
#         result = await collection.update_one(
#             {"accounts.account": username},
#             {"$set": {
#                 "current_account": username,
#                 "time_logged_in": datetime.now(),
#                 "accounts.$[elem].status": statusFB
#                 }
#             },
#             array_filters=[{"elem.account": username}]
#         )
#     elif statusFB == "Offline":
#         result = await collection.update_many(
#             {"device_id": username},
#             {"$set": {
#                 "accounts.$[elem].status": "Offline",
#                 "time_logged_in": None,
#                 "current_account": ""
#                 }
#             },
#             array_filters=[{"elem.status": "Online"}]
#         )
#     elif statusFB == "Crash":
#         result = await collection.update_one(
#             {"accounts.account": username},
#             {"$set": {
#                 "accounts.$[elem].status": statusFB,
#                 "time_logged_in": None,
#                 "current_account": ""}
#             },
#             array_filters=[{"elem.account": username}]
#         )
#     if result.matched_count == 0:
#         return {'message': '❌ Thiết bị không tồn tại'}, logging.ERROR
#     if result.modified_count == 0:
#         return {'message': '⚠️ Trạng thái Facebook không thay đổi'}, logging.WARNING
#     return {'message': '✅ Cập nhật trạng thái Facebook thành công'}, logging.INFO

# async def update_statusFB(username, statusFB, device_id=None, device_name=None):
# async def update_statusFB(username=None, statusFB=None, device_id=None, device_name=None):
#     """
#     Cập nhật trạng thái Facebook của tài khoản trên thiết bị.

#     - Online/Crash: match theo accounts.account + device_name để tránh đụng nhầm
#       khi nhiều máy có cùng accounts.account.
#     - Offline: giữ logic theo device_id để đưa toàn bộ account online trên đúng máy về Offline.
#     """
#     collection = get_async_collection("devices")

#     resolved_device_name = device_name
#     if not resolved_device_name and device_id:
#         resolved_device_name = await get_device_name_by_id(device_id)
#         if resolved_device_name == "Unknown Device":
#             resolved_device_name = None

#     if statusFB == "Online":
#         query = {"accounts.account": username}
#         if resolved_device_name:
#             query["device_name"] = resolved_device_name

#         result = await collection.update_one(
#             query,
#             {"$set": {
#                 "current_account": username,
#                 "time_logged_in": datetime.now(),
#                 "accounts.$[elem].status": statusFB
#                 }
#             },
#             array_filters=[{"elem.account": username}]
#         )

#     elif statusFB == "Offline":
#         query = {"device_id": device_id}
#         if resolved_device_name:
#             query["device_name"] = resolved_device_name

#         result = await collection.update_many(
#             query,
#             {"$set": {
#                 "accounts.$[elem].status": "Offline",
#                 "time_logged_in": None,
#                 "current_account": ""
#                 }
#             },
#             array_filters=[{"elem.status": "Online"}]
#         )

#     elif statusFB == "Crash":
#         query = {"accounts.account": username}
#         if resolved_device_name:
#             query["device_name"] = resolved_device_name

#         result = await collection.update_one(
#             query,
#             {"$set": {
#                 "accounts.$[elem].status": statusFB,
#                 "time_logged_in": None,
#                 "current_account": ""}
#             },
#             array_filters=[{"elem.account": username}]
#         )
#     else:
#         return {'message': f'❌ Trạng thái Facebook không hợp lệ: {statusFB}'}, logging.ERROR

#     if result.matched_count == 0:
#         if statusFB == "Offline":
#             target = f"device_id={device_id}"
#             if resolved_device_name:
#                 target += f", device_name={resolved_device_name}"
#         else:
#             target = f"account={username}"
#             if resolved_device_name:
#                 target += f", device_name={resolved_device_name}"
#         return {'message': f'❌ Không tìm thấy bản ghi phù hợp ({target})'}, logging.ERROR

#     if result.modified_count == 0:
#         return {'message': '⚠️ Trạng thái Facebook không thay đổi'}, logging.WARNING

#     return {'message': '✅ Cập nhật trạng thái Facebook thành công'}, logging.INFO

async def update_statusFB(
    username=None,
    account=None,
    statusFB=None,
    device_id=None,
    device_name=None,
    number_of_friends=None,
):
    """
    Cập nhật trạng thái Facebook của tài khoản trên thiết bị.

    Quy ước:
    - Online:
        + ưu tiên match theo device_id + accounts.account
        + fallback theo device_id + accounts.name
        + đảm bảo chỉ còn duy nhất 1 account Online trên đúng thiết bị
    - Offline:
        + đưa toàn bộ account Online trên thiết bị về Offline
    - Crash:
        + ưu tiên cập nhật theo account, fallback theo name
    """
    collection = get_async_collection("devices")

    resolved_device_name = device_name
    if not resolved_device_name and device_id:
        resolved_device_name = await get_device_name_by_id(device_id)
        if resolved_device_name == "Unknown Device":
            resolved_device_name = None

    # Log đầu vào để debug
    log_message(
        f"[DEBUG-MONGO] update_statusFB CALLED | username={username} | account={account} | "
        f"statusFB={statusFB} | device_id={device_id} | resolved_device_name={resolved_device_name}",
        logging.INFO
    )

    changed_at = datetime.now()
    fallback_online_start_at = changed_at - timedelta(minutes=10)

    if statusFB == "Online":
        if not device_id:
            return {'message': '❌ Thiếu device_id khi cập nhật trạng thái Online'}, logging.ERROR

        if not account and not username:
            return {'message': '❌ Thiếu account hoặc username khi cập nhật trạng thái Online'}, logging.ERROR

        base_query = {"device_id": device_id}
        if resolved_device_name:
            base_query["device_name"] = resolved_device_name

        # 1) Đưa mọi account đang Online trên máy này về Offline
        target_probe = account or username
        target_account_doc = None
        if account:
            doc = await collection.find_one(
                {**base_query, "accounts.account": account},
                {"accounts": {"$elemMatch": {"account": account}}, "device_id": 1}
            )
        else:
            doc = await collection.find_one(
                {**base_query, "accounts.name": username},
                {"accounts": {"$elemMatch": {"name": username}}, "device_id": 1}
            )
        if doc and doc.get("accounts"):
            target_account_doc = doc["accounts"][0]
            target_probe = target_account_doc.get("account") or target_probe

        reset_filter = {"elem.status": "Online"}
        if target_probe:
            reset_filter["elem.account"] = {"$ne": target_probe}

        await collection.update_one(
            base_query,
            {
                "$set": {
                    "accounts.$[elem].last_online_start_at": fallback_online_start_at
                }
            },
            array_filters=[{
                "elem.status": "Online",
                "elem.account": {"$ne": target_probe},
                "elem.last_online_start_at": None
            }]
        )

        reset_result = await collection.update_one(
            base_query,
            {
                "$set": {
                    "accounts.$[elem].status": "Offline",
                    "accounts.$[elem].last_online_end_at": changed_at
                }
            },
            array_filters=[reset_filter]
        )

        log_message(
            f"[DEBUG-MONGO] reset online->offline | base_query={base_query} | "
            f"matched={reset_result.matched_count} | modified={reset_result.modified_count}",
            logging.INFO
        )

        # 2) Xác định query target:
        # ưu tiên account vì ổn định hơn name
        target_query = None
        array_filter = None
        resolved_account = account

        if account:
            target_query = {
                **base_query,
                "accounts.account": account
            }
            array_filter = [{"elem.account": account}]
        else:
            target_query = {
                **base_query,
                "accounts.name": username
            }
            array_filter = [{"elem.name": username}]

            # Nếu update theo name, lấy ra account thật để set current_account cho chuẩn
            doc = await collection.find_one(
                target_query,
                {"accounts": {"$elemMatch": {"name": username}}, "device_id": 1}
            )
            if doc and doc.get("accounts"):
                resolved_account = doc["accounts"][0].get("account")

        log_message(
            f"[DEBUG-MONGO] target_query Online = {target_query} | "
            f"array_filter={array_filter} | resolved_account={resolved_account}",
            logging.INFO
        )

        update_set = {
            "time_logged_in": changed_at,
            "accounts.$[elem].status": "Online",
            "accounts.$[elem].last_online_end_at": None
        }
        if not target_account_doc or str(target_account_doc.get("status") or "") != "Online" or not target_account_doc.get("last_online_start_at"):
            update_set["accounts.$[elem].last_online_start_at"] = changed_at
        if number_of_friends is not None:
            update_set["accounts.$[elem].number_of_friends"] = int(number_of_friends)

        # current_account luôn nên là account thật, không phải name
        if resolved_account:
            update_set["current_account"] = resolved_account
        elif account:
            update_set["current_account"] = account
        else:
            update_set["current_account"] = ""

        result = await collection.update_one(
            target_query,
            {"$set": update_set},
            array_filters=array_filter
        )

        log_message(
            f"[DEBUG-MONGO] Online update result | matched={result.matched_count} | modified={result.modified_count}",
            logging.INFO
        )

    elif statusFB == "Offline":
        if not device_id:
            return {'message': '❌ Thiếu device_id khi cập nhật trạng thái Offline'}, logging.ERROR

        query = {"device_id": device_id}
        if resolved_device_name:
            query["device_name"] = resolved_device_name

        log_message(
            f"[DEBUG-MONGO] Offline query = {query}",
            logging.INFO
        )

        await collection.update_many(
            query,
            {
                "$set": {
                    "accounts.$[elem].last_online_start_at": fallback_online_start_at
                }
            },
            array_filters=[{"elem.status": "Online", "elem.last_online_start_at": None}]
        )

        result = await collection.update_many(
            query,
            {
                "$set": {
                    "accounts.$[elem].status": "Offline",
                    "accounts.$[elem].last_online_end_at": changed_at,
                    "time_logged_in": None,
                    "current_account": ""
                }
            },
            array_filters=[{"elem.status": "Online"}]
        )

        log_message(
            f"[DEBUG-MONGO] Offline update result | matched={result.matched_count} | modified={result.modified_count}",
            logging.INFO
        )

    elif statusFB == "Crash":
        if not device_id and not resolved_device_name:
            return {'message': '❌ Thiếu device_id/device_name khi cập nhật trạng thái Crash'}, logging.ERROR

        if not account and not username:
            return {'message': '❌ Thiếu account hoặc username khi cập nhật trạng thái Crash'}, logging.ERROR

        query = {}
        if device_id:
            query["device_id"] = device_id
        if resolved_device_name:
            query["device_name"] = resolved_device_name

        if account:
            query["accounts.account"] = account
            array_filter = [{"elem.account": account}]
        else:
            query["accounts.name"] = username
            array_filter = [{"elem.name": username}]

        log_message(
            f"[DEBUG-MONGO] Crash query = {query} | array_filter={array_filter}",
            logging.INFO
        )

        await collection.update_one(
            query,
            {
                "$set": {
                    "accounts.$[elem].last_online_start_at": fallback_online_start_at
                }
            },
            array_filters=[{**array_filter[0], "elem.status": "Online", "elem.last_online_start_at": None}]
        )

        result = await collection.update_one(
            query,
            {
                "$set": {
                    "accounts.$[elem].status": "Crash",
                    "accounts.$[elem].last_online_end_at": changed_at,
                    "time_logged_in": None,
                    "current_account": ""
                }
            },
            array_filters=array_filter
        )

        log_message(
            f"[DEBUG-MONGO] Crash update result | matched={result.matched_count} | modified={result.modified_count}",
            logging.INFO
        )

    else:
        return {'message': f'❌ Trạng thái Facebook không hợp lệ: {statusFB}'}, logging.ERROR

    if result.matched_count == 0:
        if statusFB == "Offline":
            target = f"device_id={device_id}"
            if resolved_device_name:
                target += f", device_name={resolved_device_name}"
        else:
            target = f"username={username}, account={account}"
            if device_id:
                target += f", device_id={device_id}"
            if resolved_device_name:
                target += f", device_name={resolved_device_name}"

        return {'message': f'❌ Không tìm thấy bản ghi phù hợp ({target})'}, logging.ERROR

    if result.modified_count == 0:
        return {
            'message': (
                f'⚠️ Trạng thái Facebook không thay đổi '
                f'(username={username}, account={account}, device_id={device_id})'
            )
        }, logging.WARNING

    return {
        'message': (
            f'✅ Cập nhật trạng thái Facebook thành công '
            f'(username={username}, account={account}, device_id={device_id})'
        )
    }, logging.INFO
    
async def update_device_status(device_id, status, extra_fields=None):
    """Cập nhật trạng thái hoạt động của thiết bị + touch heartbeat time."""
    collection = get_async_collection("devices")
    now = datetime.now()
    fallback_online_start_at = now - timedelta(minutes=10)

    set_fields = {
        "status": status,
        "last_seen": now,
        "heartbeat_at": now,
        "updated_at": now,
    }

    if extra_fields and isinstance(extra_fields, dict):
        set_fields.update(extra_fields)

    if device_id is None:
        result = await collection.update_many(
            {},
            {"$set": set_fields}
        )
        matched = result.matched_count
        modified = result.modified_count
    else:
        result = await collection.update_one(
            {"device_id": device_id},
            {"$set": set_fields}
        )
        matched = result.matched_count
        modified = result.modified_count

    if matched == 0:
        return {'message': '❌ Thiết bị không tồn tại'}, logging.ERROR
    if status is False:
        close_query = {} if device_id is None else {"device_id": device_id}
        await collection.update_many(
            close_query,
            {
                "$set": {
                    "accounts.$[elem].last_online_start_at": fallback_online_start_at,
                }
            },
            array_filters=[{"elem.status": "Online", "elem.last_online_start_at": None}]
        )
        await collection.update_many(
            close_query,
            {
                "$set": {
                    "accounts.$[elem].status": "Offline",
                    "accounts.$[elem].last_online_end_at": now,
                    "current_account": "",
                    "time_logged_in": None,
                }
            },
            array_filters=[{"elem.status": "Online"}]
        )
    if modified == 0:
        return {'message': '⚠️ Heartbeat thiết bị đã được touch nhưng dữ liệu không đổi'}, logging.INFO
    return {'message': '✅ Cập nhật trạng thái/heartbeat thiết bị thành công'}, logging.INFO

async def touch_device_heartbeat(device_id, is_rabbit_connected=None, is_adb_connected=None, note=None):
    """Heartbeat nhẹ, gọi định kỳ để giữ trạng thái máy luôn mới."""
    extra = {}

    if is_rabbit_connected is not None:
        extra["rabbit_connected"] = bool(is_rabbit_connected)

    if is_adb_connected is not None:
        extra["adb_connected"] = bool(is_adb_connected)

    if note:
        extra["heartbeat_note"] = note

    return await update_device_status(device_id, True, extra_fields=extra)

async def check_facebook_link(device_id):
    """Kiểm tra xem đã có link facebook chưa"""
    collection = get_async_collection("devices")
    device = await collection.find_one({"device_id": device_id})
    current_account = device.get("current_account") if device else None
    if not current_account:
        return False
    account = next((acc for acc in device["accounts"] if acc["account"] == current_account), None) if device else None
    if not account:
        return False
    return "facebook_link" in account and account["facebook_link"] != ""

async def update_facebook_link(device_id, facebook_link):
    """Cập nhật link facebook của thiết bị."""
    collection = get_async_collection("devices")
    device = await collection.find_one({"device_id": device_id})
    current_account = device.get("current_account") if device else None
    if not current_account:
        return {'message': '❌ Thiết bị không có tài khoản đang đăng nhập'}, logging.ERROR
    result = await collection.update_one(
        {"device_id": device_id, "accounts.account": current_account},
        {"$set": {"accounts.$[elem].facebook_link": facebook_link}},
        array_filters=[{"elem.account": current_account}]
    )
    if result.matched_count == 0:
        return {'message': '❌ Thiết bị hoặc tài khoản không tồn tại'}, logging.ERROR
    if result.modified_count == 0:
        return {'message': '⚠️ Link Facebook không thay đổi'}, logging.WARNING
    return {'message': '✅ Cập nhật link Facebook thành công'}, logging.INFO

async def switch_online_account_for_device(
    device_id,
    current_account,
    account_name=None,
    number_of_friends=None,
):
    collection = get_async_collection("devices")
    changed_at = datetime.now()
    fallback_online_start_at = changed_at - timedelta(minutes=10)
    current_account = str(current_account or "").strip()
    target_doc = None
    if current_account:
        doc = await collection.find_one(
            {"device_id": device_id, "accounts.account": current_account},
            {"accounts": {"$elemMatch": {"account": current_account}}, "device_id": 1}
        )
        if doc and doc.get("accounts"):
            target_doc = doc["accounts"][0]

    await collection.update_one(
        {"device_id": device_id},
        {
            "$set": {
                "accounts.$[elem].last_online_start_at": fallback_online_start_at
            }
        },
        array_filters=[{
            "elem.status": "Online",
            "elem.account": {"$ne": current_account},
            "elem.last_online_start_at": None
        }]
    )

    await collection.update_one(
        {"device_id": device_id},
        {
            "$set": {
                "accounts.$[elem].status": "Offline",
                "accounts.$[elem].last_online_end_at": changed_at
            }
        },
        array_filters=[{"elem.status": "Online", "elem.account": {"$ne": current_account}}]
    )

    update_fields = {
        "accounts.$.status": "Online",
        "accounts.$.last_online_end_at": None,
        "current_account": current_account,
        "time_logged_in": changed_at
    }
    if not target_doc or str(target_doc.get("status") or "") != "Online" or not target_doc.get("last_online_start_at"):
        update_fields["accounts.$.last_online_start_at"] = changed_at
    if number_of_friends is not None:
        update_fields["accounts.$.number_of_friends"] = int(number_of_friends)

    await collection.update_one(
        {
            "device_id": device_id,
            "accounts.account": current_account
        },
        {
            "$set": update_fields
        }
    )
