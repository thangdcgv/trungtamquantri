import os
from pathlib import Path
from dotenv import load_dotenv
from supabase import create_client, Client

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
    SUPABASE_SERVICE_ROLE_KEY: str = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip() # Service Role Key mới
    SECRET_KEY: str = os.getenv(
        "SECRET_KEY", "default-secret-key-change-it-in-production"
    )


settings = Settings()


def get_supabase_client() -> Client:
    if not settings.SUPABASE_URL or not settings.SUPABASE_KEY:
        print("⚠️ Cảnh báo: Thiếu SUPABASE_URL hoặc SUPABASE_KEY!")
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)


def get_supabase_admin_client() -> Client:
    """Client đặc quyền cao nhất (Service Role) dùng để tạo/xóa user bên Auth."""
    if not settings.SUPABASE_URL or not settings.SUPABASE_SERVICE_ROLE_KEY:
        print("⚠️ Cảnh báo: Thiếu SUPABASE_SERVICE_ROLE_KEY trong .env!")
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_ROLE_KEY)


# Khởi tạo sẵn các instance để import sử dụng
supabase: Client = get_supabase_client()
supabase_admin: Client = get_supabase_admin_client() # <--- Thêm dòng này