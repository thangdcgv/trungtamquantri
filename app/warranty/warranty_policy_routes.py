from typing import Optional
from fastapi import APIRouter, Request, Depends, HTTPException, Query, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from app.auth import get_current_user_or_redirect, require_login
from config import supabase

# Khai báo Jinja2 Templates
templates = Jinja2Templates(directory="app/templates")

router = APIRouter(prefix="/admin/chinh-sach-bao-hanh", tags=["Warranty Policy"])


# =========================================================================
# PYDANTIC SCHEMA (Cập nhật đồng bộ với CSDL & Template)
# =========================================================================
class PolicySchema(BaseModel):
    id: Optional[str] = None
    policy_name: str
    category: str
    condition: str = "Mới"
    warranty_months: int = 0
    head_warranty_months: int = 0
    cartridge_warranty_months: int = 0
    
    # Giới hạn Trang in
    page_limit_body: int = 0
    page_limit_head: int = 0
    no_page_limit: bool = False
    
    # Giới hạn Số mét (Bổ sung mới)
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
        res = supabase.table("warranty_policy").select("*").order("created_at", desc=True).execute()
        policies = res.data if res.data else []
    except Exception as db_err:
        policies = []

    return templates.TemplateResponse(
        request=request,
        name="warranty/warranty_policy.html",
        context={
            "request": request,
            "current_user": user,
            "policies": policies
        }
    )


@router.post("/save")
async def save_policy(
    payload: PolicySchema,
    current_user: dict = Depends(require_login) # Bổ sung bảo mật
):
    """Thêm mới hoặc cập nhật chính sách bảo hành"""
    try:
        # Tương thích Pydantic v1 và v2
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
        return JSONResponse(status_code=400, content={"success": False, "message": str(e)})


@router.post("/toggle-status/{policy_id}")
async def toggle_policy_status(
    policy_id: str, 
    status: bool = Query(...),
    current_user: dict = Depends(require_login) # Bổ sung bảo mật
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
    current_user: dict = Depends(require_login) # Bổ sung bảo mật
):
    """Xóa chính sách bảo hành"""
    try:
        supabase.table("warranty_policy").delete().eq("id", policy_id).execute()
        return {"success": True}
    except Exception as e:
        return JSONResponse(status_code=400, content={"success": False, "message": str(e)})