import os
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv
from supabase import create_client, Client, ClientOptions  # Fix 1: Import trực tiếp từ root

# 1. Tìm đường dẫn tuyệt đối đến file .env ở thư mục gốc dự án
BASE_DIR = Path(__file__).resolve().parent.parent  
ENV_PATH = BASE_DIR / ".env"

if ENV_PATH.exists():
    load_dotenv(dotenv_path=ENV_PATH)
else:
    load_dotenv()


class Settings:
    SUPABASE_URL: str = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    SUPABASE_KEY: str = os.getenv("SUPABASE_KEY", "").strip() # Anon Key
    SUPABASE_SERVICE_ROLE_KEY: str = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip() # Service Role Key
    SECRET_KEY: str = os.getenv(
        "SECRET_KEY", "default-secret-key-change-it-in-production"
    )


settings = Settings()

# Cấu hình ClientOptions chuẩn từ gốc package
CUSTOM_OPTIONS = ClientOptions(
    postgrest_client_timeout=15,  # Giới hạn 15s cho mỗi truy vấn
    auto_refresh_token=True,      # Tự động làm mới token xác thực
)


def get_supabase_client() -> Optional[Client]:
    if not settings.SUPABASE_URL or not settings.SUPABASE_KEY:
        print("⚠️ Cảnh báo: Thiếu SUPABASE_URL hoặc SUPABASE_KEY!")
        return None  # Fix 2: Tránh gọi create_client với chuỗi rỗng gây crash app
    return create_client(
        settings.SUPABASE_URL, 
        settings.SUPABASE_KEY, 
        options=CUSTOM_OPTIONS
    )


def get_supabase_admin_client() -> Optional[Client]:
    """Client đặc quyền cao nhất (Service Role) dùng để tạo/xóa user bên Auth."""
    if not settings.SUPABASE_URL or not settings.SUPABASE_SERVICE_ROLE_KEY:
        print("⚠️ Cảnh báo: Thiếu SUPABASE_SERVICE_ROLE_KEY trong .env!")
        return None  # Fix 2: Tránh gọi create_client với chuỗi rỗng gây crash app
    return create_client(
        settings.SUPABASE_URL, 
        settings.SUPABASE_SERVICE_ROLE_KEY, 
        options=CUSTOM_OPTIONS
    )


# Khởi tạo sẵn các instance để import sử dụng
supabase: Optional[Client] = get_supabase_client()
supabase_admin: Optional[Client] = get_supabase_admin_client()