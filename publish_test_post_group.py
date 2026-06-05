import asyncio
import json
import aio_pika

RABBIT_URL = "amqp://admin:123456@43.239.223.214:5672"
EXCHANGE_NAME = "ex.machine"

# ===== sửa các giá trị này =====
POST_iod = "69c3436e6c1c917d52017f0c"
COMMAND_ID = "69c3436e6c1c917d52017f0d"  # lấy từ script seed
MACHINE_CODE = "EY5H9DJNIVNFH6OR"  # serial adb thật
USER_ID = "0987610815"
GROUP_LINK = "groups/vieclamcokhitaihanoi"
CONTENT = """TUYỂN GẤP THỢ CƠ KHÍ & LAO ĐỘNG PHỔ THÔNG
Làm việc tại: Vân Canh, Hoài Đức, Hà Nội
Vị trí:
Thợ cơ khí
Thợ phụ
Bốc xếp
 Thu nhập: 9 – 15 triệu/tháng + thưởng
Quyền lợi:
Bao ăn ở cho người ở xa
Đóng BH đầy đủ
Tăng lương theo năng lực
Được đào tạo tay nghề
 Yêu cầu:
Chăm chỉ, có sức khỏe
Không cần kinh nghiệm (được đào tạo)
 Thời gian: Thứ 2 – Thứ 7 (8h – 17h)
 Inbox hoặc liên hệ ngay để nhận việc sớm!
"""

payload = {
    "command_id": COMMAND_ID,
    "type": "post_group",
    "user_id": USER_ID,
    "params": {
        "oid": POST_iod,
        "content": CONTENT,
        "files": [],
        "group_link": GROUP_LINK,
        "auto_join_if_needed": False,
        "join_timeout_sec": 15,
    },
}


async def main():
    routing_key = f"machine.{MACHINE_CODE}"

    conn = await aio_pika.connect_robust(RABBIT_URL)
    async with conn:
        ch = await conn.channel()
        ex = await ch.declare_exchange(
            EXCHANGE_NAME,
            aio_pika.ExchangeType.DIRECT,
            durable=True,
        )

        msg = aio_pika.Message(
            body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
        )

        await ex.publish(msg, routing_key=routing_key)
        print("Đã publish job:")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        print("routing_key =", routing_key)


asyncio.run(main())
