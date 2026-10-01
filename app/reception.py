from datetime import datetime, timezone
from typing import Optional
from pathlib import Path
import asyncio
import logging
import requests
from fastapi import APIRouter, HTTPException, Query, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from config import (
    supabase,
    ZNS_RECEIVE_TEMPLATE_ID,
    ZNS_RETURN_TEMPLATE_ID
)
from .zalo_helper import get_zalo_access_token
from app.websocket_manager import manager, VN_TZ
from app.admin_routes import require_roles

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
router = APIRouter(prefix="/api/reception", tags=["Reception"])
logger = logging.getLogger("uvicorn.error")

# --- SCHEMAS ---
class ReceiveItemRequest(BaseModel):
    customer_name: str = Field(..., min_length=1)
    phone: str = Field(..., min_length=8)
    received_note: Optional[str] = None

class ReturnItemRequest(BaseModel):
    returned_note: Optional[str] = None

# --- GỬI ZNS ---
def send_zalo_reception_msg(phone: str, customer_name: str, code: str, *, msg_type: str):
    """
    msg_type: 'receive' → tiếp nhận máy | 'return' → trả máy
    Chỉ truyền đúng 2 biến: customer_name, ticket_code
    """
    template_id = (
        ZNS_RECEIVE_TEMPLATE_ID if msg_type == "receive"
        else ZNS_RETURN_TEMPLATE_ID
    )
    token = get_zalo_access_token()

    if not phone or not token or not template_id:
        logger.warning(f"Thiếu thông tin ZNS [{msg_type}] — phone/token/template_id")
        return None

    # Chuẩn hóa SĐT: 091... → 8491..., giữ nguyên nếu đã có 84
    phone_clean = phone.strip()
    if phone_clean.startswith("0"):
        phone_clean = "84" + phone_clean[1:]
    elif not phone_clean.startswith("84"):
        phone_clean = "84" + phone_clean

    # ✅ ĐÚNG URL — KHÔNG có /send ở cuối
    url = "https://business.openapi.zalo.me/message/template"
    headers = {"access_token": token, "Content-Type": "application/json"}
    payload = {
        "phone": phone_clean,
        "template_id": template_id,
        "template_data": {
            "customer_name": customer_name or "Khách hàng",
            "ticket_code": code
        },
        "tracking_id": f"{msg_type}_{code}_{int(datetime.now().timestamp())}"
    }

    try:
        res = requests.post(url, json=payload, headers=headers, timeout=5)
        data = res.json()
        logger.info(f"📨 ZNS [{msg_type}] {code}: {data}")
        return data
    except Exception as e:
        logger.error(f"Lỗi gửi ZNS: {e}")
        return None

# --- TRANG GIAO DIỆN ---
@router.get("/delivery", response_class=HTMLResponse)
async def reception_page(
    request: Request,
    user: dict = Depends(require_roles([ "cskh", "ktv", "admin", "super admin", "system admin"]))
):
    return templates.TemplateResponse(
        request=request,
        name="reception.html",
        context={"current_user": user}
    )

# --- 1. TIẾP NHẬN MÁY ---
@router.post("/receive")
async def create_reception_record(data: ReceiveItemRequest):
    try:
        phone_clean = data.phone.strip()
        name_clean = data.customer_name.strip()

        # Sinh mã phiếu: NT-xxxx-xxx (đếm tăng dần trong ngày)
        today_str = datetime.now(VN_TZ).strftime("%Y%m%d")
        prefix = f"NT-{today_str[-4:]}-"

        # Lấy số thứ tự tiếp theo trong ngày
        today_start = datetime.now(VN_TZ).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        res = supabase.table("reception_records") \
            .select("code") \
            .gte("received_date", today_start) \
            .order("created_at", desc=True) \
            .limit(1) \
            .execute()

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

        # ✅ Khớp 100% cấu trúc bảng reception_records
        payload = {
            "code": code,
            "customer_name": name_clean,
            "phone": phone_clean,
            "received_note": data.received_note,
            "status": "received",
            "received_date": datetime.now(timezone.utc).isoformat()
            # created_at / updated_at có default → không cần truyền
        }

        insert_res = supabase.table("reception_records").insert(payload).execute()
        if not insert_res.data or len(insert_res.data) == 0:
            raise HTTPException(status_code=500, detail="Không thể lưu phiếu vào CSDL")

        record = insert_res.data[0]

        # Gửi ZNS — chạy nền không block
        asyncio.create_task(asyncio.to_thread(
            send_zalo_reception_msg,
            phone_clean, name_clean, code,
            msg_type="receive"
        ))

        await manager.broadcast({
            "type": "reception_new",
            "record": record
        })

        return {
            "success": True,
            "message": "Đã tạo phiếu & gửi Zalo cho khách!",
            "data": record
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Lỗi tiếp nhận: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# --- 2. TRA CỨU ---
@router.get("/search")
async def search_reception_records(query_str: str = Query(..., description="SĐT / Tên / Mã phiếu")):
    try:
        q = query_str.strip()
        if not q:
            return {"success": True, "total": 0, "data": []}

        res = supabase.table("reception_records") \
            .select("*") \
            .or_(f"phone.ilike.%{q}%,customer_name.ilike.%{q}%,code.ilike.%{q}%") \
            .order("created_at", desc=True) \
            .limit(50) \
            .execute()

        return {
            "success": True,
            "total": len(res.data or []),
            "data": res.data or []
        }
    except Exception as e:
        logger.error(f"Lỗi tra cứu: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# --- 3. TRẢ MÁY ---
@router.post("/return/{record_id}")
async def return_item_to_customer(record_id: str, data: ReturnItemRequest):
    try:
        now_utc = datetime.now(timezone.utc).isoformat()

        # ✅ Khớp cấu trúc bảng: updated_at cũng cập nhật
        payload = {
            "status": "returned",
            "returned_date": now_utc,
            "returned_note": data.returned_note,
            "updated_at": now_utc
        }

        res = supabase.table("reception_records") \
            .update(payload) \
            .eq("id", record_id) \
            .execute()

        if not res.data or len(res.data) == 0:
            raise HTTPException(status_code=404, detail="Không tìm thấy phiếu nhận hàng")

        record = res.data[0]

        # Gửi ZNS thông báo đã trả máy
        asyncio.create_task(asyncio.to_thread(
            send_zalo_reception_msg,
            record["phone"],
            record["customer_name"],
            record["code"],
            msg_type="return"
        ))

        await manager.broadcast({
            "type": "reception_returned",
            "record": record
        })

        return {
            "success": True,
            "message": "Đã trả máy & gửi Zalo cho khách!",
            "data": record
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Lỗi trả máy: {e}")
        raise HTTPException(status_code=500, detail=str(e))