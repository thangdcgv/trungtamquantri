import logging
from typing import Optional
from fastapi import APIRouter, Request, Depends, Query, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app.auth import get_current_user_or_redirect, require_login
from app.permissions import can_access
from app.templates_config import templates
from config import supabase_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/chinh-sach-bao-hanh", tags=["Warranty Policy"])

DANH_SACH_LOAI_MAY = ["In phun", "In Laser", "In kim", "Cắt bế", "Máy Scan", "Thiết bị điện", "Thiết bị khác"]


# =========================================================================
# PYDANTIC SCHEMA
# =========================================================================
class PolicySchema(BaseModel):
    id: Optional[str] = None
    brand_name: str
    policy_name: str
    category: str
    condition: str = "Máy mới"
    warranty_months: int = 0
    head_warranty_months: int = 0
    cartridge_warranty_months: int = 0
    
    # Giới hạn Trang in
    page_limit_body: int = 0
    page_limit_head: int = 0
    no_page_limit: bool = False
    
    # Giới hạn Số mét
    meter_limit_body: int = 0
    meter_limit_head: int = 0
    no_meter_limit: bool = False
    
    is_active: bool = True


# =========================================================================
# ROUTE GIAO DIỆN & APIS
# =========================================================================

@router.get("", response_class=HTMLResponse)
async def list_policies(request: Request):
    """Hiển thị danh sách chính sách bảo hành"""
    user = await get_current_user_or_redirect(request)
    if not user:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_303_SEE_OTHER)

    # 🛡️ KIỂM TRA PHÂN QUYỀN ĐỘNG DYNAMIC RBAC
    if not can_access('warranty_policy', user):
        return HTMLResponse("<h2>⚠️ Bạn không có quyền truy cập Cấu hình Chính sách bảo hành!</h2>", status_code=403)

    try:
        # 1. Truy vấn danh sách chính sách bất đồng bộ
        def _fetch_data():
            res_p = (
                supabase_admin.table("warranty_policy")
                .select("*")
                .order("brand_name", desc=True)
                .order("policy_name", desc=False)
                .execute()
            )
            res_pr = (
                supabase_admin.table("list_printer")
                .select("brand_name")
                .execute()
            )
            return res_p.data or [], res_pr.data or []

        policies_data, printers_data = await run_in_threadpool(_fetch_data)

        # Lọc danh sách hãng độc nhất
        existing_brands = sorted(list(set(p["brand_name"] for p in printers_data if p.get("brand_name"))))

    except Exception as db_err:
        logger.error(f"❌ Lỗi tải danh sách chính sách bảo hành: {db_err}")
        policies_data = []
        existing_brands = []

    return templates.TemplateResponse(
        request=request,
        name="warranty/warranty_policy.html",
        context={
            "request": request,
            "current_user": user,
            "policies": policies_data,
            "existing_brands": existing_brands,
            "categories": DANH_SACH_LOAI_MAY,
        }
    )


@router.post("/save")
async def save_policy(
    payload: PolicySchema,
    current_user: dict = Depends(require_login)
):
    """Thêm mới hoặc cập nhật chính sách bảo hành"""
    # 🛡️ KIỂM TRA PHÂN QUYỀN ĐỘNG
    if not can_access('warranty_policy', current_user):
        return JSONResponse(
            status_code=403, 
            content={"success": False, "message": "⛔ Bạn không có quyền chỉnh sửa chính sách bảo hành!"}
        )

    try:
        data = payload.model_dump(exclude_none=True) if hasattr(payload, "model_dump") else payload.dict(exclude_none=True)
        policy_id = data.pop("id", None)

        if policy_id:
            res = await run_in_threadpool(
                lambda: supabase_admin.table("warranty_policy").update(data).eq("id", policy_id).execute()
            )
        else:
            res = await run_in_threadpool(
                lambda: supabase_admin.table("warranty_policy").insert(data).execute()
            )

        return {"success": True, "data": res.data}
    except Exception as e:
        err_msg = str(e)
        logger.error(f"❌ Lỗi lưu chính sách bảo hành: {err_msg}")
        
        if "23505" in err_msg or "warranty_policy_name_key" in err_msg:
            return JSONResponse(
                status_code=400, 
                content={
                    "success": False, 
                    "message": f"Tên chính sách '{payload.policy_name}' đã tồn tại! Vui lòng đặt một tên khác (VD: thêm hậu tố khổ lớn, độ chế...)."
                }
            )
            
        return JSONResponse(status_code=400, content={"success": False, "message": err_msg})


@router.post("/toggle-status/{policy_id}")
async def toggle_policy_status(
    policy_id: str, 
    status: bool = Query(...),
    current_user: dict = Depends(require_login)
):
    """Bật/tắt trạng thái chính sách"""
    # 🛡️ KIỂM TRA PHÂN QUYỀN ĐỘNG
    if not can_access('warranty_policy', current_user):
        return JSONResponse(
            status_code=403, 
            content={"success": False, "message": "⛔ Bạn không có quyền thay đổi trạng thái chính sách!"}
        )

    try:
        await run_in_threadpool(
            lambda: supabase_admin.table("warranty_policy").update({"is_active": status}).eq("id", policy_id).execute()
        )
        return {"success": True}
    except Exception as e:
        logger.error(f"❌ Lỗi đổi trạng thái chính sách ID {policy_id}: {e}")
        return JSONResponse(status_code=400, content={"success": False, "message": str(e)})


@router.post("/delete/{policy_id}")
async def delete_policy(
    policy_id: str,
    current_user: dict = Depends(require_login)
):
    """Xóa chính sách bảo hành"""
    # 🛡️ KIỂM TRA PHÂN QUYỀN ĐỘNG
    if not can_access('warranty_policy', current_user):
        return JSONResponse(
            status_code=403, 
            content={"success": False, "message": "⛔ Bạn không có quyền xóa chính sách bảo hành!"}
        )

    try:
        await run_in_threadpool(
            lambda: supabase_admin.table("warranty_policy").delete().eq("id", policy_id).execute()
        )
        return {"success": True}
    except Exception as e:
        logger.error(f"❌ Lỗi xóa chính sách ID {policy_id}: {e}")
        return JSONResponse(status_code=400, content={"success": False, "message": str(e)})