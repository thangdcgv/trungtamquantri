import os
import sys
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv
from supabase import create_client, Client, ClientOptions

# 1. Tự động tìm file .env
BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
PARENT_ENV_PATH = BASE_DIR.parent / ".env"

if ENV_PATH.exists():
    load_dotenv(dotenv_path=ENV_PATH)
elif PARENT_ENV_PATH.exists():
    load_dotenv(dotenv_path=PARENT_ENV_PATH)
else:
    load_dotenv()

class Settings:
    # === SUPABASE ===
    SUPABASE_URL: str = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    SUPABASE_KEY: str = os.getenv("SUPABASE_KEY", "").strip()
    SUPABASE_SERVICE_ROLE_KEY: str = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()

    # === BẢO MẬT ===
    SECRET_KEY: str = os.getenv(
        "SECRET_KEY", "default-secret-key-change-it-in-production"
    ).strip()

settings = Settings()

# === SUPABASE OPTIONS (Tối ưu cho Backend) ===
CUSTOM_OPTIONS = ClientOptions(
    postgrest_client_timeout=10,  # Giảm timeout xuống 10s để hủy sớm các request bị treo
    auto_refresh_token=False,     # Tắt auto refresh token để nhẹ Backend
)

def get_supabase_client() -> Client:
    if not settings.SUPABASE_URL or not settings.SUPABASE_KEY:
        print("❌ LỖI NGHIÊM TRỌNG: Thiếu SUPABASE_URL hoặc SUPABASE_KEY trong file .env!")
        sys.exit(1) # Dừng app ngay lập tức để dễ phát hiện lỗi cấu hình
    return create_client(
        settings.SUPABASE_URL,
        settings.SUPABASE_KEY,
        options=CUSTOM_OPTIONS
    )

def get_supabase_admin_client() -> Client:
    if not settings.SUPABASE_URL or not settings.SUPABASE_SERVICE_ROLE_KEY:
        print("⚠️ Cảnh báo: Thiếu SUPABASE_SERVICE_ROLE_KEY, dùng tạm anon key!")
        return get_supabase_client()
    return create_client(
        settings.SUPABASE_URL,
        settings.SUPABASE_SERVICE_ROLE_KEY,
        options=CUSTOM_OPTIONS
    )

# === KHỞI TẠO GLOBAL INSTANCES ===
supabase: Client = get_supabase_client()
supabase_admin: Client = get_supabase_admin_client()

# === XUẤT BIẾN DÙNG CHUNG ===
SUPABASE_URL = settings.SUPABASE_URL
SUPABASE_KEY = settings.SUPABASE_KEY 
SECRET_KEY = settings.SECRET_KEY