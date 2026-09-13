import logging
from typing import Optional
from fastapi import APIRouter, Request, status, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from config import supabase_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["Internal Chat"])


# =========================================================
# SCHEMAS
# =========================================================
class SendMessageRequest(BaseModel):
    target: str = Field(..., description="Dạng 'role:ke_toan' hoặc 'user:<auth_id>'")
    content: str = Field(..., min_length=1, max_length=1000)


# =========================================================
# HELPER VERIFY CLIENT
# =========================================================
def _check_supabase_client():
    if not supabase_admin:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Chưa cấu hình Supabase Admin Client (Thiếu Service Role Key trong .env)."
        )


# =========================================================
# ENDPOINTS
# =========================================================

@router.post("/send")
async def send_message(request: Request, body: SendMessageRequest):
    """Gửi tin nhắn nội bộ (1-1 hoặc theo Role/Phòng ban)."""
    _check_supabase_client()

    current_user_id = request.session.get("user_id")
    current_user_name = (
        request.session.get("ho_ten") 
        or request.session.get("username") 
        or "Người dùng"
    )

    if not current_user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail="Chưa đăng nhập"
        )

    target = body.target.strip()
    content = body.content.strip()

    if not content:
        return JSONResponse(
            status_code=400, 
            content={"success": False, "message": "Nội dung tin nhắn không được rỗng."}
        )

    # Phân tích đối tượng nhận (Role hay Cá nhân)
    receiver_id: Optional[str] = None
    target_role: Optional[str] = None

    if target.startswith("role:"):
        target_role = target.replace("role:", "").strip().lower()
    elif target.startswith("user:"):
        receiver_id = target.replace("user:", "").strip()
    else:
        receiver_id = target

    payload = {
        "sender_id": current_user_id,
        "sender_name": current_user_name,
        "receiver_id": receiver_id,
        "target_role": target_role,
        "content": content,
        "is_read": False  # Đảm bảo tin nhắn mới luôn ở trạng thái chưa đọc
    }

    try:
        def _insert_msg():
            return supabase_admin.table("internal_messages").insert(payload).execute()

        res = await run_in_threadpool(_insert_msg)
        
        if res and res.data:
            return {
                "success": True, 
                "message": "Gửi tin nhắn thành công", 
                "data": res.data[0]
            }
        
        return JSONResponse(
            status_code=500, 
            content={"success": False, "message": "Không thể lưu tin nhắn."}
        )

    except Exception as e:
        logger.error(f"CHAT SEND ERROR: {e}")
        return JSONResponse(
            status_code=500, 
            content={"success": False, "message": f"Lỗi hệ thống: {str(e)}"}
        )


@router.post("/read/{sender_id}")
async def mark_messages_as_read(sender_id: str, request: Request):
    """Đánh dấu tất cả tin nhắn gửi từ sender_id tới user hiện tại là đã đọc."""
    _check_supabase_client()

    current_user_id = request.session.get("user_id")
    current_role = str(request.session.get("role") or "user").strip().lower()

    if not current_user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail="Chưa đăng nhập"
        )

    sender_id = str(sender_id).strip()

    try:
        def _update_read_status():
            # Cập nhật is_read = True cho tin nhắn từ người gửi này tới tài khoản hoặc role của bạn
            return (
                supabase_admin.table("internal_messages")
                .update({"is_read": True})
                .eq("sender_id", sender_id)
                .or_(
                    f"receiver_id.eq.{current_user_id},"
                    f"target_role.eq.{current_role}"
                )
                .execute()
            )

        res = await run_in_threadpool(_update_read_status)
        return {
            "success": True, 
            "message": "Đã cập nhật trạng thái đã đọc",
            "updated_count": len(res.data) if res and res.data else 0
        }

    except Exception as e:
        logger.error(f"CHAT MARK READ ERROR: {e}")
        return JSONResponse(
            status_code=500, 
            content={"success": False, "message": f"Lỗi cập nhật trạng thái đọc: {str(e)}"}
        )


@router.get("/history")
async def get_chat_history(request: Request, limit: int = 50):
    """Lấy danh sách tin nhắn gần nhất liên quan tới người dùng hiện tại."""
    _check_supabase_client()

    current_user_id = request.session.get("user_id")
    current_role = str(request.session.get("role") or "user").strip().lower()

    if not current_user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail="Chưa đăng nhập"
        )

    try:
        def _fetch_history():
            return (
                supabase_admin.table("internal_messages")
                .select("*")
                .or_(
                    f"sender_id.eq.{current_user_id},"
                    f"receiver_id.eq.{current_user_id},"
                    f"target_role.eq.{current_role}"
                )
                .order("created_at", desc=False)
                .limit(limit)
                .execute()
            )

        res = await run_in_threadpool(_fetch_history)
        messages = res.data if res and res.data else []

        return {
            "success": True, 
            "data": messages, 
            "current_user_id": current_user_id
        }

    except Exception as e:
        logger.error(f"CHAT HISTORY ERROR: {e}")
        return JSONResponse(
            status_code=500, 
            content={"success": False, "message": f"Lỗi lấy lịch sử chat: {str(e)}"}
        )


@router.get("/users")
async def get_chat_users(request: Request):
    """Lấy danh sách người dùng để đổ vào dropdown chọn nhắn 1-1."""
    _check_supabase_client()

    current_user_id = request.session.get("user_id")

    if not current_user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail="Chưa đăng nhập"
        )

    try:
        def _fetch_users():
            return (
                supabase_admin.table("quan_tri_vien")
                .select("auth_id, username, ho_ten, role")
                .neq("auth_id", current_user_id)
                .execute()
            )

        res = await run_in_threadpool(_fetch_users)
        users = res.data if res and res.data else []

        return {"success": True, "data": users}

    except Exception as e:
        logger.error(f"CHAT USERS ERROR: {e}")
        return JSONResponse(
            status_code=500, 
            content={"success": False, "message": f"Lỗi lấy danh sách user: {str(e)}"}
        )


@router.delete("/delete/{message_id}")
async def delete_message(message_id: str, request: Request):
    """Thu hồi và xóa vĩnh viễn tin nhắn khỏi Database."""
    _check_supabase_client()

    current_user_id = request.session.get("user_id")
    if not current_user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail="Chưa đăng nhập"
        )

    current_user_id = str(current_user_id).strip()
    message_id = str(message_id).strip()

    try:
        def _check_msg():
            return (
                supabase_admin.table("internal_messages")
                .select("id, sender_id")
                .eq("id", message_id)
                .execute()
            )

        check_res = await run_in_threadpool(_check_msg)
        
        if not check_res.data:
            return JSONResponse(
                status_code=404,
                content={"success": False, "message": "Tin nhắn không tồn tại trên hệ thống."}
            )

        msg_data = check_res.data[0]
        if str(msg_data.get("sender_id")) != current_user_id:
            logger.warning(f"User {current_user_id} cố xóa tin nhắn của {msg_data.get('sender_id')}")
            return JSONResponse(
                status_code=403,
                content={"success": False, "message": "Bạn không có quyền thu hồi tin nhắn của người khác."}
            )

        def _delete_msg():
            return (
                supabase_admin.table("internal_messages")
                .delete()
                .eq("id", message_id)
                .execute()
            )

        res = await run_in_threadpool(_delete_msg)
        return {
            "success": True, 
            "message": "Thu hồi tin nhắn thành công", 
            "deleted_id": message_id
        }

    except Exception as e:
        logger.error(f"CHAT DELETE ERROR: {e}")
        return JSONResponse(
            status_code=500, 
            content={"success": False, "message": f"Lỗi thu hồi tin nhắn: {str(e)}"}
        )