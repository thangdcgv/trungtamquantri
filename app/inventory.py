from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional, Dict, Any, List

from fastapi import APIRouter, Request, Query, HTTPException, Depends, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

# Imports đồng bộ với hệ thống hiện tại
from config import supabase
from app.auth import require_login, get_current_user_or_redirect

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

templates = Jinja2Templates(directory="app/templates")


# ==========================================
# PYDANTIC SCHEMAS
# ==========================================
class SerialScanRequest(BaseModel):
    serial_number: str = Field(..., min_length=1, max_length=100, description="Số Seri của máy in")
    printer_id: int = Field(..., gt=0, description="ID khóa ngoại liên kết tới bảng list_printer")
    image_url: Optional[str] = Field(None, description="Link hình ảnh máy/tem mã")


# ==========================================
# 1. HTML ROUTER (Giao diện Quét Mã cho User)
# ==========================================
@router.get("/scan", response_class=HTMLResponse)
async def get_scan_page(request: Request, user: dict = Depends(get_current_user_or_redirect)):
    """
    Render giao diện quét mã Seri máy in cho người dùng/nhân viên
    """
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

@api_router.get("/printers", status_code=status.HTTP_200_OK)
async def get_printer_list(
    q: Optional[str] = Query(None, description="Từ khóa tìm kiếm theo Thương hiệu hoặc Model"),
    current_user: dict = Depends(require_login)
):
    """
    Lấy danh sách Thương hiệu & Model từ bảng list_printer.
    Hỗ trợ lọc live-search không phân biệt hoa/thường qua query param 'q'.
    """
    try:
        # Khởi tạo query nền tảng
        query = supabase.table("list_printer").select("id, brand_name, model_code")

        # Nếu người dùng truyền từ khóa q, áp dụng lọc không phân biệt chữ hoa/thường (ILIKE)
        if q and q.strip():
            search_str = q.strip()
            # Lọc nếu brand_name HOẶC model_code chứa từ khóa
            query = query.or_(f"brand_name.ilike.%{search_str}%,model_code.ilike.%{search_str}%")

        # Sắp xếp kết quả và thực thi
        response = query.order("brand_name").order("model_code").execute()

        return {"status": "success", "data": response.data}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Lỗi khi tải danh sách máy in: {str(e)}"
        )


@api_router.post("/scan-serial", status_code=status.HTTP_201_CREATED)
async def create_serial_record(
    payload: SerialScanRequest,
    current_user: dict = Depends(require_login)
):
    """
    API tiếp nhận dữ liệu quét từ Frontend, gắn ID người dùng thực hiện và lưu vào inventory_serials
    """
    try:
        user_id = current_user.get("id") if isinstance(current_user, dict) else getattr(current_user, "id", None)

        data_to_insert = {
            "serial_number": payload.serial_number.strip(),
            "printer_id": payload.printer_id,
            "image_url": payload.image_url,
            "created_by": user_id
        }

        # Thực thi Insert vào Supabase
        response = supabase.table("inventory_serials").insert(data_to_insert).execute()

        if not response.data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Không thể lưu dữ liệu vào cơ sở dữ liệu."
            )

        return {
            "status": "success",
            "message": f"Đã lưu thành công Seri: {payload.serial_number}",
            "data": response.data[0]
        }

    except HTTPException:
        raise

    except Exception as e:
        error_msg = str(e)
        
        # Xử lý lỗi trùng lặp Seri (Unique constraint)
        if "duplicate key value violates unique constraint" in error_msg or "23505" in error_msg:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Mã Seri '{payload.serial_number}' đã tồn tại trong hệ thống!"
            )
        
        # Xử lý lỗi không tìm thấy printer_id trong bảng list_printer (Foreign key constraint)
        if "foreign key constraint" in error_msg or "23503" in error_msg:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Model máy in chọn không hợp lệ (printer_id: {payload.printer_id} không tồn tại)!"
            )
        
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Lỗi Server: {error_msg}"
        )


@api_router.get("/history", status_code=status.HTTP_200_OK)
async def get_scan_history(current_user: dict = Depends(require_login)):
    """
    API lấy lịch sử quét kèm thông tin Thương hiệu & Model (JOIN 2 bảng)
    """
    try:
        # Cú pháp JOIN bảng list_printer qua FK printer_id của Supabase
        response = (
            supabase.table("inventory_serials")
            .select("id, serial_number, image_url, status, created_at, list_printer(brand_name, model_code)")
            .order("created_at", desc=True)
            .limit(50)
            .execute()
        )
        return {"status": "success", "data": response.data}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Lỗi khi lấy lịch sử kiểm kê: {str(e)}"
        )