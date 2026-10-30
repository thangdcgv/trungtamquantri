# app/templates_config.py
from fastapi.templating import Jinja2Templates
from config import SUPABASE_URL, SUPABASE_KEY
from app.permissions import can_access

# Khởi tạo Jinja2Templates duy nhất cho toàn ứng dụng
templates = Jinja2Templates(directory="app/templates")

# Đăng ký các biến & hàm global dùng chung
templates.env.globals["SUPABASE_URL"] = SUPABASE_URL
templates.env.globals["SUPABASE_KEY"] = SUPABASE_KEY
templates.env.globals["can_access"] = can_access