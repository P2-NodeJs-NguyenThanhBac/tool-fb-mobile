import socketio
import logging
from util.my_utils import log_message

class CRMBridge:
    def __init__(self, receiver_url="http://localhost:5001", sender_url="http://localhost:5002"):
        self.sio_receiver = socketio.Client()
        self.sio_sender = socketio.Client()
        self.receiver_url = receiver_url
        self.sender_url = sender_url
        self.is_connected = False
        self.is_sender_connected = False
        self.device_queues = {}  # Map user_id -> urgent_queue
        self.bot_type = "mobile" # Phân biệt với "browser" ở Folder 1
        self.setup_events()

    def setup_events(self):
        # Events cho API Receiver (5001)
        @self.sio_receiver.event
        def connect():
            self.is_connected = True
            log_message("[CRM Bridge] Kết nối thành công tới API Receiver (5001)")

        @self.sio_receiver.event
        def disconnect():
            self.is_connected = False
            log_message("[CRM Bridge] Ngắt kết nối tới API Receiver", logging.WARNING)

        # Events cho API Sender (5002) - NHẬN LỆNH
        @self.sio_sender.event
        def connect():
            self.is_sender_connected = True
            log_message("[CRM Bridge] Kết nối thành công tới API Sender (5002) để nhận lệnh")

        @self.sio_sender.on('post_news_feed_command')
        async def on_post_news_feed(data):
            """Lệnh đăng bài từ CRM"""
            user_id = data.get('user_id_chat')
            log_message(f"[CRM Bridge] Nhận lệnh đăng bài cho {user_id}")
            await self._enqueue_task(user_id, {
                "action": "post_wall",
                "params": data
            })

    def connect(self):
        try:
            if not self.is_connected:
                self.sio_receiver.connect(self.receiver_url)
            if not self.is_sender_connected:
                self.sio_sender.connect(self.sender_url)
        except Exception as e:
            log_message(f"[CRM Bridge] Lỗi kết nối: {e}", logging.ERROR)

    def register_queue(self, user_id, queue, event):
        """Đăng ký queue của device để Bridge biết nơi đẩy task vào"""
        self.device_queues[user_id] = (queue, event)

    async def _enqueue_task(self, user_id, task_data):
        if user_id in self.device_queues:
            queue, event = self.device_queues[user_id]
            await queue.put(task_data)
            event.set()
            log_message(f"[CRM Bridge] Đã đẩy task vào queue cho user {user_id}")
        else:
            log_message(f"[CRM Bridge] Không tìm thấy thiết bị cho user {user_id}", logging.WARNING)

    def register_device(self, user_id_chat):
        """Đăng ký thiết bị/tài khoản với Receiver"""
        if self.is_connected:
            self.sio_receiver.emit('bot_register', {
                'user_id_chat': user_id_chat,
                'bot_type': self.bot_type
            })

    def send_messages(self, user_id_chat, chat_id, sender_name, content):
        """Gửi tin nhắn thu thập được từ điện thoại về CRM"""
        if not self.is_connected:
            self.connect()
        
        if self.is_connected:
            message_payload = {
                'user_id_chat': user_id_chat,
                'bot_source': self.bot_type,
                'messages': [{
                    'participant_name': sender_name,
                    'participant_url': str(chat_id),
                    'conversation_url': str(chat_id),
                    'content': content,
                    'sender_name': sender_name,
                    'is_reply': False
                }]
            }
            self.sio_receiver.emit('new_messages', message_payload)
            return True
        return False

# Khởi tạo instance dùng chung
crm_bridge = CRMBridge()
