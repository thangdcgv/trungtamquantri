from fastapi import APIRouter, Request, Form, HTTPException, status, Depends
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from config import supabase

router = APIRouter(prefix="/admin", tags=["Admin"])
templates = Jinja2Templates(directory="app/templates")


# ==========================================
# DEPENDENCY KIỂM TRA QUYỀN ADMIN
# ==========================================
async def get_current_admin(request: Request):
    """Dependency bắt buộc phải đăng nhập và có quyền Admin"""
    user_id = request.session.get('user_id')
    role = request.session.get('role', 'User')

    if not user_id:
        # Sử dụng trực tiếp RedirectResponse để điều hướng chuẩn xác nhất trong FastAPI
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/auth/login"}
        )
    
    if role != 'Admin':
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="⛔ Truy cập bị từ chối: Bạn không có quyền quản trị hệ thống!"
        )
    
    return {
        "auth_id": user_id,
        "name": request.session.get('ho_ten', 'Quản trị viên'),
        "role": role
    }


# ==========================================
# 1. TRANG DASHBOARD
# ==========================================
@router.get("")
@router.get("/")
async def admin_dashboard(request: Request, admin: dict = Depends(get_current_admin)):
    return templates.TemplateResponse(
        request=request,
        name="admin.html",
        context={"current_user": admin}
    )


# ==========================================
# 2. QUẢN LÝ TÀI KHOẢN (USERS)
# ==========================================
@router.get("/users")
async def list_users(request: Request, admin: dict = Depends(get_current_admin)):
    users = []
    error_msg = request.session.pop("error_message", None)
    success_msg = request.session.pop("success_message", None)

    try:
        if supabase:
            response = supabase.table('quan_tri_vien').select('*').order('id', desc=True).execute()
            users = response.data if response and response.data else []
    except Exception as e:
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
    username: str = Form(None),
    role: str = Form("User"),
    chuc_danh: str = Form(None),
    so_dien_thoai: str = Form(None),
    admin: dict = Depends(get_current_admin)
):
    if not supabase:
        request.session["error_message"] = "Không thể kết nối đến cơ sở dữ liệu."
        return RedirectResponse(url="/admin/users", status_code=status.HTTP_303_SEE_OTHER)

    auth_id = None
    try:
        # Bước 1: Tạo tài khoản bên Supabase Auth
        auth_response = supabase.auth.admin.create_user({
            "email": email.strip(),
            "password": password.strip(),
            "email_confirm": True
        })
        
        if not auth_response or not auth_response.user:
            raise Exception("Không thể khởi tạo tài khoản trên hệ thống Auth.")

        auth_id = auth_response.user.id
        final_username = username.strip() if username and username.strip() else email.split('@')[0]

        # Bước 2: Lưu vào bảng quan_tri_vien
        try:
            supabase.table('quan_tri_vien').insert({
                "auth_id": auth_id,
                "email": email.strip(),
                "username": final_username,
                "ho_ten": ho_ten.strip(),
                "role": role,
                "chuc_danh": chuc_danh.strip() if chuc_danh else None,
                "so_dien_thoai": so_dien_thoai.strip() if so_dien_thoai else None
            }).execute()

            request.session["success_message"] = f"Thêm tài khoản {email} thành công!"

        except Exception as db_err:
            # ROLLBACK: Nếu insert DB thất bại, xóa user vừa tạo bên Auth để tránh rác
            if auth_id:
                supabase.auth.admin.delete_user(auth_id)
            raise Exception(f"Lỗi lưu thông tin hồ sơ: {str(db_err)}")

    except Exception as e:
        request.session["error_message"] = f"Tạo tài khoản thất bại: {str(e)}"

    return RedirectResponse(url="/admin/users", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/users/edit/{user_id}")
async def edit_user(
    request: Request,
    user_id: int,
    ho_ten: str = Form(...),
    username: str = Form(None),
    chuc_danh: str = Form(None),
    so_dien_thoai: str = Form(None),
    role: str = Form("User"),
    new_password: str = Form(None),
    admin: dict = Depends(get_current_admin)
):
    try:
        if supabase:
            # 1. Tra cứu auth_id
            res = supabase.table('quan_tri_vien').select('auth_id').eq("id", user_id).execute()
            if not res or not res.data:
                raise Exception("Không tìm thấy tài khoản cần chỉnh sửa.")

            auth_id = res.data[0].get('auth_id')

            # 2. Cập nhật mật khẩu bên Supabase Auth nếu người dùng có nhập
            if new_password and new_password.strip():
                if len(new_password.strip()) < 6:
                    raise Exception("Mật khẩu mới phải có ít nhất 6 ký tự.")
                supabase.auth.admin.update_user_by_id(auth_id, {"password": new_password.strip()})

            # 3. Cập nhật thông tin bảng quan_tri_vien
            update_payload = {
                "ho_ten": ho_ten.strip(),
                "chuc_danh": chuc_danh.strip() if chuc_danh else None,
                "so_dien_thoai": so_dien_thoai.strip() if so_dien_thoai else None,
                "role": role
            }
            if username and username.strip():
                update_payload["username"] = username.strip()

            supabase.table('quan_tri_vien').update(update_payload).eq("id", user_id).execute()
            request.session["success_message"] = f"Cập nhật tài khoản #{user_id} thành công!"

    except Exception as e:
        request.session["error_message"] = f"Cập nhật thất bại: {str(e)}"
        
    return RedirectResponse(url="/admin/users", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/users/delete/{user_id}")
async def delete_user(
    request: Request, 
    user_id: int, 
    admin: dict = Depends(get_current_admin)
):
    try:
        if supabase:
            # 1. Lấy thông tin user cần xóa
            res = supabase.table('quan_tri_vien').select('auth_id, email').eq("id", user_id).execute()
            if not res or not res.data:
                raise Exception("Tài khoản không tồn tại.")

            target_user = res.data[0]
            target_auth_id = target_user.get('auth_id')

            # 2. CHẶN TỰ XÓA CHÍNH MÌNH
            if target_auth_id == admin["auth_id"]:
                raise Exception("Bạn không thể tự xóa tài khoản quản trị đang đăng nhập!")

            # 3. Xóa hồ sơ DB trước, sau đó xóa Auth
            supabase.table('quan_tri_vien').delete().eq("id", user_id).execute()

            if target_auth_id:
                supabase.auth.admin.delete_user(target_auth_id)

            request.session["success_message"] = "Đã xóa tài khoản thành công."

    except Exception as e:
        request.session["error_message"] = f"Xóa tài khoản thất bại: {str(e)}"
        
    return RedirectResponse(url="/admin/users", status_code=status.HTTP_303_SEE_OTHER)


# ==========================================
# 3. CẤU HÌNH CHẤM CÔNG (CONFIG)
# ==========================================
@router.get("/config-cham-cong")
async def get_config_cham_cong(request: Request, admin: dict = Depends(get_current_admin)):
    config_data = {}
    try:
        if supabase:
            res = supabase.table('cau_hinh_cham_cong').select('*').limit(1).execute()
            if res and res.data:
                config_data = res.data[0]
    except Exception as e:
        print(f"❌ Lỗi tải cấu hình chấm công: {e}")

    return templates.TemplateResponse(
        request=request,
        name="config_cham_cong.html",
        context={"config": config_data, "current_user": admin}
    )


@router.post("/config-cham-cong/save")
async def save_config_cham_cong(request: Request, admin: dict = Depends(get_current_admin)):
    try:
        form_data = await request.form()
        update_dict = {key: value for key, value in form_data.items()}
        
        if supabase:
            check = supabase.table('cau_hinh_cham_cong').select('id').limit(1).execute()
            if check and check.data:
                config_id = check.data[0]['id']
                supabase.table('cau_hinh_cham_cong').update(update_dict).eq('id', config_id).execute()
            else:
                supabase.table('cau_hinh_cham_cong').insert(update_dict).execute()
                
        request.session["success_message"] = "Cập nhật cấu hình chấm công thành công!"
    except Exception as e:
        request.session["error_message"] = f"Lưu cấu hình thất bại: {str(e)}"
        
    return RedirectResponse(url="/admin/config-cham-cong", status_code=status.HTTP_303_SEE_OTHER)