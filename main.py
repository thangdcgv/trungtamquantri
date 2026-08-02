from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from app.routes import router as main_router
from app.warranty import router as warranty_router
from app.auth import router as auth_router
from app.admin_routes import router as admin_router
from app.cham_cong import router as cham_cong_router

app = FastAPI(title="Máy In Đại Thành Center Hub")

# Mount static files từ thư mục app/static
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Thêm middleware session
app.add_middleware(SessionMiddleware, secret_key="your-super-secret-key-change-it")

# Đăng ký các router
app.include_router(main_router)
app.include_router(auth_router)
app.include_router(admin_router)  
app.include_router(warranty_router)
app.include_router(cham_cong_router)

if __name__ == "__main__":
    import uvicorn
    # Sửa "main.py" thành "main:app" để reload chạy đúng
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)