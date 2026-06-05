import asyncio
import json
import aio_pika

RABBIT_URL = "amqp://admin:123456@43.239.223.214:5672"
EXCHANGE_NAME = "ex.machine"

# ===== sửa các giá trị này =====
MACHINE_CODE = "IZDEGA8TFYXWRK9X"  # serial adb thật
COMMAND_ID = "69b4d10ddd520fcf01ab2c7f"  # lấy từ script seed
USER_ID = "nguonlucviet365@gmail.com"
GROUP_LINK = "groups/469383163393248"
CONTENT = "Em 2005, đang muốn tìm việc. Em đã từng làm về Thủy sản, chịu được áp lực. Sdt zalo của e ạ: 0982305784"

payload = {
    "command_id": COMMAND_ID,
    "type": "post_group",
    "user_id": USER_ID,
    "params": {
        "oid": "69b4d10ddd520fcf01ab2c7e",
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
