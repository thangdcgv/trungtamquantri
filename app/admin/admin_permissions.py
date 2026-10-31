import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app.auth import require_login
from app.permissions import load_permissions_cache, can_access, ROLE_LEVELS
from app.templates_config import templates
from config import supabase_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/phan-quyen", tags=["Phân quyền"])

# Danh sách 4 vai trò quản trị chuẩn khớp CSDL
ROLES_LIST = ["System Admin", "Super Admin", "Admin", "User"]


# 💡 Schema Pydantic: Bổ sung allowed_departments
class PermissionUpdateItem(BaseModel):
    id: str | int
    allowed_roles: List[str]
    allowed_departments: Optional[List[str]] = ["ALL"]


@router.get("", response_class=HTMLResponse)
async def page_permissions_ui(request: Request, user: dict = Depends(require_login)):
    # 1. Kiểm tra quyền truy cập trang
    if not can_access('config_permissions', user):
        return HTMLResponse("Bạn không có quyền truy cập trang này!", status_code=status.HTTP_403_FORBIDDEN)
        
    try:
        # Truy vấn danh sách phân quyền bất đồng bộ qua threadpool
        res = await run_in_threadpool(
            lambda: supabase_admin.table("page_permissions").select("*").order("category").execute()
        )
        permissions_data = res.data or []
    except Exception as e:
        logger.error(f"Error fetching permissions: {e}")
        permissions_data = []

    return templates.TemplateResponse(
        request=request,
        name="admin/page_permissions.html",
        context={
            "request": request,
            "current_user": user,
            "permissions": permissions_data,
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

        # 2. Lấy đầy đủ thông tin trang từ DB (gồm cả allowed_departments)
        current_db_res = await run_in_threadpool(
            lambda: supabase_admin.table("page_permissions")
                .select("id, page_key, page_name, category, allowed_roles, allowed_departments")
                .execute()
        )
            
        db_perm_map = {
            str(item["id"]).strip().lower(): item
            for item in (current_db_res.data or [])
        }

        cleaned_updates = []
        for item in payload:
            item_id_str = str(item.id).strip().lower()
            db_item = db_perm_map.get(item_id_str)
            
            if not db_item:
                logger.warning(f"⚠️ Không tìm thấy permission ID trong CSDL: {item.id}")
                continue

            # a. Xử lý danh sách Roles & Bảo vệ chống leo thang quyền hạn
            old_roles = [str(r).strip() for r in (db_item.get("allowed_roles") or [])]
            old_roles_lower = {r.lower() for r in old_roles}
            
            new_roles_map = {r.strip().lower(): r.strip() for r in item.allowed_roles}
            final_roles_set = set()

            for role_item in ROLES_LIST:
                r_lower = role_item.lower()
                r_level = ROLE_LEVELS.get(r_lower, 1)

                if r_level >= user_level:
                    if r_lower in old_roles_lower:
                        matched_old = next((r for r in old_roles if r.lower() == r_lower), role_item)
                        final_roles_set.add(matched_old)
                else:
                    if r_lower in new_roles_map:
                        final_roles_set.add(new_roles_map[r_lower])

            # System Admin luôn có toàn quyền
            final_roles_set.add("System Admin")

            # b. Xử lý danh sách Phòng Ban (allowed_departments)
            raw_depts = item.allowed_departments if item.allowed_departments is not None else (db_item.get("allowed_departments") or ["ALL"])
            clean_depts = list({str(d).strip().upper() for d in raw_depts if str(d).strip()})
            if not clean_depts:
                clean_depts = ["ALL"]

            # c. Đóng gói payload cập nhật đầy đủ các cột NOT NULL
            cleaned_updates.append({
                "id": db_item["id"],
                "page_key": db_item["page_key"],
                "page_name": db_item["page_name"],
                "category": db_item.get("category") or "Chung",
                "allowed_roles": list(final_roles_set),
                "allowed_departments": clean_depts
            })

        if cleaned_updates:
            # 3. Thực hiện Bulk Upsert bất đồng bộ
            await run_in_threadpool(
                lambda: supabase_admin.table("page_permissions").upsert(cleaned_updates).execute()
            )
        
        # 4. Ép buộc làm mới bộ nhớ đệm RAM ngay lập tức
        load_permissions_cache(force=True)
        
        return {"success": True, "message": "Đã lưu cấu hình phân quyền mới thành công!"}
        
    except Exception as e:
        logger.error(f"SAVE PERMISSION ERR: {e}", exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"success": False, "message": f"Lỗi hệ thống khi lưu phân quyền: {str(e)}"}
        )