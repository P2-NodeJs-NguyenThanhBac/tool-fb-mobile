-- -- Create employees table
-- CREATE TABLE IF NOT EXISTS employees (
--     emp_id TEXT PRIMARY KEY,
--     name   TEXT NOT NULL
-- );

-- CREATE TABLE IF NOT EXISTS devices (
--     device_id TEXT PRIMARY KEY,      -- có thể là serial number từ adb
--     emp_id    TEXT NOT NULL,
--     brand     TEXT,
--     total_limit INT DEFAULT 30,
--     plugged_in BOOLEAN DEFAULT 0,
--     FOREIGN KEY (emp_id) REFERENCES employees(emp_id)
-- );

-- -- Create customers table
-- CREATE TABLE IF NOT EXISTS customers (
--     customer_id INTEGER PRIMARY KEY AUTOINCREMENT,
--     customer_email TEXT NOT NULL,
--     emp_id TEXT NOT NULL,
--     sent BOOLEAN DEFAULT 0,
--     date TEXT,
--     subject TEXT,
--     content TEXT,
--     FOREIGN KEY (emp_id) REFERENCES employees (emp_id),
--     UNIQUE(customer_email, emp_id, date)
-- );

-- -- Create email_accounts table - quản lý quota
-- CREATE TABLE IF NOT EXISTS email_accounts (
--     id INTEGER PRIMARY KEY AUTOINCREMENT,
--     emp_id TEXT NOT NULL,
--     email_account TEXT NOT NULL,
--     num_sent INTEGER DEFAULT 0,
--     is_active BOOLEAN DEFAULT 1,
--     FOREIGN KEY (emp_id) REFERENCES employees (emp_id),
--     UNIQUE(emp_id, email_account)
-- );

-- -- Create indexes
-- CREATE INDEX IF NOT EXISTS idx_customers_emp_email_date ON customers(customer_email, emp_id, date);
-- CREATE INDEX IF NOT EXISTS idx_customers_sent ON customers(sent);
-- CREATE INDEX IF NOT EXISTS idx_email_accounts_emp_id ON email_accounts(emp_id);


INSERT INTO devices (device_id, emp_id, brand, total_limit, plugged_in)
VALUES 
('EM4DYTEITCCYJNFU', '22616467', 'Redmi', 30, 1);

-- # 22616467

-- Còn thiêu dữ liệu Lê Thuỳ 22636101
-- Thừa máy của 22614471,22636101,22789191, 22833463
SELECT device_id,emp_id, brand from devices
WHERE emp_id = '22636101';

UPDATE devices SET emp_id = 22616467 
WHERE device_id = 'EM4DYTEITCCYJNFU';

UPDATE devices SET device_id='CQIZKJ8P59AY7DHI'
WHERE emp_id = '22889226'

SELECT count(*) from devices


SELECT emp_id, COUNT(device_id) AS device_count
FROM devices
GROUP BY emp_id;


DELETE FROM customers
WHERE customer_id IN (
    SELECT customer_id
    FROM customers
    ORDER BY customer_id DESC
    LIMIT 1
);
UPDATE email_accounts SET device_name = 'A00' 



ALTER TABLE email_accounts 
ADD COLUMN device_name TEXT DEFAULT 'A00';


SELECT emp_id, device_name from devices

UPDATE devices
SET emp_id = '22911349'
WHERE device_name IN ('A23','A24');

UPDATE email_accounts
SET emp_id = '22911349'
WHERE device_name IN ('A23','A24');