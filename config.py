import os
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv
from supabase import create_client, Client, ClientOptions

# 1. Tự động tìm file .env ở thư mục hiện tại hoặc thư mục gốc dự án
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
    SUPABASE_URL: str = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    SUPABASE_KEY: str = os.getenv("SUPABASE_KEY", "").strip()  # Anon Key
    SUPABASE_SERVICE_ROLE_KEY: str = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()  # Service Role Key
    SECRET_KEY: str = os.getenv(
        "SECRET_KEY", "default-secret-key-change-it-in-production"
    ).strip()
    
    # Config Zalo OA & ZNS (Đã thêm .strip() loại bỏ khoảng trắng)
    ZALO_OA_ACCESS_TOKEN: str = os.getenv("ZALO_OA_ACCESS_TOKEN", "").strip()
    ZNS_TEMPLATE_ID: str = os.getenv("ZNS_TEMPLATE_ID", "").strip()


settings = Settings()

# Cấu hình ClientOptions chuẩn từ gốc package
CUSTOM_OPTIONS = ClientOptions(
    postgrest_client_timeout=15,  # Giới hạn 15s cho mỗi truy vấn
    auto_refresh_token=True,      # Tự động làm mới token xác thực
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
    """Client đặc quyền cao nhất (Service Role) dùng để tạo/xóa user bên Auth."""
    if not settings.SUPABASE_URL or not settings.SUPABASE_SERVICE_ROLE_KEY:
        print("⚠️ Cảnh báo: Thiếu SUPABASE_SERVICE_ROLE_KEY trong file .env!")
        return None
    return create_client(
        settings.SUPABASE_URL, 
        settings.SUPABASE_SERVICE_ROLE_KEY, 
        options=CUSTOM_OPTIONS
    )


# Khởi tạo sẵn các instance & xuất biến ra ngoài để import sử dụng tiện lợi
supabase: Optional[Client] = get_supabase_client()
supabase_admin: Optional[Client] = get_supabase_admin_client()

# 🔥 BỔ SUNG: Xuất biến Zalo cấu hình để tickets.py có thể import trực tiếp
ZALO_OA_ACCESS_TOKEN = settings.ZALO_OA_ACCESS_TOKEN
ZNS_TEMPLATE_ID = settings.ZNS_TEMPLATE_ID