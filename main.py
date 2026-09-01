import os
import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

# Import các routers từ thư mục app
from app.routes import router as main_router
from app.auth import router as auth_router
from app.admin_routes import router as admin_router
from app.warranty import router as warranty_router
from app.cham_cong import router as cham_cong_router
from app.admin_key import router as kho_key_router, api_router as kho_key_api_router
from app.admin_quan_ly_key import router as quan_ly_key_router, api_router as quan_ly_key_api_router
from app.report import router as report_router
from app.warranty_report import router as warranty_report_router

# 👈 IMPORT MODULE QUẢN LÝ KHO & SERI MÁY IN MỚI
from app.inventory import router as inventory_router, api_router as inventory_api_router


app = FastAPI(
    title="Máy In Đại Thành Center Hub",
    description="Hệ thống quản lý chấm công, bảo hành và quản trị nội bộ",
    version="1.0.0"
)

# Đảm bảo thư mục static tồn tại trước khi mount
os.makedirs("app/static", exist_ok=True)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

@app.get('/favicon.png', include_in_schema=False)
@app.get('/favicon.ico', include_in_schema=False)
async def favicon():
    return FileResponse('app/static/favicon.png')


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    # Lấy chi tiết lỗi từ Pydantic
    errors = exc.errors()
    error_messages = []
    for error in errors:
        field = " -> ".join(str(loc) for loc in error.get("loc", []))
        msg = error.get("msg", "")
        error_messages.append(f"Trường [{field}]: {msg}")
    
    return JSONResponse(
        status_code=422,
        content={
            "success": False,
            "message": "Lỗi định dạng dữ liệu (422)",
            "details": error_messages
        }
    )

# Khai báo Secret Key (Ưu tiên lấy từ biến môi trường .env)
SECRET_KEY = os.getenv("SECRET_KEY", "mayindaithanh-centerhub-secret-key-2026")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)

# Đăng ký các Router vào hệ thống
app.include_router(main_router)       # Trang chủ & điều hướng chung
app.include_router(auth_router)       # Đăng nhập, đăng xuất, đổi mật khẩu
app.include_router(admin_router)      # Trang Quản trị (/admin, /admin/users, /admin/config-cham-cong)
app.include_router(warranty_router)   # Phiếu bảo hành (/warranty/create, detail, ...)
app.include_router(cham_cong_router)  # Chấm công lắp đặt (/cham-cong/form, api, ...)
app.include_router(kho_key_router)
app.include_router(kho_key_api_router)
app.include_router(quan_ly_key_router)
app.include_router(quan_ly_key_api_router, prefix="/admin")
app.include_router(report_router)  
app.include_router(warranty_report_router)

# 👈 ĐĂNG KÝ ROUTER QUẢN LÝ KHO & SERI MÁY IN
app.include_router(inventory_router)      # Giao diện quét mã: /inventory/scan
app.include_router(inventory_api_router)  # API lưu Seri: /api/inventory/scan-serial


if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)