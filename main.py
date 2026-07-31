from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware
from app.routes import router as main_router
from app.auth import router as auth_router
from app.admin_routes import router as admin_router  # <--- Import router admin

app = FastAPI()

# Thêm middleware session
app.add_middleware(SessionMiddleware, secret_key="your-super-secret-key-change-it")

# Đăng ký các router
app.include_router(main_router)
app.include_router(auth_router)
app.include_router(admin_router)  # <--- Đăng ký router quản trị

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main.py", host="127.0.0.1", port=8000, reload=True)