# Email\src_api\addMail.py
import sys
import os
from flask import Flask, request, jsonify
from datetime import datetime
import sqlite3
import pytz # Import pytz for timezone handling

# Define Vietnam timezone
VIETNAM_TIMEZONE = pytz.timezone('Asia/Ho_Chi_Minh')

# Add the project root directory to sys.path to enable relative imports when running directly
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(script_dir, '..', '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from Email.business.create_business_db import add_customer_safe

app = Flask(__name__)

# Database path
db_path = os.path.join(script_dir, '..', 'business', 'businesses.db')

def init_template_table():
    """Tạo bảng email_templates nếu chưa có"""
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS email_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                emp_id INTEGER UNIQUE,
                subject TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT,
                updated_at TEXT
            )
        ''')
        conn.commit()
    except sqlite3.Error as e:
        print(f"Database error: {e}")
    finally:
        if conn:
            conn.close()

def save_template(emp_id, subject, content):
    """Lưu/cập nhật template cho emp_id"""
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Get current time in Vietnam timezone
        now_vietnam = datetime.now(VIETNAM_TIMEZONE)
        timestamp_str = now_vietnam.strftime("%Y-%m-%d %H:%M:%S")

        # Kiểm tra xem emp_id đã có template chưa
        cursor.execute("SELECT id FROM email_templates WHERE emp_id = ?", (emp_id,))
        existing = cursor.fetchone()
        
        if existing:
            # Cập nhật template hiện có
            cursor.execute('''
                UPDATE email_templates 
                SET subject = ?, content = ?, updated_at = ?
                WHERE emp_id = ?
            ''', (subject, content, timestamp_str, emp_id))
            action = "updated"
        else:
            # Tạo template mới
            cursor.execute('''
                INSERT INTO email_templates (emp_id, subject, content, created_at, updated_at) 
                VALUES (?, ?, ?, ?, ?)
            ''', (emp_id, subject, content, timestamp_str, timestamp_str))
            action = "created"
        
        conn.commit()
        return True, action
    except sqlite3.Error as e:
        print(f"Database error: {e}")
        return False, "error"
    finally:
        if conn:
            conn.close()

# Khởi tạo bảng template khi start app
init_template_table()

@app.route('/add-email', methods=['POST'])
def add_email():
    """API gốc - thêm email với đầy đủ thông tin"""
    data = request.get_json(silent=True)
    if data and "email" in data:
        email = data["email"]
    elif "email" in request.form:
        email = request.form["email"]
    else:
        return jsonify({"error": "Thiếu biến 'email'"}), 400
        
    if data and "emp_id" in data:
        emp_id = data["emp_id"]
    elif "emp_id" in request.form:
        emp_id = request.form["emp_id"]
    else:
        return jsonify({"error": "Thiếu biến 'emp_id'"}), 400
    
    if data and "subject" in data:
        subject = data["subject"]
    elif "subject" in request.form:
        subject = request.form["subject"]
    else:
        return jsonify({"error": "Thiếu biến 'subject'"}), 400
    
    if data and "content" in data:
        content = data["content"]
    elif "content" in request.form:
        content = request.form["content"]
    else:
        return jsonify({"error": "Thiếu biến 'content'"}), 400

    # Use add_customer_safe to add the email to the database
    success = add_customer_safe(emp_id=emp_id, customer_email=email, sent=0, subject=subject, content=content)
    
    if success:
        print(f"✅ Thêm email {email} cho emp_id {emp_id} vào DB")
        return jsonify({
            "status": "added", 
            "emp_id": emp_id, 
            "email": email,
            "date": datetime.now(VIETNAM_TIMEZONE).strftime("%Y-%m-%d")
        })
    else:
        print(f"ℹ️ Email {email} đã tồn tại cho emp_id {emp_id} trong ngày hôm nay hoặc có lỗi xảy ra")
        return jsonify({
            "status": "existed_today_or_error", 
            "message": f"Email {email} đã được thêm cho emp_id {emp_id} trong ngày hôm nay hoặc có lỗi xảy ra",
            "emp_id": emp_id, 
            "email": email,
            "date": datetime.now(VIETNAM_TIMEZONE).strftime("%Y-%m-%d")
        })

@app.route('/set-template', methods=['POST'])
def set_template():
    """API mới - Bên A gọi để set template cho emp_id (3 biến: emp_id, subject, content)"""
    data = request.get_json(silent=True) or {}
    form_data = request.form
    
    # Lấy dữ liệu từ JSON hoặc form
    emp_id = data.get('emp_id') or form_data.get('emp_id')
    subject = data.get('subject') or form_data.get('subject')
    content = data.get('content') or form_data.get('content')
    
    # Kiểm tra dữ liệu bắt buộc
    if not emp_id:
        return jsonify({"error": "Thiếu biến 'emp_id'"}), 400
    if not subject:
        return jsonify({"error": "Thiếu biến 'subject'"}), 400
    if not content:
        return jsonify({"error": "Thiếu biến 'content'"}), 400
    
    try:
        emp_id = int(emp_id)
    except ValueError:
        return jsonify({"error": "emp_id phải là số"}), 400
    
    # Lưu template
    success, action = save_template(emp_id, subject, content)
    
    if success:
        # Không thêm dòng mẫu vào bảng customers nữa theo yêu cầu
        
        # Get current time in Vietnam timezone for response
        now_vietnam = datetime.now(VIETNAM_TIMEZONE)
        timestamp_str = now_vietnam.strftime("%Y-%m-%d %H:%M:%S")

        print(f"✅ Template {action} cho emp_id {emp_id}")
        return jsonify({
            "status": "success",
            "action": action,
            "emp_id": emp_id,
            "subject": subject,
            "timestamp": timestamp_str
        })
    else:
        return jsonify({"error": "Không thể lưu template"}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5468, debug=True)
