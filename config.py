import os
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

# === SUPABASE OPTIONS ===
CUSTOM_OPTIONS = ClientOptions(
    postgrest_client_timeout=15,
    auto_refresh_token=True,
)

def get_supabase_client() -> Optional[Client]:
    if not settings.SUPABASE_URL or not settings.SUPABASE_KEY:
        print("⚠️ Cảnh báo: Thiếu SUPABASE_URL hoặc SUPABASE_KEY trong file .env!")
        return None
    return create_client(
        settings.SUPABASE_URL,
        settings.SUPABASE_KEY,
        options=CUSTOM_OPTIONS
    )

def get_supabase_admin_client() -> Optional[Client]:
    if not settings.SUPABASE_URL or not settings.SUPABASE_SERVICE_ROLE_KEY:
        print("⚠️ Cảnh báo: Thiếu SUPABASE_SERVICE_ROLE_KEY trong file .env!")
        return None
    return create_client(
        settings.SUPABASE_URL,
        settings.SUPABASE_SERVICE_ROLE_KEY,
        options=CUSTOM_OPTIONS
    )

# === KHỞI TẠO ===
supabase: Optional[Client] = get_supabase_client()
supabase_admin: Optional[Client] = get_supabase_admin_client()

# === XUẤT BIẾN ĐỒ DÙNG CHUNG ===
SUPABASE_URL = settings.SUPABASE_URL       # ✅ Thêm
SUPABASE_KEY = settings.SUPABASE_KEY 
SECRET_KEY = settings.SECRET_KEY 

