import logging
import uuid
from pathlib import Path
from typing import Optional, Dict, Any, Tuple

from fastapi import APIRouter, Request, Form, status, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from config import supabase, supabase_admin

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["Auth"])

# =========================================================
# CẤU HÌNH HẰNG SỐ
# =========================================================
APP_CODE = "CENTER"
SUPER_ADMIN_ROLES = {"super admin", "system admin"}

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = (
    BASE_DIR.parent / "templates"
    if (BASE_DIR.parent / "templates").exists()
    else BASE_DIR / "templates"
)
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# === PHÂN LOẠI THIẾT BỊ ===
DEVICE_TYPE_DESKTOP = "desktop"
DEVICE_TYPE_MOBILE = "mobile"
# Tối đa 1 session trên mỗi loại thiết bị
MAX_SESSION_PER_DEVICE_TYPE = 1

# =========================================================
# HELPER FUNCTIONS
# =========================================================
def render_template(
    request: Request,
    name: str,
    context: Optional[dict] = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    context = context or {}
    return templates.TemplateResponse(
        request=request, name=name, context=context, status_code=status_code
    )


def is_valid_password(password: str) -> Tuple[bool, Optional[str]]:
    if not password:
        return False, "Mật khẩu không được để trống."
    if len(password) < 6:
        return False, "Mật khẩu phải có ít nhất 6 ký tự."
    return True, None


def detect_device_type(user_agent: str) -> str:
    """Phát hiện loại thiết bị từ User-Agent. Mặc định là desktop."""
    ua = (user_agent or "").lower()
    mobile_keywords = [
        "android", "iphone", "ipad", "ipod", "mobile",
        "webos", "blackberry", "windows phone", "tablet",
    ]
    if any(k in ua for k in mobile_keywords):
        return DEVICE_TYPE_MOBILE
    return DEVICE_TYPE_DESKTOP


def extract_user_from_session(request: Request) -> Optional[Dict[str, Any]]:
    user_id = request.session.get("user_id")
    session_token = request.session.get("session_token")
    if not user_id or not session_token:
        return None

    ho_ten = request.session.get("ho_ten") or "Quản trị viên"
    role = str(request.session.get("role") or "User").strip()
    dept = request.session.get("department")
    if role.lower() in SUPER_ADMIN_ROLES:
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
        "session_token": session_token,
        "device_type": request.session.get("device_type"),
    }


async def verify_active_session(user_id: str, session_token: str) -> bool:
    """Kiểm tra session_token còn tồn tại trong user_sessions không."""
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
        logger.error(f"VERIFY SESSION ERROR: {e}")
        return True  # Fail-open khi DB tạm thời lỗi


async def require_login(request: Request) -> Dict[str, Any]:
    """Dependency bảo vệ route API → trả 401 JSON khi hết hạn / bị kick."""
    user = extract_user_from_session(request)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Phiên làm việc đã hết hạn hoặc bạn chưa đăng nhập.",
        )

    is_valid = await verify_active_session(user["auth_id"], user["session_token"])
    if not is_valid:
        request.session.clear()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Tài khoản của bạn đã được đăng nhập từ một thiết bị khác cùng loại.",
        )
    return user


async def get_current_user_or_redirect(request: Request) -> Optional[Dict[str, Any]]:
    """Kiểm tra đăng nhập cho route render HTML → trả None để điều hướng về login."""
    user = extract_user_from_session(request)
    if not user:
        return None
    is_valid = await verify_active_session(user["auth_id"], user["session_token"])
    if not is_valid:
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
# 1. ĐĂNG NHẬP (1 desktop + 1 mobile, đá thiết bị cũ cùng loại)
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
async def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
):
    email_clean = email.strip().lower()
    if not email_clean or not password:
        return render_template(
            request, "auth/login.html",
            {"error": "Vui lòng nhập đầy đủ email và mật khẩu."},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    try:
        # 1. Xác thực qua Supabase Auth
        response = await run_in_threadpool(
            supabase.auth.sign_in_with_password,
            {"email": email_clean, "password": password},
        )
        if not response or not response.user:
            return render_template(
                request, "auth/login.html",
                {"error": "Email hoặc mật khẩu không chính xác."},
                status_code=status.HTTP_401_UNAUTHORIZED,
            )
        auth_id = str(response.user.id)

        # 2. Lấy profile từ bảng quan_tri_vien
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
        role = "User"
        department = "KTSC"
        if user_record and user_record.data:
            info = user_record.data[0]
            username = info.get("username") or username
            ho_ten = info.get("ho_ten") or info.get("name") or ho_ten
            role = str(info.get("role") or "User").strip()
            department = (
                "ALL"
                if role.lower() in SUPER_ADMIN_ROLES
                else str(info.get("department") or "KTSC").strip()
            )

        # 3. Thông tin thiết bị & IP
        x_forwarded_for = request.headers.get("x-forwarded-for")
        client_ip = (
            x_forwarded_for.split(",")[0].strip()
            if x_forwarded_for
            else (request.client.host if request.client else "Unknown")
        )
        user_agent = (request.headers.get("user-agent") or "Unknown")[:255]
        device_type = detect_device_type(user_agent)
        new_session_token = str(uuid.uuid4())

        # 4. Đồng bộ DB: XÓA toàn bộ session cũ CÙNG LOẠI thiết bị, rồi thêm session mới.
        #    Chạy gộp trong 1 threadpool để không block event loop.
        def _sync_db_session() -> int:
            # Đếm session cũ cùng loại để log (không phụ thuộc created_at)
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
                    .eq("user_id", auth_id) \
                    .eq("app_code", APP_CODE) \
                    .eq("device_type", device_type) \
                    .execute()
                logger.info(
                    f"Kick {old_count} session {device_type} cũ của user {auth_id}"
                )

            supabase_admin.table("user_sessions").insert({
                "user_id": auth_id,
                "app_code": APP_CODE,
                "session_token": new_session_token,
                "ip_address": client_ip,
                "user_agent": user_agent,
                "device_type": device_type,
            }).execute()
            return old_count

        kicked_count = await run_in_threadpool(_sync_db_session)

        # 5. Ghi cookie session
        request.session.clear()
        request.session["user_id"] = auth_id
        request.session["session_token"] = new_session_token
        request.session["user_email"] = response.user.email or email_clean
        request.session["username"] = username
        request.session["ho_ten"] = ho_ten
        request.session["role"] = role
        request.session["department"] = department
        request.session["device_type"] = device_type
        if response.session:
            request.session["access_token"] = response.session.access_token

        if kicked_count:
            logger.info(
                f"User {auth_id} đăng nhập từ {device_type}, "
                f"{kicked_count} thiết bị cùng loại đã bị đăng xuất."
            )

        return RedirectResponse(
            url=get_redirect_url_by_role(role),
            status_code=status.HTTP_303_SEE_OTHER,
        )

    except Exception as e:
        logger.error(f"LOGIN ERROR: {e}")
        error_msg = str(e).lower()
        if any(
            k in error_msg
            for k in ["invalid login credentials", "invalid_credentials", "email not confirmed"]
        ):
            friendly_error = "Email hoặc mật khẩu không chính xác."
        else:
            friendly_error = "Không thể đăng nhập lúc này. Vui lòng thử lại sau."
        return render_template(
            request, "auth/login.html",
            {"error": friendly_error},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )


# =========================================================
# 2. ĐĂNG XUẤT (chỉ xóa session hiện tại, không ảnh hưởng thiết bị kia)
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
    return RedirectResponse(
        url="/auth/login", status_code=status.HTTP_303_SEE_OTHER
    )


# =========================================================
# 3. ĐỔI MẬT KHẨU / QUÊN MẬT KHẨU (giữ nguyên logic cũ)
# =========================================================
@router.get("/change-password", response_class=HTMLResponse)
async def change_password_page(request: Request):
    if not request.session.get("user_id"):
        return RedirectResponse(
            url="/auth/login", status_code=status.HTTP_303_SEE_OTHER
        )
    return render_template(
        request, "auth/change_password.html", {"error": None, "success": None}
    )


@router.post("/change-password")
async def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
):
    user_id = request.session.get("user_id")
    email = request.session.get("user_email")
    if not user_id or not email:
        request.session.clear()
        return RedirectResponse(
            url="/auth/login", status_code=status.HTTP_303_SEE_OTHER
        )

    if not current_password:
        return render_template(
            request, "auth/change_password.html",
            {"error": "Vui lòng nhập mật khẩu hiện tại.", "success": None},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    valid, password_error = is_valid_password(new_password)
    if not valid:
        return render_template(
            request, "auth/change_password.html",
            {"error": password_error, "success": None},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if current_password == new_password:
        return render_template(
            request, "auth/change_password.html",
            {"error": "Mật khẩu mới không được giống mật khẩu hiện tại.", "success": None},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    try:
        test_login = await run_in_threadpool(
            supabase.auth.sign_in_with_password,
            {"email": email, "password": current_password},
        )
        if (
            not test_login
            or not test_login.user
            or str(test_login.user.id) != str(user_id)
        ):
            return render_template(
                request, "auth/change_password.html",
                {"error": "Mật khẩu hiện tại không chính xác.", "success": None},
                status_code=status.HTTP_401_UNAUTHORIZED,
            )

        await run_in_threadpool(
            supabase_admin.auth.admin.update_user_by_id,
            user_id,
            {"password": new_password},
        )
        return render_template(
            request, "auth/change_password.html",
            {"error": None, "success": "Đổi mật khẩu thành công!"},
        )
    except Exception as e:
        logger.error(f"CHANGE PASSWORD ERROR: {e}")
        return render_template(
            request, "auth/change_password.html",
            {"error": "Không thể đổi mật khẩu lúc này. Vui lòng thử lại.", "success": None},
            status_code=status.HTTP_400_BAD_REQUEST,
        )


@router.get("/forgot-password", response_class=HTMLResponse)
async def forgot_password_page(request: Request):
    return render_template(
        request, "auth/forgot_password.html", {"message": None, "error": None}
    )


@router.post("/forgot-password")
async def forgot_password(request: Request, email: str = Form(...)):
    email_clean = email.strip().lower()
    if not email_clean:
        return render_template(
            request, "auth/forgot_password.html",
            {"message": None, "error": "Vui lòng nhập email."},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    message = (
        "Nếu email tồn tại trong hệ thống, hướng dẫn khôi phục mật khẩu "
        "đã được gửi. Vui lòng kiểm tra hộp thư."
    )
    try:
        base_url = str(request.base_url).rstrip("/")
        await run_in_threadpool(
            supabase.auth.reset_password_for_email,
            email_clean,
            {"redirect_to": f"{base_url}/auth/update-password"},
        )
    except Exception as e:
        logger.error(f"FORGOT PASSWORD ERROR: {e}")

    return render_template(
        request, "auth/forgot_password.html", {"message": message, "error": None}
    )


@router.get("/update-password", response_class=HTMLResponse)
async def update_password_page(request: Request):
    return render_template(
        request, "auth/update_password.html", {"error": None, "success": None}
    )


@router.post("/update-password")
async def update_password(
    request: Request,
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    access_token: Optional[str] = Form(None),
):
    if new_password != confirm_password:
        return render_template(
            request, "auth/update_password.html",
            {"error": "Xác nhận mật khẩu không khớp.", "success": None},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    valid, password_error = is_valid_password(new_password)
    if not valid:
        return render_template(
            request, "auth/update_password.html",
            {"error": password_error, "success": None},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    try:
        if not access_token:
            raise ValueError("Thiếu mã xác thực (access_token).")

        user_res = await run_in_threadpool(
            supabase_admin.auth.get_user, access_token
        )
        if not user_res or not user_res.user:
            raise ValueError("Token không hợp lệ hoặc đã hết hạn.")

        await run_in_threadpool(
            supabase_admin.auth.admin.update_user_by_id,
            str(user_res.user.id),
            {"password": new_password},
        )
        return render_template(
            request, "auth/update_password.html",
            {
                "error": None,
                "success": "Đặt lại mật khẩu thành công! Bạn có thể đăng nhập bằng mật khẩu mới.",
            },
        )
    except Exception as e:
        logger.error(f"UPDATE PASSWORD ERROR: {e}")
        return render_template(
            request, "auth/update_password.html",
            {
                "error": "Link khôi phục không hợp lệ hoặc đã hết hạn. Vui lòng yêu cầu lại.",
                "success": None,
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )