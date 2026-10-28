import logging
import re
from pathlib import Path
from typing import Optional, Dict, Any,List

from fastapi import APIRouter, Request, Query, HTTPException, Depends, status, Path as FastAPIPath
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, HttpUrl, field_validator
import uuid # <-- Đã thêm thư viện UUID


# Imports đồng bộ với hệ thống hiện tại
from config import supabase
from app.auth import require_login, get_current_user_or_redirect

# ==========================================
# CẤU HÌNH LOGGING & TEMPLATES
# ==========================================
logger = logging.getLogger("uvicorn.error")

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

SERIAL_ALLOWED_PATTERN = re.compile(r"^[A-Za-z0-9\-\_\.\/\+\#\:\s]+$")
# Các trạng thái hợp lệ đã được đồng bộ với DB mới
VALID_STATUSES = ("pending", "verified", "rejected", "completed")

router = APIRouter(prefix="/inventory", tags=["Quản Lý Kho & Seri Máy In"])
api_router = APIRouter(prefix="/api/inventory", tags=["API Kiểm Kê Seri Máy In"])

# ==========================================
# PYDANTIC SCHEMAS
# ==========================================
class SerialScanRequest(BaseModel):
    serial_number: str = Field(..., min_length=MIN_SERIAL_LENGTH, max_length=MAX_SERIAL_LENGTH)
    printer_id: int = Field(..., gt=0)
    image_url: Optional[HttpUrl] = None

    @field_validator("serial_number")
    @classmethod
    def validate_and_normalize_serial(cls, v: str) -> str:
        normalized = " ".join(v.strip().split()).upper()
        if not normalized:
            raise ValueError("Số seri không được để trống")
        if not SERIAL_ALLOWED_PATTERN.match(normalized):
            raise ValueError("Số seri chứa ký tự không hợp lệ")
        return normalized

class StatusUpdateRequest(BaseModel):
    status: str
    note: Optional[str] = Field(None, max_length=500)

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        normalized = v.strip().lower()
        if normalized not in VALID_STATUSES:
            raise ValueError(f"Trạng thái không hợp lệ. Cho phép: {', '.join(VALID_STATUSES)}")
        return normalized


# ==========================================
# HÀM TIỆN ÍCH (ĐÃ FIX LỖI ÉP KIỂU UUID)
# ==========================================
def extract_user_id(current_user: Any) -> Optional[str]:
    """Trích xuất user_id (UUID) một cách an toàn."""
    if isinstance(current_user, dict):
        uid = current_user.get("id")
    else:
        uid = getattr(current_user, "id", None)
    
    if uid:
        return str(uid) # Supabase auth.users.id là UUID kiểu chuỗi (String)
    return None

def is_admin_user(current_user: Any) -> bool:
    if isinstance(current_user, dict):
        role = current_user.get("role", "") or current_user.get("user_role", "")
        is_admin_flag = current_user.get("is_admin", False)
    else:
        role = getattr(current_user, "role", "") or getattr(current_user, "user_role", "")
        is_admin_flag = getattr(current_user, "is_admin", False)
    return bool(is_admin_flag) or str(role).lower() in ("admin", "administrator", "manager", "quanly", "super admin", "system admin")

def validate_uuid(val: str):
    """Kiểm tra xem chuỗi có phải là UUID hợp lệ không."""
    try:
        uuid.UUID(str(val))
        return True
    except ValueError:
        return False


# ==========================================
# 1. HTML ROUTER
# ==========================================
@router.get("/scan", response_class=HTMLResponse)
def get_scan_page(request: Request, user: dict = Depends(get_current_user_or_redirect)):
    return templates.TemplateResponse(
        request=request, name="scan_inventory.html",
        context={"user": user, "page_title": "Quét Mã & Kiểm Kê Seri Máy In"}
    )


# ==========================================
# 2. API ROUTERS
# ==========================================
@api_router.get("/printers", status_code=status.HTTP_200_OK)
def get_printer_list(
    q: Optional[str] = Query(None, max_length=100),
    page: int = Query(1, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    current_user: dict = Depends(require_login)
):
    try:
        query = supabase.table("list_printer").select("id, brand_name, model_code", count="exact")
        if q and q.strip():
            escaped = q.strip().replace("%", "\\%").replace("_", "\\_")
            query = query.or_(f"brand_name.ilike.%{escaped}%,model_code.ilike.%{escaped}%")

        offset = (page - 1) * page_size
        response = query.order("brand_name").order("model_code").range(offset, offset + page_size - 1).execute()
        total = response.count if response.count is not None else len(response.data or [])

        return {
            "status": "success",
            "data": response.data or [],
            "pagination": {
                "page": page, "page_size": page_size, "total": total,
                "total_pages": (total + page_size - 1) // page_size if page_size else 1
            }
        }
    except Exception as e:
        logger.exception("Lỗi khi tải danh sách máy in: %s", str(e))
        raise HTTPException(status_code=500, detail="Lỗi hệ thống khi tải danh sách máy in.")


@api_router.post("/scan-serial", status_code=status.HTTP_201_CREATED)
def create_serial_record(payload: SerialScanRequest, current_user: dict = Depends(require_login)):
    user_id = extract_user_id(current_user)
    if not user_id:
        raise HTTPException(status_code=401, detail="Không xác định được thông tin người dùng.")

    serial = payload.serial_number

    try:
        existing = supabase.table("inventory_serials").select("id").eq("serial_number", serial).limit(1).execute()
        if existing.data:
            raise HTTPException(status_code=409, detail=f"Mã Seri '{serial}' đã tồn tại trong hệ thống!")

        printer = supabase.table("list_printer").select("id, brand_name, model_code").eq("id", payload.printer_id).limit(1).execute()
        if not printer.data:
            raise HTTPException(status_code=400, detail="Model máy in không tồn tại!")

        data_to_insert = {
            "serial_number": serial,
            "printer_id": payload.printer_id,
            "image_url": str(payload.image_url) if payload.image_url else None,
            "created_by": user_id,
            "status": "pending"
        }

        response = supabase.table("inventory_serials").insert(data_to_insert).execute()
        if not response.data:
            raise HTTPException(status_code=400, detail="Không thể lưu dữ liệu.")

        return {
            "status": "success", "message": f"Đã lưu thành công Seri: {serial}",
            "data": response.data[0], "printer_info": printer.data[0]
        }
    except HTTPException:
        raise
    except Exception as e:
        error_msg = str(e)
        if "duplicate key" in error_msg or "23505" in error_msg:
            raise HTTPException(status_code=409, detail=f"Mã Seri '{serial}' đã tồn tại trong hệ thống!")
        raise HTTPException(status_code=500, detail="Lỗi hệ thống khi lưu dữ liệu.")


@api_router.get("/history", status_code=status.HTTP_200_OK)
def get_scan_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    status_filter: Optional[str] = Query(None),
    serial_search: Optional[str] = Query(None, max_length=100),
    current_user: dict = Depends(require_login)
):
    user_id = extract_user_id(current_user)
    if not user_id:
        raise HTTPException(status_code=401, detail="Không xác định được người dùng.")

    admin_mode = is_admin_user(current_user)

    try:
        query = supabase.table("inventory_serials").select(
            "id, serial_number, image_url, status, created_at, created_by, list_printer(brand_name, model_code)", count="exact"
        )
        if not admin_mode:
            query = query.eq("created_by", user_id)

        if status_filter:
            status_clean = status_filter.strip().lower()
            if status_clean in VALID_STATUSES:
                query = query.eq("status", status_clean)

        if serial_search and serial_search.strip():
            escaped = serial_search.strip().replace("%", "\\%").replace("_", "\\_")
            query = query.ilike("serial_number", f"%{escaped}%")

        offset = (page - 1) * page_size
        response = query.order("created_at", desc=True).range(offset, offset + page_size - 1).execute()
        total = response.count if response.count is not None else len(response.data or [])

        return {
            "status": "success", "data": response.data or [], "is_admin": admin_mode,
            "pagination": {
                "page": page, "page_size": page_size, "total": total,
                "total_pages": (total + page_size - 1) // page_size if page_size else 1
            }
        }
    except Exception:
        raise HTTPException(status_code=500, detail="Lỗi hệ thống khi lấy lịch sử.")


# ĐÃ SỬA: record_id đổi từ int -> str để hỗ trợ kiểu UUID của DB
@api_router.get("/records/{record_id}", status_code=status.HTTP_200_OK)
def get_serial_record_detail(record_id: str = FastAPIPath(...), current_user: dict = Depends(require_login)):
    if not validate_uuid(record_id):
         raise HTTPException(status_code=400, detail="ID bản ghi không hợp lệ (Phải là UUID).")
         
    user_id = extract_user_id(current_user)
    try:
        response = supabase.table("inventory_serials").select(
            "id, serial_number, image_url, status, note, created_at, created_by, list_printer(brand_name, model_code)"
        ).eq("id", record_id).limit(1).execute()

        if not response.data:
            raise HTTPException(status_code=404, detail=f"Không tìm thấy bản ghi ID {record_id}.")

        record = response.data[0]
        if not is_admin_user(current_user) and record.get("created_by") != user_id:
            raise HTTPException(status_code=403, detail="Bạn không có quyền xem bản ghi này.")

        return {"status": "success", "data": record}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Lỗi hệ thống.")


# ĐÃ SỬA: record_id từ int -> str
@api_router.patch("/records/{record_id}/status", status_code=status.HTTP_200_OK)
def update_record_status(record_id: str, payload: StatusUpdateRequest, current_user: dict = Depends(require_login)):
    if not is_admin_user(current_user):
        raise HTTPException(status_code=403, detail="Yêu cầu quyền quản trị.")
    if not validate_uuid(record_id):
         raise HTTPException(status_code=400, detail="ID bản ghi không hợp lệ.")

    try:
        exist = supabase.table("inventory_serials").select("id").eq("id", record_id).limit(1).execute()
        if not exist.data:
            raise HTTPException(status_code=404, detail="Không tìm thấy bản ghi.")

        update_data = {"status": payload.status}
        if payload.note is not None:
            update_data["note"] = payload.note.strip()

        response = supabase.table("inventory_serials").update(update_data).eq("id", record_id).execute()
        return {"status": "success", "message": f"Đã cập nhật trạng thái", "data": response.data[0] if response.data else None}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Lỗi hệ thống.")


# ĐÃ SỬA: record_id từ int -> str
@api_router.delete("/records/{record_id}", status_code=status.HTTP_200_OK)
def delete_record(record_id: str, current_user: dict = Depends(require_login)):
    if not is_admin_user(current_user):
        raise HTTPException(status_code=403, detail="Yêu cầu quyền quản trị.")
    if not validate_uuid(record_id):
         raise HTTPException(status_code=400, detail="ID bản ghi không hợp lệ.")

    try:
        exist = supabase.table("inventory_serials").select("serial_number").eq("id", record_id).limit(1).execute()
        if not exist.data:
            raise HTTPException(status_code=404, detail="Không tìm thấy bản ghi.")

        supabase.table("inventory_serials").delete().eq("id", record_id).execute()
        return {"status": "success", "message": f"Đã xóa Seri: {exist.data[0].get('serial_number')}"}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Lỗi hệ thống khi xóa.")


@api_router.get("/stats", status_code=status.HTTP_200_OK)
def get_inventory_stats(current_user: dict = Depends(require_login)):
    user_id = extract_user_id(current_user)
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

        return {"status": "success", "data": {"total": total, "by_status": counts, "is_admin": admin_mode}}
    except Exception:
        raise HTTPException(status_code=500, detail="Lỗi hệ thống khi lấy thống kê.")
    
@router.get("/admin/review", response_class=HTMLResponse)
def get_admin_review_page(
    request: Request,
    current_user: dict = Depends(get_current_user_or_redirect)
):
    """Render giao diện Dashboard Duyệt kiểm kê cho Admin"""
    if not is_admin_user(current_user):
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/", status_code=303)

    return templates.TemplateResponse(
        request=request,
        name="admin/admin_inventory_review.html",  # Cập nhật đường dẫn thư mục ở đây
        context={
            "current_user": current_user,
        }
    )

# ==========================================
# 3. API BATCH (THEO LÔ) — ĐÃ SỬA HOÀN CHỈNH
# ==========================================

class BatchStatusRequest(BaseModel):
    ids: List[str] = Field(..., min_length=1, max_length=5000, description="Danh sách ID cần cập nhật")
    status: str = Field(..., description="Trạng thái mới")
    note: Optional[str] = Field(None, max_length=500)

    @field_validator("status")
    @classmethod
    def validate_batch_status(cls, v: str) -> str:
        normalized = v.strip().lower()
        if normalized not in ("verified", "rejected", "pending"):
            raise ValueError("Trạng thái batch chỉ cho phép: verified, rejected, pending")
        return normalized

class BatchDeleteRequest(BaseModel):
    ids: List[str] = Field(..., min_length=1, max_length=5000, description="Danh sách ID cần xóa")


@api_router.patch("/records/batch-status", status_code=status.HTTP_200_OK)
def batch_update_status(
    payload: BatchStatusRequest,
    current_user: dict = Depends(require_login)
):
    """Cập nhật trạng thái hàng loạt theo lô (chỉ admin)."""
    if not is_admin_user(current_user):
        raise HTTPException(status_code=403, detail="Yêu cầu quyền quản trị.")

    # Validate tất cả ID là UUID
    invalid_ids = [uid for uid in payload.ids if not validate_uuid(uid)]
    if invalid_ids:
        raise HTTPException(status_code=400, detail=f"Có {len(invalid_ids)} ID không hợp lệ (không phải UUID).")

    try:
        update_data: Dict[str, Any] = {"status": payload.status}
        if payload.note is not None:
            update_data["note"] = payload.note.strip()

        response = supabase.table("inventory_serials") \
            .update(update_data) \
            .in_("id", payload.ids) \
            .execute()

        updated_count = len(response.data or [])
        return {
            "status": "success",
            "message": f"Đã cập nhật {updated_count}/{len(payload.ids)} bản ghi",
            "updated": updated_count,
            "requested": len(payload.ids)
        }
    except Exception as e:
        logger.exception("Lỗi batch update status: %s", str(e))
        raise HTTPException(status_code=500, detail="Lỗi hệ thống khi cập nhật hàng loạt.")


@api_router.post("/records/batch-delete", status_code=status.HTTP_200_OK)
def batch_delete_records(
    payload: BatchDeleteRequest,
    current_user: dict = Depends(require_login)
):
    """Xóa hàng loạt theo lô (chỉ admin). Dùng POST thay vì DELETE để tránh bị bỏ body."""
    if not is_admin_user(current_user):
        raise HTTPException(status_code=403, detail="Yêu cầu quyền quản trị.")

    invalid_ids = [uid for uid in payload.ids if not validate_uuid(uid)]
    if invalid_ids:
        raise HTTPException(status_code=400, detail=f"Có {len(invalid_ids)} ID không hợp lệ.")

    try:
        # Lấy danh sách serial để trả về (log)
        existing = supabase.table("inventory_serials") \
            .select("id, serial_number") \
            .in_("id", payload.ids) \
            .execute()
        existing_ids = [r["id"] for r in (existing.data or [])]
        missing = len(payload.ids) - len(existing_ids)

        if existing_ids:
            supabase.table("inventory_serials").delete().in_("id", existing_ids).execute()

        return {
            "status": "success",
            "message": f"Đã xóa {len(existing_ids)} bản ghi" + (f", {missing} ID không tồn tại" if missing else ""),
            "deleted": len(existing_ids),
            "requested": len(payload.ids),
            "missing": missing
        }
    except Exception as e:
        logger.exception("Lỗi batch delete: %s", str(e))
        raise HTTPException(status_code=500, detail="Lỗi hệ thống khi xóa hàng loạt.")