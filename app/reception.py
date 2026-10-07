import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import re
import urllib.parse

from fastapi import APIRouter, HTTPException, Query, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from config import supabase
from app.websocket_manager import manager, VN_TZ
from app.admin.admin_routes import require_roles
from app.auth import get_current_user_or_redirect

logger = logging.getLogger("uvicorn.error")

# === CẤU HÌNH TEMPLATES ĐỘNG LINH HOẠT ===
BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = (
    BASE_DIR.parent / "templates"
    if (BASE_DIR.parent / "templates").exists()
    else BASE_DIR / "templates"
)
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter(prefix="/api/reception", tags=["Reception"])

# Phân quyền truy cập dùng chung
AUTH_DEPENDENCY = Depends(require_roles(["cskh", "user", "admin", "super admin", "system admin"]))


# --- SCHEMAS ---
class ReceiveItemRequest(BaseModel):
    customer_name: str = Field(..., min_length=1)
    phone: str = Field(..., min_length=8)
    received_note: Optional[str] = None


class ReturnItemRequest(BaseModel):
    returned_note: Optional[str] = None
    returned_method: Optional[str] = Field(None, description="Hình thức hoàn trả: Tại cửa hàng, Gửi Ship, ...")


# --- HELPER TẠO NỘI DUNG VÀ DEEP LINK SMS ---
def generate_sms_info(phone: str, customer_name: str, code: str, note: Optional[str], msg_type: str) -> dict:
    """Tạo nội dung SMS và Deep Link tương thích iPhone/Android/Windows"""
    # 1. Chuẩn hóa SĐT: Chỉ giữ lại chữ số, chuyển 84xxx -> 0xxx
    phone_digits = re.sub(r"\D", "", phone or "")
    if phone_digits.startswith("84"):
        phone_clean = "0" + phone_digits[2:]
    elif not phone_digits.startswith("0") and len(phone_digits) >= 9:
        phone_clean = "0" + phone_digits
    else:
        phone_clean = phone_digits

    now_str = datetime.now(VN_TZ).strftime("%H:%M %d/%m/%Y")
    note_str = note.strip() if note and note.strip() else "Bình thường"

    # 2. Soạn mẫu tin nhắn
    if msg_type == "receive":
        sms_text = (
            f"May in Dai Thanh da tao phieu [{code}] ghi nhan thiet bi cua KH {customer_name} "
            f"luc {now_str}. Tinh trang: {note_str}."
        )
    else:
        sms_text = (
            f"May in Dai Thanh da hoan tra thiet bi [{code}] cho KH {customer_name} "
            f"luc {now_str}. Cam on Quy khach!"
        )

    # 3. Mã hóa URL cho nội dung SMS
    encoded_text = urllib.parse.quote(sms_text)

    # 4. Tạo deep link chuẩn
    sms_link_android = f"sms:{phone_clean}?body={encoded_text}"
    sms_link_ios = f"sms:{phone_clean}&body={encoded_text}"

    return {
        "phone": phone_clean,
        "text": sms_text,
        "sms_link": sms_link_android,
        "sms_link_ios": sms_link_ios
    }


# --- TRANG GIAO DIỆN ---
@router.get("/delivery", response_class=HTMLResponse)
def reception_page(
    request: Request,
    user: dict = Depends(get_current_user_or_redirect)
):
    return templates.TemplateResponse(
        request=request,
        name="reception.html",
        context={"current_user": user}
    )


# --- 1. TIẾP NHẬN MÁY ---
@router.post("/receive")
async def create_reception_record(
    data: ReceiveItemRequest,
    user: dict = Depends(get_current_user_or_redirect)
):
    try:
        phone_clean = data.phone.strip()
        name_clean = data.customer_name.strip()

        now_vn = datetime.now(VN_TZ)
        today_str = now_vn.strftime("%Y%m%d")
        prefix = f"NT-{today_str[-4:]}-"

        # Đặt mốc đầu ngày hôm nay theo UTC ISO format
        today_start = now_vn.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).isoformat()

        # Dùng asyncio.to_thread để không làm block Event Loop chính
        query_last_code = (
            supabase.table("reception_records")
            .select("code")
            .gte("received_date", today_start)
            .order("created_at", desc=True)
            .limit(1)
        )
        res = await asyncio.to_thread(query_last_code.execute)

        next_num = 1
        if res.data and len(res.data) > 0:
            last_code = res.data[0].get("code", "")
            parts = last_code.split("-")
            if len(parts) >= 3:
                try:
                    next_num = int(parts[-1]) + 1
                except (ValueError, IndexError):
                    pass

        code = f"{prefix}{next_num:03d}"

        payload = {
            "code": code,
            "customer_name": name_clean,
            "phone": phone_clean,
            "received_note": data.received_note,
            "status": "received",
            "received_date": datetime.now(timezone.utc).isoformat()
        }

        query_insert = supabase.table("reception_records").insert(payload)
        insert_res = await asyncio.to_thread(query_insert.execute)

        if not insert_res.data or len(insert_res.data) == 0:
            raise HTTPException(status_code=500, detail="Không thể lưu phiếu vào CSDL")

        record = insert_res.data[0]

        # Phát tin nhắn WebSocket cập nhật Realtime cho các máy khác
        await manager.broadcast({
            "type": "reception_new",
            "record": record
        })

        # Tạo thông tin SMS trả về cho Frontend
        sms_info = generate_sms_info(
            phone=phone_clean,
            customer_name=name_clean,
            code=code,
            note=data.received_note,
            msg_type="receive"
        )

        return {
            "success": True,
            "message": "Đã tạo phiếu tiếp nhận!",
            "data": record,
            "sms_info": sms_info
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Lỗi tiếp nhận: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# --- 2. TRA CỨU & TẢI DANH SÁCH MÁY (CÓ PHÂN TRANG) ---
@router.get("/search")
def search_reception_records(  # ✅ Chuyển sang synchronous `def` do chỉ gọi Supabase
    query_str: Optional[str] = Query(None, description="SĐT / Tên / Mã phiếu"),
    page: int = Query(1, ge=1, description="Số trang (bắt đầu từ 1)"),
    limit: int = Query(20, ge=1, le=100, description="Số lượng máy trên 1 trang"),
    user: dict = AUTH_DEPENDENCY
):
    try:
        q = (query_str or "").strip()
        
        db_query = supabase.table("reception_records").select("*", count="exact")

        if q:
            db_query = db_query.or_(f"phone.ilike.%{q}%,customer_name.ilike.%{q}%,code.ilike.%{q}%")

        start_index = (page - 1) * limit
        end_index = start_index + limit - 1

        res = db_query.order("created_at", desc=True).range(start_index, end_index).execute()

        total_records = res.count if res.count is not None else len(res.data or [])
        total_pages = (total_records + limit - 1) // limit if total_records > 0 else 1

        return {
            "success": True,
            "data": res.data or [],
            "pagination": {
                "page": page,
                "limit": limit,
                "total": total_records,
                "total_pages": total_pages
            }
        }
    except Exception as e:
        logger.error(f"Lỗi tra cứu: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# --- 3. TRẢ MÁY ---
@router.post("/return/{record_id}")
async def return_item_to_customer(
    record_id: str, 
    data: ReturnItemRequest,
    user: dict = AUTH_DEPENDENCY
):
    try:
        now_utc = datetime.now(timezone.utc).isoformat()

        payload = {
            "status": "returned",
            "returned_date": now_utc,
            "returned_note": data.returned_note,
            "returned_method": data.returned_method,
            "updated_at": now_utc
        }

        query_update = (
            supabase.table("reception_records")
            .update(payload)
            .eq("id", record_id)
        )
        res = await asyncio.to_thread(query_update.execute)

        if not res.data or len(res.data) == 0:
            raise HTTPException(status_code=404, detail="Không tìm thấy phiếu nhận hàng")

        record = res.data[0]

        # Broadcast thông báo cho các máy khác qua WebSocket
        await manager.broadcast({
            "type": "reception_returned",
            "record": record
        })

        # Tạo thông tin SMS trả máy
        sms_info = generate_sms_info(
            phone=record["phone"],
            customer_name=record["customer_name"],
            code=record["code"],
            note=data.returned_note,
            msg_type="return"
        )

        return {
            "success": True,
            "message": "Đã ghi nhận trả máy!",
            "data": record,
            "sms_info": sms_info
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Lỗi trả máy: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))