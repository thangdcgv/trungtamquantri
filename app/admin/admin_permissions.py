import logging
from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from app.auth import require_login
from app.permissions import load_permissions_cache, can_access
from config import supabase
from main import templates

logger = logging.getLogger(__name__)

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
    # 1. Bổ sung kiểm tra quyền thực thi (Bảo mật bắt buộc cho API POST)
    if not can_access('config_permissions', user):
        return JSONResponse(
            status_code=403, 
            content={"success": False, "message": "⛔ Bạn không có quyền thực hiện thao tác này!"}
        )
        
    try:
        data = await request.json() # Nhận payload: [{"id": "uuid", "allowed_roles": [...]}]
        
        for item in data:
            allowed_roles = item.get("allowed_roles", [])
            
            # 2. Đảm bảo System Admin LUÔN LUÔN có quyền (Phòng chống bypass từ client)
            role_lowers = [str(r).strip().lower() for r in allowed_roles]
            if "system admin" not in role_lowers:
                allowed_roles.append("System Admin")

            supabase.table("page_permissions").update({
                "allowed_roles": allowed_roles
            }).eq("id", item["id"]).execute()
            
        # Làm mới Cache sau khi lưu
        load_permissions_cache()
        return {"success": True, "message": "Đã lưu cấu hình phân quyền mới thành công!"}
        
    except Exception as e:
        logger.error(f"SAVE PERMISSION ERR: {e}")
        return JSONResponse(
            status_code=500, 
            content={"success": False, "message": f"Lỗi hệ thống khi lưu phân quyền: {str(e)}"}
        )