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
def apply_department_scope(query, user: dict):
    """
    Tự động áp đặt phạm vi dữ liệu dựa trên Phòng ban của User:
    - System Admin / Super Admin hoặc Department == 'ALL': Xem toàn bộ dữ liệu.
    - Admin / User của phòng ban khác (KTSC, KD, KT...): Chỉ xem/sửa dữ liệu phòng ban mình.
    """
    user_role = str(user.get("role") or "").strip().lower()
    user_dept = str(user.get("department") or "KTSC").strip().upper()

    # 1. Quản trị cấp cao hoặc tài khoản ALL được toàn quyền xem dữ liệu các phòng ban
    if user_role in ["system admin", "super admin"] or user_dept == "ALL":
        return query

    # 2. Ngược lại, ép buộc truy vấn CSDL chỉ trả về dữ liệu thuộc phòng ban của User đó
    return query.eq("department", user_dept)