from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from config import supabase

router = APIRouter(prefix="/admin", tags=["Admin"])
templates = Jinja2Templates(directory="app/templates")

def verify_admin_access(request: Request):
    """Hàm kiểm tra bảo mật: Bắt buộc phải đăng nhập và phải có quyền Admin"""
    if 'user_id' not in request.session:
        return RedirectResponse(url="/auth/login", status_code=303)
    
    user_role = request.session.get('role', 'User')
    if user_role != 'Admin':
        raise HTTPException(
            status_code=403, 
            detail="⛔ Truy cập bị từ chối: Bạn không có quyền quản trị hệ thống tài khoản!"
        )
    return None

@router.get("/users")
async def list_users(request: Request):
    # Kiểm tra quyền Admin
    auth_check = verify_admin_access(request)
    if isinstance(auth_check, RedirectResponse):
        return auth_check
    
    users = []
    try:
        if supabase:
            response = supabase.table('quan_tri_vien').select('*').order('id', desc=True).execute()
            users = response.data if response and response.data else []
    except Exception as e:
        print(f"❌ Lỗi tải danh sách tài khoản: {e}")

    current_user = {
        "name": request.session.get('ho_ten', 'Quản trị viên'),
        "role": request.session.get('role', 'User')
    }

    return templates.TemplateResponse(
        request,
        "admin_users.html",
        {
            "request": request,
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
    verify_admin_access(request) # Chặn nếu không phải Admin
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

    return RedirectResponse(url="/admin/users", status_code=303)

@router.post("/users/edit/{user_id}")
async def edit_user(
    request: Request,
    user_id: int,
    ho_ten: str = Form(...),
    chuc_danh: str = Form(None),
    so_dien_thoai: str = Form(None),
    role: str = Form("User")
):
    verify_admin_access(request) # Chặn nếu không phải Admin
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
        
    return RedirectResponse(url="/admin/users", status_code=303)

@router.post("/users/update-role/{user_id}")
async def update_user_role(request: Request, user_id: int, role: str = Form(...)):
    verify_admin_access(request) # Chặn nếu không phải Admin
    try:
        if supabase:
            supabase.table('quan_tri_vien').update({"role": role}).eq("id", user_id).execute()
    except Exception as e:
        print(f"❌ Lỗi cập nhật quyền: {e}")
        
    return RedirectResponse(url="/admin/users", status_code=303)

@router.post("/users/delete/{user_id}")
async def delete_user(request: Request, user_id: int):
    verify_admin_access(request) # Chặn nếu không phải Admin
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
        
    return RedirectResponse(url="/admin/users", status_code=303)
def verify_admin_access(request: Request):
    if 'user_id' not in request.session:
        return RedirectResponse(url="/auth/login", status_code=303)
    
    user_role = request.session.get('role', 'User')
    if user_role != 'Admin':
        raise HTTPException(
            status_code=403, 
            detail="⛔ Truy cập bị từ chối: Bạn không có quyền quản trị hệ thống tài khoản!"
        )
    return None