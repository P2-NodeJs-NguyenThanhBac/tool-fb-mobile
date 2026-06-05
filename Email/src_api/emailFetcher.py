# Email/src_api/fetch_and_add_emails.py
import sqlite3
import requests
import json
import os
import sys
import time
from datetime import datetime

# Add the project root directory to sys.path
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(script_dir, '..', '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from Email.business.create_business_db import add_customer_safe

# Database path
db_path = os.path.join(script_dir, '..', 'business', 'businesses.db')

def get_emp_ids_from_db():
    """Lấy tất cả emp_id từ bảng employees"""
    emp_ids = []
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT emp_id FROM employees")
        rows = cursor.fetchall()
        emp_ids = [row[0] for row in rows]
    except sqlite3.Error as e:
        print(f"Database error: {e}")
    finally:
        if conn:
            conn.close()
    return emp_ids

def get_template_by_emp_id(emp_id):
    """Lấy template theo emp_id từ bảng email_templates"""
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT subject, content FROM email_templates WHERE emp_id = ?", 
            (emp_id,)
        )
        result = cursor.fetchone()
        if result:
            return {"subject": result[0], "content": result[1]}
        return None
    except sqlite3.Error as e:
        print(f"Database error: {e}")
        return None
    finally:
        if conn:
            conn.close()

def get_customer_emails_for_emp_id(emp_id):
    """Gọi API để lấy email khách hàng cho một emp_id cụ thể"""
    url = "https://api.timviec365.vn/api/crm/customer/getNTDByEmpIdToGetPhoneNumber"
    headers = {"Content-Type": "application/json"}
    payload = {
        "emp_ids": [emp_id],
        "size": 1,  # Có thể tăng số lượng nếu cần
        "key": "1697a131cb22ea0ab9510d379a8151f1",
        "isGetEmail": True
    }

    try:
        response = requests.post(url, headers=headers, data=json.dumps(payload))
        response.raise_for_status()
        data = response.json()
        
        if "data" in data and str(emp_id) in data["data"]:
            customers = data["data"][str(emp_id)]
            emails = []
            for customer in customers:
                if customer.get("email"):
                    emails.append({
                        "email": customer["email"],
                        "name": customer.get("name", ""),
                        "phone": customer.get("phone_number", "")
                    })
            return emails
        return []
    except requests.exceptions.RequestException as e:
        print(f"API request failed for emp_id {emp_id}: {e}")
        return []

def process_single_emp_id(emp_id):
    """Xử lý một emp_id: lấy template + lấy email khách hàng + thêm vào DB"""
    # print(f"\n🔄 Xử lý emp_id: {emp_id}")
    
    # 1. Lấy template
    template = get_template_by_emp_id(emp_id)
    if not template:
        print(f"❌ Không tìm thấy template cho emp_id {emp_id}")
        return {
            "emp_id": emp_id,
            "status": "no_template",
            "added_count": 0,
            "total_emails": 0
        }
    
    
    # 2. Lấy email khách hàng
    customer_emails = get_customer_emails_for_emp_id(emp_id)
    if not customer_emails:
        print(f"⚠️ Không tìm thấy email khách hàng cho emp_id {emp_id}")
        return {
            "emp_id": emp_id,
            "status": "no_customers",
            "added_count": 0,
            "total_emails": 0
        }
    
    # print(f"📧 Tìm thấy {len(customer_emails)} email khách hàng")
    
    # 3. Thêm từng email vào database
    added_count = 0
    existed_count = 0
    
    for customer in customer_emails:
        email = customer["email"]
        success = add_customer_safe(
            emp_id=emp_id,
            customer_email=email,
            sent=0,
            subject=template["subject"],
            content=template["content"]
        )
        
        if success:
            added_count += 1
            # print(f"  ✅ Thêm: {email}")
        else:
            existed_count += 1
            print(f"  ℹ️ Đã tồn tại: {email}")
    
    # print(f"📊 Kết quả emp_id {emp_id}: Thêm {added_count}/{len(customer_emails)} email")
    
    return {
        "emp_id": emp_id,
        "status": "processed",
        "template": template,
        "added_count": added_count,
        "existed_count": existed_count,
        "total_emails": len(customer_emails),
        "emails": customer_emails
    }

def process_all_emp_ids():
    """Xử lý tất cả emp_id - bỏ qua những emp_id không có email khách hàng"""
    print("🚀 Bắt đầu xử lý tất cả emp_id...")
    
    # Lấy danh sách emp_id
    emp_ids = get_emp_ids_from_db()
    if not emp_ids:
        print("❌ Không tìm thấy emp_id nào trong database")
        return
    
    print(f"📋 Tìm thấy {len(emp_ids)} emp_id: {emp_ids}")
    
    # Xử lý từng emp_id
    results = []
    total_added = 0
    skipped_count = 0
    
    for emp_id in emp_ids:
        result = process_single_emp_id(emp_id)
        
        # Chỉ thêm vào results nếu có email khách hàng hoặc có template
        if result["status"] == "no_customers" and result["total_emails"] == 0:
            skipped_count += 1
            print(f"⏭️ Bỏ qua emp_id {emp_id} - không có email khách hàng")
            continue
            
        results.append(result)
        total_added += result["added_count"]
    
    # Tóm tắt kết quả
    print(f"\n📊 === TỔNG KẾT ===")
    print(f"Đã xử lý: {len(results)} emp_id (bỏ qua {skipped_count} emp_id không có email)")
    print(f"Tổng email được thêm: {total_added}")
    
    for result in results:
        status_icon = "✅" if result["status"] == "processed" else "❌"
        print(f"{status_icon} EMP {result['emp_id']}: {result['added_count']}/{result['total_emails']} email")
    
    return results

def run_periodically_update():
    """Hàm chính để chạy định kỳ - gọn gàng và đơn giản"""
    try:
        print(f"⏰ Bắt đầu cập nhật email - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        results = process_all_emp_ids()
        
        # Chỉ log tóm tắt
        processed = len([r for r in results if r["status"] == "processed"])
        total_added = sum(r["added_count"] for r in results)
        
        print(f"✅ Hoàn thành: {processed} emp_id xử lý, {total_added} email mới được thêm")
        return True
        
    except Exception as e:
        print(f"❌ Lỗi khi cập nhật email: {e}")
        return False

if __name__ == "__main__":
    # Chạy hàm cập nhật định kỳ mỗi 10 phút
    while True:
        run_periodically_update()
        print(f"😴 Đang chờ 10 phút trước lần cập nhật tiếp theo...")
        time.sleep(60) # 600 giây = 10 phút
