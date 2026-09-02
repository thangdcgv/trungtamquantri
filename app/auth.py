import logging
from pathlib import Path
from typing import Optional, Dict, Any, Tuple

from fastapi import APIRouter, Request, Form, status, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from config import supabase

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Auth"])

# --- CẤU HÌNH THƯ MỤC TEMPLATES DÙNG CHUNG ---
BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = (
    BASE_DIR.parent / "templates"
    if (BASE_DIR.parent / "templates").exists()
    else BASE_DIR / "templates"
)
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# =========================================================
# HELPER FUNCTIONS & DEPENDENCIES
# =========================================================

def render_template(
    request: Request,
    name: str,
    context: Optional[dict] = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    """Helper dùng chung để render Jinja template."""
    context = context or {}
    return templates.TemplateResponse(
        request=request,
        name=name,
        context=context,
        status_code=status_code,
    )


def is_valid_password(password: str) -> Tuple[bool, Optional[str]]:
    """Kiểm tra độ dài và tính hợp lệ cơ bản của mật khẩu."""
    if not password:
        return False, "Mật khẩu không được để trống."
    if len(password) < 6:
        return False, "Mật khẩu phải có ít nhất 6 ký tự."
    return True, None


def extract_user_from_session(request: Request) -> Optional[Dict[str, Any]]:
    """Trích xuất và chuẩn hóa thông tin người dùng từ Cookie Session."""
    user_id = request.session.get("user_id")
    if not user_id:
        return None

    ho_ten = request.session.get("ho_ten") or "Quản trị viên"
    return {
        "auth_id": str(user_id),
        "id": str(user_id),
        "email": request.session.get("user_email") or "",
        "username": request.session.get("username") or "",
        "ho_ten": ho_ten,
        "name": ho_ten,
        "role": str(request.session.get("role") or "User").strip(),
        "access_token": request.session.get("access_token"),
    }


async def require_login(request: Request) -> Dict[str, Any]:
    """Dependency bảo vệ các route API (Trả về JSON Error 401 khi hết session)."""
    user = extract_user_from_session(request)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Phiên làm việc đã hết hạn hoặc bạn chưa đăng nhập.",
        )
    return user


async def get_current_user_or_redirect(request: Request) -> Optional[Dict[str, Any]]:
    """Helper kiểm tra đăng nhập cho các route render giao diện HTML."""
    return extract_user_from_session(request)


# =========================================================
# 1. ĐĂNG NHẬP (LOGIN)
# =========================================================

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Hiển thị trang đăng nhập."""
    if request.session.get("user_id"):
        user_role = str(request.session.get("role") or "User").strip().lower()
        if user_role in ["admin", "super admin", "system admin"]:
            return RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

    return render_template(request, "login.html", {"error": None})


@router.post("/login")
async def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
):
    """Xác thực người dùng bằng Supabase Auth và lưu thông tin vào Session."""
    email_clean = email.strip().lower()

    if not email_clean or not password:
        return render_template(
            request,
            "login.html",
            {"error": "Vui lòng nhập đầy đủ email và mật khẩu."},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    try:
        response = await run_in_threadpool(
            supabase.auth.sign_in_with_password,
            {"email": email_clean, "password": password}
        )

        if not response or not response.user:
            return render_template(
                request,
                "login.html",
                {"error": "Email hoặc mật khẩu không chính xác."},
                status_code=status.HTTP_401_UNAUTHORIZED,
            )

        auth_id = str(response.user.id)

        # Lấy thông tin profile từ bảng quan_tri_vien
        def _fetch_user_profile():
            return (
                supabase.table("quan_tri_vien")
                .select("*")
                .eq("auth_id", auth_id)
                .limit(1)
                .execute()
            )

        user_record = await run_in_threadpool(_fetch_user_profile)

        username = email_clean.split("@")[0]
        ho_ten = "Quản trị viên"
        role = "User"

        if user_record and user_record.data:
            user_info = user_record.data[0]
            username = user_info.get("username") or username
            ho_ten = user_info.get("ho_ten") or user_info.get("name") or ho_ten
            role = str(user_info.get("role") or "User").strip()

        # Khởi tạo lại Session an toàn cho User này
        request.session.clear()
        request.session["user_id"] = auth_id
        request.session["user_email"] = response.user.email or email_clean
        request.session["username"] = username
        request.session["ho_ten"] = ho_ten
        request.session["role"] = role
        
        # Lưu token nếu cần dùng xác thực RLS
        if response.session:
            request.session["access_token"] = response.session.access_token

        role_clean = role.lower()
        redirect_url = "/admin" if role_clean in ["admin", "super admin", "system admin"] else "/"

        return RedirectResponse(url=redirect_url, status_code=status.HTTP_303_SEE_OTHER)

    except Exception as e:
        logger.error(f"LOGIN ERROR: {e}")
        error_msg = str(e).lower()
        
        if any(k in error_msg for k in ["invalid login credentials", "invalid_credentials", "email not confirmed"]):
            friendly_error = "Email hoặc mật khẩu không chính xác."
        else:
            friendly_error = "Không thể đăng nhập lúc này. Vui lòng thử lại sau."

        return render_template(
            request,
            "login.html",
            {"error": friendly_error},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )


# =========================================================
# 2. ĐĂNG XUẤT (LOGOUT)
# =========================================================

@router.get("/logout")
async def logout(request: Request):
    """Đăng xuất tài khoản bằng cách xóa Cookie Session cục bộ."""
    # CHỈ XÓA SESSION CỦA USER NÀY - KHÔNG GỌI supabase.auth.sign_out() TOÀN CỤC
    request.session.clear()
    return RedirectResponse(url="/auth/login", status_code=status.HTTP_303_SEE_OTHER)


# =========================================================
# 3. ĐỔI MẬT KHẨU / QUÊN MẬT KHẨU
# =========================================================

@router.get("/change-password", response_class=HTMLResponse)
async def change_password_page(request: Request):
    if not request.session.get("user_id"):
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_303_SEE_OTHER)

    return render_template(request, "change_password.html", {"error": None, "success": None})


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
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_303_SEE_OTHER)

    if not current_password:
        return render_template(
            request,
            "change_password.html",
            {"error": "Vui lòng nhập mật khẩu hiện tại.", "success": None},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    valid, password_error = is_valid_password(new_password)
    if not valid:
        return render_template(
            request,
            "change_password.html",
            {"error": password_error, "success": None},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if current_password == new_password:
        return render_template(
            request,
            "change_password.html",
            {"error": "Mật khẩu mới không được giống mật khẩu hiện tại.", "success": None},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    try:
        test_login = await run_in_threadpool(
            supabase.auth.sign_in_with_password,
            {"email": email, "password": current_password}
        )
        if not test_login or not test_login.user or str(test_login.user.id) != str(user_id):
            return render_template(
                request,
                "change_password.html",
                {"error": "Mật khẩu hiện tại không chính xác.", "success": None},
                status_code=status.HTTP_401_UNAUTHORIZED,
            )

        await run_in_threadpool(
            supabase.auth.update_user,
            {"password": new_password}
        )

        return render_template(
            request,
            "change_password.html",
            {"error": None, "success": "Đổi mật khẩu thành công!"},
        )

    except Exception as e:
        logger.error(f"CHANGE PASSWORD ERROR: {e}")
        return render_template(
            request,
            "change_password.html",
            {"error": "Không thể đổi mật khẩu lúc này. Vui lòng thử lại.", "success": None},
            status_code=status.HTTP_400_BAD_REQUEST,
        )


@router.get("/forgot-password", response_class=HTMLResponse)
async def forgot_password_page(request: Request):
    return render_template(request, "forgot_password.html", {"message": None, "error": None})


@router.post("/forgot-password")
async def forgot_password(request: Request, email: str = Form(...)):
    email_clean = email.strip().lower()

    if not email_clean:
        return render_template(
            request,
            "forgot_password.html",
            {"message": None, "error": "Vui lòng nhập email."},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    message = (
        "Nếu email tồn tại trong hệ thống, hướng dẫn khôi phục "
        "mật khẩu đã được gửi. Vui lòng kiểm tra hộp thư."
    )

    try:
        base_url = str(request.base_url).rstrip("/")
        redirect_link = f"{base_url}/auth/update-password"

        await run_in_threadpool(
            supabase.auth.reset_password_for_email,
            email_clean,
            {"redirect_to": redirect_link}
        )
    except Exception as e:
        logger.error(f"FORGOT PASSWORD ERROR: {e}")

    return render_template(request, "forgot_password.html", {"message": message, "error": None})


@router.get("/update-password", response_class=HTMLResponse)
async def update_password_page(request: Request):
    return render_template(request, "update_password.html", {"error": None, "success": None})


@router.post("/update-password")
async def update_password(
    request: Request,
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    access_token: Optional[str] = Form(None),
):
    if new_password != confirm_password:
        return render_template(
            request,
            "update_password.html",
            {"error": "Xác nhận mật khẩu không khớp.", "success": None},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    valid, password_error = is_valid_password(new_password)
    if not valid:
        return render_template(
            request,
            "update_password.html",
            {"error": password_error, "success": None},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    try:
        def _perform_update():
            if access_token:
                supabase.auth.set_session(access_token, "")
            return supabase.auth.update_user({"password": new_password})

        res = await run_in_threadpool(_perform_update)
        
        if not res or not res.user:
            raise ValueError("Cập nhật thất bại hoặc phiên làm việc đã hết hạn.")

        return render_template(
            request,
            "update_password.html",
            {
                "error": None,
                "success": "Đặt lại mật khẩu thành công! Bạn có thể đăng nhập bằng mật khẩu mới.",
            },
        )

    except Exception as e:
        logger.error(f"UPDATE PASSWORD ERROR: {e}")
        return render_template(
            request,
            "update_password.html",
            {
                "error": "Link khôi phục không hợp lệ hoặc đã hết hạn. Vui lòng yêu cầu lại.",
                "success": None,
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )