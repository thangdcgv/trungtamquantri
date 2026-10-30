import logging
import uuid
from pathlib import Path
from typing import Optional, Dict, Any, Tuple
from fastapi import APIRouter, Request, Form, status, HTTPException, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool
from config import supabase, supabase_admin
from app.templates_config import templates

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["Auth"])

# =========================================================
# CẤU HÌNH HẰNG SỐ
# =========================================================
APP_CODE = "CENTER"
SUPER_ADMIN_ROLES = {"super admin", "system admin"}
MIN_PASSWORD_LENGTH = 8  # ✅ Nâng từ 6 lên 8

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = (
    BASE_DIR.parent / "templates"
    if (BASE_DIR.parent / "templates").exists()
    else BASE_DIR / "templates"
)

DEVICE_TYPE_DESKTOP = "desktop"
DEVICE_TYPE_MOBILE = "mobile"


# =========================================================
# HELPER FUNCTIONS
# =========================================================
def render_template(request, name, context=None, status_code=200):
    return templates.TemplateResponse(
        request=request, name=name, context=context or {}, status_code=status_code
    )


def is_valid_password(password: str) -> Tuple[bool, Optional[str]]:
    if not password:
        return False, "Mật khẩu không được để trống."
    if len(password) < MIN_PASSWORD_LENGTH:
        return False, f"Mật khẩu phải có ít nhất {MIN_PASSWORD_LENGTH} ký tự."
    return True, None


def detect_device_type(user_agent: str) -> str:
    ua = (user_agent or "").lower()
    mobile_keywords = ["android", "iphone", "ipad", "ipod", "mobile", "webos", "blackberry", "windows phone", "tablet"]
    return DEVICE_TYPE_MOBILE if any(k in ua for k in mobile_keywords) else DEVICE_TYPE_DESKTOP


def _is_auth_error(e: Exception) -> bool:
    """Phân biệt lỗi xác thực thật vs lỗi hệ thống/DB."""
    msg = str(e).lower()
    auth_keywords = [
        "invalid login", "invalid credentials", "invalid_credentials",
        "email not confirmed", "user not found", "invalid password",
        "wrong password", "email not found", "user already registered",
    ]
    return any(k in msg for k in auth_keywords)

async def refresh_supabase_token_if_needed(request: Request) -> Optional[str]:
    """Kiểm tra và tự động gia hạn Access Token bằng Refresh Token nếu đã hết hạn."""
    access_token = request.session.get("access_token")
    refresh_token = request.session.get("refresh_token")

    if not access_token or not refresh_token:
        return access_token

    def _check_and_refresh():
        try:
            # 1. Thử xác thực access_token hiện tại với Supabase
            user_res = supabase.auth.get_user(access_token)
            if user_res and user_res.user:
                return access_token
        except Exception:
            # 2. Nếu access_token hết hạn, thử dùng refresh_token để lấy cặp token mới
            try:
                res = supabase.auth.refresh_session(refresh_token)
                if res and res.session:
                    # Cập nhật token mới vào session cookie
                    request.session["access_token"] = res.session.access_token
                    request.session["refresh_token"] = res.session.refresh_token
                    logger.info("✅ Auto-refreshed Supabase Access Token successfully!")
                    return res.session.access_token
            except Exception as refresh_err:
                logger.error(f"❌ Refresh Token failed/expired: {refresh_err}")
        return None

    return await run_in_threadpool(_check_and_refresh)

def extract_user_from_session(request: Request) -> Optional[Dict[str, Any]]:
    user_id = request.session.get("user_id")
    session_token = request.session.get("session_token")
    if not user_id or not session_token:
        return None

    ho_ten = request.session.get("ho_ten") or "Quản trị viên"
    role = str(request.session.get("role") or "user").strip().lower()
    dept = request.session.get("department")
    if role in SUPER_ADMIN_ROLES:
        dept = "ALL"
    else:
        dept = dept or "KTSC"

    return {
        "auth_id": str(user_id),
        "id": str(user_id),
        "email": request.session.get("user_email") or "",
        "username": request.session.get("username") or "",
        "ho_ten": ho_ten,
        "name": ho_ten,
        "role": role,
        "department": dept,
        "access_token": request.session.get("access_token"),
        "refresh_token": request.session.get("refresh_token"), # 👈 Thêm vào dictionary trả về
        "session_token": session_token,
        "device_type": request.session.get("device_type"),
    }


async def verify_active_session(user_id: str, session_token: str) -> bool:
    """Kiểm tra session còn hiệu lực. ✅ Fail-closed: DB lỗi → từ chối."""
    def _check():
        res = (
            supabase_admin.table("user_sessions")
            .select("id")
            .eq("user_id", user_id)
            .eq("app_code", APP_CODE)
            .eq("session_token", session_token)
            .limit(1)
            .execute()
        )
        return bool(res and res.data)
    try:
        return await run_in_threadpool(_check)
    except Exception as e:
        logger.error(f"VERIFY SESSION DB ERROR: {e}")
        return False  # ✅ Fail-closed — KHÔNG còn return True


async def require_login(request: Request) -> Dict[str, Any]:
    """Dependency cho API JSON → trả 401."""
    user = extract_user_from_session(request)
    if not user:
        raise HTTPException(status_code=401, detail="Chưa đăng nhập hoặc phiên đã hết hạn.")
    
    # 💡 Kiểm tra & Tự động gia hạn Supabase Access Token ngầm
    await refresh_supabase_token_if_needed(request)

    if not await verify_active_session(user["auth_id"], user["session_token"]):
        request.session.clear()
        raise HTTPException(status_code=401, detail="Tài khoản đã được đăng nhập từ thiết bị khác cùng loại.")
    return user


# ✅ Thêm alias cho dễ import
get_current_user = require_login


async def get_current_user_or_redirect(request: Request) -> Optional[Dict[str, Any]]:
    """Dependency cho trang HTML → trả None, route tự redirect."""
    user = extract_user_from_session(request)
    if not user:
        return None
    if not await verify_active_session(user["auth_id"], user["session_token"]):
        request.session.clear()
        return None
    return user


def get_redirect_url_by_role(role: str) -> str:
    role_clean = str(role or "user").strip().lower()
    if role_clean == "admin":
        return "/cham-cong"
    if role_clean in SUPER_ADMIN_ROLES:
        return "/admin"
    return "/"


# =========================================================
# 1. ĐĂNG NHẬP
# =========================================================
@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if request.session.get("user_id"):
        return RedirectResponse(
            url=get_redirect_url_by_role(request.session.get("role")),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return render_template(request, "auth/login.html", {"error": None})


@router.post("/login")
async def login(request: Request, email: str = Form(...), password: str = Form(...)):
    email_clean = email.strip().lower()
    if not email_clean or not password:
        return render_template(request, "auth/login.html", {"error": "Vui lòng nhập đầy đủ email và mật khẩu."}, status_code=400)

    # BƯỚC 1: Xác thực Supabase — tách riêng để phân biệt lỗi
    try:
        response = await run_in_threadpool(
            supabase.auth.sign_in_with_password,
            {"email": email_clean, "password": password},
        )
    except Exception as e:
        if _is_auth_error(e):
            logger.info(f"LOGIN FAILED (sai thông tin): {email_clean} — {e}")
            return render_template(request, "auth/login.html", {"error": "Email hoặc mật khẩu không chính xác."}, status_code=401)
        logger.error(f"LOGIN SYSTEM ERROR (Supabase): {e}", exc_info=True)
        return render_template(request, "auth/login.html", {"error": "Hệ thống tạm thời gián đoạn, vui lòng thử lại sau."}, status_code=503)

    if not response or not response.user:
        return render_template(request, "auth/login.html", {"error": "Email hoặc mật khẩu không chính xác."}, status_code=401)

    auth_id = str(response.user.id)

    # BƯỚC 2: Lấy profile + đồng bộ session — lỗi ở đây KHÔNG phải sai mật khẩu
    try:
        def _fetch_profile():
            return (
                supabase_admin.table("quan_tri_vien")
                .select("*")
                .eq("auth_id", auth_id)
                .limit(1)
                .execute()
            )
        user_record = await run_in_threadpool(_fetch_profile)

        username = email_clean.split("@")[0]
        ho_ten = "Quản trị viên"
        role = "user"
        department = "KTSC"
        if user_record and user_record.data:
            info = user_record.data[0]
            username = info.get("username") or username
            ho_ten = info.get("ho_ten") or info.get("name") or ho_ten
            role = str(info.get("role") or "user").strip().lower()  # ✅ Chuẩn hóa lower
            department = "ALL" if role in SUPER_ADMIN_ROLES else str(info.get("department") or "KTSC").strip()

        xff = request.headers.get("x-forwarded-for")
        client_ip = xff.split(",")[0].strip() if xff else (request.client.host if request.client else "Unknown")
        user_agent = (request.headers.get("user-agent") or "Unknown")[:255]
        device_type = detect_device_type(user_agent)
        new_session_token = str(uuid.uuid4())

        def _sync_db_session() -> int:
            old = (
                supabase_admin.table("user_sessions")
                .select("session_token")
                .eq("user_id", auth_id)
                .eq("app_code", APP_CODE)
                .eq("device_type", device_type)
                .execute()
            )
            old_count = len(old.data) if old and old.data else 0
            if old_count:
                supabase_admin.table("user_sessions").delete() \
                    .eq("user_id", auth_id).eq("app_code", APP_CODE) \
                    .eq("device_type", device_type).execute()
                logger.info(f"Kick {old_count} session {device_type} cũ của user {auth_id}")
            supabase_admin.table("user_sessions").insert({
                "user_id": auth_id, "app_code": APP_CODE,
                "session_token": new_session_token,
                "ip_address": client_ip, "user_agent": user_agent,
                "device_type": device_type,
            }).execute()
            return old_count

        kicked_count = await run_in_threadpool(_sync_db_session)

    except Exception as e:
        # Lỗi DB sau khi xác thực thành công → báo hệ thống, KHÔNG báo sai mật khẩu
        logger.error(f"LOGIN SYSTEM ERROR (profile/session): {e}", exc_info=True)
        return render_template(request, "auth/login.html", {"error": "Hệ thống tạm thời gián đoạn, vui lòng thử lại sau."}, status_code=503)

    # BƯỚC 3: Lưu session (Đã bổ sung refresh_token)
    request.session.clear()
    request.session.update({
        "user_id": auth_id,
        "session_token": new_session_token,
        "user_email": response.user.email or email_clean,
        "username": username,
        "ho_ten": ho_ten,
        "role": role,
        "department": department,
        "device_type": device_type,
        "access_token": response.session.access_token if response.session else None,
        "refresh_token": response.session.refresh_token if response.session else None,  # 👈 BỔ SUNG DÒNG NÀY
    })

    if kicked_count:
        logger.info(f"User {auth_id} đăng nhập từ {device_type}, {kicked_count} thiết bị cùng loại bị đăng xuất.")

    return RedirectResponse(url=get_redirect_url_by_role(role), status_code=status.HTTP_303_SEE_OTHER)


# =========================================================
# 2. ĐĂNG XUẤT
# =========================================================
@router.get("/logout")
async def logout(request: Request):
    user_id = request.session.get("user_id")
    session_token = request.session.get("session_token")
    if user_id and session_token:
        def _remove():
            return (
                supabase_admin.table("user_sessions")
                .delete()
                .eq("user_id", user_id)
                .eq("app_code", APP_CODE)
                .eq("session_token", session_token)
                .execute()
            )
        try:
            await run_in_threadpool(_remove)
        except Exception as e:
            logger.error(f"LOGOUT DB ERROR: {e}")
    request.session.clear()
    return RedirectResponse(url="/auth/login", status_code=status.HTTP_303_SEE_OTHER)


# =========================================================
# 3. ĐỔI MẬT KHẨU — ✅ Kiểm tra phiên bằng Depends
# =========================================================
@router.get("/change-password", response_class=HTMLResponse)
async def change_password_page(request: Request, user=Depends(get_current_user_or_redirect)):
    if not user:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_303_SEE_OTHER)
    return render_template(request, "auth/change_password.html", {"error": None, "success": None})


@router.post("/change-password")
async def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    user=Depends(get_current_user_or_redirect),
):
    if not user:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_303_SEE_OTHER)

    if not current_password:
        return render_template(request, "auth/change_password.html", {"error": "Vui lòng nhập mật khẩu hiện tại.", "success": None}, status_code=400)

    valid, password_error = is_valid_password(new_password)
    if not valid:
        return render_template(request, "auth/change_password.html", {"error": password_error, "success": None}, status_code=400)

    if current_password == new_password:
        return render_template(request, "auth/change_password.html", {"error": "Mật khẩu mới không được giống mật khẩu hiện tại.", "success": None}, status_code=400)

    try:
        test_login = await run_in_threadpool(
            supabase.auth.sign_in_with_password,
            {"email": user["email"], "password": current_password},
        )
        if not test_login or not test_login.user or str(test_login.user.id) != str(user["auth_id"]):
            return render_template(request, "auth/change_password.html", {"error": "Mật khẩu hiện tại không chính xác.", "success": None}, status_code=401)

        await run_in_threadpool(
            supabase_admin.auth.admin.update_user_by_id,
            user["auth_id"],
            {"password": new_password},
        )
        return render_template(request, "auth/change_password.html", {"error": None, "success": "Đổi mật khẩu thành công!"})
    except Exception as e:
        if _is_auth_error(e):
            return render_template(request, "auth/change_password.html", {"error": "Mật khẩu hiện tại không chính xác.", "success": None}, status_code=401)
        logger.error(f"CHANGE PASSWORD SYSTEM ERROR: {e}", exc_info=True)
        return render_template(request, "auth/change_password.html", {"error": "Hệ thống tạm thời gián đoạn, vui lòng thử lại.", "success": None}, status_code=503)


# =========================================================
# 4. QUÊN & ĐẶT LẠI MẬT KHẨU
# =========================================================
@router.get("/forgot-password", response_class=HTMLResponse)
async def forgot_password_page(request: Request):
    return render_template(request, "auth/forgot_password.html", {"message": None, "error": None})


@router.post("/forgot-password")
async def forgot_password(request: Request, email: str = Form(...)):
    email_clean = email.strip().lower()
    if not email_clean:
        return render_template(request, "auth/forgot_password.html", {"message": None, "error": "Vui lòng nhập email."}, status_code=400)
    message = "Nếu email tồn tại, hướng dẫn khôi phục đã được gửi. Vui lòng kiểm tra hộp thư."
    try:
        base_url = str(request.base_url).rstrip("/")
        await run_in_threadpool(
            supabase.auth.reset_password_for_email,
            email_clean,
            {"redirect_to": f"{base_url}/auth/update-password"},
        )
    except Exception as e:
        logger.error(f"FORGOT PASSWORD ERROR: {e}")
    return render_template(request, "auth/forgot_password.html", {"message": message, "error": None})


@router.get("/update-password", response_class=HTMLResponse)
async def update_password_page(request: Request):
    return render_template(request, "auth/update_password.html", {"error": None, "success": None})


@router.post("/update-password")
async def update_password(
    request: Request,
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    access_token: Optional[str] = Form(None),
):
    if new_password != confirm_password:
        return render_template(request, "auth/update_password.html", {"error": "Xác nhận mật khẩu không khớp.", "success": None}, status_code=400)
    valid, password_error = is_valid_password(new_password)
    if not valid:
        return render_template(request, "auth/update_password.html", {"error": password_error, "success": None}, status_code=400)

    token = access_token or request.query_params.get("access_token")
    if not token:
        return render_template(request, "auth/update_password.html", {"error": "Thiếu mã xác thực (access_token).", "success": None}, status_code=400)

    try:
        user_res = await run_in_threadpool(supabase_admin.auth.get_user, token)
        if not user_res or not user_res.user:
            raise ValueError("Token không hợp lệ")
        await run_in_threadpool(
            supabase_admin.auth.admin.update_user_by_id,
            str(user_res.user.id),
            {"password": new_password},
        )
        return render_template(request, "auth/update_password.html", {"error": None, "success": "Đặt lại mật khẩu thành công! Bạn có thể đăng nhập bằng mật khẩu mới."})
    except Exception as e:
        logger.error(f"UPDATE PASSWORD ERROR: {e}")
        return render_template(request, "auth/update_password.html", {"error": "Link khôi phục không hợp lệ hoặc đã hết hạn.", "success": None}, status_code=400)