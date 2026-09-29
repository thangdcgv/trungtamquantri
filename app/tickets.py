from datetime import datetime, timezone, timedelta
import logging
import re
from typing import Optional, Literal
import requests
from fastapi import APIRouter, HTTPException, Query, Request, status as http_status
from pydantic import BaseModel, Field
from config import supabase, ZALO_OA_ACCESS_TOKEN, ZNS_TEMPLATE_ID


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

# Tên phòng ban hiển thị
DEPT_NAME_MAP = {
    "repair": "Phòng Sửa Chữa",
    "installation": "Phòng Lắp Đặt",
    "sales": "Phòng Kinh Doanh",
    "cskh": "Phòng CSKH"
}

# Logger cho server
logger = logging.getLogger("uvicorn.error")

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


class CallTicketRequest(BaseModel):
    assigned_to: Optional[str] = None


class TransferDepartmentRequest(BaseModel):
    new_department: DepartmentType
    assigned_to: Optional[str] = None
    device_info: Optional[str] = None


class CheckAdminRequest(BaseModel):
    zalo_id: Optional[str] = None
    phone: Optional[str] = None


# --- HELPER FUNCTIONS ---

def check_supabase():
    """Kiểm tra kết nối Supabase trước khi truy vấn"""
    if supabase is None:
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Chưa cấu hình SUPABASE_URL hoặc SUPABASE_KEY trong file .env!"
        )


def get_today_utc_start() -> str:
    """Trả về mốc 00:00:00 ngày hôm nay theo giờ Việt Nam dạng chuỗi UTC chuẩn (An toàn cho Query URL)"""
    now_vn = datetime.now(VN_TZ)
    today_vn_start = now_vn.replace(hour=0, minute=0, second=0, microsecond=0)
    # 🟢 Dùng chuẩn ISO đuôi 'Z' để không bị lỗi decode dấu '+' thành khoảng trắng trên URL
    return today_vn_start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def generate_ticket_code(department: str) -> str:
    """Sinh mã STT dạng PREFIX-00X theo phòng ban trong ngày."""
    check_supabase()
    prefix = DEPT_PREFIX_MAP.get(department, "STT")
    today_start = get_today_utc_start()

    try:
        # 🟢 Sắp xếp theo ID giảm dần thay vì created_at để đảm bảo lấy phiếu mới nhất
        res = supabase.table("tickets") \
            .select("ticket_code") \
            .eq("department", department) \
            .gte("created_at", today_start) \
            .order("id", desc=True) \
            .limit(1) \
            .execute()

        next_number = 1

        if res.data and len(res.data) > 0:
            last_code = res.data[0].get("ticket_code", "")
            match = re.search(r'(\d+)$', last_code)
            if match:
                next_number = int(match.group(1)) + 1

        return f"{prefix}-{next_number:03d}"

    except Exception as e:
        logger.error(f"Lỗi sinh mã ticket: {e}")
        return f"{prefix}-001"


def send_zalo_notification(phone: str, customer_name: str, ticket_code: str):
    """Gửi thông báo ZBS/ZNS tới khách hàng"""
    if not phone or not ZALO_OA_ACCESS_TOKEN or not ZNS_TEMPLATE_ID:
        logger.warning("Thiếu thông tin SĐT, ZALO_OA_ACCESS_TOKEN hoặc ZNS_TEMPLATE_ID")
        return None

    phone_clean = phone.strip()
    if phone_clean.startswith("0"):
        phone_clean = "84" + phone_clean[1:]

    url = "https://business.openapi.zalo.me/message/template/send"
    headers = {
        "access_token": ZALO_OA_ACCESS_TOKEN,
        "Content-Type": "application/json"
    }

    payload = {
        "phone": phone_clean,
        "template_id": ZNS_TEMPLATE_ID,
        "template_data": {
            "customer_name": customer_name or "Khách hàng",
            "ticket_code": ticket_code
        },
        "tracking_id": f"call_{ticket_code}_{int(datetime.now().timestamp())}"
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=5)
        res_data = response.json()
        logger.info(f"Kết quả gửi Zalo ZBS tới {phone_clean}: {res_data}")
        return res_data
    except Exception as e:
        logger.error(f"Lỗi gửi API Zalo ZBS: {str(e)}")
        return None


# --- WEBHOOK ZALO ---

@router.post("/webhook")
@router.get("/webhook")
async def zalo_webhook(request: Request):
    return {"status": "ok", "message": "Webhook received successfully"}


# --- API ENDPOINTS ---

@router.post("/tickets/create")
async def create_ticket(data: CreateTicketRequest):
    """API Bốc số mới cho Khách hàng"""
    check_supabase()
    try:
        phone_clean = data.phone.strip() if data.phone and data.phone.strip() else None
        zalo_clean = data.zalo_id.strip() if data.zalo_id and data.zalo_id.strip() else None
        customer_name_clean = data.customer_name.strip() if data.customer_name else "Khách hàng"
        device_info_clean = data.device_info.strip() if data.device_info and data.device_info.strip() else None

        if not phone_clean and not zalo_clean:
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail="Vui lòng cung cấp số điện thoại hoặc Zalo ID!"
            )

        today_start = get_today_utc_start()

        # Kiểm tra vé đang chờ/đang xử lý trong ngày
        query = supabase.table("tickets") \
            .select("*") \
            .gte("created_at", today_start) \
            .in_("status", ["waiting", "processing"])

        if phone_clean and zalo_clean:
            query = query.or_(f"phone.eq.{phone_clean},zalo_id.eq.{zalo_clean}")
        elif phone_clean:
            query = query.eq("phone", phone_clean)
        else:
            query = query.eq("zalo_id", zalo_clean)

        existing_ticket = query.execute()

        if existing_ticket.data and len(existing_ticket.data) > 0:
            return {
                "success": False,
                "message": "Bạn đã có một phiếu đang chờ xử lý trên hệ thống!",
                "ticket": existing_ticket.data[0]
            }

        ticket_code = generate_ticket_code(data.department)

        payload = {
            "ticket_code": ticket_code,
            "department": data.department,
            "customer_name": customer_name_clean,
            "phone": phone_clean,
            "zalo_id": zalo_clean,
            "device_info": device_info_clean,
            "status": "waiting"
        }

        insert_res = supabase.table("tickets").insert(payload).execute()

        if insert_res.data and len(insert_res.data) > 0:
            return {
                "success": True,
                "message": "Bốc số thành công!",
                "ticket": insert_res.data[0]
            }
        else:
            raise HTTPException(
                status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Không thể lưu thông tin bốc số vào CSDL."
            )

    except HTTPException as http_ex:
        raise http_ex
    except Exception as e:
        logger.error(f"Lỗi bốc số API: {str(e)}")
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Lỗi hệ thống: {str(e)}"
        )


@router.post("/tickets/call/{ticket_id}")
async def call_ticket(ticket_id: int, data: Optional[CallTicketRequest] = None):
    """API Gọi số"""
    check_supabase()
    try:
        existing = supabase.table("tickets").select("*").eq("id", ticket_id).execute()
        if not existing.data:
            raise HTTPException(status_code=404, detail="Không tìm thấy phiếu bốc số này!")

        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        payload = {
            "status": "processing",
            "updated_at": now_iso,
            "called_at": now_iso
        }
        if data and data.assigned_to:
            payload["assigned_to"] = data.assigned_to

        res = supabase.table("tickets").update(payload).eq("id", ticket_id).execute()

        if not res.data:
            raise HTTPException(status_code=500, detail="Không thể cập nhật trạng thái gọi số.")

        updated_ticket = res.data[0]

        # Gửi tin Zalo
        customer_phone = updated_ticket.get("phone")
        customer_name = updated_ticket.get("customer_name", "Khách hàng")
        ticket_code = updated_ticket.get("ticket_code", "")

        if customer_phone:
            send_zalo_notification(
                phone=customer_phone,
                customer_name=customer_name,
                ticket_code=ticket_code
            )

        return {
            "success": True,
            "message": f"Đã gọi số {ticket_code} thành công!",
            "ticket": updated_ticket
        }

    except HTTPException as http_ex:
        raise http_ex
    except Exception as e:
        logger.error(f"Lỗi gọi số API: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tickets/my-ticket")
async def get_my_ticket(phone: Optional[str] = None, zalo_id: Optional[str] = None):
    """API Tra cứu vé hiện tại của Khách hàng"""
    check_supabase()
    phone_clean = phone.strip() if phone else None
    zalo_clean = zalo_id.strip() if zalo_id else None

    if not phone_clean and not zalo_clean:
        raise HTTPException(status_code=400, detail="Cần cung cấp Số điện thoại hoặc Zalo ID")

    today_start = get_today_utc_start()

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

    res = query.order("created_at", desc=True).limit(1).execute()

    if res.data and len(res.data) > 0:
        return {"has_ticket": True, "ticket": res.data[0]}

    return {"has_ticket": False, "ticket": None}


@router.get("/tickets/queue")
async def get_queue_list(
    department: Optional[str] = Query(None, description="Lọc phòng ban: repair, installation, sales, cskh hoặc 'all'"),
    status_filter: Optional[str] = Query(None, alias="status", description="Lọc trạng thái: waiting, processing, completed, cancelled")
):
    """API Lấy danh sách hàng chờ & Thống kê cho Dashboard"""
    check_supabase()
    try:
        today_start = get_today_utc_start()

        query = supabase.table("tickets").select("*").gte("created_at", today_start)

        if department and department != "all":
            query = query.eq("department", department)

        if status_filter and status_filter != "all":
            query = query.eq("status", status_filter)

        res = query.order("id", desc=False).execute()
        tickets = res.data or []

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
        logger.error(f"Lỗi get_queue_list: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/tickets/{ticket_id}/status")
async def update_ticket_status(ticket_id: int, data: UpdateStatusRequest):
    """API KTV đổi trạng thái phiếu"""
    check_supabase()
    try:
        payload = {
            "status": data.status,
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
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
    """API Chuyển tiếp vé sang phòng ban khác"""
    check_supabase()
    try:
        new_code = generate_ticket_code(data.new_department)

        payload = {
            "department": data.new_department,
            "ticket_code": new_code,
            "status": "waiting",
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
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
    """API Phân quyền KTV / Admin"""
    check_supabase()
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
    except Exception as e:
        logger.error(f"Lỗi check_admin_permission: {str(e)}")
        return {"is_admin": False, "role": None}
# --- QUẢN LÝ THÀNH VIÊN (CHỈ SUPER_ADMIN) ---
class AdminUserCreate(BaseModel):
    name: str
    phone: Optional[str] = None
    zalo_id: str
    department: str = "repair"
    role: str = "ktv"

class AdminUserUpdate(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    zalo_id: Optional[str] = None
    department: Optional[str] = None
    role: Optional[str] = None

@router.get("/admin/users")
async def list_admin_users():
    """Lấy danh sách tất cả thành viên quản trị"""
    check_supabase()
    try:
        result = supabase.table("admin_users").select("*").order("id", desc=True).execute()
        return {"success": True, "users": result.data or []}
    except Exception as e:
        logger.error(f"Lỗi tải danh sách thành viên: {e}")
        raise HTTPException(status_code=500, detail=f"Lỗi hệ thống: {str(e)}")

@router.post("/admin/users")
async def create_admin_user(data: AdminUserCreate):
    """Thêm thành viên quản trị mới"""
    check_supabase()
    try:
        # Kiểm tra trùng Zalo ID
        exist = supabase.table("admin_users")\
            .select("id")\
            .eq("zalo_id", data.zalo_id)\
            .execute()
        if exist.data and len(exist.data) > 0:
            raise HTTPException(status_code=400, detail="Zalo ID đã tồn tại!")
        
        result = supabase.table("admin_users").insert({
            "name": data.name.strip(),
            "phone": data.phone.strip() if data.phone else None,
            "zalo_id": data.zalo_id.strip(),
            "department": data.department,
            "role": data.role
        }).execute()
        
        return {"success": True, "user": result.data[0]}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Lỗi thêm thành viên: {e}")
        raise HTTPException(status_code=500, detail=f"Lỗi hệ thống: {str(e)}")

@router.patch("/admin/users/{user_id}")
async def update_admin_user(user_id: int, data: AdminUserUpdate):
    """Cập nhật thông tin thành viên"""
    check_supabase()
    try:
        update_data = {}
        if data.name: update_data["name"] = data.name.strip()
        if data.phone: update_data["phone"] = data.phone.strip()
        if data.zalo_id: update_data["zalo_id"] = data.zalo_id.strip()
        if data.department: update_data["department"] = data.department
        if data.role: update_data["role"] = data.role
        
        if not update_data:
            raise HTTPException(status_code=400, detail="Không có dữ liệu để cập nhật")
        
        result = supabase.table("admin_users")\
            .update(update_data)\
            .eq("id", user_id)\
            .execute()
        
        if not result.data or len(result.data) == 0:
            raise HTTPException(status_code=404, detail="Không tìm thấy thành viên")
        
        return {"success": True, "user": result.data[0]}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Lỗi sửa thành viên: {e}")
        raise HTTPException(status_code=500, detail=f"Lỗi hệ thống: {str(e)}")

@router.delete("/admin/users/{user_id}")
async def delete_admin_user(user_id: int):
    """Xóa thành viên"""
    check_supabase()
    try:
        # Ngăn tự xóa chính mình (có thể thêm kiểm tra hơn)
        supabase.table("admin_users").delete().eq("id", user_id).execute()
        return {"success": True}
    except Exception as e:
        logger.error(f"Lỗi xóa thành viên: {e}")
        raise HTTPException(status_code=500, detail=f"Lỗi hệ thống: {str(e)}")