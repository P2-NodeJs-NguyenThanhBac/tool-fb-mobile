import uiautomator2 as u2
import time
import os
import json
from filelock import FileLock
import sqlite3


class HtmlRenderSimulator:
    def __init__(self, device_id: str, customer_id: int, MODE=2):
        self.BASE_DIR = os.path.dirname(os.path.abspath(__file__))
        self.device_id = device_id
        self.customer_id = customer_id
        self.MODE = MODE
        self.html_processed = False
        # Database path
        self.DB_PATH = os.path.join(self.BASE_DIR, "..", "business", "businesses.db")
        
        self.EMP_ID = self._get_emp_id_from_customer(customer_id)
        if not self.EMP_ID:
            raise ValueError(f"Không tìm thấy emp_id cho Customer ID: {self.customer_id}")

        self.device_brand = self._get_device_brand(self.device_id)
        if not self.device_brand:
            raise ValueError(f"Không tìm thấy device_brand cho Device ID: {self.device_id}")

        self.d = u2.connect(self.device_id)
        self.width, self.height = self.d.window_size()
        
        # Get email content from database
        self.BUSINESS_SUBJECT = None
        self.BUSINESS_WRITEN_MAIL = None
        
        self.load_customer_data()

    def _get_db_connection(self):
        conn = sqlite3.connect(self.DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn

    def _get_emp_id_from_customer(self, customer_id):
        """Lấy emp_id từ bảng customers dựa trên customer_id"""
        conn = self._get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT emp_id FROM customers WHERE customer_id = ?", (customer_id,))
        result = cursor.fetchone()
        conn.close()
        return result["emp_id"] if result else None

    def _get_device_brand(self, device_id):
        """Lấy device_brand từ bảng devices dựa trên device_id"""
        conn = self._get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT brand FROM devices WHERE device_id = ?", (device_id,))
        result = cursor.fetchone()
        conn.close()
        return result["brand"] if result else None

    def load_customer_data(self):
        """Load subject và content từ bảng customers"""
        conn = None
        try:
            conn = self._get_db_connection()
            cursor = conn.cursor()
            
            cursor.execute(
                "SELECT subject, content FROM customers WHERE customer_id = ? AND emp_id = ?", 
                (self.customer_id, self.EMP_ID)
            )
            result = cursor.fetchone()
            
            if result:
                self.BUSINESS_SUBJECT = result[0] if result[0] else ""
                self.BUSINESS_WRITEN_MAIL = result[1] if result[1] else ""
            else:
                raise ValueError(f"Customer ID {self.customer_id} not found for employee {self.EMP_ID}")
                
        except sqlite3.Error as e:
            raise Exception(f"Database error: {e}")
        finally:
            if conn:
                conn.close()

    def get_subject(self):
        """Lấy subject từ database"""
        if self.BUSINESS_SUBJECT is None and self.customer_id:
            self.load_customer_data()
        return self.BUSINESS_SUBJECT if self.BUSINESS_SUBJECT else ""

    def get_content(self):
        """Lấy content từ database"""
        if self.BUSINESS_WRITEN_MAIL is None and self.customer_id:
            self.load_customer_data()
        return self.BUSINESS_WRITEN_MAIL if self.BUSINESS_WRITEN_MAIL else ""
    
    
    def beautify_html(self):
        """Chuyển text dạng html thành text render có format"""
        if self.html_processed:
            print("HTML đã được xử lý rồi")
            return None
        # chạy lấy rendered text
        self.open_html_app()
        self.compile_html()
        
        print("HTML processing hoàn thành, chờ 5s...")
        time.sleep(5)
        self.html_processed = True
        print("HTML processing đã được đánh dấu hoàn thành")

        return None
    
    def open_html_app(self):
        """
        Mở app Google trên thiết bị
        Lưu ý khi mở app phải ở trong thẻ .html rồi 
        sử dụng weditor để dễ bề biết trước xpath
        """
        if self.device_brand == "Redmi":
            # Thao tác mở Google trên Redmi
            self.d(resourceId="com.android.systemui:id/center_group").click()
            time.sleep(1)
            self.d.swipe_ext("up", scale=0.8)
            time.sleep(1)
            self.d(resourceId="com.gogo.launcher:id/search_container_all_apps").click()
            time.sleep(1)
            self.d.send_keys("Google", clear=True)
            time.sleep(1)
            self.d(resourceId="com.gogo.launcher:id/icon", text="Google").click()
            time.sleep(1)
            print("Đang mở Google trên Redmi...")
        elif self.device_brand == "Samsung":
            # Thao tác mở Google trên Samsung
            self.d(resourceId="com.android.systemui:id/center_group").click()
            time.sleep(1)
            self.d.swipe_ext("up", scale=0.8)
            time.sleep(1)
            self.d(resourceId="com.sec.android.app.launcher:id/app_search_edit_text_wrapper").click()
            time.sleep(1)
            self.d.send_keys("Google", clear=True)
            time.sleep(1)
            self.d(resourceId="com.sec.android.app.launcher:id/label", text="Google").click()
            time.sleep(1)
            # Ví dụ: self.d.app_start("com.android.chrome") hoặc các thao tác khác
            print("Đang mở Google trên Samsung...")
        else:
            raise ValueError(f"Thiết bị {self.device_brand} không được hỗ trợ.")
        
        self.search_html_online_viewer()
        
    def search_html_online_viewer(self):
        """Mở đúng website htmlonlineviewer"""
        if self.device_brand == "Redmi":
            # Thao tác tìm kiếm HTML Online Viewer trên Redmi
            if self.d(resourceId="com.android.chrome:id/url_bar", textContains="html.onlineviewer.net").exists(timeout=2):
                print("Đúng trang HTML Online Viewer rồi → clear luôn")
                self.clear_old_html()
            else:
                self.d.xpath('//*[@resource-id="googleapp_facade_search_box"]/android.widget.TextView[1]').click()
                time.sleep(1)  
                self.d(resourceId="com.google.android.googlequicksearchbox:id/googleapp_search_box").click()
                time.sleep(1)
                self.d.send_keys("https://html.onlineviewer.net/", clear=True)
                time.sleep(1)
                self.d.press("enter") 
                time.sleep(3)
                self.clear_old_html()
        elif self.device_brand == "Samsung":
            # Thao tác tìm kiếm HTML Online Viewer trên Samsung
            # Ví dụ: self.d.open_url("https://html.onlineviewer.net/")
            print("Tìm kiếm HTML Online Viewer trên Samsung...")
            if self.d(resourceId="com.android.chrome:id/url_bar", textContains="html.onlineviewer.net").exists(timeout=2):
                print("Đúng trang HTML Online Viewer rồi → clear luôn")
                self.clear_old_html()
            else:
                # Điền các thao tác cụ thể cho Samsung tại đây
                self.d(resourceId="googleapp_facade_search_box").click()
                time.sleep(1)
                self.d.send_keys("https://html.onlineviewer.net/", clear=True)
                time.sleep(1)
                self.d.press("enter") 
                time.sleep(2)
                self.clear_old_html()
                
        else:
            raise ValueError(f"Thiết bị {self.device_brand} không được hỗ trợ.")
        
    def clear_old_html(self):
        if self.device_brand == "Redmi":
            # Thao tác xóa HTML cũ trên Redmi
            x = self.width * 0.476
            y = self.height * 0.210
            self.d.long_click(x, y, duration=1.0)
            time.sleep(1)
            
            if not self.d(resourceId="android:id/floating_toolbar_menu_item_text", text="Chọn tất cả").exists(timeout=2):
                if self.d(resourceId="android:id/overflow").exists(timeout=2):
                    self.d(resourceId="android:id/overflow").click()
                    time.sleep(1)
            if self.d(resourceId="android:id/floating_toolbar_menu_item_text", text="Chọn tất cả").exists(timeout=3):
                self.d(resourceId="android:id/floating_toolbar_menu_item_text", text="Chọn tất cả").click()
                time.sleep(1.5)
            else:
                print("Không tìm thấy tuỳ chọn Chọn tất cả")
            self.d.long_click(x, y, duration=1.0)
            time.sleep(1)
            
            if self.d(text="Cắt").exists(timeout=3):
                self.d(text="Cắt").click()
                time.sleep(1.5)
                print("Đã xoá cũ")
            else:
                print("Không tìm thấy tuỳ chọn Cắt")
                self.d.send_keys(self.BUSINESS_WRITEN_MAIL, clear=True)
            self.d.send_keys(self.BUSINESS_WRITEN_MAIL)
        elif self.device_brand == "Samsung":
            # Thao tác xóa HTML cũ trên Samsung
            x = self.width * 0.476
            y = self.height * 0.210
            self.d.long_click(x, y, duration=1.0)
            time.sleep(1)
            
            if not self.d(resourceId="android:id/floating_toolbar_menu_item_text", text="Chọn tất cả").exists(timeout=2):
                if self.d(resourceId="android:id/overflow").exists(timeout=2):
                    self.d(resourceId="android:id/overflow").click()
                    time.sleep(1)
            if self.d(resourceId="android:id/floating_toolbar_menu_item_text", text="Chọn tất cả").exists(timeout=3):
                self.d(resourceId="android:id/floating_toolbar_menu_item_text", text="Chọn tất cả").click()
                time.sleep(1.5)
            else:
                print("Không tìm thấy tuỳ chọn Chọn tất cả")
            self.d.long_click(x, y, duration=1.0)
            time.sleep(1)
            
            if self.d(text="Cắt").exists(timeout=3):
                self.d(text="Cắt").click()
                time.sleep(1.5)
                print("Đã xoá cũ")
            else:
                print("Không tìm thấy tuỳ chọn Cắt")
                self.d.send_keys(self.BUSINESS_WRITEN_MAIL, clear=True)
            self.d.send_keys(self.BUSINESS_WRITEN_MAIL)
            # Ví dụ: self.d(resourceId="com.android.chrome:id/url_bar").long_click()
            print("Xóa HTML cũ trên Samsung...")
        else:
            raise ValueError(f"Thiết bị {self.device_brand} không được hỗ trợ.")
        
    def compile_html(self):
        print("➡️ Bắt đầu compile_html (copy rendered text)...")
        if self.device_brand == "Redmi":
            # Thao tác copy rendered text trên Redmi
            x = self.width * 0.678
            y = self.height * 0.138
            self.d.long_click(x, y, duration=1.0)
            time.sleep(1)

            if not self.d(resourceId="android:id/floating_toolbar_menu_item_text", text="Chọn tất cả").exists(timeout=2):
                if self.d(resourceId="android:id/overflow").exists(timeout=2):
                    print("Long click thành công")
                    self.d(resourceId="android:id/overflow").click()
                    time.sleep(1)
                    
            elif self.d(resourceId="android:id/floating_toolbar_menu_item_text", text="Chọn tất cả").exists(timeout=3):
                self.d(resourceId="android:id/floating_toolbar_menu_item_text", text="Chọn tất cả").click()
                print("Long click được và chọn tất cả được")
                time.sleep(1.5)
            else:
                print("Không nhấn được longclick???? Rõ code y hệt hàm clear mà")
            
            self.d.long_click(x, y, duration=1.0)
            time.sleep(1)
            
            if self.d(text="Chọn tất cả").exists(timeout=3):
                self.d(text="Chọn tất cả").click()
                time.sleep(1.5)
                if self.d(text="Sao chép").exists(timeout=3):
                    self.d(text="Sao chép").click()
                    time.sleep(1.5)
                    print("✅ Đã sao chép rendered text")
            else:
                print("❌ Không tìm thấy tuỳ chọn 'sao chép' trong compile_html")
        elif self.device_brand == "Samsung":
            # Thao tác copy rendered text trên Samsung
            x = self.width * 0.678
            y = self.height * 0.138
            self.d.long_click(x, y, duration=1.0)
            time.sleep(1)

            if not self.d(resourceId="android:id/floating_toolbar_menu_item_text", text="Chọn tất cả").exists(timeout=2):
                if self.d(resourceId="android:id/overflow").exists(timeout=2):
                    print("Long click thành công")
                    self.d(resourceId="android:id/overflow").click()
                    time.sleep(1)
                    
            elif self.d(resourceId="android:id/floating_toolbar_menu_item_text", text="Chọn tất cả").exists(timeout=3):
                self.d(resourceId="android:id/floating_toolbar_menu_item_text", text="Chọn tất cả").click()
                print("Long click được và chọn tất cả được")
                time.sleep(1.5)
            else:
                print("Không nhấn được longclick???? Rõ code y hệt hàm clear mà")
            
            self.d.long_click(x, y, duration=1.0)
            time.sleep(1)
            
            if self.d(text="Chọn tất cả").exists(timeout=3):
                self.d(text="Chọn tất cả").click()
                time.sleep(1.5)
                if self.d(text="Chép").exists(timeout=3):
                    self.d(text="Chép").click()
                    time.sleep(1.5)
                    print("✅ Đã sao chép rendered text")
            else:
                print("❌ Không tìm thấy tuỳ chọn 'sao chép' trong compile_html")
            print("Copy rendered text trên Samsung...")
        else:
            raise ValueError(f"Thiết bị {self.device_brand} không được hỗ trợ.")
        
    def delete_recent_tab(self):
        if self.device_brand == "Redmi":
            # Thao tác xóa tab gần đây trên Redmi
            self.d(resourceId="com.android.systemui:id/recent_apps").click()
            time.sleep(1)
            self.d.swipe(0.476, 0.74, 0.476, 0.0)
            time.sleep(1)
            self.d(resourceId="com.android.systemui:id/center_group").click()
            time.sleep(1)
        elif self.device_brand == "Samsung":
            ### Chú thích điền gì ###
            # Thao tác xóa tab gần đây trên Samsung
            print("Xóa tab gần đây trên Samsung...")
        else:
            raise ValueError(f"Thiết bị {self.device_brand} không được hỗ trợ.")
        
# The run_simulator wrapper function is likely not used by watch_email.py directly,
# but if it is, it will need to be updated or removed.
# For now, I will comment it out as it will likely break.
# def run_simulator(EMP_ID, BUSINESS_SUBJECT_PATH, BUSINESS_WRITEN_MAIL_PATH, MODE):
#     simulator = HtmlRenderSimulator(EMP_ID=EMP_ID, BUSINESS_SUBJECT_PATH=BUSINESS_SUBJECT_PATH, BUSINESS_WRITEN_MAIL_PATH=BUSINESS_WRITEN_MAIL_PATH, MODE=MODE)
#     return simulator
