from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from app.routes import router as auth_router

# Lấy đường dẫn thư mục 'app'
BASE_DIR = Path(__file__).resolve().parent

def create_app() -> FastAPI:
    app = FastAPI(title="Center Hub Portal", version="1.0.0")

    # Chỉ định đúng thư mục app/static
    app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

    app.include_router(auth_router, prefix="/auth", tags=["Auth"])

    return app