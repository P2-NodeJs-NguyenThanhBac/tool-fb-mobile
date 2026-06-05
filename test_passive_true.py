import json
import pika

RABBIT_URL = "amqp://guest:guest@localhost:5672/%2F"
CONTROL_QUEUE = "tool.control"

payload = {
    "action": "set_passive_wait_only",
    "value": True
}

params = pika.URLParameters(RABBIT_URL)
connection = pika.BlockingConnection(params)
channel = connection.channel()
channel.queue_declare(queue=CONTROL_QUEUE, durable=True)

channel.basic_publish(
    exchange="",
    routing_key=CONTROL_QUEUE,
    body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
    properties=pika.BasicProperties(delivery_mode=2)
)

print("Published TRUE")
connection.close()