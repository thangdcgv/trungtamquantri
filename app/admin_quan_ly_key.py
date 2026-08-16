
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional, Dict, Any, List

from fastapi import APIRouter, Request, Query, HTTPException, Depends
from app.auth import require_login
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from config import supabase

# --- ROUTER DEFINITIONS ---
router = APIRouter(
    prefix="/admin",
    tags=["Admin - Quản Lý Key"]
)
api_router = APIRouter(
    prefix="/api/quan-ly-key", 
    tags=["Admin API - Xuất Key"]
)

templates = Jinja2Templates(directory="app/templates")


# =========================================================================
# 0. DYNAMIC CONFIG SCHEMA (Cấu hình Form Động)
# =========================================================================
DEVICE_CONFIGS: Dict[str, Dict[str, Any]] = {
    "may_in": {
        "id": "may_in",
        "name": "Máy in",
        "icon": "fa-print",
        "fields": [
            {
                "name": "so_seri",
                "label": "Số Seri Máy (Serial)",
                "type": "text",
                "placeholder": "VD: S6298112...",
                "required": True,
                "is_primary_id": True,
                "uppercase": True,
                "mono": True,
                "col_span": "sm:col-span-2 lg:col-span-1"
            },
            {
                "name": "license",
                "label": "Phiên bản License",
                "type": "text",
                "placeholder": "VD: v1.2...",
                "required": False,
                "col_span": "col-span-1"
            },
            {
                "name": "firmware",
                "label": "Loại Firmware",
                "type": "text",
                "placeholder": "VD: Chipless...",
                "required": False,
                "col_span": "col-span-1"
            },
            {
                "name": "ghi_chu_bo_sung",
                "label": "Ghi chú bổ sung",
                "type": "text",
                "placeholder": "Tên khách hàng hoặc ghi chú kỹ thuật...",
                "required": False,
                "col_span": "col-span-full"
            }
        ]
    },
    "may_cat": {
        "id": "may_cat",
        "name": "Máy cắt bế",
        "icon": "fa-scissors",
        "fields": [
            {
                "name": "sdt_khach",
                "label": "SĐT Khách Hàng",
                "type": "text",
                "placeholder": "0901234567",
                "required": True,
                "is_primary_id": True,
                "col_span": "col-span-1"
            },
            {
                "name": "ten_khach",
                "label": "Tên Khách Hàng",
                "type": "text",
                "placeholder": "Anh Nam",
                "required": True,
                "col_span": "col-span-1"
            },
            {
                "name": "tools_ver",
                "label": "Phiên bản Tools",
                "type": "text",
                "placeholder": "VD: Magic cut v5.3...",
                "required": True,
                "col_span": "sm:col-span-2 lg:col-span-1"
            },
            {
                "name": "ghi_chu_may_cat",
                "label": "Ghi chú bổ sung",
                "type": "text",
                "placeholder": "Ghi chú máy cắt...",
                "required": False,
                "col_span": "col-span-full"
            }
        ]
    },
    "thiet_bi_khac": {
        "id": "thiet_bi_khac",
        "name": "Thiết bị khác",
        "icon": "fa-cube",
        "fields": [
            {
                "name": "dinh_danh",
                "label": "Mã định danh / Seri",
                "type": "text",
                "placeholder": "NHẬP MÃ ĐỊNH DANH...",
                "required": True,
                "is_primary_id": True,
                "uppercase": True,
                "mono": True,
                "col_span": "col-span-full"
            },
            {
                "name": "ghi_chu_chi_tiet",
                "label": "Ghi chú chi tiết",
                "type": "text",
                "placeholder": "Thông tin chi tiết...",
                "required": False,
                "col_span": "col-span-full"
            }
        ]
    }
}


# --- HELPER FUNCTIONS ---
def format_date_vn(dt_str: Optional[str]) -> str:
    if not dt_str:
        return "-"
    try:
        dt_str_clean = str(dt_str).replace("Z", "+00:00")
        dt = datetime.fromisoformat(dt_str_clean)
        dt_vn = dt.astimezone(ZoneInfo("Asia/Ho_Chi_Minh"))
        return dt_vn.strftime("%d/%m/%Y %H:%M")
    except Exception:
        return str(dt_str)[:19]


def resolve_device_name(device_input: str) -> str:
    """Ánh xạ ID thiết bị (VD: 'may_in') thành Tên hiển thị CSDL (VD: '🖨️ Máy in')."""
    if device_input in DEVICE_CONFIGS:
        return DEVICE_CONFIGS[device_input]["name"]
    for config in DEVICE_CONFIGS.values():
        if config["name"] == device_input:
            return config["name"]
    return device_input


# --- SCHEMAS FOR API REQUESTS ---
class ConfirmSuccessReq(BaseModel):
    loai_thiet_bi: str
    ten_may: str
    key_id: int
    dinh_danh_may: str
    sdt_khach_hang: Optional[str] = None
    ghi_chu: Optional[str] = ""

class NapKeyNhanhSchema(BaseModel):
    ma_key: str
    loai_thiet_bi: Optional[str] = "Máy in"
    dinh_danh: Optional[str] = ""
    ghi_chu: Optional[str] = ""

class ReportFailReq(BaseModel):
    key_id: int
    loai_thiet_bi: str
    ten_may: str
    ly_do: Optional[str] = "Key không hoạt động trên thiết bị"

# =========================================================================
# 1. TRANG CHÍNH: GIAO DIỆN QUẢN LÝ & XUẤT KEY (ĐÃ GỘP TÌM KIẾM)
# =========================================================================
@router.get("/quan-ly-key", response_class=HTMLResponse)
async def trang_quan_ly_key(
    request: Request,
    search: Optional[str] = Query(None)
):
    if not request.session.get("user_id"):
        return RedirectResponse(url="/auth/login", status_code=303)
    search_keyword = search.strip() if search else ""
    history_logs = []

    try:
        # 1. Khởi tạo query từ bảng quan_ly_key
        query = supabase.table("quan_ly_key").select("*")

        # 2. Xử lý lọc điều kiện nếu có từ khóa tìm kiếm
        if search_keyword:
            pattern = f"%{search_keyword}%"
            or_clause = (
                f"so_seri.ilike.{pattern},"
                f"sdt_khach_hang.ilike.{pattern},"
                f"key_chipless.ilike.{pattern},"
                f"ten_may.ilike.{pattern},"
                f"username.ilike.{pattern},"
                f"ghi_chu.ilike.{pattern}"
            )
            query = query.or_(or_clause)

        # 3. Lấy dữ liệu (nếu tìm kiếm lấy 50 bản ghi, mặc định lấy 20 bản ghi mới nhất)
        limit_count = 15 if search_keyword else 10
        res_hist = query.order("id", desc=True).limit(limit_count).execute()

        history_logs = res_hist.data or []
        
        # 4. Định dạng thời gian Việt Nam
        for h in history_logs:
            dt_val = h.get("thoi_gian") or h.get("created_at") or h.get("ngay_tao")
            h["thoi_gian_fmt"] = format_date_vn(dt_val)

    except Exception as e:
        print(f"❌ Lỗi lấy lịch sử / tìm kiếm key: {repr(e)}", flush=True)
        import traceback
        traceback.print_exc()

    return templates.TemplateResponse(
        request=request,
        name="admin_quan_ly_key.html",
        context={
            "request": request,
            "title": "Quản Lý Key - Máy In Đại Thành",
            "history_logs": history_logs,
            "search_keyword": search_keyword  # Giữ từ khóa trên ô input
        }
    )
@router.get("/api/quan-ly-key/search-live")
async def search_live(
    q: Optional[str] = Query(None),
    loai_thiet_bi: Optional[str] = Query(None)
):
    """
    API Tra cứu nhật ký cấp Key thời gian thực (Live Search)
    """
    try:
        # Truy vấn dữ liệu từ Supabase
        query = supabase.table("quan_ly_key").select("*")

        if loai_thiet_bi:
            query = query.eq("loai_thiet_bi", loai_thiet_bi)

        if q:
            # Tìm kiếm chứa từ khóa trong các trường ten_may, so_seri, key_chipless, ghi_chu
            query = query.or_(
                f"ten_may.ilike.%{q}%,so_seri.ilike.%{q}%,key_chipless.ilike.%{q}%,ghi_chu.ilike.%{q}%"
            )

        # Sắp xếp mới nhất lên đầu
        res = query.order("thoi_gian", desc=True).limit(50).execute()
        items = res.data or []

        # 👉 BỔ SUNG ĐOẠN NÀY ĐỂ ĐỊNH DẠNG THỜI GIAN CHO LIVE SEARCH
        for h in items:
            dt_val = h.get("thoi_gian") or h.get("created_at") or h.get("ngay_tao")
            h["thoi_gian_fmt"] = format_date_vn(dt_val)

        return {"status": "success", "items": items}
    except Exception as e:
        return {"status": "error", "message": str(e), "items": []}
# =========================================================================
# 2. CÁC API ENDPOINTS
# =========================================================================

# API 0: Lấy Schema Dynamic Config cho Frontend
@api_router.get("/config-schema")
async def get_config_schema():
    """Trả về Schema động cấu hình giao diện Form."""
    return {"status": "success", "devices": DEVICE_CONFIGS}


# API 1: Lấy danh sách Model khả dụng theo loại thiết bị
@api_router.get("/get-models")
async def get_models(loai_thiet_bi: str = Query(...)):
    try:
        real_device_name = resolve_device_name(loai_thiet_bi.strip())

        res = supabase.table("kho_key")\
            .select("ten_may")\
            .eq("trang_thai", "Còn lượt")\
            .eq("loai_thiet_bi", real_device_name)\
            .execute()

        if not res.data:
            return {"models": []}

        models = sorted(list(set([r["ten_may"] for r in res.data if r.get("ten_may")])))
        return {"models": models}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Lỗi lấy danh sách Model: {str(e)}")


# API 2: Lấy thông tin Mã Key XEM TRƯỚC (Preview - Chưa trừ lượt)
@api_router.get("/get-available-key")
async def get_available_key(loai_thiet_bi: str = Query(...), ten_may: str = Query(...)):
    try:
        real_device_name = resolve_device_name(loai_thiet_bi.strip())

        res = supabase.table("kho_key").select("*")\
            .eq("loai_thiet_bi", real_device_name)\
            .eq("ten_may", ten_may.strip())\
            .eq("trang_thai", "Còn lượt")\
            .order("da_dung", desc=False)\
            .order("ngay_nhap", desc=False)\
            .limit(1).execute()

        if not res.data:
            return {"available": False, "message": "Đã hết key khả dụng cho model này"}

        key_info = res.data[0]
        ton_thuc_te = key_info.get("gioi_han", 1) - key_info.get("da_dung", 0)

        return {
            "available": True,
            "key_data": {
                "id": key_info["id"],
                "ma_key": key_info["ma_key"],
                "gioi_han": key_info.get("gioi_han", 1),
                "da_dung": key_info.get("da_dung", 0),
                "ton_thuc_te": ton_thuc_te
            }
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Lỗi truy vấn key: {str(e)}")


# API 3: Xác nhận NẠP THÀNH CÔNG -> Ghi lịch sử & Trừ lượt trong Kho
@api_router.post("/confirm-success")
async def confirm_success_action(
    payload: ConfirmSuccessReq, 
    request: Request,
    current_user: dict = Depends(require_login)  # <-- TẬN DỤNG TẠI ĐÂY
):
    try:
        dinh_danh = payload.dinh_danh_may.strip()
        if not dinh_danh:
            raise HTTPException(status_code=400, detail="Mã định danh hoặc Số Serial không được để trống!")

        real_device_name = resolve_device_name(payload.loai_thiet_bi.strip())

        # 1. Kiểm tra trùng Serial/Định danh trong lịch sử
        res_check = supabase.table("quan_ly_key").select("id").eq("so_seri", dinh_danh).execute()
        is_duplicate = len(res_check.data) > 0 if res_check.data else False

        # 2. Kiểm tra lại trạng thái Key từ CSDL kho_key
        res_key = supabase.table("kho_key").select("*").eq("id", payload.key_id).execute()
        if not res_key.data or res_key.data[0].get("trang_thai") != "Còn lượt":
            raise HTTPException(status_code=400, detail="Mã Key này không còn khả dụng để hoàn tất!")

        key_info = res_key.data[0]

        # =========================================================================
        # 3. LẤY USER CHÍNH XÁC TỪ DEPENDENCY (ĐÃ TỐI ƯU SIÊU NGẮN GỌN)
        # =========================================================================
        # Ưu tiên lấy username -> nếu không có thì lấy ho_ten -> mặc định KTV
        user_name = current_user.get("username") or current_user.get("ho_ten") or "Kỹ thuật viên"

        # 4. Xác định SĐT khách hàng
        sdt_khach = payload.sdt_khach_hang
        if not sdt_khach and "Máy cắt" in real_device_name:
            sdt_khach = dinh_danh

        # 5. GHI LỊCH SỬ CHÍNH THỨC vào `quan_ly_key`
        data_history = {
            "loai_thiet_bi": real_device_name,
            "ten_may": payload.ten_may,
            "so_seri": dinh_danh,
            "key_chipless": key_info["ma_key"],
            "sdt_khach_hang": sdt_khach,
            "limit_may": key_info.get("gioi_han", 1),
            "username": user_name,  # Lưu tên user chính xác tuyệt đối
            "ghi_chu": payload.ghi_chu
        }
        
        insert_res = supabase.table("quan_ly_key").insert(data_history).execute()
        if not insert_res.data:
            raise HTTPException(status_code=500, detail="Lỗi lưu lịch sử xuất key vào CSDL")

        # 6. TRỪ LƯỢT VÀ CẬP NHẬT TRẠNG THÁI ở `kho_key`
        next_da_dung = key_info.get("da_dung", 0) + 1
        gioi_han = key_info.get("gioi_han", 1)
        next_status = "Còn lượt" if next_da_dung < gioi_han else "Hết lượt"

        supabase.table("kho_key").update({
            "da_dung": next_da_dung,
            "trang_thai": next_status
        }).eq("id", key_info["id"]).execute()

        return {
            "status": "success",
            "message": "Đã xác nhận nạp Key thành công và lưu lịch sử!",
            "is_duplicate_warning": is_duplicate,
            "key_issued": key_info["ma_key"]
        }

    except HTTPException as http_ex:
        raise http_ex
    except Exception as e:
        err_msg = str(e)
        if "23505" in err_msg or "unique" in err_msg:
            raise HTTPException(status_code=400, detail="Số Seri/Định danh này đã được lưu trước đó!")
        raise HTTPException(status_code=500, detail=f"Lỗi hệ thống khi chốt key thành công: {err_msg}")


@api_router.post("/report-fail-retry")
async def report_fail_retry_action(
    payload: ReportFailReq,
    current_user: dict = Depends(require_login)  # Sử dụng require_login
):
    try:
        # Lấy danh tính người dùng thực hiện báo lỗi
        username_ktv = (
            current_user.get("username") or 
            current_user.get("ho_ten") or 
            current_user.get("name") or 
            current_user.get("email") or 
            "Kỹ thuật viên"
        )

        real_device_name = resolve_device_name(payload.loai_thiet_bi.strip())

        # 1. Đánh dấu Key bị lỗi trong `kho_key` thành 'Báo lỗi'
        supabase.table("kho_key").update({
            "trang_thai": "Báo lỗi"
        }).eq("id", payload.key_id).execute()

        print(f"⚠️ [KEY FAILED]: KTV '{username_ktv}' đã đánh dấu Key ID {payload.key_id} là 'Báo lỗi'", flush=True)

        # 2. Tìm Key thay thế tiếp theo trong kho còn lượt
        res_next = supabase.table("kho_key").select("*")\
            .eq("loai_thiet_bi", real_device_name)\
            .eq("ten_may", payload.ten_may.strip())\
            .eq("trang_thai", "Còn lượt")\
            .order("da_dung", desc=False)\
            .order("ngay_nhap", desc=False)\
            .limit(1).execute()

        if not res_next.data:
            return {
                "available": False,
                "message": f"KTV {username_ktv} đã ghi nhận Key lỗi. Tuy nhiên trong kho hiện đã HẾT Key thay thế cho model này!"
            }

        new_key_info = res_next.data[0]
        ton_thuc_te = new_key_info.get("gioi_han", 1) - new_key_info.get("da_dung", 0)

        return {
            "available": True,
            "message": "Đã đổi sang Key mới! Vui lòng thử nạp lại.",
            "reported_by": username_ktv,
            "new_key_data": {
                "id": new_key_info["id"],
                "ma_key": new_key_info["ma_key"],
                "gioi_han": new_key_info.get("gioi_han", 1),
                "da_dung": new_key_info.get("da_dung", 0),
                "ton_thuc_te": ton_thuc_te
            }
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi hệ thống khi xử lý báo lỗi đổi key: {str(e)}")


@api_router.post("/nap-key-nhanh")
async def api_nap_key_nhanh(
    data: NapKeyNhanhSchema,
    current_user: dict = Depends(require_login)  # Sử dụng require_login
):
    ma_key = data.ma_key.strip()
    if not ma_key:
        raise HTTPException(status_code=400, detail="Mã Key không được để trống!")

    try:
        # Lấy chính xác danh tính người dùng từ require_login
        username_ktv = (
            current_user.get("username") or 
            current_user.get("ho_ten") or 
            current_user.get("name") or 
            current_user.get("email") or 
            "Kỹ thuật viên"
        )

        # 1. Truy vấn kiểm tra key trong kho_key
        res_key = supabase.table("kho_key").select("*").eq("ma_key", ma_key).execute()
        existing_keys = res_key.data or []

        if existing_keys:
            # --- TRƯỜNG HỢP 1: KEY ĐÃ TỒN TẠI TRONG KHO ---
            key_item = existing_keys[0]
            gioi_han = key_item.get("gioi_han") or 1
            da_dung = key_item.get("da_dung") or 0
            trang_thai = key_item.get("trang_thai", "Còn lượt")

            # Kiểm tra nếu số lần đã dùng >= giới hạn hoặc trạng thái báo Hết lượt
            if da_dung >= gioi_han or trang_thai == "Hết lượt":
                return JSONResponse(
                    status_code=400,
                    content={
                        "success": False, 
                        "message": f"❌ Key [{ma_key}] đã HẾT LƯỢT sử dụng! (Đã dùng {da_dung}/{gioi_han} lượt)."
                    }
                )

            # Còn lượt: Tăng số lần đã dùng lên 1
            da_dung_moi = da_dung + 1
            trang_thai_moi = "Hết lượt" if da_dung_moi >= gioi_han else "Còn lượt"

            # Cập nhật CSDL kho_key
            supabase.table("kho_key").update({
                "da_dung": da_dung_moi,
                "trang_thai": trang_thai_moi
            }).eq("id", key_item["id"]).execute()

            ten_may = key_item.get("ten_may") or data.loai_thiet_bi
            luot_con_lai = gioi_han - da_dung_moi

        else:
            # --- TRƯỜNG HỢP 2: KEY CHƯA CÓ -> TỰ ĐỘNG THÊM MỚI (DÙNG NGAY 1/1 LƯỢT) ---
            new_key_data = {
                "ten_may": data.loai_thiet_bi,
                "ma_key": ma_key,
                "gioi_han": 1,
                "da_dung": 1,
                "trang_thai": "Hết lượt",
                "loai_thiet_bi": data.loai_thiet_bi
            }
            supabase.table("kho_key").insert(new_key_data).execute()
            ten_may = data.loai_thiet_bi
            luot_con_lai = 0

        # 2. Ghi nhật ký vào bảng quan_ly_key
        log_entry = {
            "loai_thiet_bi": data.loai_thiet_bi,
            "ten_may": ten_may,
            "username": username_ktv,
            "so_seri": data.dinh_danh or "-",
            "key_chipless": ma_key,
            "ghi_chu": data.ghi_chu or f"KTV {username_ktv} nạp key nhanh"
        }
        supabase.table("quan_ly_key").insert(log_entry).execute()

        return {
            "success": True,
            "message": f"✅ Xác nhận sử dụng Key [{ma_key}] thành công! (Còn lại: {luot_con_lai} lượt)"
        }

    except Exception as e:
        print(f"❌ Lỗi Nạp Key Nhanh: {repr(e)}")
        raise HTTPException(status_code=500, detail=f"Lỗi xử lý CSDL: {str(e)}")

