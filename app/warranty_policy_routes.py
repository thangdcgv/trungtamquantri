from fastapi import APIRouter, Request, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from typing import Optional
from fastapi.templating import Jinja2Templates

try:
    from config import supabase
except ImportError:
    from config import supabase, templates

# Khai báo thư mục chứa file HTML (thay đổi "app/templates" nếu cấu trúc thư mục của bạn khác)
templates = Jinja2Templates(directory="app/templates")
router = APIRouter(prefix="/admin/chinh-sach-bao-hanh", tags=["Warranty Policy"])

class PolicySchema(BaseModel):
    id: Optional[str] = None
    policy_name: str
    category: str
    condition: str = "Mới"
    warranty_months: int = 0
    head_warranty_months: int = 0
    cartridge_warranty_months: int = 0
    page_limit_body: int = 0
    page_limit_head: int = 0
    no_page_limit: bool = False
    is_active: bool = True

@router.get("", response_class=HTMLResponse)
async def list_policies(request: Request):
    """Hiển thị danh sách chính sách bảo hành"""
    res = supabase.table("warranty_policy").select("*").order("created_at", desc=True).execute()
    policies = res.data if res.data else []
    
    return templates.TemplateResponse(
        request=request,
        name="admin/warranty_policy.html",
        context={"policies": policies}
    )

@router.post("/save")
async def save_policy(payload: PolicySchema):
    """Thêm mới hoặc cập nhật chính sách bảo hành"""
    try:
        data = payload.dict(exclude_none=True)
        policy_id = data.pop("id", None)

        if policy_id:
            # Update
            res = supabase.table("warranty_policy").update(data).eq("id", policy_id).execute()
        else:
            # Insert
            res = supabase.table("warranty_policy").insert(data).execute()

        return {"success": True, "data": res.data}
    except Exception as e:
        return JSONResponse(status_code=400, content={"success": False, "message": str(e)})

@router.post("/toggle-status/{policy_id}")
async def toggle_policy_status(policy_id: str, status: bool = Query(...)):
    """Bật/tắt trạng thái chính sách"""
    try:
        supabase.table("warranty_policy").update({"is_active": status}).eq("id", policy_id).execute()
        return {"success": True}
    except Exception as e:
        return JSONResponse(status_code=400, content={"success": False, "message": str(e)})

@router.post("/delete/{policy_id}")
async def delete_policy(policy_id: str):
    """Xóa chính sách bảo hành"""
    try:
        supabase.table("warranty_policy").delete().eq("id", policy_id).execute()
        return {"success": True}
    except Exception as e:
        return JSONResponse(status_code=400, content={"success": False, "message": str(e)})