from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from app.routes import router as auth_router


def create_app() -> FastAPI:
    app = FastAPI(title="Center Hub Portal", version="1.0.0")

    # Mount static files
    app.mount("/static", StaticFiles(directory="static"), name="static")

    # Đăng ký các Router
    app.include_router(
        auth_router, prefix="/auth", tags=["Auth"]
    )  # Đường dẫn sẽ bắt đầu bằng /auth

    return app