@echo off
REM Chạy Email/src_api/watch_email.py và Email/src_api/device_monitor.py cùng lúc

start cmd /k "C:\Users\Admin\AppData\Local\Programs\Python\Python39\python.exe" "D:\Ttap\tool-fb-mobile\Email\src_api\watch_email.py"
start cmd /k "C:\Users\Admin\AppData\Local\Programs\Python\Python39\python.exe" "D:\Ttap\tool-fb-mobile\Email\src_api\device_monitor.py"

echo Các tiến trình đã được khởi động. Đóng cửa sổ này để dừng tất cả.
pause
