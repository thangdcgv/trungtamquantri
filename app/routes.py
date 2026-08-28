import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional

from fastapi import APIRouter, Request, Depends
from fastapi.responses import RedirectResponse, JSONResponse
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


def get_first_day_of_month_utc_iso() -> str:
    """Lấy ngày đầu tiên của tháng theo giờ VN nhưng đổi về định dạng UTC ISO chuẩn để query DB."""
    now_vn = datetime.now(VIETNAM_TZ)
    first_day_vn = datetime(now_vn.year, now_vn.month, 1, 0, 0, 0, tzinfo=VIETNAM_TZ)
    # Chuyển về UTC chuẩn
    first_day_utc = first_day_vn.astimezone(timezone.utc)
    return first_day_utc.strftime("%Y-%m-%dT%H:%M:%SZ")


@router.get("/")
def index(
    request: Request,
    current_user: Optional[Dict[str, Any]] = Depends(get_current_user_from_session)
):
    """
    Trang chủ Dashboard: Hiển thị 5 phiếu bảo hành, 5 lượt chấm công.
    Dùng 'def' để FastAPI tự đẩy I/O của Supabase sang ThreadPool.
    """
    # 1. Điều hướng nếu chưa đăng nhập
    if not current_user:
        return RedirectResponse(url="/auth/login", status_code=303)

    warranties = []
    installations = []

    # 2. Lấy mốc thời gian ISO chuẩn UTC
    first_day_iso = get_first_day_of_month_utc_iso()

    try:
        if supabase:
            # Lấy 5 phiếu bảo hành gần nhất trong tháng
            res_warranty = (
                supabase.table('warranty_records')
                .select('id, serial_number, customer_name, model_name, created_at, category')
                .gte('created_at', first_day_iso)
                .order('created_at', desc=True)
                .limit(5)
                .execute()
            )
            warranties = res_warranty.data or []

            # Lấy 5 đơn chấm công gần nhất trong tháng
            res_cham_cong = (
                supabase.table('cham_cong')
                .select('id, ten, thoi_gian, so_hoa_don, thanh_tien,trang_thai')
                .gte('thoi_gian', first_day_iso)
                .order('id', desc=True)
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


@router.get("/api/health-status")
def health_status():
    """API kiểm tra trạng thái hoạt động hệ thống."""
    db_connected = bool(supabase)
    return {
        "status": "success" if db_connected else "degraded",
        "database": "online" if db_connected else "offline",
        "timekeeping": "online",
        "warranty": "online",
        "library": "online"
    }