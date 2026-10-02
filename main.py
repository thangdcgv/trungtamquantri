import os
import traceback
import uvicorn
from fastapi import FastAPI, Request, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from starlette.concurrency import run_in_threadpool

from config import supabase, SUPABASE_URL, SUPABASE_KEY

# Import các routers
from app.routes import router as main_router
from app.auth import router as auth_router
from app.admin_routes import router as admin_router
from app.warranty import router as warranty_router
from app.cham_cong import router as cham_cong_router
from app.admin_key import router as kho_key_router, api_router as kho_key_api_router
from app.admin_quan_ly_key import router as quan_ly_key_router, api_router as quan_ly_key_api_router
from app.report import router as report_router
from app.warranty_report import router as warranty_report_router
from app.warranty_policy_routes import router as warranty_policy_router
from app.inventory import router as inventory_router, api_router as inventory_api_router
from app.tickets import router as tickets_router
from app.reception import router as reception_router

app = FastAPI(
    title="Máy In Đại Thành Center Hub",
    description="Hệ thống quản trị nội bộ",
    version="1.0.0"
)

# === 1. Session Middleware (Thêm trước) ===
SECRET_KEY = os.getenv("SECRET_KEY", "mayindaithanh-centerhub-secret-key-2026")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)

# === 2. CORS Middleware (Thêm sau để bọc ngoài cùng - LIFO) ===
raw_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000,http://localhost:3000")
ALLOWED_ORIGINS = [origin.strip() for origin in raw_origins.split(",") if origin.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# === Templates & Static ===
templates = Jinja2Templates(directory="app/templates")
templates.env.globals["SUPABASE_URL"] = SUPABASE_URL
templates.env.globals["SUPABASE_KEY"] = SUPABASE_KEY

os.makedirs("app/static", exist_ok=True)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# === Favicon & Zalo Verify ===
@app.get('/favicon.png', include_in_schema=False)
@app.get('/favicon.ico', include_in_schema=False)
async def favicon():
    return FileResponse('app/static/favicon.png')

# === Validation Error Handler ===
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    error_messages = []
    for error in exc.errors():
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

# === Log lỗi CSDL ===
async def log_error_to_db(request: Request, exc: Exception, module: str = "System"):
    if not supabase:
        return
    try:
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
        await run_in_threadpool(lambda: supabase.table('system_logs').insert(log_payload).execute())
    except Exception as db_err:
        print(f"❌ Lỗi ghi system_logs: {db_err}")

# === Global Exception Handler ===
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    await log_error_to_db(request, exc)
    
    # Bỏ qua trả về HTTP Response nếu kết nối là WebSocket
    if request.scope.get("type") == "websocket":
        raise exc

    if request.url.path.startswith("/api/"):
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Đã xảy ra lỗi hệ thống. Ban quản trị đã ghi nhận sự cố."}
        )
    return HTMLResponse(
        content=f"<h2>⚠️ Sự cố hệ thống (500)</h2><p>Đã xảy ra lỗi: {str(exc)}</p><a href='/admin'>Quay lại Admin</a>",
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
    )

# === ĐĂNG KÝ ROUTERS ===
app.include_router(main_router)
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(warranty_router)
app.include_router(cham_cong_router)
app.include_router(kho_key_router)
app.include_router(quan_ly_key_router)
app.include_router(quan_ly_key_api_router, prefix="/admin")
app.include_router(report_router)
app.include_router(warranty_report_router)
app.include_router(warranty_policy_router)
app.include_router(inventory_router)
app.include_router(inventory_api_router)

# Lưu ý kiểm tra URL WebSocket trong tickets_router
app.include_router(tickets_router)
app.include_router(reception_router)

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)