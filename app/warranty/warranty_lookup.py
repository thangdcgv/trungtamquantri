import io
import re
import html
import base64
from datetime import datetime, date
from typing import Optional
from dateutil.relativedelta import relativedelta

from fastapi import APIRouter, Request, Query, HTTPException, Response
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

# Import Supabase client từ file config dự án của bạn
from config import supabase

# Import module tạo PDF/QR bảo hành
try:
    from app.pdf_warranty import create_warranty_pdf
except ImportError:
    try:
        from app.pdf_warranty import create_warranty_pdf
    except ImportError:
        create_warranty_pdf = None

router = APIRouter(prefix="/warranty", tags=["Bảo Hành Điện Tử"])
templates = Jinja2Templates(directory="app/templates")


# =========================================================
# HELPER LOGIC & CHUYỂN ĐỔI DỮ LIỆU AN TOÀN
# =========================================================

def mask_phone(phone: str) -> str:
    """Ẩn 4 số cuối số điện thoại để bảo mật thông tin khách hàng"""
    if not phone:
        return ""
    phone_str = str(phone).strip()
    if len(phone_str) <= 4:
        return "*" * len(phone_str)
    return phone_str[:-4] + "****"


def safe_parse_datetime(val) -> Optional[datetime]:
    """Chuyển đổi các định dạng chuỗi Ngày/Thời gian từ Supabase sang datetime an toàn"""
    if not val:
        return None
    if isinstance(val, datetime):
        return val
    if isinstance(val, date):
        return datetime.combine(val, datetime.min.time())
    
    val_str = str(val).strip()
    # Thử parse ISO format từ Supabase (vd: 2026-03-15T10:30:00+00:00)
    try:
        return datetime.fromisoformat(val_str.replace('Z', '+00:00'))
    except ValueError:
        pass
    
    # Thử parse YYYY-MM-DD
    try:
        return datetime.strptime(val_str.split('T')[0], '%Y-%m-%d')
    except ValueError:
        return None


def format_dt_str(val, fmt='%d/%m/%Y') -> str:
    """Format đối tượng datetime hoặc chuỗi ISO sang chuỗi định dạng %d/%m/%Y"""
    dt = safe_parse_datetime(val)
    return dt.strftime(fmt) if dt else ""


def get_warranty_status(start_date, body_months, head_months, body_limit, head_limit, current_counter):
    """Tính toán trạng thái bảo hành hai chỉ số (thời gian & số trang in)"""
    today = datetime.today().replace(hour=0, minute=0, second=0, microsecond=0)
    
    dt_start = safe_parse_datetime(start_date) or today

    def check_part(months, limit):
        if months <= 0:
            return False, 0, dt_start, False
        
        end_date = dt_start + relativedelta(months=months)
        days_left = (end_date - today).days
        is_time_ok = days_left >= 0
        is_page_ok = current_counter < limit if limit > 0 else True
        
        return (is_time_ok and is_page_ok), max(0, days_left), end_date, True

    body_ok, body_days, body_end, body_exists = check_part(body_months, body_limit)
    head_ok, head_days, head_end, head_exists = check_part(head_months, head_limit)

    reasons = []
    if body_exists and not body_ok:
        reasons.append("Thân máy hết hạn")
    if head_exists and not head_ok:
        reasons.append("Linh kiện hết hạn")

    return {
        "is_active": len(reasons) == 0,
        "reason": " / ".join(reasons) if reasons else "Đang trong hạn",
        "body": {
            "is_ok": body_ok,
            "days_left": body_days,
            "end_date": body_end.strftime('%d/%m/%Y')
        },
        "head": {
            "is_ok": head_ok,
            "days_left": head_days,
            "end_date": head_end.strftime('%d/%m/%Y') if head_exists else "N/A"
        }
    }


def format_warranty_text_v3(item: dict) -> str:
    """Định dạng chuỗi bảo hành phục vụ tạo file PDF"""
    body_months = item.get('warranty_months', 0) or 0
    body_pages = item.get('page_limit_body', 0) or 0
    no_limit = item.get('no_page_limit', False)
    
    category = item.get('category', '') or ''
    is_laser = "laser" in category.lower()
    comp_label = "Hộp mực" if is_laser else "Đầu phun"
    
    if is_laser:
        comp_months = item.get('cartridge_warranty_months', 0) or 0
    else:
        comp_months = item.get('head_warranty_months', 0) or 0
    
    comp_pages = item.get('page_limit_head', 0) or 0

    def format_line(label, months, pages):
        text = f"{label}: {months} tháng"
        if not no_limit and pages and pages > 0:
            pages_formatted = "{:,}".format(pages).replace(",", ".")
            text += f" hoặc {pages_formatted} trang"
        return text

    line1 = format_line("Cơ máy", body_months, body_pages)
    line2 = format_line(comp_label, comp_months, comp_pages)

    return f"{line1}\n{line2}"


# =========================================================
# ROUTE TRA CỨU & XUẤT FILE BẢO HÀNH
# =========================================================

@router.get("/list", response_class=HTMLResponse)
@router.get("/lookup", response_class=HTMLResponse)
async def lookup_warranty_page(
    request: Request,
    q: Optional[str] = Query(None, description="Số Serial hoặc số điện thoại")
):
    """Render giao diện tra cứu bảo hành chuẩn Streamlit"""
    results = []
    search_query = (q or "").strip()

    if search_query:
        try:
            # Tra cứu đồng thời trên serial_number hoặc phone_number từ Supabase
            res = supabase.table("warranty_records")\
                .select("*")\
                .or_(f"serial_number.ilike.%{search_query}%,phone_number.ilike.%{search_query}%")\
                .order("created_at", desc=True)\
                .execute()
            
            raw_data = res.data or []

            for item in raw_data:
                start_dt = safe_parse_datetime(item.get('purchase_date')) or datetime.today()

                category_raw = item.get("category") or "Máy in phun"
                cat_lower = category_raw.lower()

                is_inkjet = "phun" in cat_lower
                is_dot_matrix = "kim" in cat_lower
                is_laser = "laser" in cat_lower
                is_scan = "scan" in cat_lower
                is_electric = any(k in cat_lower for k in ["điện", "khác", "thiết bị"])

                condition = item.get("condition") or "Mới"
                no_page_limit = item.get("no_page_limit", False)

                if is_inkjet:
                    sub_label = "Đầu phun"
                    sub_icon = "💧"
                elif is_dot_matrix:
                    sub_label = "Đầu kim"
                    sub_icon = "🖨️"
                elif is_laser:
                    sub_label = "Hộp mực"
                    sub_icon = "📦"
                else:
                    sub_label = "Linh kiện đi kèm"
                    sub_icon = "⚙️"

                body_limit = item.get("page_limit_body", 0) or 0
                head_limit = item.get("page_limit_head", 0) or 0
                current_cnt = item.get("current_counter", 0) or 0
                initial_cnt = item.get("initial_counter", 0) or 0
                actual_printed = max(0, current_cnt - initial_cnt)

                body_months = item.get("warranty_months", 0) or 0
                
                if is_inkjet or is_dot_matrix:
                    head_months = item.get("head_warranty_months", 0) or 0
                elif is_laser:
                    head_months = item.get("cartridge_warranty_months", 0) or 0
                else:
                    head_months = (item.get("head_warranty_months", 0) or 0) or (item.get("cartridge_warranty_months", 0) or 0)

                full_status = get_warranty_status(
                    start_date=start_dt,
                    body_months=body_months,
                    head_months=head_months,
                    body_limit=0 if no_page_limit else body_limit,
                    head_limit=0 if no_page_limit else head_limit,
                    current_counter=actual_printed
                )

                has_page_limit = (body_limit > 0 or head_limit > 0) and not no_page_limit

                body_text = (
                    f"{body_months} tháng" + (f" hoặc {body_limit:,} bản in" if body_limit > 0 and not no_page_limit else "")
                    if body_months > 0 else "Không bảo hành"
                )
                head_text = (
                    f"{head_months} tháng" + (f" hoặc {head_limit:,} bản in" if head_limit > 0 and not no_page_limit else "")
                    if head_months > 0 else "Không bảo hành"
                )

                # Format nhật ký chỉnh sửa
                log_lines = []
                raw_log = item.get("edit_log")
                if raw_log and str(raw_log).lower() != "none":
                    log_lines = [line.strip() for line in str(raw_log).split('\n') if line.strip()]
                    log_lines.reverse()

                last_update = item.get("pages_updated_at")
                created_date = item.get("created_at")
                
                dt_last_update = safe_parse_datetime(last_update)
                dt_created = safe_parse_datetime(created_date)
                has_real_update = bool(dt_last_update and dt_created and dt_last_update != dt_created)

                item["computed"] = {
                    "purchase_date_str": start_dt.strftime('%d/%m/%Y'),
                    "masked_phone": mask_phone(item.get("phone_number")),
                    "is_inkjet": is_inkjet,
                    "is_dot_matrix": is_dot_matrix,
                    "is_laser": is_laser,
                    "is_scan": is_scan,
                    "is_electric": is_electric,
                    "sub_label": sub_label,
                    "sub_icon": sub_icon,
                    "body_text": body_text,
                    "head_text": head_text,
                    "head_months": head_months,
                    "status_obj": full_status,
                    "actual_printed": actual_printed,
                    "has_page_limit": has_page_limit,
                    "has_real_update": has_real_update,
                    "log_lines": log_lines,
                    "last_update_str": format_dt_str(last_update),
                    "created_str": format_dt_str(created_date)
                }
                results.append(item)

        except Exception as e:
            print(f"Lỗi truy vấn phiếu bảo hành từ Supabase: {e}")

    current_user = getattr(request.state, "user", None)

    return templates.TemplateResponse(
        request=request,
        name="warranty/warranty_lookup.html",
        context={
            "request": request,
            "search_query": search_query,
            "results": results,
            "current_user": current_user
        }
    )


@router.get("/pdf/{record_id}")
async def download_pdf_file(record_id: str):
    """API xuất và tải file PDF bảo hành"""
    if not create_warranty_pdf:
        raise HTTPException(status_code=500, detail="Thư viện tạo PDF bảo hành chưa được cấu hình")
    
    res = supabase.table("warranty_records").select("*").eq("id", record_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Không tìm thấy bản ghi bảo hành")
    
    item = res.data[0]
    p_date = format_dt_str(item.get("purchase_date"))

    formatted_warranty = format_warranty_text_v3(item)
    pdf_content, _ = create_warranty_pdf({
        "customer_name": item.get("customer_name", ""),
        "phone_number": item.get("phone_number", ""),
        "purchase_date": p_date,
        "warranty": formatted_warranty,
        "model": item.get("model_name", ""),
        "serial": item.get("serial_number", "")
    })

    filename = f"BH_{item.get('serial_number', 'NoSerial')}.pdf"
    return StreamingResponse(
        io.BytesIO(pdf_content),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@router.get("/qr/{record_id}")
async def download_qr_file(record_id: str):
    """API xuất và tải file ảnh QR Code bảo hành"""
    if not create_warranty_pdf:
        raise HTTPException(status_code=500, detail="Thư viện tạo QR bảo hành chưa được cấu hình")
    
    res = supabase.table("warranty_records").select("*").eq("id", record_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Không tìm thấy bản ghi bảo hành")

    item = res.data[0]
    p_date = format_dt_str(item.get("purchase_date"))

    formatted_warranty = format_warranty_text_v3(item)
    _, qr_content = create_warranty_pdf({
        "customer_name": item.get("customer_name", ""),
        "phone_number": item.get("phone_number", ""),
        "purchase_date": p_date,
        "warranty": formatted_warranty,
        "model": item.get("model_name", ""),
        "serial": item.get("serial_number", "")
    })

    filename = f"QR_{item.get('serial_number', 'NoSerial')}.png"
    return StreamingResponse(
        io.BytesIO(qr_content),
        media_type="image/png",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )