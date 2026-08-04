from fastapi import APIRouter, Request, Form, HTTPException, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from config import supabase

router = APIRouter(prefix="/admin", tags=["Admin"])
templates = Jinja2Templates(directory="app/templates")


def verify_admin_access(request: Request):
    """Bắt buộc người dùng phải đăng nhập và có quyền Admin để tiếp tục"""
    if 'user_id' not in request.session:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/auth/login"}
        )
    
    user_role = request.session.get('role', 'User')
    if user_role != 'Admin':
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="⛔ Truy cập bị từ chối: Bạn không có quyền quản trị hệ thống!"
        )


# ==========================================
# 1. TRANG DASHBOARD QUẢN TRỊ TRUNG TÂM
# ==========================================
@router.get("")
@router.get("/")
async def admin_dashboard(request: Request):
    verify_admin_access(request)
    
    current_user = {
        "name": request.session.get('ho_ten', 'Quản trị viên'),
        "role": request.session.get('role', 'Admin')
    }

    return templates.TemplateResponse(
        request=request,
        name="admin.html",
        context={"current_user": current_user}
    )


# ==========================================
# 2. QUẢN LÝ TÀI KHOẢN (USERS)
# ==========================================
@router.get("/users")
async def list_users(request: Request):
    verify_admin_access(request)
    
    users = []
    try:
        if supabase:
            response = supabase.table('quan_tri_vien').select('*').order('id', desc=True).execute()
            users = response.data if response and response.data else []
    except Exception as e:
        print(f"❌ Lỗi tải danh sách tài khoản: {e}")

    current_user = {
        "name": request.session.get('ho_ten', 'Quản trị viên'),
        "role": request.session.get('role', 'Admin')
    }

    return templates.TemplateResponse(
        request=request,
        name="admin_users.html",  # Hoặc "admin.html" tùy thuộc file template danh sách user của bạn
        context={
            "users": users,
            "current_user": current_user
        }
    )


@router.post("/users/add")
async def add_user(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    ho_ten: str = Form(...),
    username: str = Form(...),
    role: str = Form("User"),
    chuc_danh: str = Form(None),
    so_dien_thoai: str = Form(None)
):
    verify_admin_access(request)
    try:
        if supabase:
            auth_response = supabase.auth.admin.create_user({
                "email": email,
                "password": password,
                "email_confirm": True
            })
            
            if auth_response and auth_response.user:
                auth_id = auth_response.user.id
                supabase.table('quan_tri_vien').insert({
                    "auth_id": auth_id,
                    "email": email,
                    "username": username,
                    "ho_ten": ho_ten,
                    "role": role,
                    "chuc_danh": chuc_danh,
                    "so_dien_thoai": so_dien_thoai
                }).execute()
    except Exception as e:
        print(f"❌ Lỗi thêm tài khoản: {e}")

    return RedirectResponse(url="/admin/users", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/users/edit/{user_id}")
async def edit_user(
    request: Request,
    user_id: int,
    ho_ten: str = Form(...),
    chuc_danh: str = Form(None),
    so_dien_thoai: str = Form(None),
    role: str = Form("User")
):
    verify_admin_access(request)
    try:
        if supabase:
            supabase.table('quan_tri_vien').update({
                "ho_ten": ho_ten,
                "chuc_danh": chuc_danh,
                "so_dien_thoai": so_dien_thoai,
                "role": role
            }).eq("id", user_id).execute()
    except Exception as e:
        print(f"❌ Lỗi cập nhật tài khoản: {e}")
        
    return RedirectResponse(url="/admin/users", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/users/update-role/{user_id}")
async def update_user_role(request: Request, user_id: int, role: str = Form(...)):
    verify_admin_access(request)
    try:
        if supabase:
            supabase.table('quan_tri_vien').update({"role": role}).eq("id", user_id).execute()
    except Exception as e:
        print(f"❌ Lỗi cập nhật quyền: {e}")
        
    return RedirectResponse(url="/admin/users", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/users/delete/{user_id}")
async def delete_user(request: Request, user_id: int):
    verify_admin_access(request)
    try:
        if supabase:
            res = supabase.table('quan_tri_vien').select('auth_id').eq("id", user_id).execute()
            if res and res.data:
                auth_id = res.data[0].get('auth_id')
                if auth_id:
                    supabase.auth.admin.delete_user(auth_id)
            supabase.table('quan_tri_vien').delete().eq("id", user_id).execute()
    except Exception as e:
        print(f"❌ Lỗi xóa tài khoản: {e}")
        
    return RedirectResponse(url="/admin/users", status_code=status.HTTP_303_SEE_OTHER)


# ==========================================
# 3. CẤU HÌNH CHẤM CÔNG (CONFIG)
# ==========================================
@router.get("/config-cham-cong")
async def get_config_cham_cong(request: Request):
    verify_admin_access(request)
    
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
        context={"config": config_data}
    )


@router.post("/config-cham-cong/save")
async def save_config_cham_cong(request: Request):
    verify_admin_access(request)
    
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
    except Exception as e:
        print(f"❌ Lỗi lưu cấu hình chấm công: {e}")
        
    return RedirectResponse(url="/admin/config-cham-cong", status_code=status.HTTP_303_SEE_OTHER)