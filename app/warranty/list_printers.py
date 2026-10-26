import logging
from typing import Optional, List
from fastapi import APIRouter, Request, Depends, HTTPException, Query, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from app.auth import get_current_user_or_redirect, require_login
from config import supabase

logger = logging.getLogger(__name__)

# --- ROUTER DEFINITIONS ---
router = APIRouter(prefix="/warranty/danh-sach-may-in", tags=["Admin - Danh Sách Máy In"])
api_router = APIRouter(prefix="/warranty/api/danh-sach-may-in", tags=["Admin API - Danh Sách Máy In"])

templates = Jinja2Templates(directory="app/templates")

# --- DANH MỤC MẶC ĐỊNH PHỤC VỤ DROPDOWN ---
DANH_SACH_LOAI_MAY = ["In phun", "In Laser", "In kim", "Cắt bế", "Cán màng", "Ép nhiệt","Máy Scan", "Thiết bị khác"]


# =========================================================================
# PYDANTIC SCHEMAS
# =========================================================================
class PrinterItemSchema(BaseModel):
    id: Optional[int] = None
    brand_name: str = Field(..., description="Tên thương hiệu (Epson, Canon, Graphtec...)")
    model_code: str = Field(..., description="Mã Model (C5290, L18050, CE6000...)")
    category: str = Field(default="In phun", description="Loại thiết bị")


# =========================================================================
# 1. TRANG GIAO DIỆN QUẢN LÝ (HTML)
# =========================================================================
@router.get("", response_class=HTMLResponse)
async def view_list_printers(
    request: Request,
    search: Optional[str] = Query(None),
    brand: Optional[str] = Query(None),
    category: Optional[str] = Query(None)
):
    user = await get_current_user_or_redirect(request)
    if not user:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_303_SEE_OTHER)

    try:
        query = supabase.table("list_printer").select("*").order("updated_at", desc=True)

        if brand and brand.strip():
            query = query.eq("brand_name", brand.strip())

        if category and category.strip():
            query = query.eq("category", category.strip())

        if search and search.strip():
            s = search.strip()
            query = query.or_(f"brand_name.ilike.%{s}%,model_code.ilike.%{s}%")

        res = query.execute()
        printers = res.data or []

        # Lấy danh sách Thương hiệu duy nhất để hiển thị bộ lọc
        res_brands = supabase.table("list_printer").select("brand_name").execute()
        existing_brands = sorted(list(set(item["brand_name"] for item in (res_brands.data or []) if item.get("brand_name"))))

    except Exception as e:
        logger.error(f"Lỗi lấy danh sách máy in: {str(e)}")
        printers = []
        existing_brands = []

    return templates.TemplateResponse(
        request=request,
        name="warranty/list_printers.html",
        context={
            "request": request,
            "current_user": user,
            "printers": printers,
            "existing_brands": existing_brands,
            "categories": DANH_SACH_LOAI_MAY,
            "search": search or "",
            "selected_brand": brand or "",
            "selected_category": category or "",
        }
    )


# =========================================================================
# 2. CÁC API ENDPOINTS (JSON)
# =========================================================================

@api_router.get("/options")
async def get_printer_options():
    """Trả về các danh mục gợi ý cho Dropdown chọn khi tạo phiếu bảo hành"""
    try:
        res = supabase.table("list_printer").select("brand_name, model_code, category").execute()
        all_data = res.data or []

        brands = sorted(list(set(item["brand_name"] for item in all_data if item.get("brand_name"))))
        models = sorted(list(set(item["model_code"] for item in all_data if item.get("model_code"))))

        return {
            "status": "success",
            "brands": brands,
            "models": models,
            "categories": DANH_SACH_LOAI_MAY,
            "raw_items": all_data
        }
    except Exception as e:
        return JSONResponse(status_code=400, content={"status": "error", "message": str(e)})


@api_router.get("/get-models-by-brand")
async def get_models_by_brand(brand_name: str = Query(...)):
    """Lấy danh sách Model thuộc Thương hiệu cụ thể (Dùng cho dropdown động khi lập Phiếu Bảo Hành)"""
    try:
        res = supabase.table("list_printer")\
            .select("id, model_code, category")\
            .eq("brand_name", brand_name.strip())\
            .order("model_code", desc=False)\
            .execute()

        return {"status": "success", "data": res.data or []}
    except Exception as e:
        return JSONResponse(status_code=400, content={"status": "error", "message": str(e)})


@api_router.post("/save")
async def save_printer(
    payload: PrinterItemSchema,
    current_user: dict = Depends(require_login)
):
    """Thêm mới hoặc cập nhật thông tin dòng máy in"""
    try:
        brand = payload.brand_name.strip()
        model = payload.model_code.strip().upper()
        category = payload.category.strip()

        data = {
            "brand_name": brand,
            "model_code": model,
            "category": category,
        }

        if payload.id:
            # UPDATE
            res = supabase.table("list_printer").update(data).eq("id", payload.id).execute()
            action_type = "cập nhật"
        else:
            # INSERT
            res = supabase.table("list_printer").insert(data).execute()
            action_type = "thêm mới"

        return {
            "success": True, 
            # ĐÃ SỬA: Xóa dấu ngắt dòng trong chuỗi f-string
            "message": f"Đã {action_type} dòng máy [{brand} {model} ] thành công!",
            "data": res.data
        }

    except Exception as e:
        err_msg = str(e)
        logger.error(f"Lỗi khi lưu máy in: {err_msg}")
        
        # Bắt lỗi trùng lặp ràng buộc Unique
        if "23505" in err_msg or "unique" in err_msg.lower():
            return JSONResponse(
                status_code=400, 
                # ĐÃ SỬA: Xóa dấu ngắt dòng
                content={"success": False, "message": f"Dòng máy [{payload.brand_name} {payload.model_code}] đã tồn tại trong hệ thống!"}
            )

        return JSONResponse(status_code=400, content={"success": False, "message": f"Lỗi lưu dữ liệu: {err_msg}"})


@api_router.post("/delete/{printer_id}")
async def delete_printer(
    printer_id: int,
    current_user: dict = Depends(require_login)
):
    """Xóa dòng máy khỏi danh mục"""
    try:
        supabase.table("list_printer").delete().eq("id", printer_id).execute()
        return {"success": True, "message": "Đã xóa thành công khỏi danh mục!"}
    except Exception as e:
        return JSONResponse(status_code=400, content={"success": False, "message": f"Lỗi khi xóa: {str(e)}"})