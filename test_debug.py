# test_debug.py
import asyncio
# Import các hàm từ file thư viện của bạn
from pymongo_management import get_comment, check_kpi, get_account

async def main():
    user_id_test = "22789191" # Điền ID facebook cần test vào đây

    print(f"--- Đang test cho User: {user_id_test} ---")

    # 1. Test xem lấy được comment không
    try:
        result = await get_comment(user_id_test)
        print("KẾT QUẢ GET_COMMENT:", result)
    except Exception as e:
        print("Lỗi get_comment:", e)

    # 2. Test xem KPI thế nào
    try:
        kpi_status = await check_kpi(user_id_test, "Bình luận")
        print("CÒN KPI KHÔNG (True=Còn, False=Hết):", kpi_status)
    except Exception as e:
        print("Lỗi check_kpi:", e)

# Chạy hàm async
if __name__ == "__main__":
    asyncio.run(main())