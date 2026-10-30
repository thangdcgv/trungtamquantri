from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from app.auth import require_login
from app.permissions import load_permissions_cache, can_access
from config import supabase
from main import templates

router = APIRouter(prefix="/admin/phan-quyen", tags=["Phân quyền"])

ROLES_LIST = ["Super Admin", "System Admin", "Kỹ Thuật Viên", "Kế Toán", "Bán Hàng"]

@router.get("", response_class=HTMLResponse)
async def page_permissions_ui(request: Request, user: dict = Depends(require_login)):
    if not can_access('config_permissions', user):
        return HTMLResponse("Bạn không có quyền truy cập trang này!", status_code=403)
        
    res = supabase.table("page_permissions").select("*").order("category").execute()
    
    return templates.TemplateResponse(
        request=request,
        name="admin/page_permissions.html",
        context={
            "current_user": user,
            "permissions": res.data or [],
            "all_roles": ROLES_LIST
        }
    )

@router.post("/save")
async def save_permissions(request: Request, user: dict = Depends(require_login)):
    data = await request.json() # Nhận payload: [{"id": "uuid", "allowed_roles": [...]}]
    for item in data:
        supabase.table("page_permissions").update({
            "allowed_roles": item["allowed_roles"]
        }).eq("id", item["id"]).execute()
        
    # Làm mới Cache sau khi lưu
    load_permissions_cache()
    return {"success": True, "message": "Đã lưu cấu hình phân quyền mới!"}