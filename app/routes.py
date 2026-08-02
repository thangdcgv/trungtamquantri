from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates
from datetime import datetime, timezone
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
            # Lấy mốc thời gian ngày đầu tiên của tháng hiện tại (chuẩn UTC)
            now = datetime.now(timezone.utc)
            first_day_of_month = datetime(now.year, now.month, 1, tzinfo=timezone.utc).isoformat()

            # 1. Truy vấn 5 phiếu bảo hành gần nhất trong tháng hiện tại (dựa vào created_at)
            res_warranty = supabase.table('warranty_records') \
                .select('*') \
                .gte('created_at', first_day_of_month) \
                .order('created_at', desc=True) \
                .limit(5) \
                .execute()
            warranties = res_warranty.data if res_warranty and res_warranty.data else []

            # 2. Truy vấn 5 đơn chấm công gần nhất trong tháng hiện tại (dựa vào thoi_gian)
            res_cham_cong = supabase.table('cham_cong') \
                .select('*') \
                .gte('thoi_gian', first_day_of_month) \
                .order('id', desc=True) \
                .limit(5) \
                .execute()
            installations = res_cham_cong.data if res_cham_cong and res_cham_cong.data else []

            # 3. Truy vấn 3 bài hướng dẫn gần nhất từ bảng guide (giữ nguyên không giới hạn tháng)
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