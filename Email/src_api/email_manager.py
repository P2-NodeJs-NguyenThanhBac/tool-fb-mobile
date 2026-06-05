import os
import sqlite3
from datetime import datetime
from filelock import FileLock

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "..", "business", "businesses.db")

MAX_PER_DAY = 3
ENABLE_RESET = False  # <-- bật True để auto reset mỗi ngày, False để test

class EmailManager:
    def __init__(self, device_id: str):
        self.device_id = device_id
        self.today = datetime.now().strftime("%Y-%m-%d")
        if ENABLE_RESET:
            self._ensure_reset_today()

    def _get_db_connection(self):
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_reset_today(self):
        """Reset toàn bộ counters nếu chưa reset hôm nay."""
        # Sử dụng FileLock để tránh xung đột khi reset
        lock_file = DB_PATH + ".lock"
        with FileLock(lock_file, timeout=10):
            conn = self._get_db_connection()
            cursor = conn.cursor()
            
            # Kiểm tra xem đã reset hôm nay chưa bằng cách tạo bảng settings
            self._create_settings_table_if_not_exists(cursor)
            
            # Kiểm tra last_reset_date
            cursor.execute("SELECT value FROM settings WHERE key = 'last_reset_date'")
            last_reset = cursor.fetchone()
            
            if not last_reset or last_reset["value"] != self.today:
                # Reset counters
                cursor.execute("UPDATE email_accounts SET num_sent = 0 WHERE device_id = ?", (self.device_id,))
                # Cập nhật last_reset_date
                cursor.execute(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES ('last_reset_date', ?)",
                    (self.today,)
                )
                conn.commit()
                print(f"🔄 Reset toàn bộ counter về 0 cho DEVICE_ID {self.device_id} cho ngày {self.today}")
            else:
                print(f"✅ Counter đã được reset hôm nay ({self.today})")
            
            conn.close()

    def _create_settings_table_if_not_exists(self, cursor):
        """Tạo bảng settings nếu chưa tồn tại"""
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        ''')

    def get_available_account(self):
        """Trả về email có num_sent < MAX_PER_DAY hoặc None nếu hết."""
        conn = self._get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """SELECT email_account, id, num_sent 
               FROM email_accounts 
               WHERE device_id = ? AND is_active = 1 AND num_sent < ? 
               ORDER BY num_sent ASC 
               LIMIT 1""",
            (self.device_id, MAX_PER_DAY)
        )
        account = cursor.fetchone()
        conn.close()
        
        if account:
            print(f"📧 Sử dụng account: {account['email_account']} (đã gửi: {account['num_sent']}/{MAX_PER_DAY})")
            return account["email_account"]
        
        print(f"❌ Không còn email account khả dụng cho DEVICE_ID {self.device_id} (tất cả đã đạt limit {MAX_PER_DAY}/ngày)")
        return None

    def increase_counter(self, email_account):
        """Tăng counter cho một account trong DB."""
        # Sử dụng FileLock để tránh xung đột khi update counter
        lock_file = DB_PATH + ".lock"
        with FileLock(lock_file, timeout=10):
            conn = self._get_db_connection()
            cursor = conn.cursor()
            
            # Kiểm tra xem account có tồn tại không
            cursor.execute(
                "SELECT num_sent FROM email_accounts WHERE device_id = ? AND email_account = ?",
                (self.device_id, email_account)
            )
            current_record = cursor.fetchone()
            
            if not current_record:
                conn.close()
                raise ValueError(f"Email account {email_account} không tồn tại cho DEVICE_ID {self.device_id}")
            
            current_count = current_record["num_sent"]
            
            if current_count >= MAX_PER_DAY:
                conn.close()
                raise ValueError(f"Email account {email_account} đã đạt giới hạn {MAX_PER_DAY} emails/ngày")
            
            # Tăng counter
            cursor.execute(
                "UPDATE email_accounts SET num_sent = num_sent + 1 WHERE device_id = ? AND email_account = ?",
                (self.device_id, email_account)
            )
            conn.commit()
            
            # Lấy giá trị mới
            cursor.execute(
                "SELECT num_sent FROM email_accounts WHERE device_id = ? AND email_account = ?",
                (self.device_id, email_account)
            )
            new_val = cursor.fetchone()["num_sent"]
            conn.close()
            
            print(f"🔒 Đã tăng counter {email_account}: {current_count} → {new_val}/{MAX_PER_DAY}")

    def get_account_status(self):
        """Lấy trạng thái tất cả accounts của employee"""
        conn = self._get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """SELECT email_account, num_sent, is_active 
               FROM email_accounts 
               WHERE device_id = ? 
               ORDER BY num_sent ASC""",
            (self.device_id,)
        )
        accounts = cursor.fetchall()
        conn.close()
        
        if not accounts:
            print(f"❌ Không tìm thấy email accounts cho DEVICE_ID {self.device_id}")
            return []
        
        print(f"📊 Trạng thái email accounts cho DEVICE_ID {self.device_id}:")
        status_list = []
        for account in accounts:
            status = "🟢 Active" if account["is_active"] else "🔴 Inactive"
            limit_status = f"{account['num_sent']}/{MAX_PER_DAY}"
            if account["num_sent"] >= MAX_PER_DAY:
                limit_status += " ❌ FULL"
            else:
                limit_status += " ✅ Available"
            
            print(f"  • {account['email_account']}: {limit_status} ({status})")
            status_list.append({
                'email_account': account['email_account'],
                'num_sent': account['num_sent'],
                'is_active': bool(account['is_active']),
                'available': account['num_sent'] < MAX_PER_DAY and account['is_active']
            })
        
        return status_list

    def reset_account_counter(self, email_account):
        """Reset counter của một account cụ thể (để test hoặc reset thủ công)"""
        lock_file = DB_PATH + ".lock"
        with FileLock(lock_file, timeout=10):
            conn = self._get_db_connection()
            cursor = conn.cursor()
            
            cursor.execute(
                "UPDATE email_accounts SET num_sent = 0 WHERE device_id = ? AND email_account = ?",
                (self.device_id, email_account)
            )
            
            if cursor.rowcount == 0:
                conn.close()
                raise ValueError(f"Email account {email_account} không tồn tại cho DEVICE_ID {self.device_id}")
            
            conn.commit()
            conn.close()
            print(f"🔄 Đã reset counter cho {email_account} về 0")

    def has_available_accounts(self):
        """Kiểm tra xem còn accounts khả dụng không"""
        return self.get_available_account() is not None
