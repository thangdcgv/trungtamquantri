import io
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo 
from fastapi import APIRouter, File, Form, Request, UploadFile, status, Query, Depends
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from PIL import Image, ImageOps
from pydantic import BaseModel, Field, validator
from app.auth import require_login
from config import supabase  # Supabase client instance

logger = logging.getLogger(__name__)

# --- CẤU HÌNH THƯ MỤC TEMPLATES ---
BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = (
    BASE_DIR.parent / "templates"
    if (BASE_DIR.parent / "templates").exists()
    else BASE_DIR / "templates"
)

if not TEMPLATES_DIR.exists():
    logger.error(f"CRITICAL: Không tìm thấy thư mục templates tại: {TEMPLATES_DIR}")

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter(prefix="/cham-cong", tags=["Chấm Công Lắp Đặt"])


# ==========================================
# 1. HELPER FUNCTIONS & LOGIC CHẤM CÔNG
# ==========================================

DEFAULT_CONFIG = {
    "phu_cap_tho_phu": 80000,
    "phu_cap_di_tinh": 500000,
    "phu_cap_ngay_an": 200000,
    "phu_cap_dem_ks": 350000,
    "moc_km_1": 30000,       # <= 20km
    "moc_km_2": 50000,       # 21-30km
    "moc_km_3": 70000,       # 31-40km
    "moc_km_4": 80000,       # 41-50km
    "phi_vuot_50km": 5000,   # mỗi km > 50km
    "price_may_lon": 80000,
    "price_may_nho": 30000,
    "price_may_ep_near": 80000, # <= 20km
    "price_may_ep_far": 50000   # > 20km
}


def get_config_cham_cong() -> dict:
    """Lấy cấu hình đơn giá linh hoạt từ config_cham_cong với DEFAULT_CONFIG làm fallback"""
    config = DEFAULT_CONFIG.copy()
    try:
        res_cfg = supabase.table("config_cham_cong").select("key_name, value_num").execute()
        if res_cfg.data:
            for item in res_cfg.data:
                k = item.get("key_name")
                v = item.get("value_num")
                if k and v is not None:
                    try:
                        config[k] = float(v)
                    except (ValueError, TypeError):
                        config[k] = v
            return config
    except Exception:
        pass

    try:
        res_row = supabase.table("config_cham_cong").select("*").limit(1).execute()
        if res_row.data and len(res_row.data) > 0:
            for k, v in res_row.data[0].items():
                if k != "id" and v is not None:
                    try:
                        config[k] = float(v)
                    except (ValueError, TypeError):
                        config[k] = v
    except Exception as e:
        logger.error(f"Lỗi lấy config từ DB: {e}")

    return config


def process_image(file_bytes: bytes, rotation: int = 0) -> bytes:
    """Sửa hướng EXIF tự động, xoay ảnh và tối ưu dung lượng"""
    img = Image.open(io.BytesIO(file_bytes))
    img = ImageOps.exif_transpose(img)
    
    if rotation != 0:
        img = img.rotate(-rotation, expand=True)

    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")

    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=85, optimize=True)
    return buffer.getvalue()


def upload_image_to_supabase(file_bytes: bytes, filename: str) -> Optional[str]:
    """Upload ảnh lên Supabase Storage"""
    try:
        bucket_name = "AnhChamCong"
        time_prefix = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_filename = f"{time_prefix}_{filename.replace(' ', '_')}"
        
        supabase.storage.from_(bucket_name).upload(
            path=safe_filename,
            file=file_bytes,
            file_options={"content-type": "image/jpeg"}
        )
        return supabase.storage.from_(bucket_name).get_public_url(safe_filename)
    except Exception as e:
        logger.error(f"Lỗi upload ảnh lên Storage: {e}")
        return None


def check_duplicate_invoice(so_hd: str, edit_id: Optional[int] = None) -> tuple[bool, str]:
    """Kiểm tra mã hóa đơn trùng lặp (xóa khoảng trắng, chuẩn hóa chữ hoa)"""
    so_hd_clean = re.sub(r'\s+', '', so_hd.strip().upper())
    formatted_hd = so_hd_clean if so_hd_clean.startswith("HD") else f"HD{so_hd_clean}"
    
    query = supabase.table("cham_cong").select("id").eq("so_hoa_don", formatted_hd)
    if edit_id:
        query = query.neq("id", edit_id)
        
    res = query.execute()
    if res.data and len(res.data) > 0:
        return False, formatted_hd
    return True, formatted_hd


def parse_datetime_field(item: dict):
    """Chuyển đổi trường thoi_gian từ chuỗi UTC sang datetime object theo giờ Việt Nam"""
    if item and isinstance(item.get("thoi_gian"), str):
        try:
            dt = datetime.fromisoformat(item["thoi_gian"].replace("Z", "+00:00"))
            local_tz = ZoneInfo("Asia/Ho_Chi_Minh")
            item["thoi_gian"] = dt.astimezone(local_tz)
        except Exception as e:
            logger.warning(f"Lỗi chuyển đổi múi giờ: {e}")
    return item


def tinh_tien_lap_dat(
    quang_duong: float,
    so_may_lon: int,
    so_may_nho: int,
    so_may_ep: int,
    so_nguoi_di_cung: int,
    so_ngay_an: int,
    so_dem_ks: int,
    is_di_tinh: bool,
    is_ngoai_gio: bool,
    gia_thuong_luong: float,
    phu_phi_phat_sinh: float,
    config: dict
) -> dict:
    cfg = config or {}

    phu_cap_tho_phu = cfg.get("phu_cap_tho_phu", 80000)
    phu_cap_di_tinh = cfg.get("phu_cap_di_tinh", 500000)
    phu_cap_ngay_an = cfg.get("phu_cap_ngay_an", 200000)
    phu_cap_dem_ks = cfg.get("phu_cap_dem_ks", 350000)

    moc_1 = cfg.get("moc_km_1", 30000)
    moc_2 = cfg.get("moc_km_2", 50000)
    moc_3 = cfg.get("moc_km_3", 70000)
    moc_4 = cfg.get("moc_km_4", 80000)
    phi_vuot_50km = cfg.get("phi_vuot_50km", 5000)

    price_may_lon = cfg.get("price_may_lon", 80000)
    price_may_nho = cfg.get("price_may_nho", 30000)
    price_may_ep_near = cfg.get("price_may_ep_near", 80000)
    price_may_ep_far = cfg.get("price_may_ep_far", 50000)

    device_cost = 0.0
    distance_cost = 0.0
    tho_phu_cost = 0.0
    di_tinh_cost = 0.0

    if is_di_tinh:
        base_di_tinh_self = phu_cap_di_tinh + (so_ngay_an * phu_cap_ngay_an) + (so_dem_ks * phu_cap_dem_ks)
        tien_nguoi_di_cung = so_nguoi_di_cung * base_di_tinh_self
        di_tinh_cost = float(base_di_tinh_self + tien_nguoi_di_cung)
        tong_tien_chinh = di_tinh_cost
    else:
        if quang_duong <= 0:
            tien_quang_duong = 0
        elif quang_duong <= 20:
            tien_quang_duong = moc_1
        elif quang_duong <= 30:
            tien_quang_duong = moc_2
        elif quang_duong <= 40:
            tien_quang_duong = moc_3
        elif quang_duong <= 50:
            tien_quang_duong = moc_4
        else:
            tien_quang_duong = moc_4 + (quang_duong - 50) * phi_vuot_50km

        distance_cost = float(tien_quang_duong)

        tien_may_lon = so_may_lon * price_may_lon
        co_may_chinh = (so_may_lon > 0) or (so_may_ep > 0)
        if co_may_chinh:
            tien_may_nho = so_may_nho * price_may_nho
        else:
            tien_may_nho = max(0, so_may_nho - 1) * price_may_nho

        tien_may_ep = 0
        if so_may_ep > 0:
            don_gia_ep = price_may_ep_near if quang_duong <= 20 else price_may_ep_far
            tien_may_ep = so_may_ep * don_gia_ep

        device_cost = float(tien_may_lon + tien_may_nho + tien_may_ep)

        if so_nguoi_di_cung > 0:
            if so_may_ep > 0:
                gia_ep_tho = price_may_ep_near if quang_duong <= 20 else price_may_ep_far
                phi_qd_tho = tien_quang_duong if quang_duong > 20 else 0
                tien_nguoi_di_cung = so_nguoi_di_cung * (gia_ep_tho + phi_qd_tho)
            else:
                tien_nguoi_di_cung = so_nguoi_di_cung * phu_cap_tho_phu
        else:
            tien_nguoi_di_cung = 0.0

        tho_phu_cost = float(tien_nguoi_di_cung)
        tong_tien_chinh = distance_cost + device_cost + tho_phu_cost

    tien_ngoai_gio = float(gia_thuong_luong) if is_ngoai_gio else 0.0
    phu_phi = float(phu_phi_phat_sinh)

    tong_tien = float(tong_tien_chinh + tien_ngoai_gio + phu_phi)

    return {
        "device_cost": device_cost,
        "distance_cost": distance_cost,
        "tho_phu_cost": tho_phu_cost,
        "di_tinh_cost": di_tinh_cost,
        "tien_ngoai_gio": tien_ngoai_gio,
        "phu_phi_phat_sinh": phu_phi,
        "tong_tien": tong_tien
    }


def build_noi_dung(
    noi_dung_goc: str,
    is_di_tinh: bool,
    so_ngay_an: int,
    so_dem_ks: int,
    so_nguoi_di_cung: int,
    phu_phi_khac: float,
    is_ngoai_gio: bool,
    gia_thuong_luong: float
) -> str:
    """Tạo ghi chú nội dung tổng hợp (Đã fix lỗi trùng lặp khi Edit)"""
    # Xóa các chuỗi thông tin bổ sung cũ dạng [...] nếu có
    clean_base = re.sub(r'\s*\[.*?\]$', '', noi_dung_goc.strip())

    details = []
    if is_di_tinh:
        di_tinh_str = "Đi tỉnh"
        if so_ngay_an > 0 or so_dem_ks > 0:
            sub_di = []
            if so_ngay_an > 0:
                sub_di.append(f"{so_ngay_an} ngày ăn")
            if so_dem_ks > 0:
                sub_di.append(f"{so_dem_ks} đêm KS")
            di_tinh_str += f" ({', '.join(sub_di)})"
        details.append(di_tinh_str)
    if so_nguoi_di_cung > 0:
        details.append(f"+{so_nguoi_di_cung} người đi cùng")
    if is_ngoai_gio and gia_thuong_luong > 0:
        details.append(f"Ngoài giờ: {gia_thuong_luong:,.0f}đ")
    if phu_phi_khac > 0:
        details.append(f"Phụ phí: {phu_phi_khac:,.0f}đ")
        
    extra_info = f" [{', '.join(details)}]" if details else ""
    return f"{clean_base}{extra_info}"


# ==========================================
# 2. SCHEMAS
# ==========================================

class DuyetPhieuSchema(BaseModel):
    trang_thai: str = Field(..., description="Trạng thái: 'Đã duyệt', 'Từ chối', hoặc 'Chờ duyệt'")
    ghi_chu_duyet: Optional[str] = Field(default="", description="Ghi chú từ QTV")

    @validator('trang_thai')
    def validate_trang_thai(cls, v):
        allowed = ['Đã duyệt', 'Từ chối', 'Chờ duyệt']
        if v not in allowed:
            raise ValueError(f"Trạng thái phải thuộc một trong các giá trị: {allowed}")
        return v


# ==========================================
# 3. ROUTES CHẤM CÔNG HÀNG NGÀY
# ==========================================

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def list_cham_cong(
    request: Request,
    current_user: dict = Depends(require_login)
):
    """Danh sách 5 phiếu chấm công mới nhất trong tháng"""
    try:
        now = datetime.now()
        first_day_of_month = datetime(now.year, now.month, 1, tzinfo=timezone.utc).isoformat()

        res_cc = (
            supabase.table('cham_cong')
            .select('*')
            .gte('thoi_gian', first_day_of_month)
            .order('id', desc=True)
            .limit(5)
            .execute()
        )
        
        recent_installations = res_cc.data or []
        for item in recent_installations:
            parse_datetime_field(item)
        
        item = recent_installations[0] if recent_installations else {
            "id": 0,
            "so_hoa_don": "Chưa có",
            "noi_dung": "Chưa có dữ liệu chấm công trong tháng",
            "thanh_tien": 0,
            "trang_thai": "Chưa có",
            "quang_duong": 0,
            "combo": 0,
            "hinh_anh": "",
            "thoi_gian": datetime.now(timezone.utc)
        }

        return templates.TemplateResponse(
            request=request,
            name="cham_cong_detail.html",
            context={
                "request": request,
                "recent_installations": recent_installations,
                "item": item,
                "current_user": current_user
            }
        )
    except Exception as e:
        logger.error(f"Lỗi truy vấn danh sách chấm công: {str(e)}")
        return HTMLResponse(content=f"<h3>Lỗi hệ thống: {str(e)}</h3>", status_code=500)


@router.get("/detail/{item_id}", response_class=HTMLResponse)
async def detail_cham_cong(
    request: Request,
    item_id: int,
    current_user: dict = Depends(require_login)
):
    """Xem chi tiết phiếu chấm công"""
    try:
        res = supabase.table('cham_cong').select('*').eq('id', item_id).execute()
        if not res.data:
            return RedirectResponse(url="/cham-cong", status_code=status.HTTP_303_SEE_OTHER)

        item = res.data[0]
        parse_datetime_field(item)
        
        return templates.TemplateResponse(
            request=request,
            name="cham_cong_detail.html",
            context={
                "request": request,
                "item": item,
                "current_user": current_user
            }
        )
    except Exception as e:
        logger.error(f"Lỗi chi tiết phiếu {item_id}: {str(e)}")
        return RedirectResponse(url="/cham-cong", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/form", response_class=HTMLResponse)
async def get_form_cham_cong(
    request: Request,
    current_user: dict = Depends(require_login),
    edit_id: Optional[int] = None
):
    """Giao diện tạo mới / chỉnh sửa phiếu chấm công"""
    try:
        cfg = get_config_cham_cong()
        edit_data = None
        employees_list = []

        user_role = str(current_user.get("role") or "User").strip()
        current_username = current_user.get("username")

        if user_role in ("Admin", "Super Admin", "System Admin"):
            try:
                emp_res = supabase.table("quan_tri_vien").select("username, ho_ten").execute()
                if emp_res.data:
                    employees_list = emp_res.data
            except Exception as e:
                logger.error(f"Lỗi tải danh sách nhân viên cho Admin: {e}")

        if edit_id:
            res = supabase.table("cham_cong").select("*").eq("id", edit_id).execute()
            if res.data:
                record = res.data[0]
                
                # Kiểm tra trạng thái đơn (chỉ cho phép sửa khi chưa duyệt / Chờ duyệt)
                is_approved = record.get("trang_thai") == "Đã duyệt"
                
                # Kiểm tra quyền chính chủ (Admin có thể sửa mọi đơn chưa duyệt, User chỉ sửa đơn của chính mình)
                is_owner = (record.get("username") == current_username) or (user_role in ("Admin", "Super Admin", "System Admin"))

                if is_approved:
                    return HTMLResponse(content="<h3>Đơn này đã được duyệt, không thể chỉnh sửa!</h3>", status_code=403)

                if not is_owner:
                    return HTMLResponse(content="<h3>Bạn không có quyền chỉnh sửa đơn này!</h3>", status_code=403)

                edit_data = record

        return templates.TemplateResponse(
            request=request,
            name="lap_dat.html",
            context={
                "request": request,
                "config": cfg,
                "edit_data": edit_data,
                "employees_list": employees_list,
                "current_user": current_user
            }
        )
    except Exception as e:
        logger.error(f"Lỗi tải form chấm công: {e}")
        return HTMLResponse(content=f"<h3>Lỗi hệ thống: {str(e)}</h3>", status_code=500)


@router.get("/api/search-invoice")
async def search_invoice(
    so_hd: str,
    current_user: dict = Depends(require_login)
):
    """API tra cứu hóa đơn theo số HD để chỉnh sửa"""
    try:
        so_hd_clean = re.sub(r'\s+', '', so_hd.strip().upper())
        if not so_hd_clean:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"success": False, "message": "❌ Vui lòng nhập số hóa đơn!"}
            )

        res = supabase.table("cham_cong").select("id, so_hoa_don").ilike("so_hoa_don", f"%{so_hd_clean}%").limit(1).execute()
        if res.data and len(res.data) > 0:
            return JSONResponse(status_code=status.HTTP_200_OK, content={"success": True, "data": res.data[0]})
        
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"success": False, "message": f"❌ Không tìm thấy hóa đơn '{so_hd}'!"}
        )
    except Exception as e:
        logger.error(f"Lỗi tra cứu hóa đơn: {e}")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"success": False, "message": f"❌ Lỗi hệ thống: {str(e)}"}
        )


@router.post("/api/submit")
async def submit_cham_cong(
    request: Request,
    current_user: dict = Depends(require_login),
    so_hoa_don: str = Form(...),
    noi_dung: str = Form(...),
    quang_duong: float = Form(0.0),
    combo_may_lon: int = Form(0),
    combo_may_nho: int = Form(0),
    combo_may_ep: int = Form(0),
    so_ngay_an: int = Form(0),
    so_dem_ks: int = Form(0),
    so_nguoi_di_cung: int = Form(0),
    phu_phi_phat_sinh: float = Form(0.0),
    gia_thuong_luong: float = Form(0.0),
    is_di_tinh: bool = Form(False),
    is_ngoai_gio: bool = Form(False),
    is_hotro_khac: bool = Form(False),
    image_rotation: int = Form(0),
    target_username: Optional[str] = Form(None),
    edit_id: Optional[str] = Form(None),
    existing_image_url: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None)
):
    try:
        # ==========================================
        # VALIDATION CÁC TRƯỜNG ĐẦU VÀO
        # ==========================================
        clean_noi_dung = noi_dung.strip()
        if not clean_noi_dung or len(clean_noi_dung) < 8:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"success": False, "message": "❌ Nội dung / Địa chỉ công việc quá ngắn (tối thiểu 8 ký tự)!"}
            )

        if quang_duong < 0 or quang_duong > 1000:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"success": False, "message": "❌ Quãng đường không hợp lệ (phải từ 0 đến 1000 KM)!"}
            )

        if combo_may_lon < 0 or combo_may_nho < 0 or combo_may_ep < 0:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"success": False, "message": "❌ Số lượng máy móc không được là số âm!"}
            )

        if not is_di_tinh and (combo_may_lon + combo_may_nho + combo_may_ep) <= 0:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"success": False, "message": "❌ Vui lòng chọn ít nhất 1 thiết bị (Máy lớn, Máy nhỏ hoặc Máy ép)!"}
            )

        if is_hotro_khac and so_nguoi_di_cung <= 0:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"success": False, "message": "❌ Bạn đã chọn 'Có thợ phụ', vui lòng nhập số lượng thợ phụ (ít nhất là 1)!"}
            )

        if phu_phi_phat_sinh < 0 or gia_thuong_luong < 0 or so_ngay_an < 0 or so_dem_ks < 0:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"success": False, "message": "❌ Các giá trị phụ phí/chi phí không được là số âm!"}
            )

        parsed_edit_id = int(edit_id) if edit_id and edit_id.strip().isdigit() else None
        
        session_user = current_user.get("username", "system_user")
        session_fullname = current_user.get("ho_ten") or current_user.get("username", "system_user")
        user_role = str(current_user.get("role") or "User").strip()

        # ==========================================
        # KIỂM TRA QUYỀN CHỈNH SỬA (KHI EDIT_ID TỒN TẠI)
        # ==========================================
        if parsed_edit_id:
            existing_res = supabase.table("cham_cong").select("username, trang_thai").eq("id", parsed_edit_id).execute()
            if not existing_res.data:
                return JSONResponse(
                    status_code=status.HTTP_404_NOT_FOUND,
                    content={"success": False, "message": "❌ Không tìm thấy đơn chấm công cần chỉnh sửa!"}
                )
            
            existing_record = existing_res.data[0]
            
            # 1. Kiểm tra đơn đã duyệt chưa (chỉ chặn nếu đã duyệt)
            if existing_record.get("trang_thai") == "Đã duyệt":
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={"success": False, "message": "❌ Đơn này đã được duyệt, không thể chỉnh sửa!"}
                )
                
            # 2. Kiểm tra quyền sở hữu đơn
            is_owner = (existing_record.get("username") == session_user) or (user_role in ("Admin", "Super Admin", "System Admin"))
            if not is_owner:
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={"success": False, "message": "❌ Bạn không có quyền chỉnh sửa đơn của người khác!"}
                )

        # Phân quyền: Chỉ Admin mới được phép chỉ định target_username
        if target_username and target_username.strip():
            if user_role in ("Admin", "Super Admin", "System Admin"):
                target_user = target_username.strip()
                try:
                    emp_res = supabase.table("quan_tri_vien").select("ho_ten").eq("username", target_user).limit(1).execute()
                    ho_ten_target = emp_res.data[0].get("ho_ten", target_user) if emp_res.data else target_user
                except Exception:
                    ho_ten_target = target_user
            else:
                target_user = session_user
                ho_ten_target = session_fullname
        else:
            target_user = session_user
            ho_ten_target = session_fullname
        
        # Kiểm tra trùng hóa đơn
        hop_le, final_hd = check_duplicate_invoice(so_hoa_don, parsed_edit_id)
        if not hop_le:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"success": False, "message": f"❌ Số hóa đơn {final_hd} đã tồn tại!"}
            )

        # Validate File Upload
        final_image_url = existing_image_url
        if file and file.filename:
            if not file.content_type.startswith("image/"):
                return JSONResponse(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    content={"success": False, "message": "❌ Định dạng tệp đính kèm không hợp lệ! Chỉ chấp nhận file ảnh."}
                )

            file_bytes = await file.read()
            if len(file_bytes) > 15 * 1024 * 1024:
                return JSONResponse(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    content={"success": False, "message": "❌ Dung lượng ảnh quá lớn (vượt quá 15MB)!"}
                )

            processed_bytes = process_image(file_bytes, image_rotation)
            cloud_url = upload_image_to_supabase(processed_bytes, file.filename)
            if cloud_url:
                final_image_url = cloud_url
            else:
                return JSONResponse(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    content={"success": False, "message": "❌ Lỗi upload ảnh lên Cloud Storage!"}
                )

        if not final_image_url:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"success": False, "message": "❌ Yêu cầu ảnh đính kèm chứng từ!"}
            )

        actual_so_nguoi_di_cung = so_nguoi_di_cung if is_hotro_khac else 0
        actual_so_ngay_an = so_ngay_an if is_di_tinh else 0
        actual_so_dem_ks = so_dem_ks if is_di_tinh else 0

        cfg = get_config_cham_cong()
        res_tinh = tinh_tien_lap_dat(
            quang_duong=quang_duong,
            so_may_lon=combo_may_lon if not is_di_tinh else 0,
            so_may_nho=combo_may_nho if not is_di_tinh else 0,
            so_may_ep=combo_may_ep if not is_di_tinh else 0,
            so_nguoi_di_cung=actual_so_nguoi_di_cung,
            so_ngay_an=actual_so_ngay_an,
            so_dem_ks=actual_so_dem_ks,
            is_di_tinh=is_di_tinh,
            is_ngoai_gio=is_ngoai_gio,
            gia_thuong_luong=gia_thuong_luong,
            phu_phi_phat_sinh=phu_phi_phat_sinh,
            config=cfg
        )

        noi_dung_final = build_noi_dung(
            noi_dung_goc=clean_noi_dung,
            is_di_tinh=is_di_tinh,
            so_ngay_an=actual_so_ngay_an,
            so_dem_ks=actual_so_dem_ks,
            so_nguoi_di_cung=actual_so_nguoi_di_cung,
            phu_phi_khac=phu_phi_phat_sinh,
            is_ngoai_gio=is_ngoai_gio,
            gia_thuong_luong=gia_thuong_luong
        )

        # Payload khớp 100% các cột DB của public.cham_cong
        data_payload = {
            "username": target_user,
            "ten": ho_ten_target,
            "thoi_gian": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "so_hoa_don": final_hd,
            "noi_dung": noi_dung_final,
            "quang_duong": float(quang_duong),
            "combo": int(combo_may_lon) + int(combo_may_nho) + int(combo_may_ep),
            "thanh_tien": float(res_tinh.get("tong_tien", 0)),
            "device_cost": float(res_tinh.get("device_cost", 0)),
            "distance_cost": float(res_tinh.get("distance_cost", 0)),
            "tho_phu_cost": float(res_tinh.get("tho_phu_cost", 0)),
            "di_tinh_cost": float(res_tinh.get("di_tinh_cost", 0)),
            "tien_ngoai_gio": float(res_tinh.get("tien_ngoai_gio", 0)),
            "phu_phi_phat_sinh": float(res_tinh.get("phu_phi_phat_sinh", 0)),
            "hinh_anh": final_image_url,
            "trang_thai": 'Chờ duyệt',
            "ghi_chu_duyet": None  # Reset lại ghi chú khi sửa/nộp lại đơn
        }

        if parsed_edit_id:
            supabase.table("cham_cong").update(data_payload).eq("id", parsed_edit_id).execute()
            msg = f"✏️ Đã cập nhật hóa đơn {final_hd}!"
        else:
            supabase.table("cham_cong").insert(data_payload).execute()
            msg = f"🎉 Đã gửi phiếu {final_hd} chờ duyệt!"

        return JSONResponse(status_code=status.HTTP_200_OK, content={"success": True, "message": msg})

    except Exception as e:
        logger.error(f"Lỗi nộp phiếu chấm công: {str(e)}")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"success": False, "message": f"❌ Lỗi hệ thống: {str(e)}"}
        )


@router.post("/api/duyet/{item_id}")
async def duyet_phieu(
    request: Request,
    item_id: int,
    payload: DuyetPhieuSchema,
    current_user: dict = Depends(require_login)
):
    """Phê duyệt / Từ chối phiếu (Chỉ dành cho Admin)"""
    try:
        user_role = str(current_user.get("role") or "User").strip()
        if user_role not in ("Admin", "Super Admin", "System Admin"):
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={"success": False, "message": "❌ Từ chối truy cập: Bạn không có quyền quản trị!"}
            )

        # Lấy tên hiển thị của Admin thực hiện duyệt
        admin_fullname = current_user.get("ho_ten") or current_user.get("username", "Admin")

        # Tự động xử lý ghi chú duyệt
        raw_note = payload.ghi_chu_duyet.strip() if payload.ghi_chu_duyet else ""
        
        if not raw_note and payload.trang_thai == "Đã duyệt":
            # Nếu bấm duyệt nhanh (không nhập ghi chú)
            final_note = f"{admin_fullname} - duyệt đơn hợp lệ"
        elif raw_note:
            # Nếu admin có nhập ghi chú riêng
            final_note = f"{admin_fullname}: {raw_note}"
        else:
            final_note = raw_note

        # Payload đúng chuẩn các cột tồn tại trong DB bảng cham_cong
        update_data = {
            "trang_thai": payload.trang_thai,
            "ghi_chu_duyet": final_note
        }

        res = supabase.table('cham_cong').update(update_data).eq('id', item_id).execute()
        if res.data:
            return {"success": True, "message": f"Đã cập nhật trạng thái: {payload.trang_thai}"}

        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"success": False, "message": "Không tìm thấy phiếu chấm công"}
        )
    except Exception as e:
        logger.error(f"Lỗi duyệt phiếu {item_id}: {str(e)}")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"success": False, "message": f"Lỗi hệ thống: {str(e)}"}
        )


# ==========================================
# 3.1Bổ sung xóa đơn (ADMIN)
# ==========================================
@router.delete("/api/delete/{item_id}")
async def delete_cham_cong(
    item_id: int,
    current_user: dict = Depends(require_login)
):
    """Xóa phiếu chấm công (Admin xóa mọi phiếu, User chỉ xóa phiếu của chính mình khi chưa duyệt)"""
    try:
        session_user = current_user.get("username", "")
        user_role = str(current_user.get("role") or "User").strip()

        # 1. Kiểm tra sự tồn tại của đơn
        existing_res = supabase.table("cham_cong").select("username, trang_thai").eq("id", item_id).execute()
        if not existing_res.data:
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content={"success": False, "message": "❌ Không tìm thấy đơn chấm công cần xóa!"}
            )

        existing_record = existing_res.data[0]
        is_admin = user_role in ("Admin", "Super Admin", "System Admin")
        is_owner = existing_record.get("username") == session_user

        # 2. Kiểm tra quyền xóa
        if not is_admin:
            if not is_owner:
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={"success": False, "message": "❌ Bạn không có quyền xóa đơn của người khác!"}
                )
            
            # Nếu là chính chủ nhưng đơn đã duyệt -> Không cho xóa
            if existing_record.get("trang_thai") == "Đã duyệt":
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={"success": False, "message": "❌ Đơn đã được duyệt, không thể xóa!"}
                )

        # 3. Thực hiện xóa trong DB
        supabase.table("cham_cong").delete().eq("id", item_id).execute()

        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"success": True, "message": "🗑️ Đã xóa phiếu chấm công thành công!"}
        )

    except Exception as e:
        logger.error(f"Lỗi xóa phiếu chấm công {item_id}: {str(e)}")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"success": False, "message": f"❌ Lỗi hệ thống: {str(e)}"}
        )

# ==========================================
# 4. ROUTES QUẢN LÝ CẤU HÌNH ĐỊNH MỨC (ADMIN)
# ==========================================

@router.get("/config-view", response_class=HTMLResponse)
async def get_config_page(
    request: Request,
    current_user: dict = Depends(require_login)
):
    """Trang quản trị giao diện điều chỉnh định mức chấm công cho Admin"""
    user_role = str(current_user.get("role") or "User").strip()
    if user_role not in ("Admin", "Super Admin", "System Admin"):
        return HTMLResponse(content="<h3>❌ Bạn không có quyền truy cập trang này!</h3>", status_code=403)

    cfg = get_config_cham_cong()
    return templates.TemplateResponse(
        request=request,
        name="config_cham_cong.html",
        context={"request": request, "config": cfg, "current_user": current_user}
    )


@router.post("/api/config/update")
async def update_config_cham_cong(
    request: Request,
    current_user: dict = Depends(require_login)
):
    """API lưu toàn bộ thông số định mức mới vào Supabase (Yêu cầu Admin)"""
    try:
        user_role = str(current_user.get("role") or "User").strip()
        if user_role not in ("Admin", "Super Admin", "System Admin"):
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={"success": False, "message": "❌ Bạn không có quyền thực hiện thao tác này!"}
            )

        form_data = await request.form()
        
        for key, value in form_data.items():
            if value is not None and str(value).strip() != "":
                try:
                    val_num = float(str(value).strip())
                    supabase.table("config_cham_cong").upsert(
                        {"key_name": key, "value_num": val_num},
                        on_conflict="key_name"
                    ).execute()
                except ValueError:
                    continue

        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"success": True, "message": "✅ Đã lưu cấu hình mới thành công!"}
        )
    except Exception as e:
        logger.error(f"Lỗi cập nhật config: {e}")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"success": False, "message": f"❌ Lỗi hệ thống: {str(e)}"}
        )


@router.get("/list", response_class=HTMLResponse)
async def view_danh_sach_cham_cong(
    request: Request,
    current_user: dict = Depends(require_login),
    status_filter: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    thang: Optional[int] = Query(None),
    nam: Optional[int] = Query(None),
    ktv: Optional[str] = Query(None),
    limit: int = Query(100)
):
    """Giao diện danh sách đơn phân quyền theo User / Admin chuẩn 100% Schema DB"""
    try:
        user_role = str(current_user.get("role") or "User").strip()
        current_username = current_user.get("username") or ""

        now = datetime.now()
        current_year = now.year
        current_month = now.month

        selected_month = thang if thang is not None else current_month
        selected_year = nam if nam is not None else current_year

        # 1. Query dữ liệu từ Supabase
        query = supabase.table("cham_cong").select("*")

        # 2. PHÂN QUYỀN: User thường chỉ xem đơn của chính mình
        if user_role not in ("Admin", "Super Admin", "System Admin"):
            query = query.eq("username", current_username)

        # 3. LỌC THEO TRẠNG THÁI
        if status_filter and status_filter in ["Chờ duyệt", "Đã duyệt", "Từ chối"]:
            query = query.eq("trang_thai", status_filter)

        # 4. LỌC THEO KỸ THUẬT VIÊN (Chỉ dành cho Admin)
        if ktv and ktv != "Tất cả" and user_role in ("Admin", "Super Admin", "System Admin"):
            query = query.eq("username", ktv)

        # 5. LỌC THEO THÁNG & NĂM (Dựa vào cột thoi_gian)
        if selected_year > 0:
            if selected_month > 0:
                start_date = f"{selected_year}-{selected_month:02d}-01T00:00:00"
                if selected_month == 12:
                    end_date = f"{selected_year + 1}-01-01T00:00:00"
                else:
                    end_date = f"{selected_year}-{selected_month + 1:02d}-01T00:00:00"
                query = query.gte("thoi_gian", start_date).lt("thoi_gian", end_date)
            else:
                query = query.gte("thoi_gian", f"{selected_year}-01-01T00:00:00").lt("thoi_gian", f"{selected_year + 1}-01-01T00:00:00")

        # 6. TÌM KIẾM TỪ KHÓA
        if search and search.strip():
            clean_search = search.strip()
            query = query.or_(
                f"username.ilike.%{clean_search}%,"
                f"ten.ilike.%{clean_search}%,"
                f"noi_dung.ilike.%{clean_search}%,"
                f"so_hoa_don.ilike.%{clean_search}%"
            )

        # 7. TRUY VẤN VÀ SẮP XẾP GẦN NHẤT
        res = query.order("id", desc=True).limit(limit).execute()
        danh_sach = res.data or []
        
        logger.info(f"SỐ LƯỢNG ĐƠN LẤY ĐƯỢC: {len(danh_sach)}")

        # Parse iso format thoi_gian -> datetime
        for item in danh_sach:
            try:
                parse_datetime_field(item)
            except Exception as parse_err:
                logger.warning(f"Lỗi parse datetime cho ID {item.get('id')}: {parse_err}")

        # 8. LẤY DANH SÁCH KTV CHO DROPDOWN BỘ LỌC
        danh_sach_ktv = []
        if user_role in ("Admin", "Super Admin", "System Admin"):
            try:
                ktv_res = supabase.table("cham_cong").select("username, ten").execute()
                raw_ktv = ktv_res.data or []
                seen = set()
                for k in raw_ktv:
                    u = k.get("username")
                    if u and u not in seen:
                        seen.add(u)
                        display_name = k.get("ten") or u
                        danh_sach_ktv.append((u, display_name))
            except Exception as ktv_err:
                logger.error(f"Lỗi tải danh sách KTV: {ktv_err}")

        # 9. RENDER TEMPLATE (Đã sửa đủ tham số context)
        return templates.TemplateResponse(
            request=request,
            name="danh_sach_cham_cong.html",  # Hoặc tên file template danh sách thực tế của bạn
            context={
                "request": request,
                "danh_sach": danh_sach,
                "current_user": current_user,
                "user_role": user_role,
                "current_username": current_username,
                "danh_sach_ktv": danh_sach_ktv,
                "selected_month": selected_month,
                "selected_year": selected_year,
                "status_filter": status_filter or "",
                "search": search or "",
                "ktv": ktv or ""
            }
        )
    except Exception as e:
        logger.error(f"Lỗi tải danh sách chấm công: {str(e)}")
        return HTMLResponse(content=f"<h3>Lỗi hệ thống: {str(e)}</h3>", status_code=500)