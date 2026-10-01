import logging
import zoneinfo
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime
from fastapi import APIRouter, Request, Form, HTTPException, status, Depends
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from app.auth import require_login
from starlette.concurrency import run_in_threadpool
from config import supabase, supabase_admin

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["Admin"])

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = (
    BASE_DIR.parent / "templates"
    if (BASE_DIR.parent / "templates").exists()
    else BASE_DIR / "templates"
)
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# === Đã đầy đủ: 4 vai trò + 7 phòng ban ===
ROLE_RANKS: Dict[str, int] = {
    "user": 1,
    "admin": 2,
    "super admin": 3,
    "system admin": 4
}
ALLOWED_ADMIN_ROLES = ["admin", "system admin", "super admin"]

# ✅ Đã bổ sung GN — Giao nhận
DEPARTMENTS = ["KTSC", "KTLD", "KD", "CSKH", "KT", "GN", "ALL"]
#              sửa chữa  lắp đặt kinh doanh chăm sóc kế toán giao nhận toàn công ty

def normalize_role(role_name: Optional[str]) -> str:
    """
    Chuẩn hóa role:
    - Bỏ khoảng trắng → viết thường
    - Tương đương: Super Admin / superadmin / SuperAdmin → cùng so sánh
    """
    if not role_name:
        return "user"
    return str(role_name).strip().lower()

def normalize_dept(dept_name: Optional[str]) -> str:
    """
    Chuẩn hóa phòng ban:
    - Bỏ khoảng trắng thừa → viết HOA → kiểm tra trong danh sách hợp lệ
    - Trả về KTSC mặc định nếu trống / không khớp
    """
    if not dept_name:
        return "KTSC"
    cleaned = str(dept_name).strip().upper()
    return cleaned if cleaned in DEPARTMENTS else "KTSC"

def can_manage_target_role(current_role: str, target_role: str) -> bool:
    c_role = normalize_role(current_role)
    t_role = normalize_role(target_role)
    current_rank = ROLE_RANKS.get(c_role, 1)
    target_rank = ROLE_RANKS.get(t_role, 1)
    if current_rank >= 3:
        return current_rank >= target_rank
    return current_rank > target_rank

def require_roles(allowed_roles: list[str]):
    async def role_checker(current_user: dict = Depends(get_current_admin)):
        user_role = str(current_user.get("role", "")).strip().lower()
        allowed_clean = [r.lower() for r in allowed_roles]
        if user_role in ("super admin", "system admin") or user_role in allowed_clean:
            return current_user
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Bạn không có quyền truy cập chức năng này!"
        )
    return role_checker

async def get_current_admin(request: Request) -> Dict[str, Any]:
    """Dependency kiểm tra đăng nhập + lấy phòng ban từ session."""
    user_id = request.session.get('user_id')
    raw_role = request.session.get('role', 'User')
    raw_dept = request.session.get('department', 'KTSC')
    role_clean = normalize_role(raw_role)
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
# DASHBOARD
# ==========================================
@router.get("/", response_class=HTMLResponse)
async def admin_dashboard(request: Request, admin: dict = Depends(get_current_admin)):
    return templates.TemplateResponse(
        request=request,
        name="admin.html",
        context={"current_user": admin}
    )

# ==========================================
# QUẢN LÝ TÀI KHOẢN
# ==========================================
@router.get("/users", response_class=HTMLResponse)
async def list_users(request: Request, admin: dict = Depends(get_current_admin)):
    """Danh sách tài khoản — chỉ xem phòng mình trừ ALL xem toàn bộ."""
    users = []
    error_msg = request.session.pop("error_message", None)
    success_msg = request.session.pop("success_message", None)
    current_dept = admin["department"]

    try:
        if supabase:
            def _fetch_users():
                q = supabase.table('quan_tri_vien').select('*')
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
        name="admin_users.html",
        context={
            "users": users,
            "current_user": admin,
            "current_dept": current_dept,
            "departments": DEPARTMENTS,  # ✅ Truyền danh sách đầy đủ ra giao diện
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
    department: str = Form("KTSC"),
    chuc_danh: Optional[str] = Form(None),
    so_dien_thoai: Optional[str] = Form(None),
    admin: dict = Depends(get_current_admin)
):
    if not supabase or not supabase_admin:
        request.session["error_message"] = "Không thể kết nối CSDL."
        return RedirectResponse(url="/admin/users", status_code=303)

    email_clean = email.strip().lower()
    dept_clean = normalize_dept(department)

    if not email_clean or not ho_ten.strip():
        request.session["error_message"] = "Điền đầy đủ Email và Họ tên."
        return RedirectResponse(url="/admin/users", status_code=303)
    if len(password) < 6:
        request.session["error_message"] = "Mật khẩu ít nhất 6 ký tự."
        return RedirectResponse(url="/admin/users", status_code=303)
    if not can_manage_target_role(admin["role_clean"], role):
        request.session["error_message"] = "Không đủ quyền tạo tài khoản cấp này."
        return RedirectResponse(url="/admin/users", status_code=303)

    # Chỉ ALL / System / Super Admin được gán phòng ban ALL
    if dept_clean == "ALL":
        if not (admin["department"] == "ALL" or admin["role_clean"] in ["system admin", "super admin"]):
            request.session["error_message"] = "Chỉ quản trị cấp cao mới được gán phòng ban ALL."
            dept_clean = "KTSC"

    auth_id = None
    try:
        def _create_auth_user():
            return supabase_admin.auth.admin.create_user({
                "email": email_clean,
                "password": password,
                "email_confirm": True
            })
        auth_response = await run_in_threadpool(_create_auth_user)
        if not auth_response or not auth_response.user:
            raise Exception("Không tạo được tài khoản Auth.")
        auth_id = auth_response.user.id
        final_username = username.strip() if username and username.strip() else email_clean.split('@')[0]

        def _insert_profile():
            return supabase.table('quan_tri_vien').insert({
                "auth_id": auth_id,
                "email": email_clean,
                "username": final_username,
                "ho_ten": ho_ten.strip(),
                "role": role.strip(),
                "department": dept_clean,
                "chuc_danh": chuc_danh.strip() if chuc_danh else None,
                "so_dien_thoai": so_dien_thoai.strip() if so_dien_thoai else None
            }).execute()
        await run_in_threadpool(_insert_profile)
        request.session["success_message"] = f"Đã tạo {email_clean} — {dept_clean}!"
    except Exception as e:
        if auth_id:
            await run_in_threadpool(supabase_admin.auth.admin.delete_user, auth_id)
        logger.error(f"ADD USER ERR: {e}")
        request.session["error_message"] = f"Thất bại: {str(e)}"
    return RedirectResponse(url="/admin/users", status_code=303)

@router.post("/users/edit/{user_id}")
async def edit_user(
    request: Request,
    user_id: int,
    ho_ten: str = Form(...),
    username: Optional[str] = Form(None),
    role: str = Form("User"),
    department: str = Form("KTSC"),
    chuc_danh: Optional[str] = Form(None),
    so_dien_thoai: Optional[str] = Form(None),
    new_password: Optional[str] = Form(None),
    admin: dict = Depends(get_current_admin)
):
    try:
        res = await run_in_threadpool(
            lambda: supabase.table('quan_tri_vien').select('auth_id,role,department').eq("id", user_id).execute()
        )
        if not res or not res.data:
            raise Exception("Không tìm tài khoản.")
        target = res.data[0]
        target_auth_id = target["auth_id"]
        target_role = target["role"]
        target_dept = target.get("department", "KTSC")

        if not can_manage_target_role(admin["role_clean"], target_role):
            raise Exception(f"Không sửa được tài khoản cấp {target_role}.")
        if not can_manage_target_role(admin["role_clean"], role):
            raise Exception(f"Không gán được quyền {role}.")

        dept_clean = normalize_dept(department)
        if dept_clean != target_dept:
            if not (admin["department"] == "ALL" or admin["role_clean"] in ["system admin", "super admin"]):
                raise Exception("Chỉ quản trị cấp cao mới được chuyển phòng ban.")
            if dept_clean == "ALL" and admin["department"] != "ALL":
                raise Exception("Không gán phòng ban ALL.")

        if new_password:
            if len(new_password) < 6:
                raise Exception("Mật khẩu ≥ 6 ký tự.")
            await run_in_threadpool(
                supabase_admin.auth.admin.update_user_by_id,
                target_auth_id, {"password": new_password}
            )

        update_payload = {
            "ho_ten": ho_ten.strip(),
            "role": role.strip(),
            "department": dept_clean,
            "chuc_danh": chuc_danh.strip() if chuc_danh else None,
            "so_dien_thoai": so_dien_thoai.strip() if so_dien_thoai else None
        }
        if username and username.strip():
            update_payload["username"] = username.strip()

        await run_in_threadpool(
            lambda: supabase.table('quan_tri_vien').update(update_payload).eq("id", user_id).execute()
        )
        request.session["success_message"] = "Đã cập nhật!"
    except Exception as e:
        logger.error(f"EDIT ERR: {e}")
        request.session["error_message"] = f"Lỗi: {str(e)}"
    return RedirectResponse(url="/admin/users", status_code=303)

@router.post("/users/update-role/{user_id}")
async def update_user_role(
    request: Request,
    user_id: int,
    role: str = Form(...),
    department: Optional[str] = Form(None),
    admin: dict = Depends(get_current_admin)
):
    try:
        res = await run_in_threadpool(
            lambda: supabase.table('quan_tri_vien').select('role,department').eq("id", user_id).execute()
        )
        if not res or not res.data:
            raise Exception("Không tìm tài khoản.")
        target = res.data[0]
        if not can_manage_target_role(admin["role_clean"], target["role"]):
            raise Exception("Không đủ quyền.")
        if not can_manage_target_role(admin["role_clean"], role):
            raise Exception("Không gán quyền này.")

        payload: Dict[str, Any] = {"role": role.strip()}
        if department:
            dept_clean = normalize_dept(department)
            if dept_clean != target.get("department"):
                if not (admin["department"] == "ALL" or admin["role_clean"] in ["system admin", "super admin"]):
                    raise Exception("Không chuyển phòng ban.")
                if dept_clean == "ALL" and admin["department"] != "ALL":
                    raise Exception("Không gán ALL.")
            payload["department"] = dept_clean

        await run_in_threadpool(
            lambda: supabase.table('quan_tri_vien').update(payload).eq("id", user_id).execute()
        )
        request.session["success_message"] = "Đã cập nhật!"
    except Exception as e:
        logger.error(f"UPDATE ROLE ERR: {e}")
        request.session["error_message"] = f"Lỗi: {str(e)}"
    return RedirectResponse(url="/admin/users", status_code=303)

@router.post("/users/delete/{user_id}")
async def delete_user(
    request: Request,
    user_id: int,
    admin: dict = Depends(get_current_admin)
):
    try:
        res = await run_in_threadpool(
            lambda: supabase.table('quan_tri_vien').select('auth_id,role,department,ho_ten').eq("id", user_id).execute()
        )
        if not res or not res.data:
            raise Exception("Không tồn tại.")
        target = res.data[0]
        target_auth_id = target["auth_id"]
        target_role = target["role"]
        target_dept = target.get("department", "KTSC")

        if str(target_auth_id) == str(admin["auth_id"]):
            raise Exception("Không tự xóa chính mình!")
        if not can_manage_target_role(admin["role_clean"], target_role):
            raise Exception("Không đủ quyền xóa.")
        if admin["department"] != "ALL" and target_dept != admin["department"]:
            raise Exception("Chỉ xóa tài khoản trong phòng ban của mình.")

        await run_in_threadpool(
            lambda: supabase.table('quan_tri_vien').delete().eq("id", user_id).execute()
        )
        if target_auth_id:
            await run_in_threadpool(supabase_admin.auth.admin.delete_user, target_auth_id)
        request.session["success_message"] = "Đã xóa!"
    except Exception as e:
        logger.error(f"DELETE ERR: {e}")
        request.session["error_message"] = f"Lỗi: {str(e)}"
    return RedirectResponse(url="/admin/users", status_code=303)

# ==========================================
# CÁC MODULE KHÁC — GIỮ NGUYÊN
# ==========================================
@router.get("/config-cham-cong", response_class=HTMLResponse)
async def get_config_cham_cong(request: Request, admin: dict = Depends(get_current_admin)):
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
        request=request, name="config_cham_cong.html",
        context={"config": config_dict, "current_user": admin}
    )

@router.post("/config-cham-cong/save")
async def save_config_cham_cong(request: Request, admin: dict = Depends(get_current_admin)):
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

@router.get("/logs", response_class=HTMLResponse)
async def list_system_logs(
    request: Request, level: Optional[str] = None, status_filter: Optional[str] = None, page: int = 1,
    admin: dict = Depends(get_current_admin)
):
    if admin["role_clean"] not in ["super admin", "system admin"]:
        raise HTTPException(403, "Không truy cập được.")
    logs = []
    limit, offset = 20, (page-1)*limit
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
        request=request, name="admin_logs.html",
        context={"logs": logs, "current_user": admin,
                 "current_level": level or "", "current_status": status_filter or "", "page": page}
    )

@router.post("/logs/resolve/{log_id}")
async def resolve_log(log_id: int, request: Request, admin: dict = Depends(get_current_admin)):
    try:
        await run_in_threadpool(lambda: supabase.table('system_logs').update(
            {'status': 'RESOLVED'}).eq('id', log_id).execute())
        request.session["success_message"] = "Đã đánh dấu!"
    except Exception as e:
        request.session["error_message"] = f"Lỗi: {str(e)}"
    return RedirectResponse(url="/admin/logs", status_code=303)

@router.post("/logs/clear")
async def clear_logs(request: Request, admin: dict = Depends(get_current_admin)):
    try:
        await run_in_threadpool(lambda: supabase.table('system_logs').delete().in_(
            'status', ['RESOLVED', 'IGNORED']).execute())
        request.session["success_message"] = "Đã dọn dẹp!"
    except Exception as e:
        request.session["error_message"] = f"Lỗi: {str(e)}"
    return RedirectResponse(url="/admin/logs", status_code=303)

@router.get("/audit-logs", response_class=HTMLResponse)
async def get_audit_logs(request: Request, current_user: dict = Depends(require_login)):
    if not current_user or current_user.get("role") not in ['Super Admin', 'System Admin']:
        raise HTTPException(403, "Bị từ chối")
    audit_data = supabase.table("audit_logs").select("*").order("created_at", desc=True).limit(100).execute().data or []
    return templates.TemplateResponse(
        request=request, name="audit_logs.html",
        context={"current_user": current_user, "logs": audit_data}
    )

def format_vn_time(value, fmt="%d/%m/%Y %H:%M:%S"):
    if not value: return "N/A"
    try:
        if isinstance(value, str):
            value = value.replace("Z", "+00:00")
            value = datetime.fromisoformat(value)
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=zoneinfo.ZoneInfo("UTC"))
            vn_dt = value.astimezone(zoneinfo.ZoneInfo("Asia/Ho_Chi_Minh"))
            return vn_dt.strftime(fmt)
    except Exception: pass
    return str(value)

templates.env.filters["vn_time"] = format_vn_time