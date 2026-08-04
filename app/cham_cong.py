import io
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, Request, UploadFile, status, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from PIL import Image, ImageOps
from pydantic import BaseModel, Field

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
    "price_may_nho": 50000,
    "price_may_ep_near": 80000, # <= 20km
    "price_may_ep_far": 50000   # > 20km
}


def get_config_cham_cong() -> dict:
    """Lấy cấu hình đơn giá linh hoạt từ cau_hinh_cham_cong hoặc config_cham_cong"""
    config = DEFAULT_CONFIG.copy()
    try:
        res_row = supabase.table("cau_hinh_cham_cong").select("*").limit(1).execute()
        if res_row.data and len(res_row.data) > 0:
            for k, v in res_row.data[0].items():
                if k != "id" and v is not None:
                    try:
                        config[k] = float(v)
                    except (ValueError, TypeError):
                        config[k] = v
            return config
    except Exception:
        pass

    try:
        res_cfg = supabase.table("config_cham_cong").select("key_name, value_num").execute()
        if res_cfg.data:
            for item in res_cfg.data:
                config[item['key_name']] = float(item['value_num'])
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
        bucket_name = "cham_cong_images"
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
    """Kiểm tra mã hóa đơn trùng lặp"""
    so_hd_clean = so_hd.strip().upper()
    formatted_hd = so_hd_clean if so_hd_clean.startswith("HD") else f"HD{so_hd_clean}"
    
    query = supabase.table("cham_cong").select("id").eq("so_hoa_don", formatted_hd)
    if edit_id:
        query = query.neq("id", edit_id)
        
    res = query.execute()
    if res.data and len(res.data) > 0:
        return False, formatted_hd
    return True, formatted_hd


def parse_datetime_field(item: dict):
    """Hỗ trợ chuyển đổi trường thoi_gian từ chuỗi sang datetime object để dùng được .strftime"""
    if item and isinstance(item.get("thoi_gian"), str):
        try:
            item["thoi_gian"] = datetime.fromisoformat(item["thoi_gian"].replace("Z", "+00:00"))
        except Exception:
            pass
    return item


def tinh_tien_lap_dat(
    quang_duong: int,
    so_may_lon: int,
    so_may_nho: int,
    so_may_ep: int,
    so_nguoi_di_cung: int,
    so_ngay_an: int,
    so_dem_ks: int,
    is_di_tinh: bool,
    is_ngoai_gio: bool,
    gia_thuong_luong: int,
    phu_phi_phat_sinh: int,
    config: dict
) -> dict:
    """Calculates installation charges dynamically based on DB config"""
    cfg = config or DEFAULT_CONFIG

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
    price_may_nho = cfg.get("price_may_nho", 50000)
    price_may_ep_near = cfg.get("price_may_ep_near", 80000)
    price_may_ep_far = cfg.get("price_may_ep_far", 50000)

    if is_di_tinh:
        tien_quang_duong = 0
        tien_may_lon = 0
        tien_may_nho = 0
        tien_may_ep = 0
        base_di_tinh_total = phu_cap_di_tinh + (so_ngay_an * phu_cap_ngay_an) + (so_dem_ks * phu_cap_dem_ks)
        tong_tien_chinh = base_di_tinh_total
        tien_nguoi_di_cung = so_nguoi_di_cung * base_di_tinh_total
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

        tien_may_lon = so_may_lon * price_may_lon
        tien_may_nho = so_may_nho * price_may_nho
        tien_may_ep = (price_may_ep_near if quang_duong <= 20 else price_may_ep_far) if so_may_ep > 0 else 0

        tong_tien_chinh = tien_quang_duong + tien_may_lon + tien_may_nho + tien_may_ep

        if so_nguoi_di_cung > 0:
            if so_may_ep > 0:
                gia_ep_tho = price_may_ep_near if quang_duong <= 20 else price_may_ep_far
                phi_qd_tho = tien_quang_duong if quang_duong > 20 else 0
                tien_nguoi_di_cung = so_nguoi_di_cung * (gia_ep_tho + phi_qd_tho)
            else:
                tien_nguoi_di_cung = so_nguoi_di_cung * phu_cap_tho_phu
        else:
            tien_nguoi_di_cung = 0

    tien_ngoai_gio = gia_thuong_luong if is_ngoai_gio else 0
    tong_tien = tong_tien_chinh + tien_nguoi_di_cung + tien_ngoai_gio + phu_phi_phat_sinh

    return {
        "tien_quang_duong": tien_quang_duong,
        "tien_nguoi_di_cung": tien_nguoi_di_cung,
        "tong_tien": tong_tien
    }


def build_noi_dung(
    noi_dung_goc: str,
    is_di_tinh: bool,
    so_ngay_an: int,
    so_dem_ks: int,
    so_nguoi_di_cung: int,
    phu_phi_khac: int,
    is_ngoai_gio: bool,
    gia_thuong_luong: int
) -> str:
    """Tạo ghi chú nội dung tổng hợp"""
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
    return f"{noi_dung_goc.strip()}{extra_info}"


# ==========================================
# 2. SCHEMAS
# ==========================================

class DuyetPhieuSchema(BaseModel):
    trang_thai: str = Field(..., description="Trạng thái: 'Đã duyệt', 'Từ chối', hoặc 'Chờ duyệt'")
    ghi_chu_duyet: Optional[str] = Field(default="", description="Ghi chú từ QTV")


# ==========================================
# 3. ROUTES CHẤM CÔNG HÀNG NGÀY
# ==========================================

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def list_cham_cong(request: Request):
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
        
        # Parse thời gian cho toàn bộ danh sách để tránh lỗi strftime trong vòng lặp template
        for item in recent_installations:
            parse_datetime_field(item)
        
        # Thiết lập item đầu tiên làm mặc định
        if recent_installations:
            item = recent_installations[0]
        else:
            item = {
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
                "recent_installations": recent_installations,
                "item": item
            }
        )
    except Exception as e:
        logger.error(f"Lỗi truy vấn danh sách chấm công: {str(e)}")
        return HTMLResponse(content=f"<h3>Lỗi hệ thống: {str(e)}</h3>", status_code=500)


@router.get("/detail/{item_id}", response_class=HTMLResponse)
async def detail_cham_cong(request: Request, item_id: int):
    """Xem chi tiết phiếu chấm công hướng tới giao diện cham_cong_detail.html"""
    try:
        res = supabase.table('cham_cong').select('*').eq('id', item_id).execute()
        if not res.data:
            return RedirectResponse(url="/cham-cong", status_code=status.HTTP_303_SEE_OTHER)

        item = res.data[0]
        parse_datetime_field(item)
        
        return templates.TemplateResponse(
            request=request,
            name="cham_cong_detail.html",
            context={"item": item}
        )
    except Exception as e:
        logger.error(f"Lỗi chi tiết phiếu {item_id}: {str(e)}")
        return RedirectResponse(url="/cham-cong", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/form", response_class=HTMLResponse)
async def get_form_cham_cong(request: Request, edit_id: Optional[int] = None):
    """Giao diện tạo mới / chỉnh sửa phiếu chấm công"""
    try:
        cfg = get_config_cham_cong()
        edit_data = None
        employees_list = []

        user_role = request.session.get("role", "User")
        if user_role == "Admin":
            try:
                emp_res = supabase.table("quan_tri_vien").select("username, ho_ten").execute()
                if emp_res.data:
                    employees_list = emp_res.data
            except Exception as e:
                logger.error(f"Lỗi tải danh sách nhân viên cho Admin: {e}")

        if edit_id:
            res = supabase.table("cham_cong").select("*").eq("id", edit_id).execute()
            if res.data:
                edit_data = res.data[0]

        return templates.TemplateResponse(
            request=request,
            name="lap_dat.html",
            context={
                "config": cfg,
                "edit_data": edit_data,
                "employees_list": employees_list
            }
        )
    except Exception as e:
        logger.error(f"Lỗi tải form chấm công: {e}")
        return HTMLResponse(content=f"<h3>Lỗi hệ thống: {str(e)}</h3>", status_code=500)


@router.get("/api/search-invoice")
async def search_invoice(so_hd: str):
    """API tra cứu hóa đơn theo số HD để chỉnh sửa"""
    try:
        so_hd_clean = so_hd.strip().upper()
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
    so_hoa_don: str = Form(...),
    noi_dung: str = Form(...),
    quang_duong: int = Form(0),
    combo_may_lon: int = Form(0),
    combo_may_nho: int = Form(0),
    combo_may_ep: int = Form(0),
    so_ngay_an: int = Form(0),
    so_dem_ks: int = Form(0),
    so_nguoi_di_cung: int = Form(0),
    phu_phi_phat_sinh: int = Form(0),
    gia_thuong_luong: int = Form(0),
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
        parsed_edit_id = int(edit_id) if edit_id and edit_id.strip().isdigit() else None
        
        session_user = request.session.get("username", "system_user")
        session_fullname = request.session.get("ho_ten") or request.session.get("full_name") or session_user

        if target_username and target_username.strip():
            target_user = target_username.strip()
            try:
                emp_res = supabase.table("quan_tri_vien").select("ho_ten").eq("username", target_user).limit(1).execute()
                ho_ten_target = emp_res.data[0].get("ho_ten", target_user) if emp_res.data else target_user
            except Exception:
                ho_ten_target = target_user
        else:
            target_user = session_user
            ho_ten_target = session_fullname
        
        hop_le, final_hd = check_duplicate_invoice(so_hoa_don, parsed_edit_id)
        if not hop_le:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"success": False, "message": f"❌ Số hóa đơn {final_hd} đã tồn tại!"}
            )

        final_image_url = existing_image_url
        if file and file.filename:
            file_bytes = await file.read()
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
            noi_dung_goc=noi_dung,
            is_di_tinh=is_di_tinh,
            so_ngay_an=actual_so_ngay_an,
            so_dem_ks=actual_so_dem_ks,
            so_nguoi_di_cung=actual_so_nguoi_di_cung,
            phu_phi_khac=phu_phi_phat_sinh,
            is_ngoai_gio=is_ngoai_gio,
            gia_thuong_luong=gia_thuong_luong
        )

        data_payload = {
            "username": target_user,
            "ten": ho_ten_target,
            "thoi_gian": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "so_hoa_don": final_hd,
            "noi_dung": noi_dung_final,
            "quang_duong": int(quang_duong) if not is_di_tinh else 0,
            "combo": int(combo_may_lon) + int(combo_may_nho) + int(combo_may_ep),
            "thanh_tien": float(res_tinh["tong_tien"]),
            "hinh_anh": final_image_url,
            "trang_thai": 'Chờ duyệt'
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
async def duyet_phieu(item_id: int, payload: DuyetPhieuSchema):
    """Phê duyệt / Từ chối phiếu"""
    try:
        update_data = {
            "trang_thai": payload.trang_thai,
            "ghi_chu_duyet": payload.ghi_chu_duyet.strip() if payload.ghi_chu_duyet else ""
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
# 4. ROUTES QUẢN LÝ CẤU HÌNH ĐỊNH MỨC (ADMIN)
# ==========================================

@router.get("/config-view", response_class=HTMLResponse)
async def get_config_page(request: Request):
    """Trang quản trị giao diện điều chỉnh định mức chấm công cho Admin"""
    cfg = get_config_cham_cong()
    return templates.TemplateResponse(
        request=request,
        name="config_cham_cong.html",
        context={"config": cfg}
    )


@router.post("/api/config/update")
async def update_config_cham_cong(request: Request):
    """API lưu toàn bộ thông số định mức mới vào Supabase"""
    try:
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