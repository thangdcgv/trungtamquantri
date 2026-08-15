import os
from pathlib import Path
from dotenv import load_dotenv
from supabase import create_client, Client

# 1. Tìm đường dẫn tuyệt đối đến file .env ở thư mục gốc dự án
BASE_DIR = Path(__file__).resolve().parent.parent  # Điều chỉnh .parent tùy theo cấp thư mục
ENV_PATH = BASE_DIR / ".env"

# Load file .env theo đường dẫn tuyệt đối
if ENV_PATH.exists():
    load_dotenv(dotenv_path=ENV_PATH)
else:
    load_dotenv()  # Fallback load mặc định


class Settings:
    # Bỏ khoảng trắng thừa hoặc dấu / ở cuối URL nếu có
    SUPABASE_URL: str = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    SUPABASE_KEY: str = os.getenv("SUPABASE_KEY", "").strip()
    SECRET_KEY: str = os.getenv(
        "SECRET_KEY", "default-secret-key-change-it-in-production"
    )


settings = Settings()


def get_supabase_client() -> Client:
    """Khởi tạo và trả về Supabase Client dùng chung cho toàn hệ thống."""
    # 2. Chỉ cảnh báo nếu URL hoặc KEY bị rỗng
    if not settings.SUPABASE_URL or not settings.SUPABASE_KEY:
        print(
            "⚠️  Cảnh báo: Bạn chưa cấu hình SUPABASE_URL hoặc SUPABASE_KEY trong file .env!"
        )

    return create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)


# Tạo sẵn instance client để các module khác dễ dàng import và sử dụng
supabase: Client = get_supabase_client()