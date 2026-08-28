import logging
from pathlib import Path
from typing import Dict, Any, Optional

from fastapi import APIRouter, Request, Form, HTTPException, status, Depends
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from config import supabase, supabase_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["Admin"])

# --- CẤU HÌNH THƯ MỤC TEMPLATES DÙNG CHUNG ---
BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = (
    BASE_DIR.parent / "templates"
    if (BASE_DIR.parent / "templates").exists()
    else BASE_DIR / "templates"
)
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Trọng số phân quyền từ cao đến thấp (Chuẩn hóa toàn bộ về chữ thường)
ROLE_RANKS: Dict[str, int] = {
    "user": 1,
    "admin": 2,
    "super admin": 3,
    "system admin": 4
}

ALLOWED_ADMIN_ROLES = ["admin", "system admin", "super admin"]


def normalize_role(role_name: Optional[str]) -> str:
    """Chuẩn hóa tên role về chữ thường để tránh lỗi so sánh."""
    if not role_name:
        return "user"
    return str(role_name).strip().lower()


def can_manage_target_role(current_role: str, target_role: str) -> bool:
    """
    Kiểm tra xem current_role có quyền tác động lên target_role không.
    - System Admin / Super Admin (cấp >= 3): Có thể quản lý người đồng cấp hoặc thấp hơn.
    - Admin (cấp 2): Chỉ được quản lý cấp thấp hơn hẳn (User).
    """
    c_role = normalize_role(current_role)
    t_role = normalize_role(target_role)

    current_rank = ROLE_RANKS.get(c_role, 1)
    target_rank = ROLE_RANKS.get(t_role, 1)

    if current_rank >= 3:
        return current_rank >= target_rank
    return current_rank > target_rank


# ==========================================
# DEPENDENCY KIỂM TRA QUYỀN QUẢN TRỊ
# ==========================================

async def get_current_admin(request: Request) -> Dict[str, Any]:
    """Dependency bắt buộc phải đăng nhập và thuộc các nhóm quản trị."""
    user_id = request.session.get('user_id')
    raw_role = request.session.get('role', 'User')
    role_clean = normalize_role(raw_role)

    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/auth/login"}
        )

    if role_clean not in ALLOWED_ADMIN_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="⛔ Truy cập bị từ chối: Bạn không có quyền quản trị hệ thống!"
        )

    return {
        "auth_id": str(user_id),
        "name": request.session.get('ho_ten', 'Quản trị viên'),
        "role": raw_role,  # Giữ nguyên định dạng gốc hiển thị UI
        "role_clean": role_clean
    }


# ==========================================
# 1. TRANG DASHBOARD
# ==========================================

@router.get("/", response_class=HTMLResponse)
async def admin_dashboard(request: Request, admin: dict = Depends(get_current_admin)):
    """Trang tổng quan quản trị."""
    return templates.TemplateResponse(
        request=request,
        name="admin.html",
        context={"current_user": admin}
    )


# ==========================================
# 2. QUẢN LÝ TÀI KHOẢN (USERS)
# ==========================================

@router.get("/users", response_class=HTMLResponse)
async def list_users(request: Request, admin: dict = Depends(get_current_admin)):
    """Danh sách các tài khoản quản trị/người dùng."""
    users = []
    error_msg = request.session.pop("error_message", None)
    success_msg = request.session.pop("success_message", None)

    try:
        if supabase:
            def _fetch_users():
                return supabase.table('quan_tri_vien').select('*').order('id', desc=True).execute()

            response = await run_in_threadpool(_fetch_users)
            users = response.data if response and response.data else []
    except Exception as e:
        logger.error(f"Error fetching users: {e}")
        error_msg = f"Lỗi tải danh sách tài khoản: {str(e)}"

    return templates.TemplateResponse(
        request=request,
        name="admin_users.html",
        context={
            "users": users,
            "current_user": admin,
            "error_msg": error_msg,
            "success_msg": success_msg
        }
    )


@router.post("/users/add")
async def add_user(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    ho_ten: str = Form(...),
    username: Optional[str] = Form(None),
    role: str = Form("User"),
    chuc_danh: Optional[str] = Form(None),
    so_dien_thoai: Optional[str] = Form(None),
    admin: dict = Depends(get_current_admin)
):
    """Tạo tài khoản mới (Supabase Auth + Database)."""
    if not supabase or not supabase_admin:
        request.session["error_message"] = "Không thể kết nối đến cơ sở dữ liệu."
        return RedirectResponse(url="/admin/users", status_code=status.HTTP_303_SEE_OTHER)

    email_clean = email.strip().lower()
    
    if not email_clean or not ho_ten.strip():
        request.session["error_message"] = "Vui lòng điền đầy đủ Email và Họ tên."
        return RedirectResponse(url="/admin/users", status_code=status.HTTP_303_SEE_OTHER)

    if len(password) < 6:
        request.session["error_message"] = "Mật khẩu phải chứa ít nhất 6 ký tự."
        return RedirectResponse(url="/admin/users", status_code=status.HTTP_303_SEE_OTHER)
    
    # Kiểm tra quyền khởi tạo role mục tiêu
    if not can_manage_target_role(admin["role_clean"], role):
        request.session["error_message"] = f"Tài khoản cấp {admin['role']} không có quyền khởi tạo tài khoản quyền {role}."
        return RedirectResponse(url="/admin/users", status_code=status.HTTP_303_SEE_OTHER)

    auth_id = None
    try:
        # Bước 1: Tạo tài khoản bên Supabase Auth (via Thread Pool)
        def _create_auth_user():
            return supabase_admin.auth.admin.create_user({
                "email": email_clean,
                "password": password,
                "email_confirm": True
            })

        auth_response = await run_in_threadpool(_create_auth_user)

        if not auth_response or not auth_response.user:
            raise Exception("Không thể khởi tạo tài khoản trên hệ thống Auth.")

        auth_id = auth_response.user.id
        final_username = username.strip() if username and username.strip() else email_clean.split('@')[0]

        # Bước 2: Lưu vào bảng quan_tri_vien (via Thread Pool)
        try:
            def _insert_user_profile():
                return supabase.table('quan_tri_vien').insert({
                    "auth_id": auth_id,
                    "email": email_clean,
                    "username": final_username,
                    "ho_ten": ho_ten.strip(),
                    "role": role.strip(),
                    "chuc_danh": chuc_danh.strip() if chuc_danh else None,
                    "so_dien_thoai": so_dien_thoai.strip() if so_dien_thoai else None
                }).execute()

            await run_in_threadpool(_insert_user_profile)
            request.session["success_message"] = f"Thêm tài khoản {email_clean} thành công!"

        except Exception as db_err:
            # ROLLBACK: Nếu insert DB thất bại, xóa user vừa tạo bên Auth (via Thread Pool)
            if auth_id:
                await run_in_threadpool(supabase_admin.auth.admin.delete_user, auth_id)
            raise Exception(f"Lỗi lưu thông tin hồ sơ: {str(db_err)}")

    except Exception as e:
        logger.error(f"ADD USER ERROR: {e}")
        request.session["error_message"] = f"Tạo tài khoản thất bại: {str(e)}"

    return RedirectResponse(url="/admin/users", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/users/edit/{user_id}")
async def edit_user(
    request: Request,
    user_id: int,
    ho_ten: str = Form(...),
    username: Optional[str] = Form(None),
    chuc_danh: Optional[str] = Form(None),
    so_dien_thoai: Optional[str] = Form(None),
    role: str = Form("User"),
    new_password: Optional[str] = Form(None),
    admin: dict = Depends(get_current_admin)
):
    """Cập nhật thông tin tài khoản và mật khẩu."""
    try:
        if supabase:
            # Lấy thông tin user hiện tại
            res = await run_in_threadpool(
                lambda: supabase.table('quan_tri_vien').select('auth_id, role').eq("id", user_id).execute()
            )
            if not res or not res.data:
                raise Exception("Không tìm thấy tài khoản cần chỉnh sửa.")

            target_user = res.data[0]
            target_auth_id = target_user.get('auth_id')
            target_role = target_user.get('role', 'User')

            # Kiểm tra phân quyền thao tác
            if not can_manage_target_role(admin["role_clean"], target_role):
                raise Exception(f"Bạn không có quyền chỉnh sửa tài khoản cấp {target_role}.")

            if not can_manage_target_role(admin["role_clean"], role):
                raise Exception(f"Bạn không thể phân quyền cấp {role}.")

            # Cập nhật mật khẩu bên Supabase Auth
            if new_password:
                if len(new_password) < 6:
                    raise Exception("Mật khẩu mới phải có ít nhất 6 ký tự.")
                await run_in_threadpool(
                    supabase_admin.auth.admin.update_user_by_id,
                    target_auth_id,
                    {"password": new_password}
                )

            # Cập nhật thông tin DB
            update_payload = {
                "ho_ten": ho_ten.strip(),
                "chuc_danh": chuc_danh.strip() if chuc_danh else None,
                "so_dien_thoai": so_dien_thoai.strip() if so_dien_thoai else None,
                "role": role.strip()
            }
            if username and username.strip():
                update_payload["username"] = username.strip()

            await run_in_threadpool(
                lambda: supabase.table('quan_tri_vien').update(update_payload).eq("id", user_id).execute()
            )
            request.session["success_message"] = f"Cập nhật tài khoản #{user_id} thành công!"

    except Exception as e:
        logger.error(f"EDIT USER ERROR: {e}")
        request.session["error_message"] = f"Cập nhật thất bại: {str(e)}"

    return RedirectResponse(url="/admin/users", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/users/update-role/{user_id}")
async def update_user_role(
    request: Request,
    user_id: int,
    role: str = Form(...),
    admin: dict = Depends(get_current_admin)
):
    """Thay đổi vai trò (role) của tài khoản."""
    try:
        if supabase:
            res = await run_in_threadpool(
                lambda: supabase.table('quan_tri_vien').select('role').eq("id", user_id).execute()
            )
            if not res or not res.data:
                raise Exception("Không tìm thấy tài khoản cần đổi quyền.")

            target_role = res.data[0].get('role', 'User')

            if not can_manage_target_role(admin["role_clean"], target_role):
                raise Exception(f"Bạn không có quyền thay đổi thông tin của tài khoản cấp {target_role}.")

            if not can_manage_target_role(admin["role_clean"], role):
                raise Exception(f"Bạn không có quyền nâng/gán tài khoản lên cấp {role}.")

            await run_in_threadpool(
                lambda: supabase.table('quan_tri_vien').update({'role': role.strip()}).eq("id", user_id).execute()
            )
            request.session["success_message"] = f"Đã cập nhật quyền thành {role}!"

    except Exception as e:
        logger.error(f"UPDATE ROLE ERROR: {e}")
        request.session["error_message"] = f"Đổi quyền thất bại: {str(e)}"

    return RedirectResponse(url="/admin/users", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/users/delete/{user_id}")
async def delete_user(
    request: Request,
    user_id: int,
    admin: dict = Depends(get_current_admin)
):
    """Xóa tài khoản khỏi cả Auth và DB (Xóa Auth trước để tránh mồ côi DB)."""
    try:
        if supabase and supabase_admin:
            res = await run_in_threadpool(
                lambda: supabase.table('quan_tri_vien').select('auth_id, email, role').eq("id", user_id).execute()
            )
            if not res or not res.data:
                raise Exception("Tài khoản không tồn tại.")

            target_user = res.data[0]
            target_auth_id = target_user.get('auth_id')
            target_role = target_user.get('role', 'User')

            # Chặn tự xóa chính mình
            if str(target_auth_id) == str(admin["auth_id"]):
                raise Exception("Bạn không thể tự xóa tài khoản quản trị đang đăng nhập!")

            # Kiểm tra phân cấp quyền xóa
            if not can_manage_target_role(admin["role_clean"], target_role):
                raise Exception(f"Bạn không có quyền xóa tài khoản cấp {target_role}.")

            # Xóa Auth trước (via Thread Pool)
            if target_auth_id:
                await run_in_threadpool(supabase_admin.auth.admin.delete_user, target_auth_id)

            # Xóa DB sau (via Thread Pool)
            await run_in_threadpool(
                lambda: supabase.table('quan_tri_vien').delete().eq("id", user_id).execute()
            )

            request.session["success_message"] = "Đã xóa tài khoản thành công."

    except Exception as e:
        logger.error(f"DELETE USER ERROR: {e}")
        request.session["error_message"] = f"Xóa tài khoản thất bại: {str(e)}"

    return RedirectResponse(url="/admin/users", status_code=status.HTTP_303_SEE_OTHER)


# ==========================================
# 3. CẤU HÌNH CHẤM CÔNG (CONFIG)
# ==========================================

@router.get("/config-cham-cong", response_class=HTMLResponse)
async def get_config_cham_cong(request: Request, admin: dict = Depends(get_current_admin)):
    """Trang cấu hình chấm công."""
    config_dict = {}
    try:
        if supabase:
            res = await run_in_threadpool(
                lambda: supabase.table('config_cham_cong').select('*').execute()
            )
            if res and res.data:
                # Chuyển đổi mảng Key-Value thành Dictionary để hiển thị giao diện
                for row in res.data:
                    k = row.get("key_name")
                    v = row.get("value_num")
                    if k:
                        config_dict[k] = v
    except Exception as e:
        logger.error(f"Lỗi tải cấu hình chấm công: {e}")

    return templates.TemplateResponse(
        request=request,
        name="config_cham_cong.html",
        context={"config": config_dict, "current_user": admin}
    )


@router.post("/config-cham-cong/save")
async def save_config_cham_cong(request: Request, admin: dict = Depends(get_current_admin)):
    """Lưu thông tin cấu hình chấm công theo dạng Key-Value Upsert."""
    try:
        form_data = await request.form()
        
        def _save_all_configs():
            for key, value in form_data.items():
                if value is not None and str(value).strip() != "":
                    try:
                        val_num = float(str(value).strip())
                        supabase.table("config_cham_cong").upsert(
                            {"key_name": key, "value_num": val_num},
                            on_conflict="key_name"
                        ).execute()
                    except ValueError:
                        continue

        if supabase:
            await run_in_threadpool(_save_all_configs)

        request.session["success_message"] = "Cập nhật cấu hình chấm công thành công!"
    except Exception as e:
        logger.error(f"SAVE CONFIG ERROR: {e}")
        request.session["error_message"] = f"Lưu cấu hình thất bại: {str(e)}"

    return RedirectResponse(url="/admin/config-cham-cong", status_code=status.HTTP_303_SEE_OTHER)