import json
import os
import traceback
from typing import Optional, List
from fastapi import APIRouter, Request, Form, Query, HTTPException, status, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from app.auth import get_current_user_or_redirect  # Import hàm từ auth.py
from app.auth import require_login
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from config import supabase

# =========================================================================
# HELPER AUTH DEPENDENCY DÀNH CHO API (TRẢ VỀ JSON CHI TIẾT LỖI 401/403)
# =========================================================================
async def verify_admin_user(request: Request):
    user = await get_current_user_or_redirect(request)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail="Phiên làm việc đã hết hạn. Vui lòng đăng nhập lại."
        )
    user_role = str(user.get("role", "")).strip().lower()
    if user_role not in ["admin", "super admin", "system admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="Bạn không có quyền quản trị để thực hiện thao tác này."
        )
    return user


router = APIRouter(prefix="/admin/kho-key", tags=["Admin - Quản Lý Kho Key"])

# 🔒 ĐÃ SỬA: Bổ sung dependencies=[Depends(verify_admin_user)] cho toàn bộ API Kho Key
api_router = APIRouter(
    prefix="/admin/api/kho-key", 
    tags=["Admin API - Kho Key"],
    dependencies=[Depends(verify_admin_user)]
)

templates = Jinja2Templates(directory="app/templates")
DANH_SACH_LOAI_TB = ["Máy in", "Máy cắt bế", "Thiết bị khác"]


# =========================================================================
# SCHEMAS (ĐÃ BỔ SUNG VALIDATE FIELD)
# =========================================================================
class CheckImportReq(BaseModel):
    loai_thiet_bi: str
    ten_may: str
    gioi_han: int = Field(default=1, gt=0)
    keys: List[str]

class ConfirmImportReq(BaseModel):
    loai_thiet_bi: str
    ten_may: str
    gioi_han: int = Field(default=1, gt=0)
    keys_to_insert: List[str]

class BulkUpdateLimitReq(BaseModel):
    loai_thiet_bi: str
    ten_may: str
    new_limit: int = Field(gt=0)

class BulkDeleteReq(BaseModel):
    loai_thiet_bi: str
    ten_may: str
    type_del: str  # 'safe' hoặc 'all'

class KeyItemRow(BaseModel):
    id: Optional[int] = None
    loai_thiet_bi: str
    ten_may: str
    ma_key: str
    gioi_han: int = Field(default=1, gt=0)
    da_dung: int = Field(default=0, ge=0)
    trang_thai: Optional[str] = "Còn lượt"


# =========================================================================
# 1. TRANG CHÍNH: GIAO DIỆN QUẢN LÝ KHO KEY
# =========================================================================
@router.get("", response_class=HTMLResponse)
async def list_kho_key(
    request: Request,
    search: Optional[str] = Query(None),
    current_user: dict = Depends(require_login),
    loai_thiet_bi: Optional[str] = Query(None),
    trang_thai: Optional[str] = Query(None)
):
    user = await get_current_user_or_redirect(request)
    if not user:
        return RedirectResponse(url="/auth/login", status_code=303)

    user_role = str(user.get("role", "")).strip().lower()
    if user_role not in ["admin", "super admin", "system admin"]:
        return RedirectResponse(url="/admin/quan-ly-key", status_code=303)

    try:
        query = supabase.table("kho_key").select("*").order("id", desc=True)

        if search and search.strip():
            s = search.strip()
            query = query.or_(f"ten_may.ilike.*{s}*,ma_key.ilike.*{s}*")

        if loai_thiet_bi and loai_thiet_bi.strip():
            query = query.eq("loai_thiet_bi", loai_thiet_bi.strip())

        if trang_thai and trang_thai.strip():
            query = query.eq("trang_thai", trang_thai.strip())

        response = query.execute()
        raw_keys_data = response.data or []

    except Exception as db_err:
        print(f"\n❌ [SUPABASE DATABASE ERROR]: {str(db_err)}\n")
        traceback.print_exc()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Lỗi truy vấn CSDL Supabase: {str(db_err)}"
        )

    try:
        raw_keys_json = json.dumps(raw_keys_data, default=str, ensure_ascii=False)
        danh_sach_loai_tb_json = json.dumps(DANH_SACH_LOAI_TB, ensure_ascii=False)
        # 1. Lấy user hiện tại (giống bên kho key)
        user = await get_current_user_or_redirect(request)
        if not user:
            return RedirectResponse(url="/auth/login", status_code=303)
        return templates.TemplateResponse(
            request=request,
            name="admin_kho_key.html",
            context={
                "raw_keys": raw_keys_data,
                "raw_keys_json": raw_keys_json,
                "danh_sach_loai_tb": DANH_SACH_LOAI_TB,
                "danh_sach_loai_tb_json": danh_sach_loai_tb_json,
                "search": search or "",
                "selected_loai": loai_thiet_bi or "",
                "selected_trang_thai": trang_thai or "",
                "current_user": user, 
            }
        )
    except Exception as tpl_err:
        print(f"\n❌ [JINJA2 TEMPLATE ERROR]: {str(tpl_err)}\n")
        traceback.print_exc()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Lỗi Render HTML: {str(tpl_err)}"
        )


# =========================================================================
# 2. CÁC ENDPOINT API TRẢ VỀ JSON (ĐÃ BẢO MẬT & TỐI ƯU TRUY VẤN)
# =========================================================================

@api_router.post("/check-import")
async def check_import_keys(payload: CheckImportReq):
    try:
        clean_keys = list(dict.fromkeys([k.strip() for k in payload.keys if k.strip()]))
        if not clean_keys:
            raise HTTPException(status_code=400, detail="Danh sách key rỗng.")

        existing_keys = []
        chunk_size = 500
        for i in range(0, len(clean_keys), chunk_size):
            chunk = clean_keys[i:i + chunk_size]
            res = supabase.table("kho_key").select("ma_key").in_("ma_key", chunk).execute()
            if res.data:
                existing_keys.extend([item["ma_key"] for item in res.data])

        return {
            "has_duplicates": len(existing_keys) > 0,
            "total_input": len(clean_keys),
            "duplicates": existing_keys,
            "clean_keys": [k for k in clean_keys if k not in existing_keys]
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@api_router.post("/import")
async def confirm_import_keys(payload: ConfirmImportReq):
    try:
        clean_keys = list(dict.fromkeys([k.strip() for k in payload.keys_to_insert if k.strip()]))
        if not clean_keys:
            raise HTTPException(status_code=400, detail="Danh sách key hợp lệ rỗng.")

        # 🔒 ĐÃ SỬA: Lọc bỏ key đã tồn tại trong DB ngay thời điểm Insert để chống race condition
        res = supabase.table("kho_key").select("ma_key").in_("ma_key", clean_keys).execute()
        existing_keys = set(item["ma_key"] for item in res.data) if res.data else set()
        valid_keys = [k for k in clean_keys if k not in existing_keys]

        if not valid_keys:
            raise HTTPException(status_code=400, detail="Tất cả các key này đều đã tồn tại trong CSDL.")

        records = [
            {
                "ten_may": payload.ten_may.strip(),
                "ma_key": k,
                "gioi_han": payload.gioi_han,
                "da_dung": 0,
                "trang_thai": "Còn lượt",
                "loai_thiet_bi": payload.loai_thiet_bi.strip()
            }
            for k in valid_keys
        ]

        supabase.table("kho_key").insert(records).execute()
        return {"status": "success", "inserted_count": len(records), "skipped_count": len(clean_keys) - len(valid_keys)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Lỗi khi lưu key: {str(e)}")


@api_router.post("/bulk-update-limit")
async def bulk_update_limit(payload: BulkUpdateLimitReq):
    try:
        # 🔒 ĐÃ SỬA: Đồng bộ giới hạn và trạng thái an toàn
        loai_tb = payload.loai_thiet_bi.strip()
        ten_may = payload.ten_may.strip()
        new_limit = payload.new_limit

        # 1. Cập nhật giới hạn mới
        supabase.table("kho_key")\
            .update({"gioi_han": new_limit})\
            .eq("loai_thiet_bi", loai_tb)\
            .eq("ten_may", ten_may)\
            .execute()
        
        # 2. Cập nhật trạng thái 'Còn lượt'
        supabase.table("kho_key")\
            .update({"trang_thai": "Còn lượt"})\
            .eq("loai_thiet_bi", loai_tb)\
            .eq("ten_may", ten_may)\
            .lt("da_dung", new_limit)\
            .execute()

        # 3. Cập nhật trạng thái 'Hết lượt'
        supabase.table("kho_key")\
            .update({"trang_thai": "Hết lượt"})\
            .eq("loai_thiet_bi", loai_tb)\
            .eq("ten_may", ten_may)\
            .gte("da_dung", new_limit)\
            .execute()

        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@api_router.post("/bulk-delete")
async def bulk_delete(payload: BulkDeleteReq):
    try:
        query = supabase.table("kho_key").delete()\
            .eq("loai_thiet_bi", payload.loai_thiet_bi.strip())\
            .eq("ten_may", payload.ten_may.strip())
            
        if payload.type_del == "safe":
            query = query.eq("da_dung", 0)
            
        query.execute()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@api_router.post("/save-batch-details")
async def save_batch_details(items: List[KeyItemRow]):
    try:
        if not items:
            return {"status": "success", "updated": 0, "inserted": 0}

        to_insert = []
        to_update = []

        for item in items:
            data = item.dict(exclude={"id"})
            data["ten_may"] = data["ten_may"].strip()
            data["ma_key"] = data["ma_key"].strip()
            data["loai_thiet_bi"] = data["loai_thiet_bi"].strip()
            data["trang_thai"] = "Hết lượt" if data["da_dung"] >= data["gioi_han"] else "Còn lượt"

            if item.id:
                to_update.append({"id": item.id, **data})
            else:
                to_insert.append(data)

        # 🔒 ĐÃ SỬA: Xử lý Bulk Insert & Upsert để loại bỏ N+1 Query
        if to_insert:
            supabase.table("kho_key").insert(to_insert).execute()

        if to_update:
            supabase.table("kho_key").upsert(to_update).execute()

        return {"status": "success", "inserted": len(to_insert), "updated": len(to_update)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Lỗi lưu danh sách key: {str(e)}")


# =========================================================================
# 3. CÁC ROUTE FORM POST (ĐÃ VALIDATE INPUT FORM)
# =========================================================================

@router.post("/add")
async def add_key_form(
    request: Request,
    ten_may: str = Form(...),
    ma_key_list: str = Form(...),
    loai_thiet_bi: str = Form("Máy in"),
    gioi_han: int = Form(1)
):
    user = await get_current_user_or_redirect(request)
    if not user or str(user.get("role", "")).strip().lower() not in ["admin", "super admin", "system admin"]:
        return RedirectResponse(url="/auth/login", status_code=303)

    if gioi_han <= 0:
        return RedirectResponse(url="/admin/kho-key?error=invalid_limit", status_code=303)

    try:
        keys_raw = list(dict.fromkeys([k.strip() for k in ma_key_list.splitlines() if k.strip()]))
        if not keys_raw:
            return RedirectResponse(url="/admin/kho-key?error=empty_key", status_code=303)

        res = supabase.table("kho_key").select("ma_key").in_("ma_key", keys_raw).execute()
        existing_keys = set(item["ma_key"] for item in res.data) if res.data else set()
        
        valid_keys = [k for k in keys_raw if k not in existing_keys]
        if not valid_keys:
            return RedirectResponse(url="/admin/kho-key?error=all_keys_exist", status_code=303)

        records = [
            {
                "ten_may": ten_may.strip(),
                "ma_key": k,
                "gioi_han": gioi_han,
                "da_dung": 0,
                "trang_thai": "Còn lượt",
                "loai_thiet_bi": loai_thiet_bi.strip()
            }
            for k in valid_keys
        ]

        supabase.table("kho_key").insert(records).execute()
        return RedirectResponse(url=f"/admin/kho-key?msg=added_success&added={len(valid_keys)}", status_code=303)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Lỗi khi thêm key: {str(e)}")


@router.post("/update/{key_id}")
async def update_key_form(
    request: Request,
    key_id: int,
    ten_may: str = Form(...),
    ma_key: str = Form(...),
    loai_thiet_bi: str = Form(...),
    gioi_han: int = Form(...),
    da_dung: int = Form(...)
):
    user = await get_current_user_or_redirect(request)
    if not user or str(user.get("role", "")).strip().lower() not in ["admin", "super admin", "system admin"]:
        return RedirectResponse(url="/auth/login", status_code=303)

    if gioi_han <= 0 or da_dung < 0:
        raise HTTPException(status_code=400, detail="Giới hạn hoặc lượt đã dùng không hợp lệ.")

    try:
        trang_thai = "Hết lượt" if da_dung >= gioi_han else "Còn lượt"
        update_payload = {
            "ten_may": ten_may.strip(),
            "ma_key": ma_key.strip(),
            "loai_thiet_bi": loai_thiet_bi.strip(),
            "gioi_han": gioi_han,
            "da_dung": da_dung,
            "trang_thai": trang_thai
        }
        supabase.table("kho_key").update(update_payload).eq("id", key_id).execute()
        return RedirectResponse(url="/admin/kho-key?msg=updated_success", status_code=303)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Lỗi khi cập nhật key ID {key_id}: {str(e)}")


@router.post("/delete/{key_id}")
async def delete_key_form(request: Request, key_id: int):
    user = await get_current_user_or_redirect(request)
    if not user or str(user.get("role", "")).strip().lower() not in ["admin", "super admin", "system admin"]:
        return RedirectResponse(url="/auth/login", status_code=303)

    try:
        supabase.table("kho_key").delete().eq("id", key_id).execute()
        return RedirectResponse(url="/admin/kho-key?msg=deleted_success", status_code=303)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Lỗi khi xóa key ID {key_id}: {str(e)}")