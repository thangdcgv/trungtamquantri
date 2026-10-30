from typing import Optional
from fastapi import APIRouter, Request, Depends, Query, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from app.auth import get_current_user_or_redirect, require_login
from config import supabase
from app.templates_config import templates  # 👈 Import sạch sẽ, không bị vòng lặp


router = APIRouter(prefix="/admin/chinh-sach-bao-hanh", tags=["Warranty Policy"])

DANH_SACH_LOAI_MAY = ["In phun", "In Laser", "In kim", "Cắt bế", "Máy Scan", "Thiết bị điện", "Thiết bị khác"]

# =========================================================================
# PYDANTIC SCHEMA (Cập nhật đồng bộ với CSDL & Template)
# =========================================================================
class PolicySchema(BaseModel):
    id: Optional[str] = None
    brand_name: str  # 👈 Bổ sung brand_name thay thế cho printer_id
    policy_name: str
    category: str
    condition: str = "Máy mới"
    warranty_months: int = 0
    head_warranty_months: int = 0
    cartridge_warranty_months: int = 0
    
    # Giới hạn Trang in
    page_limit_body: int = 0
    page_limit_head: int = 0
    no_page_limit: bool = False
    
    # Giới hạn Số mét
    meter_limit_body: int = 0
    meter_limit_head: int = 0
    no_meter_limit: bool = False
    
    is_active: bool = True


# =========================================================================
# ROUTE GIAO DIỆN & APIS
# =========================================================================

@router.get("", response_class=HTMLResponse)
async def list_policies(request: Request):
    """Hiển thị danh sách chính sách bảo hành"""
    user = await get_current_user_or_redirect(request)
    if not user:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_303_SEE_OTHER)

    try:
        # 1. Truy vấn danh sách chính sách (Lấy trực tiếp từ bảng, không cần JOIN list_printer)
        res_policies = (
            supabase.table("warranty_policy")
            .select("*")
            .order("brand_name", desc=True)
            .order("policy_name", desc=False)
            .execute()
        )
        policies = res_policies.data if res_policies.data else []

        # 2. Truy vấn danh mục máy in để lấy danh sách Hãng độc nhất (brand_name) nạp vào Datalist
        res_printers = (
            supabase.table("list_printer")
            .select("brand_name")
            .execute()
        )
        printers = res_printers.data if res_printers.data else []
        # Lọc ra các hãng duy nhất, loại bỏ giá trị rỗng
        existing_brands = sorted(list(set(p["brand_name"] for p in printers if p.get("brand_name"))))

    except Exception as db_err:
        policies = []
        existing_brands = []

    return templates.TemplateResponse(
        request=request,
        name="warranty/warranty_policy.html",
        context={
            "request": request,
            "current_user": user,
            "policies": policies,
            "existing_brands": existing_brands, # 👈 Truyền đúng biến cho Template
            "categories": DANH_SACH_LOAI_MAY,
        }
    )


@router.post("/save")
async def save_policy(
    payload: PolicySchema,
    current_user: dict = Depends(require_login)
):
    """Thêm mới hoặc cập nhật chính sách bảo hành"""
    try:
        data = payload.model_dump(exclude_none=True) if hasattr(payload, "model_dump") else payload.dict(exclude_none=True)
        policy_id = data.pop("id", None)

        if policy_id:
            # Update chính sách đã có
            res = supabase.table("warranty_policy").update(data).eq("id", policy_id).execute()
        else:
            # Insert chính sách mới
            res = supabase.table("warranty_policy").insert(data).execute()

        return {"success": True, "data": res.data}
    except Exception as e:
        err_msg = str(e)
        
        # Bắt lỗi Unique Constraint của Tên chính sách (warranty_policy_name_key)
        if "23505" in err_msg or "warranty_policy_name_key" in err_msg:
            return JSONResponse(
                status_code=400, 
                content={
                    "success": False, 
                    "message": f"Tên chính sách '{payload.policy_name}' đã tồn tại! Vui lòng đặt một tên khác (VD: thêm hậu tố khổ lớn, độ chế...)."
                }
            )
            
        return JSONResponse(status_code=400, content={"success": False, "message": err_msg})


@router.post("/toggle-status/{policy_id}")
async def toggle_policy_status(
    policy_id: str, 
    status: bool = Query(...),
    current_user: dict = Depends(require_login)
):
    """Bật/tắt trạng thái chính sách"""
    try:
        supabase.table("warranty_policy").update({"is_active": status}).eq("id", policy_id).execute()
        return {"success": True}
    except Exception as e:
        return JSONResponse(status_code=400, content={"success": False, "message": str(e)})


@router.post("/delete/{policy_id}")
async def delete_policy(
    policy_id: str,
    current_user: dict = Depends(require_login)
):
    """Xóa chính sách bảo hành"""
    try:
        supabase.table("warranty_policy").delete().eq("id", policy_id).execute()
        return {"success": True}
    except Exception as e:
        return JSONResponse(status_code=400, content={"success": False, "message": str(e)})