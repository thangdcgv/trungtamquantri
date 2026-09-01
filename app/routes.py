import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional

from fastapi import APIRouter, Request, Depends
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from config import supabase

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

# Múi giờ Việt Nam (UTC+7)
VIETNAM_TZ = timezone(timedelta(hours=7))


def get_current_user_from_session(request: Request) -> Optional[Dict[str, Any]]:
    """Dependency kiểm tra session người dùng."""
    user_id = request.session.get('user_id')
    if not user_id:
        return None
    return {
        "is_authenticated": True,
        "id": user_id,
        "name": request.session.get('ho_ten', 'Quản trị viên'),
        "role": request.session.get('role', 'User')
    }


@router.get("/")
def index(
    request: Request,
    current_user: Optional[Dict[str, Any]] = Depends(get_current_user_from_session)
):
    """
    Trang chủ Dashboard: Hiển thị 5 phiếu bảo hành và 5 lượt chấm công mới nhất.
    """
    # 1. Điều hướng nếu chưa đăng nhập
    if not current_user:
        return RedirectResponse(url="/auth/login", status_code=303)

    warranties = []
    installations = []

    try:
        if supabase:
            # Lấy 5 phiếu bảo hành MỚI NHẤT (Bỏ lọc .gte để tránh bị rỗng khi đầu tháng chưa có dữ liệu)
            res_warranty = (
                supabase.table('warranty_records')
                .select('id, serial_number, customer_name, model_name, created_at, category')
                .order('created_at', desc=True)
                .limit(5)
                .execute()
            )
            warranties = res_warranty.data or []

            # Lấy 5 đơn chấm công MỚI NHẤT (Sắp xếp theo thoi_gian thay vì id)
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
            "current_user": current_user,
            "recent_warranties": warranties,
            "recent_installations": installations
        }
    )