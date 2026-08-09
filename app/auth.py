from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from config import supabase

router = APIRouter(prefix="/auth", tags=["Auth"])
templates = Jinja2Templates(directory="app/templates")

@router.get("/login")
async def login_page(request: Request):
    return templates.TemplateResponse(
        request, 
        "login.html", 
        {"request": request, "error": None}
    )

@router.post("/login")
async def login(request: Request, email: str = Form(...), password: str = Form(...)):
    try:
        response = supabase.auth.sign_in_with_password({
            "email": email,
            "password": password
        })
        
        if response and response.user:
            auth_id = response.user.id
            
            # Truy vấn bảng quan_tri_vien để lấy thông tin chi tiết và role
            user_record = supabase.table('quan_tri_vien').select('*').eq('auth_id', auth_id).execute()
            
            username = email
            ho_ten = "Quản trị viên"
            role = "User"  # Mặc định là User nếu không tìm thấy
            
            if user_record and user_record.data:
                user_info = user_record.data[0]
                username = user_info.get("username", email)
                ho_ten = user_info.get("ho_ten", "Quản trị viên")
                role = user_info.get("role", "User") # Lấy quyền thực tế từ database

            # Lưu đầy đủ thông tin vào session
            request.session['user_id'] = auth_id
            request.session['user_email'] = response.user.email
            request.session['username'] = username
            request.session['ho_ten'] = ho_ten
            request.session['role'] = role  # <--- Lưu role vào session
            
            return RedirectResponse(url="/", status_code=303)
        else:
            return templates.TemplateResponse(
                request, 
                "login.html", 
                {"request": request, "error": "Tài khoản hoặc mật khẩu không chính xác."}
            )
            
    except Exception as e:
        error_msg = str(e)
        friendly_error = "Tài khoản hoặc mật khẩu không chính xác." if "Invalid login credentials" in error_msg else f"Lỗi đăng nhập: {error_msg}"
        return templates.TemplateResponse(
            request, 
            "login.html", 
            {"request": request, "error": friendly_error}
        )

@router.get("/logout")
async def logout(request: Request):
    try:
        supabase.auth.sign_out()
    except Exception:
        pass
        
    request.session.clear()
    return RedirectResponse(url="/auth/login", status_code=303)

@router.get("/change-password")
async def change_password_page(request: Request):
    if 'user_id' not in request.session:
        return RedirectResponse(url="/auth/login", status_code=303)
    return templates.TemplateResponse(request, "change_password.html", {"request": request, "error": None, "success": None})

@router.post("/change-password")
async def change_password(request: Request, current_password: str = Form(...), new_password: str = Form(...)):
    if 'user_id' not in request.session:
        return RedirectResponse(url="/auth/login", status_code=303)
    
    email = request.session.get('user_email')
    try:
        # Kiểm tra mật khẩu cũ bằng cách đăng nhập thử lại
        test_login = supabase.auth.sign_in_with_password({
            "email": email,
            "password": current_password
        })
        
        if test_login and test_login.user:
            supabase.auth.update_user({"password": new_password})
            
            return templates.TemplateResponse(request, "change_password.html", {
                "request": request, 
                "error": None, 
                "success": "Đổi mật khẩu thành công!"
            })
        else:
            return templates.TemplateResponse(request, "change_password.html", {
                "request": request, 
                "error": "Mật khẩu hiện tại không chính xác.", 
                "success": None
            })
    except Exception as e:
        return templates.TemplateResponse(request, "change_password.html", {
            "request": request, 
            "error": f"Lỗi đổi mật khẩu: {str(e)}", 
            "success": None
        })

@router.get("/forgot-password")
async def forgot_password_page(request: Request):
    return templates.TemplateResponse(request, "forgot_password.html", {"request": request, "message": None})

@router.post("/forgot-password")
async def forgot_password(request: Request, email: str = Form(...)):
    try:
        # Tự động lấy base_url hiện tại (hoạt động tốt cả khi chạy localhost lẫn trên Hugging Face Space)
        base_url = str(request.base_url).rstrip("/")
        
        # Gọi API của Supabase để gửi email khôi phục mật khẩu
        supabase.auth.reset_password_for_email(email, {
            "redirect_to": f"{base_url}/auth/update-password"
        })
        msg = "Nếu email tồn tại trong hệ thống, hướng dẫn khôi phục mật khẩu đã được gửi đi. Vui lòng kiểm tra hộp thư của bạn."
    except Exception as e:
        msg = f"Đã gửi yêu cầu khôi phục tới email: {email}"
        
    return templates.TemplateResponse(request, "forgot_password.html", {"request": request, "message": msg})

@router.get("/update-password")
async def update_password_page(request: Request):
    # Trang này sẽ chứa form HTML và JavaScript để bắt token từ URL hash
    return templates.TemplateResponse(request, "update_password.html", {"request": request})