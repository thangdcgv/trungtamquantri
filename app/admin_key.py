import json
import os
import traceback
from typing import Optional, List
from fastapi import APIRouter, Request, Form, Query, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from config import supabase

router = APIRouter(prefix="/admin/kho-key", tags=["Admin - Quản Lý Kho Key"])
api_router = APIRouter(prefix="/admin/api/kho-key", tags=["Admin API - Kho Key"])

# Cấu hình thư mục chứa file HTML
templates = Jinja2Templates(directory="app/templates")

# Danh sách phân loại thiết bị mặc định
# Nên bỏ Emoji ở backend để đồng nhất tên nhóm trong CSDL
DANH_SACH_LOAI_TB = ["Máy in", "Phần mềm / Key Reset", "Thiết bị khác"]


# =========================================================================
# SCHEMAS (PYDANTIC MODELS CHO API FETCH)
# =========================================================================
class CheckImportReq(BaseModel):
    loai_thiet_bi: str
    ten_may: str
    gioi_han: int = 1
    keys: List[str]

class ConfirmImportReq(BaseModel):
    loai_thiet_bi: str
    ten_may: str
    gioi_han: int = 1
    keys_to_insert: List[str]

class BulkUpdateLimitReq(BaseModel):
    loai_thiet_bi: str
    ten_may: str
    new_limit: int

class BulkDeleteReq(BaseModel):
    loai_thiet_bi: str
    ten_may: str
    type_del: str  # 'safe' hoặc 'all'

class KeyItemRow(BaseModel):
    id: Optional[int] = None
    loai_thiet_bi: str
    ten_may: str
    ma_key: str
    gioi_han: int = 1
    da_dung: int = 0
    trang_thai: str = "Còn lượt"


# =========================================================================
# 1. TRANG CHÍNH: GIAO DIỆN QUẢN LÝ KHO KEY
# =========================================================================
@router.get("", response_class=HTMLResponse)
async def list_kho_key(
    request: Request,
    search: Optional[str] = Query(None),
    loai_thiet_bi: Optional[str] = Query(None),
    trang_thai: Optional[str] = Query(None)
):
    raw_keys_data = []
    

    # Truy vấn Supabase DB
    try:
        query = supabase.table("kho_key").select("*").order("id", desc=True)

        # Lọc tìm kiếm (Sử dụng dấu * đại diện cho wildcard trong PostgREST)
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

    # Convert dữ liệu sang JSON an toàn trước khi Render (xử lý cả kiểu timestamp)
    try:
        raw_keys_json = json.dumps(raw_keys_data, default=str, ensure_ascii=False)
        danh_sach_loai_tb_json = json.dumps(DANH_SACH_LOAI_TB, ensure_ascii=False)

        return templates.TemplateResponse(
        request=request,                            # 👈 Khai báo rõ request
        name="admin_kho_key.html",                  # 👈 Khai báo rõ tên file template
        context={                                   # 👈 Khai báo rõ dict context
            "raw_keys": raw_keys_data,
            "raw_keys_json": raw_keys_json,
            "danh_sach_loai_tb": DANH_SACH_LOAI_TB,
            "danh_sach_loai_tb_json": danh_sach_loai_tb_json,
            "search": search or "",
            "selected_loai": loai_thiet_bi or "",
            "selected_trang_thai": trang_thai or "",
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
# 2. CÁC ENDPOINT API TRẢ VỀ JSON (DÙNG CHO JAVASCRIPT FETCH TRÊN TEMPLATE)
# =========================================================================

@api_router.post("/check-import")
async def check_import_keys(payload: CheckImportReq):
    try:
        clean_keys = list(dict.fromkeys([k.strip() for k in payload.keys if k.strip()]))
        if not clean_keys:
            raise HTTPException(status_code=400, detail="Danh sách key rỗng.")

        # Chia nhỏ mảng 500 keys / lần kiểm tra để tránh tràn header URL
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
            "clean_keys": clean_keys
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@api_router.post("/import")
async def confirm_import_keys(payload: ConfirmImportReq):
    try:
        records = [
            {
                "ten_may": payload.ten_may.strip(),
                "ma_key": k,
                "gioi_han": payload.gioi_han,
                "da_dung": 0,
                "trang_thai": "Còn lượt",
                "loai_thiet_bi": payload.loai_thiet_bi.strip()
            }
            for k in payload.keys_to_insert
        ]

        if records:
            supabase.table("kho_key").insert(records).execute()

        return {"status": "success", "inserted_count": len(records)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Lỗi khi lưu key: {str(e)}")


@api_router.post("/bulk-update-limit")
async def bulk_update_limit(payload: BulkUpdateLimitReq):
    try:
        # 1. Cập nhật gioi_han
        supabase.table("kho_key")\
            .update({"gioi_han": payload.new_limit})\
            .eq("loai_thiet_bi", payload.loai_thiet_bi)\
            .eq("ten_may", payload.ten_may)\
            .execute()
        
        # 2. Đồng bộ lại trang_thai dựa trên da_dung và gioi_han mới
        supabase.table("kho_key")\
            .update({"trang_thai": "Còn lượt"})\
            .eq("loai_thiet_bi", payload.loai_thiet_bi)\
            .eq("ten_may", payload.ten_may)\
            .lt("da_dung", payload.new_limit)\
            .execute()

        supabase.table("kho_key")\
            .update({"trang_thai": "Hết lượt"})\
            .eq("loai_thiet_bi", payload.loai_thiet_bi)\
            .eq("ten_may", payload.ten_may)\
            .gte("da_dung", payload.new_limit)\
            .execute()

        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@api_router.post("/bulk-delete")
async def bulk_delete(payload: BulkDeleteReq):
    try:
        query = supabase.table("kho_key").delete().eq("loai_thiet_bi", payload.loai_thiet_bi).eq("ten_may", payload.ten_may)
        if payload.type_del == "safe":
            query = query.eq("da_dung", 0)
        query.execute()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@api_router.post("/save-batch-details")
async def save_batch_details(items: List[KeyItemRow]):
    try:
        for item in items:
            data = item.dict(exclude={"id"})
            # Tự động tính trạng thái
            data["trang_thai"] = "Hết lượt" if data["da_dung"] >= data["gioi_han"] else "Còn lượt"

            if item.id:
                supabase.table("kho_key").update(data).eq("id", item.id).execute()
            else:
                supabase.table("kho_key").insert(data).execute()

        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# =========================================================================
# 3. CÁC ROUTE FORM TRUYỀN THỐNG (FORM POST / REDIRECT)
# =========================================================================

@router.post("/add")
async def add_key_form(
    ten_may: str = Form(...),
    ma_key_list: str = Form(...),
    loai_thiet_bi: str = Form("Máy in"),
    gioi_han: int = Form(1)
):
    try:
        keys_raw = list(dict.fromkeys([k.strip() for k in ma_key_list.splitlines() if k.strip()]))
        if not keys_raw:
            return RedirectResponse(url="/admin/kho-key?error=empty_key", status_code=303)

        # Lọc bỏ các key đã có trong CSDL
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
    key_id: int,
    ten_may: str = Form(...),
    ma_key: str = Form(...),
    loai_thiet_bi: str = Form(...),
    gioi_han: int = Form(...),
    da_dung: int = Form(...)
):
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
async def delete_key_form(key_id: int):
    try:
        supabase.table("kho_key").delete().eq("id", key_id).execute()
        return RedirectResponse(url="/admin/kho-key?msg=deleted_success", status_code=303)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Lỗi khi xóa key ID {key_id}: {str(e)}")