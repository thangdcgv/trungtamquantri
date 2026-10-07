import logging
import re
from pathlib import Path
from typing import Optional, Dict, Any

from fastapi import APIRouter, Request, Query, HTTPException, Depends, status, Path as FastAPIPath
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, HttpUrl, field_validator

# Imports đồng bộ với hệ thống hiện tại
from config import supabase
from app.auth import require_login, get_current_user_or_redirect

# ==========================================
# CẤU HÌNH LOGGING
# ==========================================
logger = logging.getLogger("uvicorn.error")

# ==========================================
# CẤU HÌNH TEMPLATES ĐỘNG LINH HOẠT
# ==========================================
BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = (
    BASE_DIR.parent / "templates"
    if (BASE_DIR.parent / "templates").exists()
    else BASE_DIR / "templates"
)
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# ==========================================
# HẰNG SỐ & THAM SỐ CẤU HÌNH
# ==========================================
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
MAX_SERIAL_LENGTH = 100
MIN_SERIAL_LENGTH = 1

# Cho phép chữ cái, số, gạch ngang, gạch dưới, dấu chấm, khoảng trắng, / + # :
SERIAL_ALLOWED_PATTERN = re.compile(r"^[A-Za-z0-9\-\_\.\/\+\#\:\s]+$")
# Các trạng thái hợp lệ của bản ghi kiểm kê
VALID_STATUSES = ("pending", "verified", "rejected", "completed")

# ==========================================
# ROUTER DEFINITIONS
# ==========================================
router = APIRouter(
    prefix="/inventory",
    tags=["Quản Lý Kho & Seri Máy In"]
)

api_router = APIRouter(
    prefix="/api/inventory",
    tags=["API Kiểm Kê Seri Máy In"]
)


# ==========================================
# PYDANTIC SCHEMAS
# ==========================================
class SerialScanRequest(BaseModel):
    serial_number: str = Field(
        ...,
        min_length=MIN_SERIAL_LENGTH,
        max_length=MAX_SERIAL_LENGTH,
        description="Số Seri của máy in"
    )
    printer_id: int = Field(
        ...,
        gt=0,
        description="ID khóa ngoại liên kết tới bảng list_printer"
    )
    image_url: Optional[HttpUrl] = Field(
        None,
        description="Link hình ảnh máy/tem mã (hợp lệ HTTP/HTTPS)"
    )

    @field_validator("serial_number")
    @classmethod
    def validate_and_normalize_serial(cls, v: str) -> str:
        """Chuẩn hóa seri: loại bỏ khoảng trắng thừa, viết hoa, kiểm tra ký tự hợp lệ."""
        normalized = " ".join(v.strip().split()).upper()
        if not normalized:
            raise ValueError("Số seri không được để trống sau khi chuẩn hóa")
        if not SERIAL_ALLOWED_PATTERN.match(normalized):
            raise ValueError(
                "Số seri chỉ được chứa chữ cái, số, khoảng trắng và các ký tự: - _ . / + # :"
            )
        return normalized


class StatusUpdateRequest(BaseModel):
    """Schema để cập nhật trạng thái bản ghi kiểm kê."""
    status: str = Field(..., description="Trạng thái mới: pending / verified / rejected / completed")
    note: Optional[str] = Field(None, max_length=500, description="Ghi chú bổ sung")

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        normalized = v.strip().lower()
        if normalized not in VALID_STATUSES:
            raise ValueError(
                f"Trạng thái không hợp lệ. Các giá trị chấp nhận: {', '.join(VALID_STATUSES)}"
            )
        return normalized


# ==========================================
# HÀM TIỆN ÍCH (HELPERS)
# ==========================================
def extract_user_id(current_user: Any) -> Optional[int]:
    """Trích xuất user_id một cách an toàn từ đối tượng current_user."""
    if isinstance(current_user, dict):
        uid = current_user.get("id")
    else:
        uid = getattr(current_user, "id", None)
    try:
        return int(uid) if uid is not None else None
    except (TypeError, ValueError):
        return None


def is_admin_user(current_user: Any) -> bool:
    """Kiểm tra người dùng có quyền admin hay không."""
    if isinstance(current_user, dict):
        role = current_user.get("role", "") or current_user.get("user_role", "")
        is_admin_flag = current_user.get("is_admin", False)
    else:
        role = getattr(current_user, "role", "") or getattr(current_user, "user_role", "")
        is_admin_flag = getattr(current_user, "is_admin", False)
    return bool(is_admin_flag) or str(role).lower() in ("admin", "administrator", "manager", "quanly", "super admin", "system admin")


# ==========================================
# 1. HTML ROUTER (Giao diện Quét Mã cho User)
# ==========================================
@router.get("/scan", response_class=HTMLResponse)
def get_scan_page(
    request: Request,
    user: dict = Depends(get_current_user_or_redirect)
):
    """Render giao diện quét mã Seri máy in cho người dùng/nhân viên."""
    return templates.TemplateResponse(
        request=request,
        name="scan_inventory.html",
        context={
            "user": user,
            "page_title": "Quét Mã & Kiểm Kê Seri Máy In"
        }
    )


# ==========================================
# 2. API ROUTERS
# ==========================================

# ---------- 2.1 Danh sách máy in (có phân trang & tìm kiếm) ----------
@api_router.get("/printers", status_code=status.HTTP_200_OK)
def get_printer_list(
    q: Optional[str] = Query(None, max_length=100, description="Từ khóa tìm kiếm theo Thương hiệu hoặc Model"),
    page: int = Query(1, ge=1, description="Số trang"),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE, description="Số bản ghi mỗi trang"),
    current_user: dict = Depends(require_login)
):
    """Lấy danh sách Thương hiệu & Model từ bảng list_printer."""
    try:
        query = supabase.table("list_printer").select("id, brand_name, model_code", count="exact")

        if q and q.strip():
            search_str = q.strip()
            escaped = search_str.replace("%", "\\%").replace("_", "\\_")
            query = query.or_(
                f"brand_name.ilike.%{escaped}%,model_code.ilike.%{escaped}%"
            )

        offset = (page - 1) * page_size
        response = (
            query
            .order("brand_name")
            .order("model_code")
            .range(offset, offset + page_size - 1)
            .execute()
        )

        total = response.count if response.count is not None else len(response.data or [])

        return {
            "status": "success",
            "data": response.data or [],
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": (total + page_size - 1) // page_size if page_size else 1
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Lỗi khi tải danh sách máy in: %s", str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Lỗi hệ thống khi tải danh sách máy in. Vui lòng thử lại sau."
        )


# ---------- 2.2 Tạo bản ghi quét seri ----------
@api_router.post("/scan-serial", status_code=status.HTTP_201_CREATED)
def create_serial_record(
    payload: SerialScanRequest,
    current_user: dict = Depends(require_login)
):
    """API tiếp nhận dữ liệu quét từ Frontend và lưu vào inventory_serials."""
    user_id = extract_user_id(current_user)
    if not user_id:
        logger.warning("Yêu cầu quét seri không xác định được user_id. current_user=%s", current_user)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Không xác định được thông tin người dùng. Vui lòng đăng nhập lại."
        )

    serial = payload.serial_number

    try:
        # Pre-check trùng seri
        existing = (
            supabase.table("inventory_serials")
            .select("id, serial_number")
            .eq("serial_number", serial)
            .limit(1)
            .execute()
        )
        if existing.data:
            logger.info("Seri trùng lặp bị từ chối: %s (user_id=%s)", serial, user_id)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Mã Seri '{serial}' đã tồn tại trong hệ thống!"
            )

        # Pre-check printer_id hợp lệ
        printer = (
            supabase.table("list_printer")
            .select("id, brand_name, model_code")
            .eq("id", payload.printer_id)
            .limit(1)
            .execute()
        )
        if not printer.data:
            logger.warning("printer_id không tồn tại: %s (user_id=%s)", payload.printer_id, user_id)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Model máy in chọn không hợp lệ (printer_id: {payload.printer_id} không tồn tại)!"
            )

        data_to_insert = {
            "serial_number": serial,
            "printer_id": payload.printer_id,
            "image_url": str(payload.image_url) if payload.image_url else None,
            "created_by": user_id,
            "status": "pending"
        }

        response = supabase.table("inventory_serials").insert(data_to_insert).execute()

        if not response.data:
            logger.error("Insert inventory_serials trả về rỗng. payload=%s", payload.model_dump())
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Không thể lưu dữ liệu vào cơ sở dữ liệu. Vui lòng thử lại."
            )

        logger.info("Đã lưu seri %s thành công (id=%s, user_id=%s)", serial, response.data[0].get("id"), user_id)

        return {
            "status": "success",
            "message": f"Đã lưu thành công Seri: {serial}",
            "data": response.data[0],
            "printer_info": printer.data[0]
        }

    except HTTPException:
        raise
    except Exception as e:
        error_msg = str(e)
        logger.exception("Lỗi không mong đợi khi lưu seri %s: %s", serial, error_msg)

        if "duplicate key value violates unique constraint" in error_msg or "23505" in error_msg:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Mã Seri '{serial}' đã tồn tại trong hệ thống!"
            )
        if "foreign key constraint" in error_msg or "23503" in error_msg:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Model máy in chọn không hợp lệ (printer_id: {payload.printer_id} không tồn tại)!"
            )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Lỗi hệ thống khi lưu dữ liệu. Vui lòng thử lại sau."
        )


# ---------- 2.3 Lịch sử quét ----------
@api_router.get("/history", status_code=status.HTTP_200_OK)
def get_scan_history(
    page: int = Query(1, ge=1, description="Số trang"),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE, description="Số bản ghi mỗi trang"),
    status_filter: Optional[str] = Query(None, description="Lọc theo trạng thái: pending/verified/rejected/completed"),
    serial_search: Optional[str] = Query(None, max_length=100, description="Tìm kiếm theo số seri"),
    current_user: dict = Depends(require_login)
):
    """API lấy lịch sử quét kèm thông tin Thương hiệu & Model."""
    user_id = extract_user_id(current_user)
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Không xác định được thông tin người dùng."
        )

    admin_mode = is_admin_user(current_user)

    try:
        query = (
            supabase.table("inventory_serials")
            .select(
                "id, serial_number, image_url, status, created_at, created_by, "
                "list_printer(brand_name, model_code)",
                count="exact"
            )
        )

        if not admin_mode:
            query = query.eq("created_by", user_id)

        if status_filter:
            status_clean = status_filter.strip().lower()
            if status_clean in VALID_STATUSES:
                query = query.eq("status", status_clean)
            else:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Trạng thái lọc không hợp lệ. Chấp nhận: {', '.join(VALID_STATUSES)}"
                )

        if serial_search and serial_search.strip():
            escaped = serial_search.strip().replace("%", "\\%").replace("_", "\\_")
            query = query.ilike("serial_number", f"%{escaped}%")

        offset = (page - 1) * page_size
        response = (
            query
            .order("created_at", desc=True)
            .range(offset, offset + page_size - 1)
            .execute()
        )

        total = response.count if response.count is not None else len(response.data or [])

        return {
            "status": "success",
            "data": response.data or [],
            "is_admin": admin_mode,
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": (total + page_size - 1) // page_size if page_size else 1
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Lỗi khi lấy lịch sử kiểm kê (user_id=%s): %s", user_id, str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Lỗi hệ thống khi lấy lịch sử kiểm kê. Vui lòng thử lại sau."
        )


# ---------- 2.4 Chi tiết 1 bản ghi ----------
@api_router.get("/records/{record_id}", status_code=status.HTTP_200_OK)
def get_serial_record_detail(
    record_id: int = FastAPIPath(..., gt=0),
    current_user: dict = Depends(require_login)
):
    """Lấy chi tiết một bản ghi kiểm kê theo ID."""
    user_id = extract_user_id(current_user)
    if not user_id:
        raise HTTPException(status_code=401, detail="Không xác định được người dùng.")

    try:
        response = (
            supabase.table("inventory_serials")
            .select(
                "id, serial_number, image_url, status, note, created_at, created_by, "
                "list_printer(brand_name, model_code)"
            )
            .eq("id", record_id)
            .limit(1)
            .execute()
        )

        if not response.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Không tìm thấy bản ghi ID {record_id}."
            )

        record = response.data[0]
        if not is_admin_user(current_user) and record.get("created_by") != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Bạn không có quyền xem bản ghi này."
            )

        return {"status": "success", "data": record}

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Lỗi khi lấy chi tiết bản ghi %s: %s", record_id, str(e))
        raise HTTPException(status_code=500, detail="Lỗi hệ thống. Vui lòng thử lại sau.")


# ---------- 2.5 Cập nhật trạng thái bản ghi (chỉ admin) ----------
@api_router.patch("/records/{record_id}/status", status_code=status.HTTP_200_OK)
def update_record_status(
    record_id: int,
    payload: StatusUpdateRequest,
    current_user: dict = Depends(require_login)
):
    """Cập nhật trạng thái bản ghi kiểm kê."""
    if not is_admin_user(current_user):
        logger.warning("Người dùng không phải admin thử cập nhật trạng thái bản ghi %s", record_id)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Bạn không có quyền cập nhật trạng thái. Yêu cầu quyền quản trị."
        )

    try:
        exist = supabase.table("inventory_serials").select("id").eq("id", record_id).limit(1).execute()
        if not exist.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Không tìm thấy bản ghi ID {record_id}."
            )

        update_data: Dict[str, Any] = {"status": payload.status}
        if payload.note is not None:
            update_data["note"] = payload.note.strip()

        response = (
            supabase.table("inventory_serials")
            .update(update_data)
            .eq("id", record_id)
            .execute()
        )

        logger.info("Admin cập nhật trạng thái bản ghi %s -> %s", record_id, payload.status)

        return {
            "status": "success",
            "message": f"Đã cập nhật trạng thái thành '{payload.status}'",
            "data": response.data[0] if response.data else None
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Lỗi khi cập nhật trạng thái bản ghi %s: %s", record_id, str(e))
        raise HTTPException(status_code=500, detail="Lỗi hệ thống khi cập nhật. Vui lòng thử lại sau.")


# ---------- 2.6 Xóa bản ghi (chỉ admin) ----------
@api_router.delete("/records/{record_id}", status_code=status.HTTP_200_OK)
def delete_record(
    record_id: int,
    current_user: dict = Depends(require_login)
):
    """Xóa một bản ghi kiểm kê."""
    if not is_admin_user(current_user):
        logger.warning("Người dùng không phải admin thử xóa bản ghi %s", record_id)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Bạn không có quyền xóa bản ghi. Yêu cầu quyền quản trị."
        )

    try:
        exist = supabase.table("inventory_serials").select("id, serial_number").eq("id", record_id).limit(1).execute()
        if not exist.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Không tìm thấy bản ghi ID {record_id}."
            )

        supabase.table("inventory_serials").delete().eq("id", record_id).execute()
        logger.info("Admin đã xóa bản ghi %s (seri=%s)", record_id, exist.data[0].get("serial_number"))

        return {
            "status": "success",
            "message": f"Đã xóa bản ghi ID {record_id} (Seri: {exist.data[0].get('serial_number')})"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Lỗi khi xóa bản ghi %s: %s", record_id, str(e))
        raise HTTPException(status_code=500, detail="Lỗi hệ thống khi xóa. Vui lòng thử lại sau.")


# ---------- 2.7 Thống kê nhanh (dashboard) ----------
@api_router.get("/stats", status_code=status.HTTP_200_OK)
def get_inventory_stats(
    current_user: dict = Depends(require_login)
):
    """Trả về số liệu thống kê nhanh: tổng bản ghi, theo trạng thái."""
    user_id = extract_user_id(current_user)
    if not user_id:
        raise HTTPException(status_code=401, detail="Không xác định được người dùng.")

    admin_mode = is_admin_user(current_user)

    try:
        query = supabase.table("inventory_serials").select("id, status", count="exact")
        if not admin_mode:
            query = query.eq("created_by", user_id)

        response = query.execute()
        total = response.count if response.count is not None else len(response.data or [])

        counts: Dict[str, int] = {s: 0 for s in VALID_STATUSES}
        for row in (response.data or []):
            st = (row.get("status") or "pending").lower()
            if st in counts:
                counts[st] += 1

        return {
            "status": "success",
            "data": {
                "total": total,
                "by_status": counts,
                "is_admin": admin_mode
            }
        }

    except Exception as e:
        logger.exception("Lỗi khi lấy thống kê: %s", str(e))
        raise HTTPException(status_code=500, detail="Lỗi hệ thống khi lấy thống kê.")