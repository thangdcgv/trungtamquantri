from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from config import supabase

router = APIRouter(prefix="/api/tickets", tags=["Bốc số thứ tự"])

# Khai báo múi giờ Việt Nam
VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")

def get_today_start_iso() -> str:
    """Lấy mốc 00:00:00 ngày hôm nay theo giờ Việt Nam"""
    today_vn = datetime.now(VN_TZ).date()
    return f"{today_vn.isoformat()}T00:00:00"

# --- SCHEMAS ---
class BookTicketRequest(BaseModel):
    customer_zalo_id: str
    customer_name: Optional[str] = "Khách hàng"
    customer_phone: Optional[str] = None
    service_type: Optional[str] = "Kỹ Thuật"
    note: Optional[str] = None

class UpdateStatusRequest(BaseModel):
    status: str  # 'waiting', 'processing', 'completed', 'cancelled'
    ktv_id: Optional[str] = None
    note: Optional[str] = None

# --- ENDPOINTS ---

@router.post("/book")
async def book_ticket(req: BookTicketRequest):
    """
    1. Khách hàng bấm Bốc Số từ Zalo Mini App.
    2. Hệ thống tìm số lớn nhất trong ngày (n) -> Cấp số mới (n + 1).
    """
    today_start = get_today_start_iso()

    try:
        # Kiểm tra nếu khách đã bốc số và đang ở trạng thái chờ trong ngày
        existing = supabase.table("tickets") \
            .select("*") \
            .eq("customer_zalo_id", req.customer_zalo_id) \
            .in_("status", ["waiting", "processing"]) \
            .gte("created_at", today_start) \
            .execute()

        if existing.data and len(existing.data) > 0:
            active_ticket = existing.data[0]
            
            # Đếm số người phía trước của vé cũ này
            ahead_count = supabase.table("tickets") \
                .select("id", count="exact") \
                .eq("status", "waiting") \
                .lt("ticket_number", active_ticket["ticket_number"]) \
                .gte("created_at", today_start) \
                .execute()

            return {
                "success": True,
                "is_existing": True,
                "message": "Bạn đã có số thứ tự đang chờ xử lý!",
                "data": active_ticket,
                "people_ahead": ahead_count.count or 0
            }

        # Tìm số lớn nhất hôm nay (n)
        last_ticket = supabase.table("tickets") \
            .select("ticket_number") \
            .gte("created_at", today_start) \
            .order("ticket_number", desc=True) \
            .limit(1) \
            .execute()

        last_num = last_ticket.data[0]["ticket_number"] if last_ticket.data else 0
        new_num = last_num + 1  # Số n + 1

        # Tạo phiếu mới
        new_ticket_data = {
            "ticket_number": new_num,
            "customer_zalo_id": req.customer_zalo_id,
            "customer_name": req.customer_name,
            "customer_phone": req.customer_phone,
            "service_type": req.service_type,
            "note": req.note,
            "status": "waiting"
        }

        insert_res = supabase.table("tickets").insert(new_ticket_data).execute()

        if not insert_res.data:
            raise HTTPException(status_code=500, detail="Không thể tạo số thứ tự")

        ticket_info = insert_res.data[0]

        # Đếm số người đang chờ phía trước
        ahead_count = supabase.table("tickets") \
            .select("id", count="exact") \
            .eq("status", "waiting") \
            .lt("ticket_number", new_num) \
            .gte("created_at", today_start) \
            .execute()

        return {
            "success": True,
            "is_existing": False,
            "message": f"Bốc số thành công! Số của bạn là {new_num:02d}",
            "data": ticket_info,
            "people_ahead": ahead_count.count or 0
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/my-status/{zalo_id}")
async def get_my_status(zalo_id: str):
    """Lấy trạng thái phiếu hiện tại của khách hàng trên Zalo"""
    today_start = get_today_start_iso()
    
    res = supabase.table("tickets") \
        .select("*") \
        .eq("customer_zalo_id", zalo_id) \
        .gte("created_at", today_start) \
        .order("created_at", desc=True) \
        .limit(1) \
        .execute()

    if not res.data:
        return {"has_ticket": False, "ticket": None}

    ticket = res.data[0]
    
    # Tính số lượt chờ phía trước nếu đang ở trạng thái 'waiting'
    people_ahead = 0
    if ticket["status"] == "waiting":
        ahead_res = supabase.table("tickets") \
            .select("id", count="exact") \
            .eq("status", "waiting") \
            .lt("ticket_number", ticket["ticket_number"]) \
            .gte("created_at", today_start) \
            .execute()
        people_ahead = ahead_res.count or 0

    return {
        "has_ticket": True,
        "ticket": ticket,
        "people_ahead": people_ahead
    }


@router.get("/queue")
async def get_queue():
    """Lấy toàn bộ danh sách hàng chờ hôm nay cho Dashboard Admin / KTV"""
    today_start = get_today_start_iso()
    
    res = supabase.table("tickets") \
        .select("*") \
        .gte("created_at", today_start) \
        .order("ticket_number", desc=False) \
        .execute()

    return {"success": True, "tickets": res.data or []}


# Hỗ trợ cả PATCH lẫn PUT để không bị lỗi 405 khi Frontend gọi sai method
@router.patch("/{ticket_id}/status")
@router.put("/{ticket_id}/status")
async def update_ticket_status(ticket_id: str, req: UpdateStatusRequest):
    """KTV tiếp nhận hoặc bấm Hoàn thành / Hủy phiếu"""
    update_data = {
        "status": req.status,
        "updated_at": datetime.now(timezone.utc).isoformat()
    }
    if req.ktv_id:
        update_data["ktv_id"] = req.ktv_id
    if req.note:
        update_data["note"] = req.note

    res = supabase.table("tickets") \
        .update(update_data) \
        .eq("id", ticket_id) \
        .execute()

    if not res.data:
        raise HTTPException(status_code=404, detail="Không tìm thấy phiếu")

    return {"success": True, "message": "Cập nhật trạng thái thành công", "ticket": res.data[0]}