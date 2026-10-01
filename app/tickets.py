import logging
import re
import asyncio
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Literal

from fastapi import APIRouter, HTTPException, Query, Request, Depends, WebSocket, WebSocketDisconnect, status as http_status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from config import supabase, ZNS_TEMPLATE_ID
from app.websocket_manager import manager, VN_TZ

# === IMPORT HELPER & AUTH ===
try:
    from app.zalo_helper import get_zalo_access_token
except ImportError:
    from zalo_helper import get_zalo_access_token

try:
    from app.admin_routes import require_roles, get_current_admin
except ImportError:
    from admin_routes import require_roles, get_current_admin

router = APIRouter(tags=["Tickets Queue"])

# Cấu hình Templates chuẩn xác theo vị trí app/tickets.py -> app/templates
BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
logger = logging.getLogger("uvicorn.error")

# === ÁNH XẠ PHÒNG BAN ===
DEPT_PREFIX_MAP = {
    "KTSC": "SC",
    "KTLD": "LD",
    "KD": "KD",
    "CSKH": "CS"
}

DEPT_NAME_MAP = {
    "KTSC": "Phòng Sửa Chữa",
    "KTLD": "Phòng Lắp Đặt",
    "KD": "Phòng Kinh Doanh",
    "CSKH": "Phòng CSKH"
}

def normalize_dept_to_ticket(dept: Optional[str]) -> str:
    if not dept:
        return "KTSC"
    d = str(dept).strip().upper()
    if d in ("KTSC", "KTLD", "KD", "CSKH", "ALL"):
        return d
    return "KTSC"

def normalize_role(raw_role: Optional[str]) -> str:
    r = str(raw_role or "").strip().lower()
    if r in ("system admin", "super admin", "super_admin", "superadmin"):
        return "super_admin"
    if r in ("admin", "quản lý", "quan ly"):
        return "admin"
    if r in ("ktv", "ktsc", "kỹ thuật", "ky thuat"):
        return "ktv"
    if r in ("kd", "kinh doanh", "kinhdoanh", "sales"):
        return "kd"
    return "ktv"

DepartmentType = Literal["KTSC", "KTLD", "KD", "CSKH"]
StatusType = Literal["waiting", "processing", "completed", "cancelled"]

# --- SCHEMAS ---
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

# --- HELPER FUNCTIONS ---
def check_supabase():
    if supabase is None:
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Chưa cấu hình SUPABASE_URL hoặc SUPABASE_KEY!"
        )

def get_today_utc_start() -> str:
    now_vn = datetime.now(VN_TZ)
    today_vn_start = now_vn.replace(hour=0, minute=0, second=0, microsecond=0)
    return today_vn_start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def generate_ticket_code(department: str) -> str:
    check_supabase()
    prefix = DEPT_PREFIX_MAP.get(department, "ST")
    today_start = get_today_utc_start()
    try:
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

# === GỬI THÔNG BÁO ZNS ===
def send_zalo_notification(phone: str, customer_name: str, ticket_code: str):
    token = get_zalo_access_token()
    if not phone or not token or not ZNS_TEMPLATE_ID:
        logger.warning("Thiếu thông tin gửi ZNS vé")
        return None

    phone_clean = re.sub(r"\D", "", phone.strip())
    if phone_clean.startswith("0"):
        phone_clean = "84" + phone_clean[1:]
    elif not phone_clean.startswith("84"):
        phone_clean = "84" + phone_clean

    url = "https://business.openapi.zalo.me/message/template"
    headers = {"access_token": token, "Content-Type": "application/json"}
    payload = {
        "phone": phone_clean,
        "template_id": ZNS_TEMPLATE_ID,
        "template_data": {
            "customer_name": customer_name or "Khách hàng",
            "ticket_code": ticket_code
        },
        "tracking_id": f"ticket_{ticket_code}_{int(datetime.now().timestamp())}"
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=5)
        res_data = response.json()
        logger.info(f"📨 ZNS vé {ticket_code}: {res_data}")
        return res_data
    except Exception as e:
        logger.error(f"Lỗi gửi ZNS vé: {str(e)}")
        return None

# ==========================================
# === 🔌 WEBSOCKET ENDPOINT ===
# ==========================================
@router.websocket("/ws/tickets")
@router.websocket("/tickets/ws")
@router.websocket("/api/ws/tickets")
async def websocket_tickets_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        await websocket.send_json({"type": "connected"})
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"Lỗi WebSocket: {e}")
        manager.disconnect(websocket)

# ==========================================
# === 🖥 GIAO DIỆN HTML ===
# ==========================================
@router.get("/ticket", response_class=HTMLResponse)
async def public_ticket_page(request: Request):
    """Giao diện bốc số điện tử dành cho khách hàng."""
    return templates.TemplateResponse(
        request=request,
        name="public_ticket.html",
        context={"request": request}
    )

# Bổ sung các alias path để hứng trọn vẹn request từ client mà không bị 404
@router.get("/admin-queue", response_class=HTMLResponse)
@router.get("/admin/queue", response_class=HTMLResponse)
@router.get("/api/admin-queue", response_class=HTMLResponse)
@router.get("/api/admin/queue", response_class=HTMLResponse)
async def admin_queue_page(request: Request, admin: dict = Depends(require_roles(["admin", "super_admin", "system_admin"]))):
    """Giao diện Quản lý hàng chờ cho nhân viên."""
    check_supabase()
    user_dept = "ALL"
    try:
        res = supabase.table("quan_tri_vien") \
            .select("department", "ho_ten") \
            .eq("auth_id", admin["auth_id"]) \
            .limit(1) \
            .execute()
        if res.data and len(res.data) > 0:
            raw_dept = res.data[0].get("department")
            if raw_dept:
                d = str(raw_dept).strip().upper()
                if d in ("KTSC", "KTLD", "KD", "CSKH", "ALL"):
                    user_dept = d
                else:
                    user_dept = "KTSC"
    except Exception as e:
        logger.error(f"Lỗi lấy department admin: {e}")

    current_user = {
        "name": admin.get("ho_ten") or admin.get("name", "Quản trị viên"),
        "ho_ten": admin.get("ho_ten", "Quản trị viên"),
        "role": normalize_role(admin.get("role")),
        "department": user_dept
    }
    
    # Render file reception.html (hoặc admin_queue.html tùy file trong app/templates của bạn)
    template_name = "admin_queue.html" if (BASE_DIR / "templates" / "admin_queue.html").exists() else "reception.html"
    return templates.TemplateResponse(
        request=request,
        name=template_name,
        context={"current_user": current_user}
    )

# WEBHOOK ZALO
@router.post("/webhook")
@router.get("/webhook")
async def zalo_webhook(request: Request):
    return {"status": "ok", "message": "Webhook received successfully"}

# ==========================================
# === 🎫 API BỐC SỐ & HÀNG CHỜ ===
# ==========================================
@router.post("/tickets/create")
@router.post("/api/tickets/create")
async def create_ticket(data: CreateTicketRequest):
    check_supabase()
    try:
        phone_clean = data.phone.strip() if data.phone and data.phone.strip() else None
        zalo_clean = data.zalo_id.strip() if data.zalo_id and data.zalo_id.strip() else None
        customer_name_clean = data.customer_name.strip() if data.customer_name else "Khách hàng"
        device_info_clean = data.device_info.strip() if data.device_info and data.device_info.strip() else None

        if not phone_clean and not zalo_clean:
            raise HTTPException(status_code=400, detail="Vui lòng cung cấp SĐT hoặc Zalo ID!")

        today_start = get_today_utc_start()
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
                "message": "Bạn đã có một phiếu đang chờ xử lý!",
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
            new_ticket = insert_res.data[0]

            await manager.broadcast({
                "type": "new_ticket",
                "ticket": new_ticket,
                "department": new_ticket["department"],
                "timestamp": datetime.now(VN_TZ).isoformat()
            })
            logger.info(f"📢 Đã tạo vé: {ticket_code}")

            if new_ticket.get("phone"):
                asyncio.create_task(asyncio.to_thread(
                    send_zalo_notification,
                    new_ticket["phone"],
                    new_ticket.get("customer_name"),
                    new_ticket["ticket_code"]
                ))

            return {"success": True, "message": "Bốc số thành công!", "ticket": new_ticket}

        raise HTTPException(status_code=500, detail="Không thể lưu vé vào CSDL.")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Lỗi bốc số: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Lỗi hệ thống: {str(e)}")

@router.get("/tickets/my-ticket")
@router.get("/api/tickets/my-ticket")
async def get_my_ticket(
    phone: Optional[str] = None,
    zalo_id: Optional[str] = None,
    ticket_id: Optional[str] = None
):
    """Tra cứu phiếu hiện tại của khách hàng & Bổ sung số đang phục vụ."""
    check_supabase()
    phone_clean = phone.strip() if phone else None
    zalo_clean = zalo_id.strip() if zalo_id else None

    if not phone_clean and not zalo_clean and not ticket_id:
        raise HTTPException(status_code=400, detail="Cần cung cấp SĐT / Zalo ID / Mã vé")

    today_start = get_today_utc_start()
    query = supabase.table("tickets") \
        .select("*") \
        .gte("created_at", today_start) \
        .in_("status", ["waiting", "processing", "completed"])

    if ticket_id:
        try:
            query = query.eq("id", int(ticket_id))
        except ValueError:
            raise HTTPException(status_code=400, detail="Mã vé không hợp lệ")
    elif phone_clean and zalo_clean:
        query = query.or_(f"phone.eq.{phone_clean},zalo_id.eq.{zalo_clean}")
    elif phone_clean:
        query = query.eq("phone", phone_clean)
    else:
        query = query.eq("zalo_id", zalo_clean)

    res = query.order("created_at", desc=True).limit(1).execute()
    if res.data and len(res.data) > 0:
        ticket = res.data[0]

        current_res = supabase.table("tickets") \
            .select("ticket_code") \
            .eq("department", ticket["department"]) \
            .eq("status", "processing") \
            .gte("created_at", today_start) \
            .order("updated_at", desc=True) \
            .limit(1) \
            .execute()

        current_number = "--"
        if current_res.data and len(current_res.data) > 0:
            current_number = current_res.data[0].get("ticket_code", "--")
        ticket["current_number"] = current_number
        
        technician_name = None
        if ticket.get("assigned_to"):
            try:
                ktv_res = supabase.table("quan_tri_vien") \
                    .select("ho_ten") \
                    .eq("id", ticket["assigned_to"]) \
                    .limit(1).execute()
                if ktv_res.data and len(ktv_res.data) > 0:
                    technician_name = ktv_res.data[0].get("ho_ten")
            except Exception:
                technician_name = ticket.get("assigned_to")
        ticket["technician_name"] = technician_name
        return {"has_ticket": True, "ticket": ticket}

    return {"has_ticket": False, "ticket": None}

@router.get("/tickets/queue")
@router.get("/api/tickets/queue")
async def get_queue_list(
    department: Optional[str] = Query(None, description="KTSC/KTLD/KD/CSKH/ALL"),
    status_filter: Optional[str] = Query(None, alias="status", description="waiting/processing/completed/cancelled")
):
    check_supabase()
    try:
        today_start = get_today_utc_start()
        query = supabase.table("tickets").select("*").gte("created_at", today_start)
        if department and department != "ALL":
            query = query.eq("department", department)
        if status_filter and status_filter != "all":
            query = query.eq("status", status_filter)

        tickets = query.order("id", desc=False).execute().data or []

        all_today_query = supabase.table("tickets") \
            .select("status") \
            .gte("created_at", today_start)
        if department and department != "ALL":
            all_today_query = all_today_query.eq("department", department)
        all_today = all_today_query.execute().data or []

        stats = {
            "waiting": sum(1 for t in all_today if t.get("status") == "waiting"),
            "processing": sum(1 for t in all_today if t.get("status") == "processing"),
            "completed": sum(1 for t in all_today if t.get("status") == "completed")
        }
        return {"success": True, "total": len(tickets), "stats": stats, "tickets": tickets}
    except Exception as e:
        logger.error(f"Lỗi hàng chờ: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
   
@router.post("/tickets/call/{ticket_id}")
@router.post("/api/tickets/call/{ticket_id}")
async def call_ticket_endpoint(ticket_id: int, data: Optional[CallTicketRequest] = None):
    """API tiếp nhận và gọi số (chuyển trạng thái sang processing)."""
    check_supabase()
    try:
        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        payload = {
            "status": "processing",
            "updated_at": now_utc
        }
        if data and data.assigned_to:
            payload["assigned_to"] = data.assigned_to

        res = supabase.table("tickets").update(payload).eq("id", ticket_id).execute()
        if res.data and len(res.data) > 0:
            updated_ticket = res.data[0]
            
            # Broadcast qua WebSocket để các màn hình khác cập nhật theo thời gian thực
            await manager.broadcast({
                "type": "status_update",
                "ticket": updated_ticket,
                "status": "processing",
                "timestamp": datetime.now(VN_TZ).isoformat()
            })
            
            logger.info(f"📢 Đã gọi/tiếp nhận vé ID {ticket_id}")
            return {"success": True, "message": "Đã tiếp nhận vé!", "ticket": updated_ticket}
            
        raise HTTPException(status_code=404, detail="Không tìm thấy phiếu yêu cầu.")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Lỗi gọi số vé: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
    
@router.patch("/tickets/{ticket_id}/status")
@router.patch("/api/tickets/{ticket_id}/status")
async def update_ticket_status(ticket_id: int, data: UpdateStatusRequest):
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
            updated_ticket = res.data[0]
            await manager.broadcast({
                "type": "status_update",
                "ticket": updated_ticket,
                "status": data.status,
                "timestamp": datetime.now(VN_TZ).isoformat()
            })
            return {"success": True, "message": "Cập nhật thành công!", "ticket": updated_ticket}
        raise HTTPException(status_code=404, detail="Không tìm thấy phiếu.")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.patch("/tickets/{ticket_id}/transfer")
@router.patch("/api/tickets/{ticket_id}/transfer")
async def transfer_ticket_department(ticket_id: int, data: TransferDepartmentRequest):
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
            updated_ticket = res.data[0]
            await manager.broadcast({
                "type": "transfer_ticket",
                "ticket": updated_ticket,
                "new_department": data.new_department,
                "timestamp": datetime.now(VN_TZ).isoformat()
            })
            logger.info(f"📢 Chuyển phòng: {new_code} → {data.new_department}")
            return {
                "success": True,
                "message": f"Đã chuyển vé sang phòng {data.new_department.upper()} với mã mới {new_code}",
                "ticket": updated_ticket
            }
        raise HTTPException(status_code=404, detail="Không tìm thấy phiếu.")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))