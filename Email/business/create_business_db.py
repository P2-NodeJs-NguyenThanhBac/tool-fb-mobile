import sqlite3
import json
import os
from datetime import datetime
import pytz # Import pytz for timezone handling

# Define Vietnam timezone
VIETNAM_TIMEZONE = pytz.timezone('Asia/Ho_Chi_Minh')

DB_PATH = 'Email/business/businesses.db'
BUSINESS_INFO_JSON = 'Email/business/business_info.json'
QUOTA_INFO_JSON = 'Email/business/email_lst.json'

def create_tables():
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # Create businesses table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS employees (
                emp_id TEXT PRIMARY KEY,
                name   TEXT NOT NULL
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS devices (
                device_id TEXT PRIMARY KEY,      -- có thể là serial number từ adb
                emp_id    TEXT NOT NULL,
                brand     TEXT,
                plugged_in INTEGER DEFAULT 0, -- 0 for not plugged in, 1 for plugged in
                FOREIGN KEY (emp_id) REFERENCES employees(emp_id)
            )
        ''')
        
        # Add plugged_in column if it doesn't exist
        cursor.execute('''
            PRAGMA table_info(devices);
        ''')
        columns = [col[1] for col in cursor.fetchall()]
        if 'plugged_in' not in columns:
            cursor.execute('''
                ALTER TABLE devices ADD COLUMN plugged_in INTEGER DEFAULT 0;
            ''')
            print("Added 'plugged_in' column to 'devices' table.")

        # Create customers table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS customers (
                customer_id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_email TEXT NOT NULL,
                emp_id TEXT NOT NULL,
                sent BOOLEAN DEFAULT 0,
                date TEXT,
                subject TEXT,
                content TEXT,
                FOREIGN KEY (emp_id) REFERENCES employees (emp_id),
                UNIQUE(customer_email, emp_id, date)
            )
        ''')
        
        # Drop email_accounts table if it exists to apply schema changes
        cursor.execute('''
            DROP TABLE IF EXISTS email_accounts;
        ''')

        # Create email_accounts table - quản lý quota
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS email_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT NOT NULL,
                email_account TEXT NOT NULL,
                num_sent INTEGER DEFAULT 0,
                is_active BOOLEAN DEFAULT 1,
                FOREIGN KEY (device_id) REFERENCES devices (device_id),
                UNIQUE(device_id, email_account)
            )
        ''')
        # Create indexes
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_customers_emp_email_date ON customers(customer_email, emp_id, date)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_customers_sent ON customers(sent)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_email_accounts_device_id ON email_accounts(device_id)')
        conn.commit()
        print("Tables 'employees', 'devices', 'customers', and 'email_accounts' created successfully in business.db")

    except sqlite3.Error as e:
        print(f"SQLite error: {e}")
    finally:
        if conn:
            conn.close()


def add_customer_safe(emp_id, customer_email, sent=0,subject=None, content=None,date=None):
    """
    Thêm customer với logic kiểm tra: cùng email + emp_id chỉ được thêm 1 lần/ngày
    """
    if date is None:
        date = datetime.now(VIETNAM_TIMEZONE).strftime("%Y-%m-%d")
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Kiểm tra xem đã tồn tại chưa
        cursor.execute('''
            SELECT COUNT(*) 
            FROM customers 
            WHERE customer_email = ? AND emp_id = ? AND date = ?
        ''', (customer_email, emp_id, date))
        
        if cursor.fetchone()[0] > 0:
            print(f"Customer {customer_email} already exists for on {date}")
            return False
        
        # Insert nếu chưa tồn tại
        cursor.execute('''
            INSERT INTO customers (customer_email, emp_id, sent, date, subject, content)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (customer_email, emp_id, sent, date, subject, content))
        
        conn.commit()
        print(f"Successfully added customer {customer_email} for emp {emp_id}")
        return True
        
    except sqlite3.IntegrityError as e:
        print(f"Integrity error: {e}")
        return False
    except sqlite3.Error as e:
        print(f"SQLite error: {e}")
        return False
    finally:
        if conn:
            conn.close()
            
# add_customer_safe("22615833", "test@gmail.com", 1, "2025-09-10", "Test Subject", "Test Content")
# add_customer_safe("22615833", "test@gmail.com", 1, None, "Test Subject", "Test Content")  # Should fail
# add_customer_safe("22615833", "test@gmail.com", 1, "2025-09-11", "Test Subject", "Test Content")  # Should success
