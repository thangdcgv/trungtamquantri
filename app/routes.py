# Sửa lại file router.py
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional

from fastapi import APIRouter, Request, Depends
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from config import supabase
from app.auth import get_current_user_or_redirect  # ✅ DÙNG CHUNG HÀM TỪ AUTH.PY

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

VIETNAM_TZ = timezone(timedelta(hours=7))

def format_vn_time(value: Optional[str]) -> str:
    if not value:
        return ""
    try:
        clean_value = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean_value)
        dt_vn = dt.astimezone(VIETNAM_TZ)
        return dt_vn.strftime("%d/%m/%Y %H:%M")
    except Exception:
        return str(value)

templates.env.filters["vn_time"] = format_vn_time

@router.get("/")
async def index(
    request: Request,
    current_user: Optional[Dict[str, Any]] = Depends(get_current_user_or_redirect) # ✅ DÙNG DEPENDENCY CHUẨN
):
    if not current_user:
        return RedirectResponse(url="/auth/login", status_code=303)

    warranties = []
    installations = []

    try:
        if supabase:
            res_warranty = (
                supabase.table('warranty_records')
                .select('id, serial_number, customer_name, model_name, created_at, category')
                .order('created_at', desc=True)
                .limit(5)
                .execute()
            )
            warranties = res_warranty.data or []

            res_cham_cong = (
                supabase.table('cham_cong')
                .select('id, ten, thoi_gian, so_hoa_don, thanh_tien, trang_thai')
                .order('thoi_gian', desc=True)
                .limit(5)
                .execute()
            )
            installations = res_cham_cong.data or []

    except Exception as e:
        logger.error(f"❌ LỖI TRUY VẤN TRANG CHỦ DASHBOARD: {e}", exc_info=True)

    return templates.TemplateResponse(
        request=request, 
        name="index.html", 
        context={
            "current_user": current_user,  # Giờ đây đã có đầy đủ department!
            "recent_warranties": warranties,
            "recent_installations": installations
        }
    )