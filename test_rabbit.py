import asyncio
import aio_pika

RABBIT_URL = "amqp://admin:123456@43.239.223.214:5672"

async def main():
    connection = None
    try:
        print("Đang kết nối RabbitMQ...")
        connection = await aio_pika.connect_robust(
            RABBIT_URL,
            timeout=10
        )
        print("Kết nối thành công")

        channel = await connection.channel()
        print("Tạo channel thành công")

        queue = await channel.declare_queue("test_queue_chatgpt", durable=True)
        print(f"Khai báo queue thành công: {queue.name}")

        await channel.default_exchange.publish(
            aio_pika.Message(body=b"hello rabbitmq"),
            routing_key="test_queue_chatgpt"
        )
        print("Gửi message thành công")

        msg = await queue.get(timeout=5, fail=False)
        if msg:
            async with msg.process():
                print("Nhận lại message:", msg.body.decode())
        else:
            print("Không đọc được message từ queue")

    except Exception as e:
        print("Lỗi kết nối/test RabbitMQ:", repr(e))
    finally:
        if connection:
            await connection.close()
            print("Đã đóng connection")

if __name__ == "__main__":
    asyncio.run(main())