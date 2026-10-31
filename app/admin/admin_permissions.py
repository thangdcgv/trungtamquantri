import logging
from typing import List
from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from app.auth import require_login
from app.permissions import load_permissions_cache, can_access, ROLE_LEVELS
from config import supabase_admin
from main import templates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/phan-quyen", tags=["Phân quyền"])

# Danh sách đầy đủ các vai trò hiển thị trên Ma trận Phân quyền
ROLES_LIST = ["System Admin", "Super Admin", "Admin", "User"]


# Schema validate dữ liệu từ Client
class PermissionUpdateItem(BaseModel):
    id: str | int
    allowed_roles: List[str]


@router.get("", response_class=HTMLResponse)
async def page_permissions_ui(request: Request, user: dict = Depends(require_login)):
    # 1. Kiểm tra quyền truy cập trang
    if not can_access('config_permissions', user):
        return HTMLResponse("Bạn không có quyền truy cập trang này!", status_code=status.HTTP_403_FORBIDDEN)
        
    # 2. Lấy danh sách phân quyền từ DB qua supabase_admin
    res = supabase_admin.table("page_permissions").select("*").order("category").execute()
    
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
async def save_permissions(
    payload: List[PermissionUpdateItem],
    user: dict = Depends(require_login)
):
    # 1. Kiểm tra quyền thực thi API
    if not can_access('config_permissions', user):
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN, 
            content={"success": False, "message": "⛔ Bạn không có quyền thực hiện thao tác này!"}
        )
        
    try:
        if not payload:
            return {"success": True, "message": "Không có dữ liệu thay đổi."}

        user_role = str(user.get("role") or "").strip().lower()
        user_level = ROLE_LEVELS.get(user_role, 1)

        # 2. Lấy dữ liệu phân quyền hiện tại từ DB để bảo vệ cấp bậc
        current_db_res = supabase_admin.table("page_permissions").select("id, allowed_roles").execute()
        db_perm_map = {
            str(item["id"]): [str(r).strip() for r in (item.get("allowed_roles") or [])]
            for item in (current_db_res.data or [])
        }

        cleaned_updates = []
        for item in payload:
            item_id = str(item.id)
            old_roles = db_perm_map.get(item_id, [])
            old_roles_lower = {r.lower() for r in old_roles}
            
            new_roles_map = {r.strip().lower(): r.strip() for r in item.allowed_roles}
            final_roles_set = set()

            # Duyệt danh sách các vai trò để kiểm tra thẩm quyền chỉnh sửa
            for role_item in ROLES_LIST:
                r_lower = role_item.lower()
                r_level = ROLE_LEVELS.get(r_lower, 1)

                # BẢO VỆ CHỐNG LEO THANG QUYỀN HẠN:
                # Nếu role có level >= level của người thao tác -> GIỮ NGUYÊN trạng thái cũ từ CSDL
                if r_level >= user_level:
                    if r_lower in old_roles_lower:
                        matched_old = next((r for r in old_roles if r.lower() == r_lower), role_item)
                        final_roles_set.add(matched_old)
                else:
                    # Nếu role có level < level của người thao tác -> CHẤP NHẬN thay đổi từ payload
                    if r_lower in new_roles_map:
                        final_roles_set.add(new_roles_map[r_lower])

            # Mặc định System Admin luôn luôn có toàn quyền
            final_roles_set.add("System Admin")

            cleaned_updates.append({
                "id": item.id,
                "allowed_roles": list(final_roles_set)
            })

        # 3. Tối ưu: Cập nhật hàng loạt (Bulk Upsert) trong 1 request duy nhất
        supabase_admin.table("page_permissions").upsert(cleaned_updates).execute()
        
        # 4. Ép buộc làm mới bộ nhớ đệm RAM ngay lập tức
        load_permissions_cache(force=True)
        
        return {"success": True, "message": "Đã lưu cấu hình phân quyền mới thành công!"}
        
    except Exception as e:
        logger.error(f"SAVE PERMISSION ERR: {e}", exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            content={"success": False, "message": f"Lỗi hệ thống khi lưu phân quyền: {str(e)}"}
        )