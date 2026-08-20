from fastapi import APIRouter, Request, Form, HTTPException, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from config import supabase


router = APIRouter(prefix="/auth", tags=["Auth"])
templates = Jinja2Templates(directory="app/templates")


# =========================================================
# HELPER FUNCTIONS
# =========================================================

def render_template(
    request: Request,
    name: str,
    context: dict | None = None,
    status_code: int = status.HTTP_200_OK,
):
    """
    Helper dùng chung để render Jinja template.
    """
    context = context or {}

    return templates.TemplateResponse(
        request=request,
        name=name,
        context=context,
        status_code=status_code,
    )


def is_valid_password(password: str) -> tuple[bool, str | None]:
    """
    Kiểm tra mật khẩu cơ bản.
    """
    if not password:
        return False, "Mật khẩu không được để trống."

    if len(password) < 6:
        return False, "Mật khẩu phải có ít nhất 6 ký tự."

    return True, None


# =========================================================
# LOGIN
# =========================================================

@router.get("/login")
async def login_page(request: Request):
    """
    Hiển thị trang đăng nhập.
    """
    # Nếu đã đăng nhập thì không cần quay lại trang login
    if request.session.get("user_id"):
        return RedirectResponse(
            url="/admin",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    return render_template(
        request,
        "login.html",
        {
            "error": None,
        },
    )


@router.post("/login")
async def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
):
    """
    Xác thực người dùng bằng Supabase Auth.
    Sau đó lấy thông tin hồ sơ và role từ bảng quan_tri_vien.
    """

    email = email.strip().lower()
    password = password.strip()

    if not email or not password:
        return render_template(
            request,
            "login.html",
            {
                "error": "Vui lòng nhập đầy đủ email và mật khẩu.",
            },
            status.HTTP_400_BAD_REQUEST,
        )

    try:
        # -------------------------------------------------
        # 1. Đăng nhập Supabase Auth
        # -------------------------------------------------
        response = supabase.auth.sign_in_with_password(
            {
                "email": email,
                "password": password,
            }
        )

        if not response or not response.user:
            return render_template(
                request,
                "login.html",
                {
                    "error": "Email hoặc mật khẩu không chính xác.",
                },
                status.HTTP_401_UNAUTHORIZED,
            )

        auth_id = str(response.user.id)

        # -------------------------------------------------
        # 2. Lấy thông tin từ bảng quan_tri_vien
        # -------------------------------------------------
        user_record = (
            supabase
            .table("quan_tri_vien")
            .select("*")
            .eq("auth_id", auth_id)
            .limit(1)
            .execute()
        )

        # Giá trị mặc định
        username = email.split("@")[0]
        ho_ten = "Quản trị viên"
        role = "User"

        if user_record and user_record.data:
            user_info = user_record.data[0]

            username = (
                user_info.get("username")
                or username
            )

            ho_ten = (
                user_info.get("ho_ten")
                or user_info.get("name")
                or ho_ten
            )

            role = str(
                user_info.get("role") or "User"
            ).strip()

        else:
            print(
                f"WARNING: Không tìm thấy auth_id={auth_id} "
                f"trong bảng quan_tri_vien"
            )

        # -------------------------------------------------
        # 3. Xóa session cũ
        # -------------------------------------------------
        request.session.clear()

        # -------------------------------------------------
        # 4. Lưu session
        # -------------------------------------------------
        request.session["user_id"] = auth_id
        request.session["user_email"] = response.user.email or email
        request.session["username"] = username
        request.session["ho_ten"] = ho_ten
        request.session["role"] = role

        # -------------------------------------------------
        # 5. Điều hướng theo Role
        # -------------------------------------------------
        # Chuẩn hóa role về chữ thường để so sánh chính xác (tránh lỗi hoa/thường)
        role_clean = str(role).strip().lower()

        if role_clean in ["Admin", "Super Admin", "System Admin"]:
            redirect_url = "/admin"
        else:
            redirect_url = "/"  # Trang chủ dành cho tài khoản User thường

        return RedirectResponse(
            url=redirect_url,
            status_code=status.HTTP_303_SEE_OTHER,
        )

    except Exception as e:
        error_msg = str(e).lower()

        print(f"LOGIN ERROR: {e}")

        if (
            "invalid login credentials" in error_msg
            or "invalid_credentials" in error_msg
            or "email not confirmed" in error_msg
        ):
            friendly_error = "Email hoặc mật khẩu không chính xác."
        else:
            friendly_error = (
                "Không thể đăng nhập lúc này. "
                "Vui lòng thử lại sau."
            )

        return render_template(
            request,
            "login.html",
            {
                "error": friendly_error,
            },
            status.HTTP_401_UNAUTHORIZED,
        )


# =========================================================
# LOGOUT
# =========================================================

@router.get("/logout")
async def logout(request: Request):
    """
    Đăng xuất và xóa session local.
    """

    try:
        supabase.auth.sign_out()
    except Exception as e:
        print(f"LOGOUT ERROR: {e}")

    request.session.clear()

    return RedirectResponse(
        url="/auth/login",
        status_code=status.HTTP_303_SEE_OTHER,
    )


# =========================================================
# CHANGE PASSWORD
# =========================================================

@router.get("/change-password")
async def change_password_page(request: Request):
    """
    Hiển thị trang đổi mật khẩu.
    """

    if not request.session.get("user_id"):
        return RedirectResponse(
            url="/auth/login",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    return render_template(
        request,
        "change_password.html",
        {
            "error": None,
            "success": None,
        },
    )


@router.post("/change-password")
async def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
):
    """
    Đổi mật khẩu cho người dùng đang đăng nhập.
    """

    if not request.session.get("user_id"):
        return RedirectResponse(
            url="/auth/login",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    email = request.session.get("user_email")

    if not email:
        request.session.clear()

        return RedirectResponse(
            url="/auth/login",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    current_password = current_password.strip()
    new_password = new_password.strip()

    # -------------------------------------------------
    # Validate
    # -------------------------------------------------
    if not current_password:
        return render_template(
            request,
            "change_password.html",
            {
                "error": "Vui lòng nhập mật khẩu hiện tại.",
                "success": None,
            },
            status.HTTP_400_BAD_REQUEST,
        )

    valid, password_error = is_valid_password(new_password)

    if not valid:
        return render_template(
            request,
            "change_password.html",
            {
                "error": password_error,
                "success": None,
            },
            status.HTTP_400_BAD_REQUEST,
        )

    if current_password == new_password:
        return render_template(
            request,
            "change_password.html",
            {
                "error": "Mật khẩu mới không được giống mật khẩu hiện tại.",
                "success": None,
            },
            status.HTTP_400_BAD_REQUEST,
        )

    try:
        # -------------------------------------------------
        # Xác minh mật khẩu hiện tại
        # -------------------------------------------------
        test_login = supabase.auth.sign_in_with_password(
            {
                "email": email,
                "password": current_password,
            }
        )

        if not test_login or not test_login.user:
            return render_template(
                request,
                "change_password.html",
                {
                    "error": "Mật khẩu hiện tại không chính xác.",
                    "success": None,
                },
                status.HTTP_401_UNAUTHORIZED,
            )

        # -------------------------------------------------
        # Đảm bảo đúng user trước khi đổi mật khẩu
        # -------------------------------------------------
        if str(test_login.user.id) != str(request.session["user_id"]):
            return render_template(
                request,
                "change_password.html",
                {
                    "error": "Không thể xác minh tài khoản hiện tại.",
                    "success": None,
                },
                status.HTTP_403_FORBIDDEN,
            )

        # -------------------------------------------------
        # Cập nhật mật khẩu
        # -------------------------------------------------
        supabase.auth.update_user(
            {
                "password": new_password,
            }
        )

        return render_template(
            request,
            "change_password.html",
            {
                "error": None,
                "success": "Đổi mật khẩu thành công!",
            },
        )

    except Exception as e:
        error_msg = str(e).lower()

        print(f"CHANGE PASSWORD ERROR: {e}")

        if (
            "invalid login credentials" in error_msg
            or "invalid_credentials" in error_msg
        ):
            friendly_error = "Mật khẩu hiện tại không chính xác."
        else:
            friendly_error = (
                "Không thể đổi mật khẩu lúc này. "
                "Vui lòng thử lại."
            )

        return render_template(
            request,
            "change_password.html",
            {
                "error": friendly_error,
                "success": None,
            },
        )


# =========================================================
# FORGOT PASSWORD
# =========================================================

@router.get("/forgot-password")
async def forgot_password_page(request: Request):
    """
    Hiển thị trang quên mật khẩu.
    """

    return render_template(
        request,
        "forgot_password.html",
        {
            "message": None,
            "error": None,
        },
    )


@router.post("/forgot-password")
async def forgot_password(
    request: Request,
    email: str = Form(...),
):
    """
    Gửi email reset password qua Supabase.
    """

    email = email.strip().lower()

    if not email:
        return render_template(
            request,
            "forgot_password.html",
            {
                "message": None,
                "error": "Vui lòng nhập email.",
            },
            status.HTTP_400_BAD_REQUEST,
        )

    try:
        base_url = str(request.base_url).rstrip("/")

        supabase.auth.reset_password_for_email(
            email,
            {
                "redirect_to": (
                    f"{base_url}/auth/update-password"
                ),
            },
        )

        # Không tiết lộ email có tồn tại hay không
        message = (
            "Nếu email tồn tại trong hệ thống, hướng dẫn khôi phục "
            "mật khẩu đã được gửi. Vui lòng kiểm tra hộp thư."
        )

    except Exception as e:
        print(f"FORGOT PASSWORD ERROR: {e}")

        # Vẫn trả về thông báo chung để tránh dò email
        message = (
            "Nếu email tồn tại trong hệ thống, hướng dẫn khôi phục "
            "mật khẩu đã được gửi. Vui lòng kiểm tra hộp thư."
        )

    return render_template(
        request,
        "forgot_password.html",
        {
            "message": message,
            "error": None,
        },
    )


# =========================================================
# UPDATE PASSWORD AFTER RESET
# =========================================================

@router.get("/update-password")
async def update_password_page(request: Request):
    """
    Hiển thị trang giao diện để người dùng nhập mật khẩu mới.
    Toàn bộ logic bắt token và gọi đổi mật khẩu sẽ do JS ở client xử lý.
    """
    return render_template(
        request,
        "update_password.html",
        {
            "error": None,
            "success": None,
        },
    )


@router.post("/update-password")
async def update_password(
    request: Request,
    new_password: str = Form(...),
    confirm_password: str = Form(...),
):
    """
    Cập nhật mật khẩu mới sau khi người dùng truy cập
    link khôi phục mật khẩu.
    """

    new_password = new_password.strip()
    confirm_password = confirm_password.strip()

    # -------------------------------------------------
    # Validate
    # -------------------------------------------------
    valid, password_error = is_valid_password(new_password)

    if not valid:
        return render_template(
            request,
            "update_password.html",
            {
                "error": password_error,
                "success": None,
            },
            status.HTTP_400_BAD_REQUEST,
        )

    if new_password != confirm_password:
        return render_template(
            request,
            "update_password.html",
            {
                "error": "Xác nhận mật khẩu không khớp.",
                "success": None,
            },
            status.HTTP_400_BAD_REQUEST,
        )

    try:
        # Supabase cần recovery session hợp lệ
        supabase.auth.update_user(
            {
                "password": new_password,
            }
        )

        return render_template(
            request,
            "update_password.html",
            {
                "error": None,
                "success": (
                    "Đặt lại mật khẩu thành công! "
                    "Bạn có thể đăng nhập bằng mật khẩu mới."
                ),
            },
        )

    except Exception as e:
        print(f"UPDATE PASSWORD ERROR: {e}")

        return render_template(
            request,
            "update_password.html",
            {
                "error": (
                    "Link khôi phục không hợp lệ hoặc đã hết hạn. "
                    "Vui lòng yêu cầu khôi phục mật khẩu lại."
                ),
                "success": None,
            },
            status.HTTP_400_BAD_REQUEST,
        )


# =========================================================
# DEPENDENCY KIỂM TRA ĐĂNG NHẬP
# =========================================================

async def require_login(request: Request) -> dict:
    """
    Dependency dùng cho các API cần kiểm tra đăng nhập.

    Ví dụ:
        @router.get("/profile")
        async def profile(user=Depends(require_login)):
            ...
    """

    user_id = request.session.get("user_id")

    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Phiên làm việc đã hết hạn hoặc bạn chưa đăng nhập.",
        )

    ho_ten = request.session.get("ho_ten") or "Quản trị viên"
    username = request.session.get("username") or ""
    email = request.session.get("user_email") or ""
    role = str(request.session.get("role") or "User").strip()

    return {
        "auth_id": str(user_id),
        "id": str(user_id),
        "email": email,
        "username": username,
        "ho_ten": ho_ten,
        "name": ho_ten,
        "role": role,
    }


# =========================================================
# HELPER DÀNH CHO CÁC TRANG HTML
# =========================================================

async def get_current_user_or_redirect(request: Request):
    """
    Dùng cho router render HTML.
    Nếu chưa đăng nhập thì trả về None.

    Ví dụ:
        user = await get_current_user_or_redirect(request)
        if not user:
            return RedirectResponse("/auth/login", status_code=303)
    """

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
    }