from config import supabase


def authenticate_user(email: str, password: str):
    """Gửi thông tin đăng nhập sang Supabase Auth để xác thực."""
    try:
        response = supabase.auth.sign_in_with_password(
            {"email": email, "password": password}
        )
        return response, None
    except Exception as e:
        # Trả về lỗi nếu sai mật khẩu hoặc tài khoản không tồn tại
        return None, str(e)