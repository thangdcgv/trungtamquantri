import os
from dotenv import load_dotenv
from supabase import create_client, Client

# Tải các biến môi trường từ file .env
load_dotenv()


class Settings:
    SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
    SUPABASE_KEY: str = os.getenv("SUPABASE_KEY", "")
    SECRET_KEY: str = os.getenv(
        "SECRET_KEY", "default-secret-key-change-it-in-production"
    )


settings = Settings()


def get_supabase_client() -> Client:
    """Khởi tạo và trả về Supabase Client dùng chung cho toàn hệ thống."""
    if (
        not settings.SUPABASE_URL
        or settings.SUPABASE_URL == "https://your-project-id.supabase.co"
    ):
        print(
            "⚠️  Cảnh báo: Bạn chưa cấu hình SUPABASE_URL thực tế trong file .env!"
        )

    return create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)


# Tạo sẵn instance client để các module khác dễ dàng import và sử dụng
supabase: Client = get_supabase_client()