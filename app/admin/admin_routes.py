import logging
import zoneinfo
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

from app.auth import require_login
from app.permissions import can_access
from app.templates_config import templates
from config import supabase, supabase_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["Admin"])

# ==========================================
# HẰNG SỐ & CẤU HÌNH ROLE / PHÒNG BAN
# ==========================================
ROLE_RANKS: Dict[str, int] = {
    "user": 1,
    "admin": 2,
    "super admin": 3,
    "system admin": 4
}

ALLOWED_ADMIN_ROLES = ["admin", "system admin", "super admin"]
DEPARTMENTS = ["KTSC", "KTLD", "KD", "CSKH", "KT", "GN", "ALL"]
VALID_DEPARTMENTS = {'KTSC', 'KTLD', 'KD', 'CSKH', 'KT', 'GN', 'ALL'}


# ==========================================
# JINJA FILTER FORMAT NGÀY GIỜ VIỆT NAM
# ==========================================
def format_vn_time(value, fmt="%d/%m/%Y %H:%M:%S"):
    if not value:
        return "N/A"
    try:
        if isinstance(value, str):
            value = value.replace("Z", "+00:00")
            value = datetime.fromisoformat(value)
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=zoneinfo.ZoneInfo("UTC"))
            vn_dt = value.astimezone(zoneinfo.ZoneInfo("Asia/Ho_Chi_Minh"))
            return vn_dt.strftime(fmt)
    except Exception:
        pass
    return str(value)

templates.env.filters["vn_time"] = format_vn_time


# ==========================================
# HÀM BỔ TRỢ & DEPENDENCIES
# ==========================================
def normalize_role(role_name: Optional[str]) -> str:
    if not role_name:
        return "user"
    return str(role_name).strip().lower()


def normalize_dept(dept_name: Optional[str]) -> str:
    if not dept_name or not str(dept_name).strip():
        return "KTSC"
    cleaned = str(dept_name).strip().upper()
    if cleaned in VALID_DEPARTMENTS:
        return cleaned
    mapping = {
        'GIAO NHAN': 'GN', 'GIAO NHẬN': 'GN', 'GN': 'GN',
        'KE TOAN': 'KT', 'KẾ TOÁN': 'KT', 'KT': 'KT',
        'TAT CA': 'ALL', 'TOAN BO': 'ALL', 'TẤT CẢ': 'ALL', 'TOÀN BỘ': 'ALL'
    }
    return mapping.get(cleaned, "KTSC")


def can_manage_target_role(current_role: str, target_role: str) -> bool:
    c_role = normalize_role(current_role)
    t_role = normalize_role(target_role)
    current_rank = ROLE_RANKS.get(c_role, 1)
    target_rank = ROLE_RANKS.get(t_role, 1)
    if current_rank >= 3:
        return current_rank >= target_rank
    return current_rank > target_rank


async def get_current_admin(request: Request) -> Dict[str, Any]:
    user_id = request.session.get('user_id')
    raw_role = request.session.get('role', 'User')
    raw_dept = request.session.get('department', 'KTSC')
    
    role_clean = normalize_role(raw_role)
    
    if role_clean in ["super admin", "system admin"]:
        dept_clean = "ALL"
    else:
        dept_clean = normalize_dept(raw_dept)

    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Chưa đăng nhập!")
    if role_clean not in ALLOWED_ADMIN_ROLES:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="⛔ Truy cập bị từ chối!")

    return {
        "auth_id": str(user_id),
        "name": request.session.get('ho_ten', 'Quản trị viên'),
        "role": raw_role,
        "role_clean": role_clean,
        "department": dept_clean
    }


# ==========================================
# 1. DASHBOARD CHÍNH
# ==========================================
@router.get("/", response_class=HTMLResponse)
async def admin_dashboard(request: Request, admin: dict = Depends(get_current_admin)):
    return templates.TemplateResponse(
        request=request,
        name="admin/admin.html",
        context={"request": request, "current_user": admin}
    )


# ==========================================
# 2. QUẢN LÝ TÀI KHOẢN (USERS)
# ==========================================
@router.get("/users", response_class=HTMLResponse)
async def list_users(request: Request, admin: dict = Depends(get_current_admin)):
    if not can_access('admin_users', admin):
        return HTMLResponse("<h2>⚠️ Bạn không có quyền truy cập Quản Lý Tài Khoản!</h2>", status_code=403)

    users = []
    error_msg = request.session.pop("error_message", None)
    success_msg = request.session.pop("success_message", None)
    current_dept = admin["department"]

    try:
        if supabase_admin:
            def _fetch_users():
                q = supabase_admin.table('quan_tri_vien').select('*')
                if current_dept != "ALL":
                    q = q.eq("department", current_dept)
                return q.order('ho_ten').execute()
            response = await run_in_threadpool(_fetch_users)
            users = response.data if response and response.data else []
    except Exception as e:
        logger.error(f"Error fetching users: {e}")
        error_msg = f"Lỗi tải danh sách: {str(e)}"

    return templates.TemplateResponse(
        request=request,
        name="admin/admin_users.html",
        context={
            "request": request,
            "users": users,
            "current_user": admin,
            "current_dept": current_dept,
            "departments": DEPARTMENTS,
            "error_msg": error_msg,
            "success_msg": success_msg
        }
    )


@router.post("/users/add")
async def add_user(
    request: Request,
    ho_ten: str = Form(...),
    username: Optional[str] = Form(None),
    email: str = Form(...),
    password: str = Form(...),
    chuc_danh: Optional[str] = Form(None),
    so_dien_thoai: Optional[str] = Form(None),
    department: str = Form("KTSC"),
    admin: dict = Depends(get_current_admin)
):
    if not can_access('admin_users', admin):
        request.session["error_message"] = "⛔ Bạn không có quyền thêm tài khoản!"
        return RedirectResponse(url="/admin/users", status_code=303)

    try:
        email_clean = email.strip().lower()
        username_clean = username.strip() if username and username.strip() else email_clean.split("@")[0]

        dept_clean = normalize_dept(department)
        if admin["department"] != "ALL" and dept_clean != admin["department"] and admin["role_clean"] == "admin":
            raise Exception("Chỉ được thêm tài khoản trong phòng ban của mình.")

        auth_res = await run_in_threadpool(
            supabase_admin.auth.admin.create_user,
            {
                "email": email_clean,
                "password": password,
                "email_confirm": True,
                "user_metadata": {"ho_ten": ho_ten.strip(), "username": username_clean}
            }
        )
        if not auth_res or not auth_res.user:
            raise Exception("Không thể tạo tài khoản trên Supabase Auth.")
        
        auth_id = auth_res.user.id

        insert_payload = {
            "auth_id": auth_id,
            "username": username_clean,
            "email": email_clean,
            "ho_ten": ho_ten.strip(),
            "chuc_danh": chuc_danh.strip() if chuc_danh else None,
            "so_dien_thoai": so_dien_thoai.strip() if so_dien_thoai else None,
            "department": dept_clean,
            "role": "User"
        }
        await run_in_threadpool(
            lambda: supabase_admin.table('quan_tri_vien').insert(insert_payload).execute()
        )
        request.session["success_message"] = "✅ Đã thêm tài khoản mới thành công!"
    except Exception as e:
        logger.error(f"ADD USER ERR: {e}")
        request.session["error_message"] = f"Lỗi thêm tài khoản: {str(e)}"
    return RedirectResponse(url="/admin/users", status_code=303)


@router.post("/users/edit/{user_id}")
async def edit_user(
    request: Request,
    user_id: str,
    ho_ten: str = Form(...),
    email: Optional[str] = Form(None),
    username: Optional[str] = Form(None),
    role: str = Form("User"),
    department: str = Form("KTSC"),
    chuc_danh: Optional[str] = Form(None),
    so_dien_thoai: Optional[str] = Form(None),
    new_password: Optional[str] = Form(None),
    admin: dict = Depends(get_current_admin)
):
    if not can_access('admin_users', admin):
        request.session["error_message"] = "⛔ Bạn không có quyền chỉnh sửa tài khoản!"
        return RedirectResponse(url="/admin/users", status_code=303)

    try:
        res = await run_in_threadpool(
            lambda: supabase_admin.table('quan_tri_vien')
                .select('auth_id,role,department,email')
                .eq("auth_id", user_id)
                .execute()
        )
        if not res or not res.data:
            raise Exception("Không tìm thấy tài khoản.")
        
        target = res.data[0]
        target_auth_id = target["auth_id"]
        target_role = target["role"]
        target_dept = target.get("department", "KTSC")
        old_email = target.get("email")

        if not can_manage_target_role(admin["role_clean"], target_role):
            raise Exception(f"Không sửa được tài khoản cấp {target_role}.")
        if not can_manage_target_role(admin["role_clean"], role):
            raise Exception(f"Không gán được quyền {role}.")

        dept_clean = normalize_dept(department)
        if dept_clean not in VALID_DEPARTMENTS:
            raise Exception(f"Phòng ban '{department}' không hợp lệ.")

        if dept_clean != target_dept:
            if admin["role_clean"] not in ["system admin", "super admin"]:
                raise Exception("Chỉ Quản trị hệ thống mới được chuyển phòng ban.")

        if new_password:
            if len(new_password) < 6:
                raise Exception("Mật khẩu phải có ít nhất 6 ký tự.")
            await run_in_threadpool(
                supabase_admin.auth.admin.update_user_by_id,
                target_auth_id,
                {"password": new_password}
            )

        email_clean = email.strip().lower() if email else old_email
        if email_clean and email_clean != old_email:
            await run_in_threadpool(
                supabase_admin.auth.admin.update_user_by_id,
                target_auth_id,
                {"email": email_clean}
            )

        update_payload = {
            "ho_ten": ho_ten.strip(),
            "email": email_clean,
            "role": role.strip(),
            "department": dept_clean,
            "chuc_danh": chuc_danh.strip() if chuc_danh else None,
            "so_dien_thoai": so_dien_thoai.strip() if so_dien_thoai else None
        }
        if username and username.strip():
            update_payload["username"] = username.strip()

        await run_in_threadpool(
            lambda: supabase_admin.table('quan_tri_vien')
                .update(update_payload)
                .eq("auth_id", user_id)
                .execute()
        )

        request.session["success_message"] = "✅ Đã cập nhật tài khoản thành công!"

    except Exception as e:
        logger.error(f"EDIT ERR: {e}")
        request.session["error_message"] = str(e)

    return RedirectResponse(url="/admin/users", status_code=303)


@router.post("/users/delete/{user_id}")
async def delete_user(
    request: Request,
    user_id: str,
    admin: dict = Depends(get_current_admin)
):
    if not can_access('admin_users', admin):
        request.session["error_message"] = "⛔ Bạn không có quyền xóa tài khoản!"
        return RedirectResponse(url="/admin/users", status_code=303)

    try:
        res = await run_in_threadpool(
            lambda: supabase_admin.table('quan_tri_vien').select('auth_id,role,department,ho_ten').eq("auth_id", user_id).execute()
        )
        if not res or not res.data:
            raise Exception("Không tồn tại.")
        target = res.data[0]
        target_auth_id = target["auth_id"]
        target_role = target["role"]
        target_dept = normalize_dept(target.get("department"))

        if str(target_auth_id) == str(admin["auth_id"]):
            raise Exception("Không tự xóa chính mình!")
        if not can_manage_target_role(admin["role_clean"], target_role):
            raise Exception("Không đủ quyền xóa.")
        if admin["department"] != "ALL" and target_dept != admin["department"]:
            raise Exception("Chỉ xóa tài khoản trong phòng ban của mình.")

        await run_in_threadpool(
            lambda: supabase_admin.table('quan_tri_vien').delete().eq("auth_id", user_id).execute()
        )
        if target_auth_id:
            await run_in_threadpool(supabase_admin.auth.admin.delete_user, target_auth_id)
        request.session["success_message"] = "Đã xóa!"
    except Exception as e:
        logger.error(f"DELETE ERR: {e}")
        request.session["error_message"] = f"Lỗi: {str(e)}"
    return RedirectResponse(url="/admin/users", status_code=303)


# ==========================================
# 3. CẤU HÌNH CHẤM CÔNG
# ==========================================
@router.get("/config-cham-cong", response_class=HTMLResponse)
async def get_config_cham_cong(request: Request, admin: dict = Depends(get_current_admin)):
    if not can_access('config_cham_cong', admin):
        return HTMLResponse("<h2>⚠️ Bạn không có quyền truy cập Cấu Hình Chấm Công!</h2>", status_code=403)

    config_dict = {}
    try:
        if supabase:
            res = await run_in_threadpool(lambda: supabase.table('config_cham_cong').select('*').execute())
            if res and res.data:
                for row in res.data:
                    k = row.get("key_name")
                    v = row.get("value_num")
                    if k: config_dict[k] = v
    except Exception as e:
        logger.error(f"Lỗi cấu hình chấm công: {e}")

    return templates.TemplateResponse(
        request=request, 
        name="installation/config_cham_cong.html",
        context={"request": request, "config": config_dict, "current_user": admin}
    )


@router.post("/config-cham-cong/save")
async def save_config_cham_cong(request: Request, admin: dict = Depends(get_current_admin)):
    if not can_access('config_cham_cong', admin):
        request.session["error_message"] = "⛔ Bạn không có quyền lưu cấu hình chấm công!"
        return RedirectResponse(url="/admin/config-cham-cong", status_code=303)

    try:
        form_data = await request.form()
        upsert_payload = []
        for key, value in form_data.items():
            if value and str(value).strip():
                try:
                    upsert_payload.append({"key_name": key, "value_num": float(str(value).strip())})
                except ValueError:
                    continue
        if supabase and upsert_payload:
            await run_in_threadpool(lambda: supabase.table("config_cham_cong").upsert(
                upsert_payload, on_conflict="key_name").execute())
        request.session["success_message"] = "Đã lưu cấu hình!"
    except Exception as e:
        logger.error(f"SAVE CONFIG ERR: {e}")
        request.session["error_message"] = f"Lỗi: {str(e)}"
    return RedirectResponse(url="/admin/config-cham-cong", status_code=303)


# ==========================================
# 4. NHẬT KÝ HỆ THỐNG & AUDIT LOGS
# ==========================================
@router.get("/logs", response_class=HTMLResponse)
async def list_system_logs(
    request: Request, level: Optional[str] = None, status_filter: Optional[str] = None, page: int = 1,
    admin: dict = Depends(get_current_admin)
):
    if not can_access('system_logs', admin):
        return HTMLResponse("<h2>⚠️ Bạn không có quyền truy cập Nhật Ký Hệ Thống!</h2>", status_code=403)

    logs = []
    limit = 20
    offset = (page - 1) * limit
    try:
        if supabase:
            def _fetch():
                q = supabase.table('system_logs').select('*')
                if level: q = q.eq('level', level.upper())
                if status_filter: q = q.eq('status', status_filter.upper())
                return q.order('id', desc=True).range(offset, offset+limit-1).execute()
            res = await run_in_threadpool(_fetch)
            logs = res.data if res.data else []
    except Exception as e:
        logger.error(f"LOG ERR: {e}")
    return templates.TemplateResponse(
        request=request, name="admin/admin_logs.html",
        context={"request": request, "logs": logs, "current_user": admin,
                 "current_level": level or "", "current_status": status_filter or "", "page": page}
    )


@router.get("/audit-logs", response_class=HTMLResponse)
async def get_audit_logs(request: Request, current_user: dict = Depends(require_login)):
    if not can_access('audit_logs', current_user):
        return HTMLResponse("<h2>⚠️ Bạn không có quyền truy cập Nhật Ký Kiểm Toán!</h2>", status_code=403)

    res = await run_in_threadpool(
        lambda: supabase.table("audit_logs").select("*").order("created_at", desc=True).limit(100).execute()
    )
    audit_data = res.data if res and res.data else []
    
    return templates.TemplateResponse(
        request=request, name="admin/audit_logs.html",
        context={"request": request, "current_user": current_user, "logs": audit_data}
    )