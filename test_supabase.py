import os
from dotenv import load_dotenv
from supabase import create_client

# 1. Kiểm tra việc đọc file .env
load_override = load_dotenv()
print(f"👉 Trạng thái load_dotenv(): {load_override}")

url = os.getenv("SUPABASE_URL")
key = os.getenv("SUPABASE_KEY")

print(f"👉 SUPABASE_URL: {url}")
print(f"👉 SUPABASE_KEY: {key[:10] if key else 'KHÔNG TÌM THẤY KEY'}...")

# 2. Thử kết nối trực tiếp Supabase
if url and key:
    try:
        client = create_client(url, key)
        # Thử truy vấn bảng cham_cong
        response = client.table('cham_cong').select('*').limit(1).execute()
        print("✅ KẾT NỐI THÀNH CÔNG! Dữ liệu trả về:")
        print(response.data)
    except Exception as e:
        print(f"❌ KẾT NỐI THẤT BẠI DO LỖI: {e}")
else:
    print("❌ LỖI: Không lấy được URL hoặc KEY từ file .env!")