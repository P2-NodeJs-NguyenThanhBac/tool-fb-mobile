import json
import pika
import sys

RABBIT_URL = "amqp://admin:123456@43.239.223.214:5672"
CONTROL_QUEUE = "q.tool.control"


def publish_control(value: bool, device_id: str = None):
    payload = {
        "action": "set_passive_wait_only",
        "value": value,
    }

    if device_id:
        payload["device_id"] = device_id

    params = pika.URLParameters(RABBIT_URL)
    connection = pika.BlockingConnection(params)
    channel = connection.channel()

    # đảm bảo queue tồn tại
    channel.queue_declare(queue=CONTROL_QUEUE, durable=True)

    channel.basic_publish(
        exchange="",  # default exchange -> publish thẳng vào queue
        routing_key=CONTROL_QUEUE,
        body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        properties=pika.BasicProperties(
            delivery_mode=2  # persistent
        ),
    )

    print("Published:", payload)
    connection.close()


if __name__ == "__main__":
    """
    Cách dùng:
      python test_control_rabbitmq.py true
      python test_control_rabbitmq.py false
      python test_control_rabbitmq.py true R8YY70F5MKN
      python test_control_rabbitmq.py false R8YY70F5MKN
    """
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python test_control_rabbitmq.py true")
        print("  python test_control_rabbitmq.py false")
        print("  python test_control_rabbitmq.py true <device_id>")
        print("  python test_control_rabbitmq.py false <device_id>")
        sys.exit(1)

    raw_value = sys.argv[1].strip().lower()
    if raw_value not in ("true", "false"):
        print("value must be true or false")
        sys.exit(1)

    value = raw_value == "true"
    device_id = sys.argv[2].strip() if len(sys.argv) >= 3 else None

    publish_control(value=value, device_id=device_id)