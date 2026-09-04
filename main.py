import os
import traceback
import uvicorn
from fastapi import FastAPI, Request, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from fastapi.exceptions import RequestValidationError
from starlette.concurrency import run_in_threadpool

from config import supabase

# Import các routers từ thư mục app
from app.routes import router as main_router
from app.auth import router as auth_router
from app.admin_routes import router as admin_router  # Nếu audit nằm trong admin_routes thì admin_router đã bao gồm nó
from app.warranty import router as warranty_router
from app.cham_cong import router as cham_cong_router
from app.admin_key import router as kho_key_router, api_router as kho_key_api_router
from app.admin_quan_ly_key import router as quan_ly_key_router, api_router as quan_ly_key_api_router
from app.report import router as report_router
from app.warranty_report import router as warranty_report_router
from app.inventory import router as inventory_router, api_router as inventory_api_router

# Nếu bạn tách audit thành file router riêng (app/audit.py), hãy bỏ comment dòng dưới:
# from app.audit import router as audit_router 


app = FastAPI(
    title="Máy In Đại Thành Center Hub",
    description="Hệ thống quản lý chấm công, bảo hành và quản trị nội bộ",
    version="1.0.0"
)

# 2. CHUYỂN SessionMiddleware LÊN TRÊN CÙNG (Ngay sau khi khởi tạo app)
SECRET_KEY = os.getenv("SECRET_KEY", "mayindaithanh-centerhub-secret-key-2026")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)

# Đảm bảo thư mục static tồn tại trước khi mount
os.makedirs("app/static", exist_ok=True)
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get('/favicon.png', include_in_schema=False)
@app.get('/favicon.ico', include_in_schema=False)
async def favicon():
    return FileResponse('app/static/favicon.png')


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
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


async def log_error_to_db(request: Request, exc: Exception, module: str = "System"):
    """Hàm phụ trợ ghi log vào Supabase bất đồng bộ."""
    if not supabase:
        return
    
    try:
        # Lấy session an toàn sau khi Middleware đã được đăng ký ở trên
        user_id = request.session.get('user_id') if "session" in request.scope else None
        log_payload = {
            "level": "CRITICAL" if isinstance(exc, SystemError) else "ERROR",
            "module": module,
            "path": str(request.url.path),
            "message": str(exc),
            "stack_trace": traceback.format_exc(),
            "user_id": str(user_id) if user_id else "Anonymous",
            "status": "OPEN"
        }
        await run_in_threadpool(
            lambda: supabase.table('system_logs').insert(log_payload).execute()
        )
    except Exception as db_err:
        print(f"❌ Lỗi ghi system_logs: {db_err}")


# Global Exception Handler cho FastAPI
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    await log_error_to_db(request, exc)
    
    if request.url.path.startswith("/api/"):
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Đã xảy ra lỗi hệ thống. Ban quản trị đã ghi nhận sự cố."}
        )
    return HTMLResponse(
        content=f"<h2>⚠️ Sự cố hệ thống (500)</h2><p>Đã xảy ra lỗi: {str(exc)}</p><a href='/admin'>Quay lại Admin</a>",
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
    )


# Đăng ký các Router vào hệ thống
app.include_router(main_router)       
app.include_router(auth_router)       
app.include_router(admin_router)      
app.include_router(warranty_router)   
app.include_router(cham_cong_router)  
app.include_router(kho_key_router)
app.include_router(kho_key_api_router)
app.include_router(quan_ly_key_router)
app.include_router(quan_ly_key_api_router, prefix="/admin")
app.include_router(report_router)  
app.include_router(warranty_report_router)
app.include_router(inventory_router)      
app.include_router(inventory_api_router)

# Nếu dùng file audit.py riêng thì kích hoạt dòng dưới:
# app.include_router(audit_router)


if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)