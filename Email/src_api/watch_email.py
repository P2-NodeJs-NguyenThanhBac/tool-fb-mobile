import time
import os
import sqlite3
from datetime import datetime
from classSend import EmailSender
from classHtmlRender import HtmlRenderSimulator
from email_manager import EmailManager # Import EmailManager

# --- Cấu hình ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "..", "business", "businesses.db")

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def get_plugged_in_device_for_emp(emp_id):
    """
    Kiểm tra xem có thiết bị nào đang cắm (plugged_in = 1) cho emp_id này không.
    Trả về device_id đầu tiên tìm thấy hoặc None.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT device_id FROM devices WHERE emp_id = ? AND plugged_in = 1 LIMIT 1",
        (emp_id,)
    )
    result = cursor.fetchone()
    conn.close()
    return result["device_id"] if result else None

def get_distinct_emp_ids_with_pending_emails():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT DISTINCT emp_id FROM customers WHERE sent = 0"
    )
    emp_ids = [row["emp_id"] for row in cursor.fetchall()]
    conn.close()
    return emp_ids

def get_next_pending_customer(emp_id):
    """Lấy khách hàng chờ xử lý tiếp theo cho một emp_id."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT customer_id FROM customers WHERE emp_id = ? AND sent = 0 ORDER BY customer_id LIMIT 1",
        (emp_id,)
    )
    result = cursor.fetchone()
    conn.close()
    return result["customer_id"] if result else None

def is_customer_sent(customer_id):
    """Kiểm tra xem một customer đã được đánh dấu là đã gửi chưa."""
    if customer_id is None:
        return True # Không có customer trước đó, coi như đã xử lý
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT sent FROM customers WHERE customer_id = ?",
        (customer_id,)
    )
    result = cursor.fetchone()
    conn.close()
    return result["sent"] == 1 if result else False

def process_next_email_for_emp(emp_id, last_processed_customer_info):
    """
    Xử lý email chờ xử lý tiếp theo cho một nhân viên.
    Kiểm tra xem email đã xử lý trước đó cho nhân viên này đã được đánh dấu là đã gửi chưa.
    Trả về ID của khách hàng vừa được xử lý, hoặc ID cũ nếu thất bại.
    """
    last_customer_id = last_processed_customer_info.get('customer_id')
    last_html_ok = last_processed_customer_info.get('html_ok', True)

    if not is_customer_sent(last_customer_id) or not last_html_ok:
        print(f"🔴 Tác vụ trước đó cho EMP_ID {emp_id} (Customer ID: {last_customer_id}) chưa hoàn tất. Tạm dừng cho nhân viên này.")
        return {'customer_id': last_customer_id, 'html_ok': last_html_ok}

    customer_id = get_next_pending_customer(emp_id)

    if customer_id is None:
        # Không còn khách hàng nào cho nhân viên này, reset trạng thái
        return {'customer_id': None, 'html_ok': True} 

    print(f"\n▶️ Đang xử lý khách hàng ID: {customer_id} cho EMP_ID: {emp_id}...")

    try:
        # Kiểm tra thiết bị đang cắm
        plugged_in_device_id = get_plugged_in_device_for_emp(emp_id)
        if not plugged_in_device_id:
            print(f"   - ⚠️ Không có thiết bị nào đang cắm cho EMP_ID {emp_id}. Bỏ qua gửi email cho customer ID: {customer_id}.")
            return {'customer_id': last_customer_id, 'html_ok': True} # Coi như đã xử lý để không bị lặp lại ngay lập tức

        # Kiểm tra tài khoản email khả dụng
        email_manager = EmailManager(device_id=plugged_in_device_id)
        if not email_manager.has_available_accounts():
            print(f"   - ⚠️ Không còn tài khoản email khả dụng cho DEVICE_ID {plugged_in_device_id}. Bỏ qua gửi email cho customer ID: {customer_id}.")
            return {'customer_id': last_customer_id, 'html_ok': True} # Coi như đã xử lý để không bị lặp lại ngay lập tức

        # 1. Xử lý HTML
        print("   - Bước 1: Xử lý HTML...")
        simulator = HtmlRenderSimulator(device_id=plugged_in_device_id, customer_id=customer_id)
        simulator.beautify_html()

        if not simulator.html_processed:
            print(f"   - ❌ Lỗi: Xử lý HTML thất bại cho customer ID: {customer_id}.")
            return {'customer_id': last_customer_id, 'html_ok': False}

        # 2. Gửi Email
        print("   - Bước 2: Gửi email...")
        sender = EmailSender(device_id=plugged_in_device_id, customer_id=customer_id)
        sender.open_gmail()
        success = sender.send_to_customer(customer_id)

        if success:
            print(f"   - ✅ Gửi email thành công cho customer ID: {customer_id}.")
            return {'customer_id': customer_id, 'html_ok': True}
        else:
            print(f"   - ❌ Lỗi: Gửi email thất bại cho customer ID: {customer_id}.")
            return {'customer_id': last_customer_id, 'html_ok': True}

    except Exception as e:
        print(f"   - ❌ Lỗi nghiêm trọng khi xử lý customer ID {customer_id}: {e}")
        return {'customer_id': last_customer_id, 'html_ok': True}

def main():
    # Dictionary để theo dõi khách hàng cuối cùng được xử lý cho mỗi nhân viên
    # Định dạng: { emp_id: {'customer_id': id, 'html_ok': True/False} }
    last_processed_status = {}

    while True:
        print(f"\n--- Chạy kiểm tra lúc {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---")
        emp_ids = get_distinct_emp_ids_with_pending_emails()

        if not emp_ids:
            print("ℹ️ Không có nhân viên nào có email cần gửi.")
        else:
            print(f"🔍 Tìm thấy {len(emp_ids)} nhân viên có email chờ xử lý: {emp_ids}")
            for emp_id in emp_ids:
                # Lấy trạng thái cuối cùng cho nhân viên này, hoặc mặc định là trạng thái sạch
                last_status = last_processed_status.get(emp_id, {'customer_id': None, 'html_ok': True})
                
                # Xử lý một email và nhận trạng thái mới
                new_status = process_next_email_for_emp(emp_id, last_status)
                
                # Cập nhật bản đồ trạng thái
                last_processed_status[emp_id] = new_status

        print(f"--- Hoàn thành chu kỳ, nghỉ 90 giây ---")
        time.sleep(90)

if __name__ == "__main__":
    main()
