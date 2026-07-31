from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates
from config import supabase

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

@router.get("/")
async def index(request: Request):
    warranties = []
    installations = []
    guides = []
    
    # Lấy trực tiếp thông tin từ session do auth.py vừa lưu
    is_logged_in = 'user_id' in request.session
    display_name = request.session.get('ho_ten', 'Quản trị viên')
    
    current_user = {
        "is_authenticated": is_logged_in,
        "name": display_name
    }
    
    try:
        if supabase:
            # 1. Truy vấn 5 phiếu bảo hành gần nhất
            res_warranty = supabase.table('warranty_records') \
                .select('*') \
                .order('created_at', desc=True) \
                .limit(5) \
                .execute()
            warranties = res_warranty.data if res_warranty and res_warranty.data else []

            # 2. Truy vấn 3 bản ghi chấm công gần nhất
            res_cham_cong = supabase.table('cham_cong') \
                .select('*') \
                .order('thoi_gian', desc=True) \
                .limit(3) \
                .execute()
            installations = res_cham_cong.data if res_cham_cong and res_cham_cong.data else []

            # 3. Truy vấn 3 bài hướng dẫn gần nhất từ bảng guide
            res_guide = supabase.table('guide') \
                .select('*') \
                .eq('is_active', True) \
                .order('created_at', desc=True) \
                .limit(3) \
                .execute()
            guides = res_guide.data if res_guide and res_guide.data else []
            
    except Exception as e:
        print(f"❌ LỖI TRUY VẤN TRANG CHỦ: {e}")

    return templates.TemplateResponse(
        request, 
        "index.html", 
        {
            "request": request,
            "current_user": current_user,
            "recent_warranties": warranties,
            "recent_installations": installations,
            "recent_library": guides
        }
    )
@router.get("/api/health-status")
async def health_status():
    return {
        "timekeeping": "online",
        "warranty": "online",
        "library": "online",
        "cloud": "online"
    }