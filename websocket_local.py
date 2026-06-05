import asyncio
import websockets
import json
import logging
import pymongo_management
from module import *
import uiautomator2 as u2

WEBSOCKET_URL = "wss://socket.hungha365.com:4000"

class WebSocketTaskHandler:
    """Xử lý task từ WebSocket server"""
    
    def __init__(self, client_id: str):
        self.client_id = client_id
        self.websocket = None
        self.connected = False
    
    async def get_account_and_driver(self, user_id: str):
        account, device_id = await pymongo_management.get_account_by_username(user_id)
        driver = u2.connect(device_id)
        return account, driver

    async def get_commands(self, user_id: str):
        return await pymongo_management.get_commands(user_id)
    
    async def run_commands(self, driver, user_id: str):
        """Lấy lệnh từ server và thực hiện"""
        commands = await self.get_commands(user_id)
        for command in commands:
            try:    
                params = command.get("params", {})
                if command['type'] == 'post_to_group':
                    await post_to_group(driver, command['_id'], user_id, params.get("group_link", ""), params.get("content", ""), params.get("files", []))
                if command['type'] == 'join_group':
                    await join_group(driver, command['_id'], user_id, params.get("group_link", ""))
                if command['type'] == 'post_to_wall':
                    await post_to_wall(driver, command['_id'], user_id, params.get("content", ""), params.get("files", []))
            except Exception as e:
                log_message(f"{DEVICE_LIST_NAME[driver.serial]} - Lỗi khi thực hiện lệnh {command['type']}: {e}", logging.ERROR)
                await pymongo_management.execute_command(command['_id'], f"Lỗi: {e}")
            await asyncio.sleep(random.uniform(4, 6))

    async def check_device_status(self, driver):
        # Lấy status từ file json tại thư mục Zalo_base
        with open(DEVICE_STATUS_PATH(driver.serial), 'r') as f:
            device_status = json.load(f).get('active', False)
        return not device_status
    
    async def update_device_status(self, driver, status: bool):
        # Cập nhật status vào file json tại thư mục Zalo_base
        with open(DEVICE_STATUS_PATH(driver.serial), 'r') as f:
            data = json.load(f)
            data['active'] = status
        with open(DEVICE_STATUS_PATH(driver.serial), 'w') as f:
            json.dump(data, f)

    async def handle_server_message(self, account, driver, message_type):
        try:
            """Xử lý message từ server và tạo task tương ứng"""
            if message_type == "new_command_notification":
                while True:
                    if await self.check_device_status(driver):
                        break
                    await asyncio.sleep(5)
                await self.update_device_status(driver, True)
                if not account:
                    log_message(f"{DEVICE_LIST_NAME[driver.serial]} - Thực hiện lệnh từ CRM: Không có user_id trong message", logging.WARNING)
                    return
                if not account['status'] == "Online":
                    acc = {
                        'name': account['name'],
                        'account': account['account'],
                        'password': account['password'],
                    }
                    success = await login.swap_account(driver, acc)
                    if not success:
                        await pymongo_management
                        await self.update_device_status(driver, False)
                        return
                # Nhận và thực hiện lệnh
                await self.run_commands(driver, account['account'])
                await self.update_device_status(driver, False)
        except Exception as e:
            log_message(f"{DEVICE_LIST_NAME[driver.serial]} - Lỗi xử lý message từ server: {e}", logging.ERROR)
            await self.update_device_status(driver, False)

    async def send_response(self, data: dict):
        """Gửi response về server"""
        if self.websocket and self.connected:
            try:
                await self.websocket.send(json.dumps(data))
                log_message(f"Sent response: {data.get('type', 'unknown')}")
            except Exception as e:
                log_message(f"Failed to send response: {e}", logging.ERROR)
    
    async def connect_websocket(self):
        """Kết nối WebSocket với auto-reconnect"""
        reconnect_interval = 5
        max_reconnect_interval = 60
        
        while True:
            try:
                log_message("Đang thử kết nối tới WebSocket server...", logging.INFO)
                
                async with websockets.connect(WEBSOCKET_URL) as websocket:
                    self.websocket = websocket
                    self.connected = True
                    log_message("Đã kết nối thành công tới WebSocket server!", logging.INFO)
                    
                    # Reset reconnect interval
                    reconnect_interval = 5
                    
                    # Gửi tin nhắn đăng ký
                    register_message = {
                        "type": "register",
                        "clientId": self.client_id,
                    }
                    await websocket.send(json.dumps(register_message))
                    log_message(f"Đã gửi tin nhắn đăng ký với clientId: {self.client_id}", logging.INFO)
                    
                    # Lắng nghe tin nhắn từ server
                    async for message in websocket:
                        try:
                            data = json.loads(message)
                            
                            # Trích xuất thông tin từ message
                            message_type = data.get("type", "")
                            if not message_type:
                                continue
                            log_message(f"Nhận lệnh mới từ server - Type: {message_type}", logging.INFO)
                            user_id = data.get("data", {}).get("user_id", "")
                            if user_id == "":
                                continue
                            log_message(f"User nhận lệnh: {user_id}")
                            account, driver = await self.get_account_and_driver(user_id)
                            
                            # Xử lý message và tạo task
                            asyncio.create_task(self.handle_server_message(account, driver, message_type))
                        except Exception as e:
                            log_message(f"Error handling message: {e}", logging.ERROR)
                            await self.update_device_status(driver, False)
            except websockets.exceptions.ConnectionClosed:
                log_message("WebSocket connection bị đóng. Sẽ thử kết nối lại...", logging.WARNING)
                self.connected = False
            except Exception as e:
                log_message(f"Lỗi kết nối WebSocket: {e}. Sẽ thử kết nối lại...", logging.ERROR)
                self.connected = False
            
            # Đợi trước khi thử kết nối lại
            log_message(f"Đợi {reconnect_interval} giây trước khi kết nối lại WebSocket...", logging.INFO)
            await asyncio.sleep(reconnect_interval)
            
            # Tăng thời gian chờ dần
            reconnect_interval = min(reconnect_interval * 1.5, max_reconnect_interval)

# Hàm chính để khởi động WebSocket client
async def start_websocket_client(client_id: str = "1498"):
    """Khởi động WebSocket client"""
    # Khởi động WebSocket client
    handler = WebSocketTaskHandler(client_id)
    await handler.connect_websocket()

if __name__ == "__main__":
    asyncio.run(start_websocket_client(1498))