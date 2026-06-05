import adbutils
import sqlite3
import time
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "..", "business", "businesses.db")

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def update_device_status():
    """
    Kiểm tra các thiết bị ADB đang cắm và cập nhật trạng thái 'plugged_in' trong DB.
    """
    connected_serials = {d.serial for d in adbutils.adb.device_list()}
    
    conn = get_db_connection()
    cursor = conn.cursor()

    # Lấy tất cả thiết bị từ bảng devices
    cursor.execute("SELECT device_id, plugged_in FROM devices")
    db_devices = cursor.fetchall()

    for db_device in db_devices:
        device_id_in_db = db_device["device_id"]
        current_plugged_in_status = db_device["plugged_in"]
        
        if device_id_in_db in connected_serials:
            # Thiết bị đang cắm, đảm bảo trạng thái là 1
            if current_plugged_in_status == 0:
                cursor.execute("UPDATE devices SET plugged_in = 1 WHERE device_id = ?", (device_id_in_db,))
                print(f"✅ Cập nhật trạng thái: Thiết bị {device_id_in_db} đã được cắm.")
        else:
            # Thiết bị không cắm, đảm bảo trạng thái là 0
            if current_plugged_in_status == 1:
                cursor.execute("UPDATE devices SET plugged_in = 0 WHERE device_id = ?", (device_id_in_db,))
                print(f"❌ Cập nhật trạng thái: Thiết bị {device_id_in_db} đã bị rút.")
    
    conn.commit()
    conn.close()

def main():
    print("🚀 Bắt đầu giám sát thiết bị ADB...")
    while True:
        update_device_status()
        print("💤 Chờ 90 giây cho lần kiểm tra tiếp theo...")
        time.sleep(90) # Kiểm tra mỗi 90 giây

if __name__ == "__main__":
    main()
