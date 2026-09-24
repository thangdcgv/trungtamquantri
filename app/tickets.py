from datetime import datetime, timezone, timedelta
from typing import Optional, Literal
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

# --- KẾT NỐI SUPABASE ---
from config import supabase

router = APIRouter(prefix="/api", tags=["Tickets & Admin"])

# Múi giờ Việt Nam (UTC+7)
VN_TZ = timezone(timedelta(hours=7))

# Mảng định nghĩa tiền tố mã số theo phòng ban
DEPT_PREFIX_MAP = {
    "repair": "SC",        # Sửa chữa
    "installation": "LD",  # Lắp đặt
    "sales": "KD",         # Kinh doanh
    "cskh": "CS"           # CSKH
}

# Định nghĩa Type Validation
DepartmentType = Literal["repair", "installation", "sales", "cskh"]
StatusType = Literal["waiting", "processing", "completed", "cancelled"]

# --- SCHEMAS (PYDANTIC MODELS) ---

class CreateTicketRequest(BaseModel):
    department: DepartmentType
    customer_name: str = Field(..., min_length=1)
    phone: str = Field(..., min_length=8)
    zalo_id: Optional[str] = None
    device_info: Optional[str] = None

class UpdateStatusRequest(BaseModel):
    status: StatusType
    assigned_to: Optional[str] = None

class TransferDepartmentRequest(BaseModel):
    new_department: DepartmentType
    assigned_to: Optional[str] = None
    device_info: Optional[str] = None

class CheckAdminRequest(BaseModel):
    zalo_id: Optional[str] = None
    phone: Optional[str] = None


# --- HELPER FUNCTIONS ---

def get_today_utc_start() -> str:
    """Trả về mốc 00:00:00 ngày hôm nay theo giờ Việt Nam (UTC+7) quy đổi sang ISO UTC"""
    now_vn = datetime.now(VN_TZ)
    today_vn_start = now_vn.replace(hour=0, minute=0, second=0, microsecond=0)
    return today_vn_start.astimezone(timezone.utc).isoformat()


def generate_ticket_code(department: str) -> str:
    """Hàm tính toán và sinh mã STT dạng PREFIX-00X theo từng phòng ban trong ngày"""
    prefix = DEPT_PREFIX_MAP.get(department, "STT")
    today_start = get_today_utc_start()

    res = supabase.table("tickets") \
        .select("id", count="exact") \
        .eq("department", department) \
        .gte("created_at", today_start) \
        .execute()

    count = (res.count or 0) + 1
    return f"{prefix}-{count:03d}"


# --- WEBHOOK ZALO ---

@router.post("/webhook")
@router.get("/webhook")
async def zalo_webhook(request: Request):
    return {"status": "ok", "message": "Webhook received successfully"}


# --- API ENDPOINTS ---

@router.post("/tickets/create")
async def create_ticket(data: CreateTicketRequest):
    """API Bốc số mới cho Khách hàng"""
    try:
        phone_clean = data.phone.strip() if data.phone else None
        zalo_clean = data.zalo_id.strip() if data.zalo_id else None
        today_start = get_today_utc_start()

        # 1. Kiểm tra xem khách có vé đang chờ/đang xử lý hôm nay không
        query = supabase.table("tickets") \
            .select("*") \
            .gte("created_at", today_start) \
            .in_("status", ["waiting", "processing"])

        if phone_clean and zalo_clean:
            query = query.or_(f"phone.eq.{phone_clean},zalo_id.eq.{zalo_clean}")
        elif phone_clean:
            query = query.eq("phone", phone_clean)
        elif zalo_clean:
            query = query.eq("zalo_id", zalo_clean)

        existing_ticket = query.execute()

        if existing_ticket.data and len(existing_ticket.data) > 0:
            return {
                "success": False,
                "message": "Bạn đã có một phiếu đang chờ xử lý trên hệ thống!",
                "ticket": existing_ticket.data[0]
            }

        # 2. Sinh mã số thứ tự mới
        ticket_code = generate_ticket_code(data.department)

        # 3. Lưu vào database Supabase
        payload = {
            "ticket_code": ticket_code,
            "department": data.department,
            "customer_name": data.customer_name.strip(),
            "phone": phone_clean,
            "zalo_id": zalo_clean,
            "device_info": data.device_info.strip() if data.device_info else None,
            "status": "waiting"
        }

        insert_res = supabase.table("tickets").insert(payload).execute()

        if insert_res.data:
            return {
                "success": True,
                "message": "Bốc số thành công!",
                "ticket": insert_res.data[0]
            }
        else:
            raise HTTPException(status_code=500, detail="Không thể lưu thông tin bốc số.")

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tickets/my-ticket")
async def get_my_ticket(phone: Optional[str] = None, zalo_id: Optional[str] = None):
    """API Tra cứu vé hiện tại của Khách hàng"""
    phone_clean = phone.strip() if phone else None
    zalo_clean = zalo_id.strip() if zalo_id else None

    if not phone_clean and not zalo_clean:
        raise HTTPException(status_code=400, detail="Cần cung cấp Số điện thoại hoặc Zalo ID")

    today_start = get_today_utc_start()
    query = supabase.table("tickets").select("*").gte("created_at", today_start)

    if phone_clean and zalo_clean:
        query = query.or_(f"phone.eq.{phone_clean},zalo_id.eq.{zalo_clean}")
    elif phone_clean:
        query = query.eq("phone", phone_clean)
    elif zalo_clean:
        query = query.eq("zalo_id", zalo_clean)

    res = query.order("created_at", desc=True).limit(1).execute()

    if res.data and len(res.data) > 0:
        return {"has_ticket": True, "ticket": res.data[0]}
    
    return {"has_ticket": False, "ticket": None}


@router.get("/tickets/queue")
async def get_queue_list(
    department: Optional[str] = Query(None, description="Lọc theo phòng ban: repair, installation, sales, cskh hoặc 'all'"),
    status: Optional[str] = Query(None, description="Lọc theo trạng thái: waiting, processing, completed, cancelled")
):
    """API Lấy danh sách hàng chờ & Thống kê chỉ số cho KTV/Admin Dashboard"""
    try:
        today_start = get_today_utc_start()
        
        # Query danh sách vé trong ngày
        query = supabase.table("tickets").select("*").gte("created_at", today_start)

        if department and department != "all":
            query = query.eq("department", department)

        if status and status != "all":
            query = query.eq("status", status)

        res = query.order("id", desc=False).execute()
        tickets = res.data or []

        # Thống kê nhanh các trạng thái trong ngày
        all_today_res = supabase.table("tickets").select("status").gte("created_at", today_start).execute()
        all_tickets = all_today_res.data or []
        
        stats = {
            "waiting": sum(1 for t in all_tickets if t.get("status") == "waiting"),
            "processing": sum(1 for t in all_tickets if t.get("status") == "processing"),
            "completed": sum(1 for t in all_tickets if t.get("status") == "completed")
        }

        return {
            "success": True,
            "total": len(tickets),
            "stats": stats,
            "tickets": tickets
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/tickets/{ticket_id}/status")
async def update_ticket_status(ticket_id: int, data: UpdateStatusRequest):
    """API KTV đổi trạng thái phiếu (Gọi số / Đã xong / Hủy)"""
    try:
        payload = {
            "status": data.status,
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
        if data.assigned_to:
            payload["assigned_to"] = data.assigned_to

        res = supabase.table("tickets").update(payload).eq("id", ticket_id).execute()

        if res.data:
            return {"success": True, "message": "Cập nhật trạng thái thành công!", "ticket": res.data[0]}
        raise HTTPException(status_code=404, detail="Không tìm thấy phiếu bốc số.")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/tickets/{ticket_id}/transfer")
async def transfer_ticket_department(ticket_id: int, data: TransferDepartmentRequest):
    """API KTV Chuyển tiếp vé sang phòng ban khác"""
    try:
        # 1. Sinh mã STT mới tương ứng với phòng ban mới
        new_code = generate_ticket_code(data.new_department)

        # 2. Đưa vé về lại trạng thái 'waiting' để phòng ban mới tiếp nhận
        payload = {
            "department": data.new_department,
            "ticket_code": new_code,
            "status": "waiting",  # 🔥 Reset trạng thái về hàng chờ mới
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
        if data.assigned_to:
            payload["assigned_to"] = data.assigned_to
            
        if data.device_info is not None:
            payload["device_info"] = data.device_info.strip()

        res = supabase.table("tickets").update(payload).eq("id", ticket_id).execute()

        if res.data:
            return {
                "success": True, 
                "message": f"Đã chuyển vé sang phòng {data.new_department.upper()} với mã mới {new_code}",
                "ticket": res.data[0]
            }
        raise HTTPException(status_code=404, detail="Không tìm thấy phiếu bốc số.")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/auth/check-admin")
async def check_admin_permission(data: CheckAdminRequest):
    """API Phân quyền tự động KTV / Admin"""
    try:
        user_phone = data.phone.strip() if data.phone else None
        user_zalo_id = data.zalo_id.strip() if data.zalo_id else None

        if not user_phone and not user_zalo_id:
            return {"is_admin": False, "role": None}

        query = supabase.table("admin_users").select("*")
        
        if user_phone and user_zalo_id:
            query = query.or_(f"phone.eq.{user_phone},zalo_id.eq.{user_zalo_id}")
        elif user_phone:
            query = query.eq("phone", user_phone)
        elif user_zalo_id:
            query = query.eq("zalo_id", user_zalo_id)

        res = query.execute()

        if res.data and len(res.data) > 0:
            user = res.data[0]
            return {
                "is_admin": True,
                "role": user.get("role", "tech"),
                "name": user.get("name"),
                "department": user.get("department", "all")
            }

        return {"is_admin": False, "role": None}
    except Exception:
        return {"is_admin": False, "role": None}