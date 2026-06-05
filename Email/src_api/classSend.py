import uiautomator2 as u2
import time
import os
import sqlite3
from email_manager import EmailManager
from filelock import FileLock

# --- Config ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "..", "business", "businesses.db")

class EmailSender:
    def __init__(self, device_id: str, customer_id: int):
        self.device_id = device_id
        self.customer_id = customer_id
        
        self.emp_id = self._get_emp_id_from_customer(customer_id)
        if not self.emp_id:
            raise ValueError(f"Không tìm thấy emp_id cho Customer ID: {self.customer_id}")

        self.device_brand = self._get_device_brand(self.device_id)
        if not self.device_brand:
            raise ValueError(f"Không tìm thấy device_brand cho Device ID: {self.device_id}")
        
        # Khởi tạo EmailManager để quản lý quota
        self.email_manager = EmailManager(self.device_id)
        
        # Kết nối device
        self.d = u2.connect(self.device_id)
        self.width, self.height = self.d.window_size()
        
    def _get_db_connection(self):
        conn = sqlite3.connect(DB_PATH)
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
    
    def _get_customer_data(self, customer_id):
        """Lấy thông tin customer từ database"""
        conn = self._get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT customer_email, subject, content FROM customers WHERE customer_id = ? AND emp_id = ?",
            (customer_id, self.emp_id)
        )
        result = cursor.fetchone()
        conn.close()
        
        if result:
            return {
                'email': result['customer_email'],
                'subject': result['subject'] if result['subject'] else "",
                'content': result['content'] if result['content'] else ""
            }
        return None    

    def _mark_customer_as_sent(self, customer_id):
        """Đánh dấu customer đã được gửi email"""
        lock_file = DB_PATH + ".lock"
        with FileLock(lock_file, timeout=10):
            conn = self._get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE customers SET sent = 1 WHERE customer_id = ? AND emp_id = ?",
                (customer_id, self.emp_id)
            )
            conn.commit()
            conn.close()
            print(f"✅ Đã đánh dấu customer_id {customer_id} là đã gửi")



    def open_gmail(self):
        """Mở Gmail trên thiết bị"""
        current_app = self.d.app_current()
        if current_app["package"] == "com.google.android.gm":
            print("✅ Đã ở trong Gmail")
            return

        if self.device_brand == "Redmi":
            # Thao tác mở Gmail trên Redmi
            self.d(resourceId="com.android.systemui:id/center_group").click()
            time.sleep(1)
            self.d.swipe_ext("up", scale=0.8)
            time.sleep(1)

            self.d(resourceId="com.gogo.launcher:id/search_container_all_apps").click()
            time.sleep(1)
            self.d.send_keys("Gmail", clear=True)
            time.sleep(1)
            self.d(resourceId="com.gogo.launcher:id/icon").click()
            time.sleep(2)
            print("📩 Đang mở Gmail trên Redmi...")
        elif self.device_brand == "Samsung":
            # Thao tác mở Gmail trên Samsung
            self.d(resourceId="com.android.systemui:id/center_group").click()
            time.sleep(1)
            self.d.swipe_ext("up", scale=0.8)
            time.sleep(1)
            self.d(resourceId="com.sec.android.app.launcher:id/app_search_edit_text_wrapper").click()
            time.sleep(1)
            self.d.send_keys("Gmail", clear=True)
            time.sleep(1)
            self.d(resourceId="com.sec.android.app.launcher:id/label", text="Gmail").click()
            time.sleep(2)
            # Ví dụ: self.d.app_start("com.google.android.gm")
            print("📩 Đang mở Gmail trên Samsung...")
        else:
            raise ValueError(f"Thiết bị {self.device_brand} không được hỗ trợ.")
        
    def choose_account(self, name_acc):
        """Chọn tài khoản Gmail"""
        if not name_acc:
            print("⚠️ Không có tên account để chọn")
            return

        try:
            if self.device_brand == "Redmi" or self.device_brand == "Samsung":
                # Thao tác chọn tài khoản Gmail trên Redmi
                self.d(resourceId="com.google.android.gm:id/og_apd_internal_image_view").click()
                time.sleep(1.5)

                try:
                    current_acc = self.d(resourceId="com.google.android.gm:id/og_bento_single_pane_account_menu_title_container").get_text()
                    if current_acc == name_acc:
                        print(f"✅ Đã đang sử dụng tài khoản {name_acc}")
                        self.d(resourceId="com.google.android.gm:id/og_bento_toolbar_close_button").click()
                        return
                except Exception:
                    pass

                try:
                    self.d(resourceId="com.google.android.gm:id/og_secondary_account_information", text=name_acc).click()
                    print(f"📌 Chuyển sang tài khoản {name_acc}")
                    time.sleep(2)
                    return
                except Exception:
                    print(f"⚠️ Không tìm thấy tài khoản {name_acc}, giữ nguyên tài khoản hiện tại")

                try:
                    self.d.press("back")
                except:
                    pass
            else:
                raise ValueError(f"Thiết bị {self.device_brand} không được hỗ trợ.")

        except Exception as e:
            print(f"⚠️ Lỗi khi chọn tài khoản: {e}")
        
    def send_email(self, to_email: str, subject: str, content: str, sender_email: str):
        """Soạn & gửi email"""
        try:
            self.choose_account(name_acc=sender_email)
            time.sleep(1)

            if self.device_brand == "Redmi" or self.device_brand == "Samsung":
                # Thao tác soạn và gửi email trên Redmi
                self.d(resourceId="com.google.android.gm:id/compose_button").click()
                time.sleep(2)

                receiver = self.d.xpath(
                    '//*[@resource-id="com.google.android.gm:id/peoplekit_autocomplete_chip_group"]/android.widget.EditText[1]'
                )
                receiver.set_text(to_email)
                time.sleep(1)

                try:
                    self.d.xpath(
                        '//*[@resource-id="com.google.android.gm:id/peoplekit_listview_flattened_row"]/android.widget.RelativeLayout[2]'
                    ).click()
                    time.sleep(1)
                except:
                    self.d.press("tab")
                    time.sleep(1)

                self.d(resourceId="com.google.android.gm:id/subject").set_text(subject)
                time.sleep(1)

                x = self.width * 0.492
                y = self.height * 0.372
                self.d.long_click(x, y, duration=1.0)
                time.sleep(1)
                
                if self.d(text="Dán").exists(timeout=3):
                    self.d(text="Dán").click()
                    print("✅ Đã dán nội dung email")
                else:
                    print("⚠️ Không tìm thấy tùy chọn Dán, nhập thủ công")
                    body_field = self.d(resourceId="com.google.android.gm:id/composearea_tap_trap_bottom")
                    body_field.click()
                    time.sleep(0.5)
                    self.d.send_keys(content)

                time.sleep(2)
                
                self.d(resourceId="com.google.android.gm:id/send").click()
                time.sleep(3)
                print(f"✅ Đã gửi email tới {to_email}")
                return True
            else:
                raise ValueError(f"Thiết bị {self.device_brand} không được hỗ trợ.")
            
        except Exception as e:
            print(f"❌ Lỗi khi gửi email: {e}")
            return False
        

        
    def send_to_customer(self, customer_id: int):
        """Gửi email cho một customer cụ thể"""
        # Lấy thông tin customer
        customer_data = self._get_customer_data(customer_id)
        if not customer_data:
            print(f"❌ Không tìm thấy customer_id {customer_id}")
            return False

        # Lấy email account khả dụng
        sender_email = self.email_manager.get_available_account()
        if not sender_email:
            print(f"❌ Không còn email account khả dụng cho EMP_ID {self.emp_id}")
            return False

        # Gửi email
        success = self.send_email(
            to_email=customer_data['email'],
            subject=customer_data['subject'],
            content=customer_data['content'],
            sender_email=sender_email
        )

        if success:
            # Tăng counter cho email account
            self.email_manager.increase_counter(sender_email)
            # Đánh dấu customer đã gửi
            self._mark_customer_as_sent(customer_id)
            
            return True
        
        return False

    def send_all_pending(self):
        """Gửi email cho tất cả customers chưa gửi"""
        conn = self._get_db_connection()
        cursor = conn.cursor()
        
        # Lấy danh sách customers chưa gửi
        cursor.execute(
            "SELECT customer_id FROM customers WHERE emp_id = ? AND sent = 0 ORDER BY customer_id",
            (self.emp_id,)
        )
        pending_customers = cursor.fetchall()
        conn.close()

        if not pending_customers:
            print(f"✅ Không có customer nào chưa gửi cho EMP_ID {self.emp_id}")
            return 0

        sent_count = 0
        print(f"📧 Bắt đầu gửi cho {len(pending_customers)} customers...")

        for customer in pending_customers:
            customer_id = customer['customer_id']
            
            # Kiểm tra còn email accounts khả dụng không
            if not self.email_manager.has_available_accounts():
                print(f"❌ Hết email accounts khả dụng. Đã gửi được {sent_count}/{len(pending_customers)} emails")
                break

            print(f"\n📤 Đang gửi cho customer_id {customer_id}...")
            success = self.send_to_customer(customer_id)
            
            if success:
                sent_count += 1
                print(f"✅ Đã gửi thành công ({sent_count}/{len(pending_customers)})")
            else:
                print(f"❌ Gửi thất bại cho customer_id {customer_id}")
            
            # Nghỉ một chút giữa các email
            time.sleep(2)

        print(f"\n🎉 Hoàn thành! Đã gửi {sent_count}/{len(pending_customers)} emails")
        return sent_count


# -------- Wrapper functions cho backward compatibility ----------
# -------- Wrapper functions cho backward compatibility ----------
# These functions are likely not used by watch_email.py directly,
# but if they are, they will need to be updated or removed.
# For now, I will leave them as is, but they will likely break.
# The primary focus is on the flow from watch_email.py -> EmailSender.

# def run_sent(emp_id, subject, content, to_email, sender_email):
#     """Gửi email đơn lẻ (backward compatibility)"""
#     try:
#         sender = EmailSender(emp_id=emp_id) # This will break
#         sender.open_gmail()
#         success = sender.send_email(to_email, subject, content, sender_email)
#         return success
#     except Exception as e:
#         print(f"⚠️ Lỗi khi gửi email: {e}")
#         return False


# def run_sent_customer(emp_id, customer_id):
#     """Gửi email cho một customer từ database"""
#     try:
#         sender = EmailSender(emp_id=emp_id, customer_id=customer_id) # This will break
#         sender.open_gmail()
#         success = sender.send_to_customer(customer_id)
#         return success
#     except Exception as e:
#         print(f"⚠️ Lỗi khi gửi email cho customer: {e}")
#         return False


# def run_sent_all_pending(emp_id):
#     """Gửi email cho tất cả customers chưa gửi"""
#     try:
#         sender = EmailSender(emp_id=emp_id) # This will break
#         sender.open_gmail()
#         sent_count = sender.send_all_pending()
#         return sent_count
#     except Exception as e:
#         print(f"⚠️ Lỗi khi gửi email hàng loạt: {e}")
#         return 0
