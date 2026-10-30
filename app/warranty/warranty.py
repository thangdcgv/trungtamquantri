from fastapi import APIRouter, Request, Form, HTTPException, status, Depends, Query
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, field_validator
from typing import List, Optional, Union, Dict, Any
from datetime import datetime

from config import supabase
from app.templates_config import templates  # 👈 Import sạch sẽ, không bị vòng lặp
from app.auth import require_login

router = APIRouter(prefix="/warranty", tags=["Warranty"])


# ==================== HÀM BỔ TRỢ & HELPER ====================

def format_phone_number(phone: Any) -> str:
    """Chuẩn hóa SĐT: tự thêm số 0 ở đầu nếu bị mất."""
    phone_raw = str(phone or "").strip()
    if phone_raw and not phone_raw.startswith('0') and len(phone_raw) in [9, 10]:
        phone_raw = "0" + phone_raw
    return phone_raw


def validate_condition(cond_str: Optional[Any], default_val: str = "Máy mới") -> str:
    """Đảm bảo condition thuộc ['Máy mới', 'Đã qua sử dụng']."""
    if not cond_str:
        return default_val
    val = str(cond_str).strip()
    val_lower = val.lower()
    
    if "qua sử dụng" in val_lower or "cũ" in val_lower or "used" in val_lower:
        return "Đã qua sử dụng"
    if "mới" in val_lower or "moi" in val_lower or "new" in val_lower:
        return "Máy mới"
        
    return val or default_val


def parse_bool(val: Any) -> bool:
    """Chuyển đổi linh hoạt các giá trị dạng string/bool từ Form hoặc JSON sang bool."""
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return val == 1
    if isinstance(val, str):
        return val.strip().lower() in ["true", "1", "on", "yes"]
    return False


def get_active_policies():
    """Lấy danh sách các chính sách bảo hành đang áp dụng (Bao gồm UUID id)."""
    try:
        res = (
            supabase.table("warranty_policy")
            .select("id, policy_name, category, brand_name, condition, warranty_months, head_warranty_months, cartridge_warranty_months, page_limit_body, page_limit_head, no_page_limit, is_active")
            .eq("is_active", True)
            .order("created_at", desc=True)
            .execute()
        )
        return res.data or []
    except Exception as e:
        print(f"❌ Lỗi lấy chính sách: {e}")
        return []


def get_staff_display_name(current_user: dict) -> str:
    if not current_user:
        return "Hệ thống"
    return (
        current_user.get("ho_ten") 
        or current_user.get("display_name") 
        or current_user.get("username") 
        or "Hệ thống"
    )


# ==================== SCHEMAS ====================

class BatchItemSchema(BaseModel):
    policy_id: str = Field(..., min_length=1)  # 👈 Bắt buộc có UUID chính sách
    serial_number: str = Field(..., min_length=1)
    customer_name: str = Field(..., min_length=1)
    phone_number: str = Field(..., min_length=8)
    brand_name: Optional[str] = ""
    model_name: Optional[str] = ""
    category: str = "In phun"
    condition: Optional[str] = "Máy mới"
    ink_type: Optional[str] = "DYE UV"
    initial_counter: int = Field(0, ge=0)
    purchase_date: Optional[str] = None

    @field_validator('policy_id')
    @classmethod
    def validate_policy_id(cls, v: str) -> str:
        if not v or not v.strip() or v.strip().upper() == "NULL":
            raise ValueError("Vui lòng chọn chính sách bảo hành hợp lệ!")
        return v.strip()

    @field_validator('condition')
    @classmethod
    def validate_cond(cls, v: Optional[str]) -> str:
        return validate_condition(v)

    @field_validator('serial_number')
    @classmethod
    def validate_serial_number(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Số Serial không được để trống!")
        return v.strip().upper()

    @field_validator('customer_name')
    @classmethod
    def validate_customer_name(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Tên khách hàng không được để trống!")
        return v.strip().title()

    @field_validator('phone_number')
    @classmethod
    def validate_phone(cls, v: str) -> str:
        clean_phone = format_phone_number(v)
        if not clean_phone or not clean_phone.isdigit() or not (9 <= len(clean_phone) <= 11):
            raise ValueError("Số điện thoại không hợp lệ (9 - 11 chữ số)!")
        return clean_phone

    @field_validator('brand_name')
    @classmethod
    def validate_brand(cls, v: Optional[str]) -> str:
        return v.strip() if v else ""

    @field_validator('model_name')
    @classmethod
    def validate_model(cls, v: Optional[str]) -> str:
        return v.strip() if v and v.strip() else ""


class BatchCreateSchema(BaseModel):
    items: List[BatchItemSchema]


class WarrantyUpdateSchema(BaseModel):
    policy_id: Optional[str] = None        # 👈 UUID khóa ngoại tới warranty_policy
    serial_number: Optional[str] = None
    customer_name: Optional[str] = None
    phone_number: Optional[str] = None
    purchase_date: Optional[str] = None
    category: Optional[str] = None
    condition: Optional[str] = None
    brand_name: Optional[str] = None
    model_name: Optional[str] = None
    ink_type: Optional[str] = None
    warranty_months: Optional[int] = None
    head_warranty_months: Optional[int] = None
    cartridge_warranty_months: Optional[int] = None
    initial_counter: Optional[int] = None
    current_counter: Optional[int] = None
    page_limit_body: Optional[int] = None
    page_limit_head: Optional[int] = None
    no_page_limit: Optional[Union[bool, str]] = None

    @field_validator('policy_id')
    @classmethod
    def validate_policy_id(cls, v: Optional[str]) -> Optional[str]:
        if v and (v.strip() == "" or v.strip().upper() == "NULL"):
            return None
        return v.strip() if v else None

    @field_validator('condition')
    @classmethod
    def validate_cond(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            return validate_condition(v)
        return v

    @field_validator('serial_number')
    @classmethod
    def validate_serial(cls, v: Optional[str]) -> Optional[str]:
        if v and v.strip():
            return v.strip().upper()
        return v

    @field_validator('customer_name')
    @classmethod
    def validate_customer(cls, v: Optional[str]) -> Optional[str]:
        if v and v.strip():
            return v.strip().title()
        return v

    @field_validator('phone_number')
    @classmethod
    def validate_phone(cls, v: Optional[str]) -> Optional[str]:
        if v and v.strip():
            clean_phone = format_phone_number(v)
            if not clean_phone.isdigit() or not (9 <= len(clean_phone) <= 11):
                raise ValueError("Số điện thoại không hợp lệ (9 - 11 chữ số)!")
            return clean_phone
        return v


# ==================== 1. ROUTE XEM DANH SÁCH ====================

@router.get("/list", response_class=HTMLResponse)
def get_warranty_list(
    request: Request,
    q: Optional[str] = None,
    category: Optional[str] = None,
    created_by: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=50),
    current_user: dict = Depends(require_login)
):
    try:
        raw_policies = get_active_policies()
        policies = sorted(raw_policies, key=lambda p: (str(p.get("category", "")), str(p.get("policy_name", ""))))
        
        raw_categories = list({p.get("category") for p in policies if p.get("category")})
        categories = sorted(raw_categories) if raw_categories else ["In phun", "In Laser", "In kim", "Cắt bế", "Scan","Thiết bị điện", "Thiết bị khác"]

        # Lấy danh sách người tạo
        creators_res = supabase.table("warranty_records").select("staff_name").limit(500).execute()
        creators_set = set()
        if creators_res.data:
            for item in creators_res.data:
                val = item.get("staff_name")
                if isinstance(val, str) and val.strip():
                    creators_set.add(val.strip())
                elif isinstance(val, dict):
                    name_str = val.get("display_name") or val.get("ho_ten") or val.get("username")
                    if name_str:
                        creators_set.add(str(name_str).strip())
        creators_list = sorted(list(creators_set))

        # Dynamic Query Builder
        query = supabase.table("warranty_records").select("*", count="exact")

        if q and q.strip():
            clean_q = q.strip().replace(",", " ")
            keyword = f"%{clean_q}%"
            query = query.or_(
                f"serial_number.ilike.{keyword},"
                f"customer_name.ilike.{keyword},"
                f"phone_number.ilike.{keyword},"
                f"brand_name.ilike.{keyword},"
                f"model_name.ilike.{keyword},"
                f"ink_type.ilike.{keyword}"
            )

        if category and category.strip():
            query = query.eq("category", category.strip())

        if created_by and created_by.strip():
            query = query.eq("staff_name", created_by.strip())

        if start_date and start_date.strip():
            query = query.gte("purchase_date", start_date.strip())
        if end_date and end_date.strip():
            query = query.lte("purchase_date", end_date.strip())

        # Phân trang
        offset_start = (page - 1) * page_size
        offset_end = offset_start + page_size - 1

        records_res = query.order("created_at", desc=True).range(offset_start, offset_end).execute()

        total_count = records_res.count or 0
        total_pages = (total_count + page_size - 1) // page_size

        return templates.TemplateResponse(
            request=request,
            name="warranty/warranty_list.html",
            context={
                "request": request,
                "items": records_res.data or [],
                "creators": creators_list,
                "categories": categories,
                "policies": policies,
                "current_user": current_user,
                "pagination": {
                    "current_page": page,
                    "total_pages": total_pages,
                    "total_count": total_count,
                    "page_size": page_size
                }
            }
        )

    except Exception as e:
        # 🟢 FIX: Sửa cú pháp TemplateResponse đúng chuẩn FastAPI/Starlette mới
        return templates.TemplateResponse(
            request=request,
            name="warranty/warranty_list.html",
            context={
                "request": request,
                "items": [],
                "creators": [],
                "categories": [],
                "policies": [],
                "error_msg": str(e)
            },
            status_code=500
        )


# ==================== 2. ROUTE BATCH API ====================

@router.post("/api/batch-create")
def batch_create_warranty_records(
    data: BatchCreateSchema,
    current_user: dict = Depends(require_login)
):
    try:
        if not data.items:
            return JSONResponse(status_code=400, content={"success": False, "message": "Danh sách rỗng!"})

        # Kiểm tra trùng Serial trong payload
        input_sns = [item.serial_number for item in data.items]
        if len(input_sns) != len(set(input_sns)):
            return JSONResponse(status_code=400, content={"success": False, "message": "🚫 Có số Serial bị trùng lặp ngay trong dữ liệu gửi lên!"})

        # Kiểm tra trùng Serial trong DB
        existing_res = supabase.table("warranty_records").select("serial_number").in_("serial_number", input_sns).execute()
        if existing_res.data:
            exist_sns = [x.get("serial_number") for x in existing_res.data]
            return JSONResponse(
                status_code=400, 
                content={"success": False, "message": f"🚫 Số Serial đã tồn tại: {', '.join(exist_sns)}"}
            )

        active_policies = get_active_policies()
        policy_by_id = {str(p.get('id')): p for p in active_policies if p.get('id')}
        
        staff_display = get_staff_display_name(current_user)
        now_dt = datetime.now()
        now_str = now_dt.strftime("%H:%M %d/%m/%Y")

        records_to_insert = []
        for item in data.items:
            p_date = item.purchase_date.strip() if item.purchase_date and item.purchase_date.strip() else now_dt.strftime("%Y-%m-%d")
            
            # Khớp chính sách dựa hoàn toàn vào policy_id (UUID)
            p_info = policy_by_id.get(str(item.policy_id), {})
            p_name = p_info.get('policy_name', 'N/A')

            w_months = int(p_info.get('warranty_months', 0))
            h_months = int(p_info.get('head_warranty_months', 0)) if item.category != "In Laser" else 0
            c_months = int(p_info.get('cartridge_warranty_months', 0)) if item.category == "In Laser" else 0
            p_body = int(p_info.get('page_limit_body', 0))
            p_head = int(p_info.get('page_limit_head', 0))
            is_no_limit = bool(p_info.get('no_page_limit', False))

            log_entry = f"• [{now_str}] {staff_display}: KHỞI TẠO (Gói: {p_name}) | Máy: {item.brand_name or ''} {item.model_name or ''} ({item.ink_type or ''}) | S/N: {item.serial_number}"

            records_to_insert.append({
                "policy_id": item.policy_id, # 👈 Sử dụng hoàn toàn khóa ngoại UUID
                "customer_name": item.customer_name,
                "phone_number": item.phone_number,
                "purchase_date": p_date,
                "category": item.category,
                "condition": validate_condition(item.condition),
                "brand_name": item.brand_name or "",
                "model_name": item.model_name or "",
                "ink_type": item.ink_type or "DYE UV",
                "serial_number": item.serial_number,
                "warranty_months": w_months,
                "head_warranty_months": h_months,
                "cartridge_warranty_months": c_months,
                "initial_counter": item.initial_counter,
                "current_counter": item.initial_counter,
                "page_limit_body": p_body,
                "page_limit_head": p_head,
                "no_page_limit": is_no_limit,
                "staff_name": staff_display,
                "edit_log": log_entry,
                "pages_updated_at": now_dt.isoformat()
            })

        res = supabase.table("warranty_records").insert(records_to_insert).execute()
        if res.data:
            return {"success": True, "message": f"Tạo thành công {len(res.data)} phiếu bảo hành!", "count": len(res.data)}
        
        return JSONResponse(status_code=500, content={"success": False, "message": "Không thể lưu dữ liệu vào cơ sở dữ liệu."})

    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "message": f"Lỗi hệ thống: {str(e)}"})


# ==================== 3. ROUTE CHI TIẾT PHIẾU BẢO HÀNH ====================

@router.get("/chi-tiet/{warranty_id}", response_class=HTMLResponse)
def render_warranty_detail(
    request: Request, 
    warranty_id: str,
    current_user: dict = Depends(require_login)
):
    try:
        res = supabase.table("warranty_records").select("*").eq("id", warranty_id).execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="Không tìm thấy phiếu bảo hành")
            
        policies = get_active_policies()
        return templates.TemplateResponse(
            request=request,
            name="warranty/detail_warranty.html",
            context={
                "request": request,
                "item": res.data[0],
                "policies": policies,
                "current_user": current_user
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi hệ thống: {str(e)}")


# ==================== 4. ROUTE CẬP NHẬT PHIẾU BẢO HÀNH ====================

@router.post("/api/update/{warranty_id}")
async def update_warranty_record(
    request: Request,
    warranty_id: str,
    current_user: dict = Depends(require_login)
):
    try:
        data: Dict[str, Any] = {}
        content_type = request.headers.get("content-type", "").lower()
        is_json_request = "application/json" in content_type

        if is_json_request:
            data = await request.json()
        else:
            form_data = await request.form()
            data = dict(form_data)

        # Lấy thông tin phiếu cũ
        old_res = supabase.table("warranty_records").select("*").eq("id", warranty_id).execute()
        if not old_res.data:
            msg = "Không tìm thấy phiếu bảo hành để cập nhật"
            return JSONResponse(status_code=404, content={"success": False, "message": msg}) if is_json_request else HTTPException(404, msg)
        
        old_data = old_res.data[0]
        staff_display = get_staff_display_name(current_user)

        # Chuẩn hóa dữ liệu văn bản
        clean_sn = str(data.get("serial_number") or old_data.get("serial_number") or "").strip().upper()
        new_customer = str(data.get("customer_name") or old_data.get("customer_name") or "").strip().title()
        new_phone = format_phone_number(data.get("phone_number") or old_data.get("phone_number"))
        new_brand = str(data.get("brand_name") or old_data.get("brand_name") or "").strip()
        new_model = str(data.get("model_name") or old_data.get("model_name") or "").strip()
        new_ink = str(data.get("ink_type") or old_data.get("ink_type") or "").strip()
        
        # Xử lý an toàn policy_id (UUID)
        raw_policy_id = data.get("policy_id") or old_data.get("policy_id")
        if raw_policy_id and str(raw_policy_id).strip().upper() in ["", "NONE", "NULL"]:
            new_policy_id = None
        else:
            new_policy_id = str(raw_policy_id).strip() if raw_policy_id else None

        purchase_date = data.get("purchase_date") or old_data.get("purchase_date")
        if purchase_date and not str(purchase_date).strip():
            purchase_date = None

        category = data.get("category") or old_data.get("category") or "In phun"
        
        # 🟢 ĐẢM BẢO CONDITION CHỈ LÀ 'Máy mới' HOẶC 'Đã qua sử dụng'
        raw_cond = data.get("condition")
        condition = validate_condition(raw_cond, default_val=old_data.get("condition") or "Máy mới")

        def parse_int(val, default_val):
            try:
                if val is None or str(val).strip() == "":
                    return default_val
                return int(val)
            except (ValueError, TypeError):
                return default_val

        initial_counter = parse_int(data.get("initial_counter"), old_data.get("initial_counter") or 0)
        current_counter = parse_int(data.get("current_counter"), old_data.get("current_counter") or initial_counter)

        # Tra cứu chính sách theo UUID
        active_policies = get_active_policies()
        policy_by_id = {str(p.get('id')): p for p in active_policies if p.get('id')}
        
        p_info = policy_by_id.get(str(new_policy_id), {}) if new_policy_id else {}
        policy_name_display = p_info.get('policy_name', 'Chưa chọn')

        if p_info:
            new_policy_id = p_info.get('id')
            warranty_months = int(p_info.get('warranty_months', 0))
            head_warranty_months = int(p_info.get('head_warranty_months', 0)) if category != "In Laser" else 0
            cartridge_warranty_months = int(p_info.get('cartridge_warranty_months', 0)) if category == "In Laser" else 0
            page_limit_body = int(p_info.get('page_limit_body', 0))
            page_limit_head = int(p_info.get('page_limit_head', 0))
            no_page_limit = bool(p_info.get('no_page_limit', False))
        else:
            warranty_months = parse_int(data.get("warranty_months"), old_data.get("warranty_months") or 0)
            head_warranty_months = parse_int(data.get("head_warranty_months"), old_data.get("head_warranty_months") or 0)
            cartridge_warranty_months = parse_int(data.get("cartridge_warranty_months"), old_data.get("cartridge_warranty_months") or 0)
            page_limit_body = parse_int(data.get("page_limit_body"), old_data.get("page_limit_body") or 0)
            page_limit_head = parse_int(data.get("page_limit_head"), old_data.get("page_limit_head") or 0)
            no_page_limit = parse_bool(data.get("no_page_limit") if "no_page_limit" in data else old_data.get("no_page_limit"))

        # Validation bắt buộc
        if not clean_sn or not new_customer:
            return JSONResponse(status_code=400, content={"success": False, "message": "Vui lòng nhập đầy đủ Serial và Tên khách hàng!"})

        if new_phone and (not new_phone.isdigit() or not (9 <= len(new_phone) <= 11)):
            return JSONResponse(status_code=400, content={"success": False, "message": "Số điện thoại không đúng định dạng (9 - 11 chữ số)!"})

        if initial_counter < 0 or current_counter < initial_counter:
            return JSONResponse(status_code=400, content={"success": False, "message": "Số trang hiện tại không hợp lệ!"})

        if clean_sn != old_data.get("serial_number"):
            sn_check = supabase.table("warranty_records").select("id").eq("serial_number", clean_sn).neq("id", warranty_id).execute()
            if sn_check.data:
                return JSONResponse(status_code=400, content={"success": False, "message": f"🚫 Số Serial '{clean_sn}' đã tồn tại ở phiếu khác!"})

        # Đọc tên chính sách cũ cho edit_log (chỉ tra cứu theo policy_id UUID)
        old_policy_id = old_data.get("policy_id")
        old_p_info = policy_by_id.get(str(old_policy_id), {}) if old_policy_id else {}
        old_policy_name = old_p_info.get("policy_name") or "Chưa chọn"

        # So sánh tạo vết thay đổi
        fields_map = {
            "serial_number": ("Số Serial", old_data.get("serial_number"), clean_sn),
            "customer_name": ("Tên KH", old_data.get("customer_name"), new_customer),
            "phone_number": ("SĐT", old_data.get("phone_number"), new_phone),
            "brand_name": ("Thương hiệu", old_data.get("brand_name"), new_brand),
            "model_name": ("Model", old_data.get("model_name"), new_model),
            "ink_type": ("Loại mực", old_data.get("ink_type"), new_ink),
            "policy_id": ("Gói BH", old_policy_name, policy_name_display),
            "condition": ("Tình trạng", old_data.get("condition"), condition),
            "warranty_months": ("BH Thân", old_data.get("warranty_months"), warranty_months),
            "head_warranty_months": ("BH Đầu phun", old_data.get("head_warranty_months"), head_warranty_months),
            "initial_counter": ("Counter ban đầu", old_data.get("initial_counter"), initial_counter),
            "current_counter": ("Counter hiện tại", old_data.get("current_counter"), current_counter),
        }
        changes = [f"{lbl}: {old_v} ➔ {new_v}" for k, (lbl, old_v, new_v) in fields_map.items() if str(old_v or '').strip() != str(new_v or '').strip()]

        now_dt = datetime.now()
        detail_str = f" | Thay đổi: {', '.join(changes)}" if changes else " | Không thay đổi nội dung"
        log_entry = f"• [{now_dt.strftime('%H:%M %d/%m/%Y')}] {staff_display}: CẬP NHẬT (Gói: {policy_name_display}){detail_str}"
        
        existing_log = str(old_data.get("edit_log", "")).strip()
        full_log = f"{log_entry}\n{existing_log}" if existing_log and existing_log.lower() != "none" else log_entry

        update_payload = {
            "policy_id": new_policy_id,
            "serial_number": clean_sn,
            "customer_name": new_customer,
            "phone_number": new_phone,
            "purchase_date": purchase_date,
            "category": category,
            "condition": condition,  # 👈 Luôn luôn là 'Máy mới' hoặc 'Đã qua sử dụng'
            "brand_name": new_brand,
            "model_name": new_model,
            "ink_type": new_ink,
            "warranty_months": warranty_months,
            "head_warranty_months": head_warranty_months,
            "cartridge_warranty_months": cartridge_warranty_months,
            "initial_counter": initial_counter,
            "current_counter": current_counter,
            "page_limit_body": page_limit_body,
            "page_limit_head": page_limit_head,
            "no_page_limit": no_page_limit,
            "staff_name": staff_display,
            "edit_log": full_log
        }

        if old_data.get("current_counter") != current_counter:
            update_payload["pages_updated_at"] = now_dt.isoformat()

        res = supabase.table("warranty_records").update(update_payload).eq("id", warranty_id).execute()

        if is_json_request:
            return {"success": True, "message": "Cập nhật thành công!"}
        
        return RedirectResponse(url=f"/warranty/chi-tiet/{warranty_id}?status=updated", status_code=status.HTTP_303_SEE_OTHER)

    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Lỗi cập nhật phiếu bảo hành ({warranty_id}): {e}")
        return JSONResponse(status_code=500, content={"success": False, "message": f"Lỗi hệ thống: {str(e)}"})


# ==================== 5. ROUTE API XÓA PHIẾU BẢO HÀNH ====================

@router.delete("/api/delete/{warranty_id}")
def delete_warranty_record(
    warranty_id: str,
    current_user: dict = Depends(require_login)
):
    try:
        old_res = supabase.table("warranty_records").select("staff_name").eq("id", warranty_id).execute()
        if not old_res.data:
            return JSONResponse(status_code=404, content={"success": False, "message": "Không tìm thấy phiếu bảo hành!"})

        staff_display = get_staff_display_name(current_user)
        
        # 🟢 FIX: Chuyển role về chữ thường để phân quyền chính xác
        user_role = str(current_user.get("role", "")).strip().lower()
        is_admin = user_role in ["admin", "super admin", "system admin"]
        is_owner = str(old_res.data[0].get("staff_name", "")).strip().lower() == staff_display.strip().lower()

        if not (is_admin or is_owner):
            return JSONResponse(status_code=403, content={"success": False, "message": "Bạn không có quyền xóa phiếu của người khác!"})

        res = supabase.table("warranty_records").delete().eq("id", warranty_id).execute()
        if res.data:
            return {"success": True, "message": "Xóa phiếu bảo hành thành công"}
            
        return JSONResponse(status_code=400, content={"success": False, "message": "Không thể xóa phiếu bảo hành."})

    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "message": f"Lỗi hệ thống: {str(e)}"})