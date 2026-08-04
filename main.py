import os
import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

# Import các routers từ thư mục app
from app.routes import router as main_router
from app.auth import router as auth_router
from app.admin_routes import router as admin_router
from app.warranty import router as warranty_router
from app.cham_cong import router as cham_cong_router

app = FastAPI(
    title="Máy In Đại Thành Center Hub",
    description="Hệ thống quản lý chấm công, bảo hành và quản trị nội bộ",
    version="1.0.0"
)

# Đảm bảo thư mục static tồn tại trước khi mount
os.makedirs("app/static", exist_ok=True)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Khai báo Secret Key (Ưu tiên lấy từ biến môi trường .env)
SECRET_KEY = os.getenv("SECRET_KEY", "mayindaithanh-centerhub-secret-key-2026")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)

# Đăng ký các Router vào hệ thống
app.include_router(main_router)       # Trang chủ & điều hướng chung
app.include_router(auth_router)       # Đăng nhập, đăng xuất, đổi mật khẩu
app.include_router(admin_router)      # Trang Quản trị (/admin, /admin/users, /admin/config-cham-cong)
app.include_router(warranty_router)   # Phiếu bảo hành (/warranty/create, detail, ...)
app.include_router(cham_cong_router)  # Chấm công lắp đặt (/cham-cong/form, api, ...)

if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)