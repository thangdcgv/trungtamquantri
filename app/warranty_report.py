import logging
import re
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.auth import require_login
from config import supabase

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/bao-cao", tags=["Báo cáo & Thống kê"])
templates = Jinja2Templates(directory="app/templates")

ALLOWED_ADMIN_ROLES = {"Super Admin", "System Admin", "Admin"}


def require_admin(user: dict = Depends(require_login)) -> dict:
    """Dependency kiểm tra quyền Admin tập trung & An toàn."""
    if not isinstance(user, dict) or user.get("role") not in ALLOWED_ADMIN_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Bạn không có quyền truy cập trung tâm báo cáo!"
        )
    return user


def parse_date_safe(date_str: Optional[str], default_date: date) -> date:
    """Parse ngày từ string an toàn, tránh văng lỗi 500 khi attacker nhập chuỗi bẩn."""
    if not date_str:
        return default_date
    try:
        return datetime.strptime(str(date_str).strip()[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return default_date


import re
from typing import Tuple

def get_model_info(raw_name: str) -> Tuple[str, str]:
    """
    Trích xuất chuẩn [Thương hiệu / Loại thiết bị] + [Mã Model] dựa trên Thư viện từ khóa
    và Quy tắc nhận diện tự động từ Mã Model.
    """
    if not raw_name:
        return "Thiết bị khác", "0"

    raw_clean = str(raw_name).strip()
    upper_name = raw_clean.upper()

    # 1. Thư viện từ khóa Hãng & Loại thiết bị tại cửa hàng
    BRAND_KEYWORDS = [
        "EPSON", "CANON", "HP", "PANTUM", "BROTHER", 
        "XEROX", "MÁY CẮT BẾ", "MAY CAT BE", "MÁY CẮT", 
        "VINFAST", "MOVE", "MIMAKI"
    ]

    # Nhận diện Hãng / Loại máy từ từ điển
    found_brand = None
    for kw in BRAND_KEYWORDS:
        if kw in upper_name:
            if "CẮT" in kw or "CAT" in kw:
                found_brand = "Máy cắt bế"
            else:
                found_brand = kw.capitalize() if kw != "HP" else "HP"
            break

    # 2. Loại bỏ từ nhiễu (mực, tình trạng, phụ kiện...)
    clean_text = re.sub(
        r"(?i)\b(mực|muc|dye|uv|pigment|bảo hành|bao hanh|hộp|hop|đầu|dau|mới|moi|cũ|cu)\b", 
        "", 
        raw_clean
    )

    # 3. Trích xuất Mã Model (Chữ + Số ghép liền hoặc cách nhau, ví dụ: L8050, L18050, LBP 8730I, AC 450)
    model_match = re.search(r"([A-Za-z]*\s*\d+[A-Za-z0-9\-]*)", clean_text)

    if model_match:
        raw_model_code = model_match.group(1).strip()
        model_code = re.sub(r"\s+", " ", raw_model_code).upper()

        # Loại bỏ tên Brand khỏi model_code nếu bị dính (ví dụ: "EPSON L18050" -> "L18050")
        if found_brand:
            model_code = re.sub(rf"(?i)^{re.escape(found_brand)}\s*", "", model_code).strip()

        # 4. TỰ ĐỘNG ĐOÁN BRAND NẾU DỮ LIỆU NHẬP THIẾU TÊN HÃNG (Ví dụ chỉ nhập "L8050" hoặc "IX6770")
        if not found_brand:
            if re.match(r"^(L\d|EP|M\d|XP|WF|SC|LBP\s*\d)", model_code):
                if re.match(r"^LBP", model_code):
                    found_brand = "Canon"
                else:
                    found_brand = "Epson"
            elif re.match(r"^(IX|G\d|TS|IP|MG)", model_code):
                found_brand = "Canon"
            elif re.match(r"^(M\d{4}|P\d{4})", model_code):
                found_brand = "Pantum"
            elif re.match(r"^(AC|CG|FC)", model_code):
                found_brand = "Máy cắt bế"

        # Lấy riêng dãy số duy nhất để làm series_id (phục vụ mục đích gom nhóm phụ)
        num_match = re.search(r"\d+", model_code)
        series_id = num_match.group(0) if num_match else "0"

        # Kết hợp thành Display Name chuẩn hóa hoàn chỉnh
        display_name = f"{found_brand} {model_code}" if found_brand else model_code
            
        return display_name, series_id

    # Fallback nếu không bắt được regex
    fallback_name = found_brand if found_brand else raw_clean
    return fallback_name, "0"


def add_months_to_date(source_date: date, months: int) -> date:
    """Cộng số tháng an toàn vào date (xử lý tràn tháng và tràn ngày cuối tháng)."""
    if months <= 0:
        return source_date
    new_month = source_date.month + months
    year_offset = (new_month - 1) // 12
    final_month = (new_month - 1) % 12 + 1
    final_year = source_date.year + year_offset
    final_day = min(source_date.day, 28)
    return date(final_year, final_month, final_day)


def process_warranty_data_by_range(
    records: List[Dict],
    policies: List[Dict],
    category_filter: Optional[str],
    condition_filter: Optional[str],
    start_date: date,
    end_date: date,
) -> Dict:
    """Xử lý tính toán báo cáo theo khoảng thời gian linh hoạt."""
    today_date = date.today()
    delta_days = (end_date - start_date).days
    
    # Gom nhóm theo Ngày nếu <= 45 ngày, ngược lại gom theo Tháng
    group_by_day = delta_days <= 45

    valid_records = []
    all_categories = set()

    # Pre-processing và thu thập danh mục
    for item in records:
        if item.get("category"):
            all_categories.add(str(item["category"]).strip())

        p_date_str = item.get("purchase_date")
        if not p_date_str:
            continue
        try:
            p_date = datetime.strptime(str(p_date_str)[:10], "%Y-%m-%d").date()
        except ValueError:
            continue

        # Lọc theo khoảng ngày
        if not (start_date <= p_date <= end_date):
            continue

        if category_filter and str(item.get("category")).strip() != category_filter:
            continue

        cond = item.get("condition") or "Mới"
        machine_type = "✨ Máy mới" if cond == "Mới" else "🇯🇵 Máy nội địa/Cũ"
        if condition_filter and machine_type != condition_filter:
            continue

        item["_p_date"] = p_date
        item["_machine_type"] = machine_type
        valid_records.append(item)

    sorted_categories = sorted(list(all_categories))

    # Tính toán Thống kê
    sales_timeline: Dict[str, int] = {}
    model_counter: Dict[str, int] = {}  # Gom nhóm trực tiếp theo Tên máy in chuẩn hóa (display_name)
    model_condition_counts: Dict[Tuple[str, str], Dict] = {}
    customer_stats: Dict[Tuple[str, str], Dict] = {}

    active_count = 0
    expired_count = 0
    new_count = 0
    used_count = 0
    expired_list = []

    for item in valid_records:
        if item["_machine_type"] == "✨ Máy mới":
            new_count += 1
        else:
            used_count += 1

        # Khớp Chính sách bảo hành (Policy Matching)
        policy = None
        applied_pname = item.get("applied_policy_name")
        if applied_pname and policies:
            policy = next((p for p in policies if p.get("policy_name") == applied_pname), None)

        if policy is None and policies:
            row_cat = str(item.get("category") or "").strip()
            row_cond = str(item.get("condition") or "").strip()
            matches = [
                p for p in policies
                if str(p.get("category")).strip() == row_cat and str(p.get("condition")).strip() == row_cond
            ]
            if matches:
                brand = "EPSON" if "EPSON" in str(item.get("model_name")).upper() else "CANON"
                brand_matches = [p for p in matches if brand in str(p.get("policy_name")).upper()]
                policy = brand_matches[0] if brand_matches else matches[0]

        # Đọc thông số điều kiện
        m_body = policy.get("warranty_months") if policy else (item.get("warranty_months") or 0)
        m_head = policy.get("head_warranty_months") if policy else (item.get("head_warranty_months") or 0)
        m_cart = policy.get("cartridge_warranty_months") if policy else (item.get("cartridge_warranty_months") or 0)

        l_body = policy.get("page_limit_body") if policy else (item.get("page_limit_body") or 0)
        l_head = policy.get("page_limit_head") if policy else (item.get("page_limit_head") or 0)
        no_limit = policy.get("no_page_limit") if policy else (item.get("no_page_limit") or False)

        curr_count = item.get("current_counter") or 0
        init_count = item.get("initial_counter") or 0
        actual_printed = max(0, curr_count - init_count)

        p_date = item["_p_date"]
        exp_body = add_months_to_date(p_date, m_body)
        exp_head = add_months_to_date(p_date, m_head)
        exp_cart = add_months_to_date(p_date, m_cart)

        reasons = []
        is_laser = "Laser" in str(item.get("category") or "")

        if m_body > 0 and exp_body < today_date:
            reasons.append("Hết TG Cơ")
        if not no_limit and l_body > 0 and actual_printed >= l_body:
            reasons.append(f"Quá trang Cơ ({actual_printed:,}/{l_body:,})")

        if not is_laser and m_head > 0:
            if exp_head < today_date:
                reasons.append("Hết TG Đầu phun")
            if not no_limit and l_head > 0 and actual_printed >= l_head:
                reasons.append(f"Quá trang Đầu phun ({actual_printed:,}/{l_head:,})")

        if m_cart > 0 and exp_cart < today_date:
            reasons.append("Hết TG Hộp mực")

        is_warranty_active = (len(reasons) == 0)
        reason_str = "✅ Trong hạn" if is_warranty_active else " & ".join(reasons)
        final_policy_name = policy.get("policy_name") if policy else (item.get("applied_policy_name") or "Mặc định")

        if is_warranty_active:
            active_count += 1
        else:
            expired_count += 1
            expired_list.append({
                "serial_number": str(item.get("serial_number") or "N/A"),
                "model_name": str(item.get("model_name") or "N/A"),
                "customer_name": str(item.get("customer_name") or "Khách lẻ"),
                "applied_policy_name": final_policy_name,
                "reason": reason_str,
            })

        # Timeline Doanh số
        time_key = p_date.strftime("%d/%m") if group_by_day else p_date.strftime("%m/%Y")
        sales_timeline[time_key] = sales_timeline.get(time_key, 0) + 1

        # ------------------------------------------------------------------
        # TRÍCH XUẤT VÀ GOM NHÓM TÊN MÁY IN CHUẨN HOÁ
        # ------------------------------------------------------------------
        raw_model = item.get("model_name") or "Khác"
        disp_name, _ = get_model_info(raw_model)

        # 1. Đếm tổng số lượng máy theo Tên thương hiệu + Model
        model_counter[disp_name] = model_counter.get(disp_name, 0) + 1

        # 2. Bảng tổng hợp Model & Phân loại
        m_key = (disp_name, item["_machine_type"])
        if m_key not in model_condition_counts:
            model_condition_counts[m_key] = {
                "display_name": disp_name,
                "loai_may": item["_machine_type"],
                "count": 0,
            }
        model_condition_counts[m_key]["count"] += 1

        # Khách hàng
        cust_name = str(item.get("customer_name") or "Khách lẻ").strip()
        phone = str(item.get("phone_number") or "").strip()
        cust_key = (cust_name, phone)
        if cust_key not in customer_stats:
            customer_stats[cust_key] = {"name": cust_name, "phone": phone or "N/A", "count": 0}
        customer_stats[cust_key]["count"] += 1

    # Format Timeline Chart
    if group_by_day:
        sorted_timeline = sorted(sales_timeline.items(), key=lambda x: datetime.strptime(x[0], "%d/%m"))
    else:
        sorted_timeline = sorted(sales_timeline.items(), key=lambda x: datetime.strptime(x[0], "%m/%Y"))

    # Sắp xếp lấy Top 15 Máy in / Thiết bị phổ biến nhất
    sorted_top_models = sorted(model_counter.items(), key=lambda x: x[1], reverse=True)[:15]

    return {
        "filters": {
            "categories": sorted_categories,
        },
        "metrics": {
            "total_machines": len(valid_records),
            "total_models": len(model_counter),
            "active_count": active_count,
            "expired_count": expired_count,
            "new_count": new_count,
            "used_count": used_count,
        },
        "charts": {
            "sales_months": [x[0] for x in sorted_timeline],
            "sales_counts": [x[1] for x in sorted_timeline],
            "series_labels": [x[0] for x in sorted_top_models],  # Danh sách Tên máy in chuẩn hóa
            "series_counts": [x[1] for x in sorted_top_models],  # Số lượng tương ứng
        },
        "expired_list": expired_list,
        "model_summary_list": sorted(model_condition_counts.values(), key=lambda x: x["count"], reverse=True),
        "top_10_customers": sorted(customer_stats.values(), key=lambda x: x["count"], reverse=True)[:10],
    }


# 1. TRUNG TÂM BÁO CÁO (Hub)
@router.get("", response_class=HTMLResponse)
async def admin_reports_hub(
    request: Request, user: dict = Depends(require_admin)
):
    return templates.TemplateResponse(
        request=request,
        name="admin_reports_hub.html",
        context={"current_user": user},
    )


# 2. BÁO CÁO PHIẾU BẢO HÀNH CHI TIẾT
@router.get("/bao-hanh", response_class=HTMLResponse)
async def report_warranty_page(
    request: Request,
    category: Optional[str] = Query(None),
    condition: Optional[str] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    user: dict = Depends(require_admin),
):
    today = date.today()
    parsed_start = parse_date_safe(start_date, today - timedelta(days=60))
    parsed_end = parse_date_safe(end_date, today)

    # Đảm bảo start_date luôn <= end_date
    if parsed_start > parsed_end:
        parsed_start, parsed_end = parsed_end, parsed_start

    # Truy vấn DB an toàn có try-except
    try:
        res_rec = (
            supabase.table("warranty_records")
            .select("*")
            .gte("purchase_date", parsed_start.strftime("%Y-%m-%d"))
            .lte("purchase_date", parsed_end.strftime("%Y-%m-%d"))
            .order("created_at", desc=True)
            .execute()
        )
        res_pol = supabase.table("warranty_policy").select("*").execute()
    except Exception as e:
        logger.error(f"Supabase Query Error: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Đã xảy ra lỗi khi kết nối với cơ sở dữ liệu."
        )

    data = process_warranty_data_by_range(
        records=res_rec.data or [],
        policies=res_pol.data or [],
        category_filter=category,
        condition_filter=condition,
        start_date=parsed_start,
        end_date=parsed_end,
    )

    return templates.TemplateResponse(
        request=request,
        name="warranty_report.html",
        context={
            "request": request,
            "current_user": user,
            "filters": data["filters"],
            "metrics": data["metrics"],
            "charts": data["charts"],
            "expired_list": data["expired_list"],
            "model_summary_list": data["model_summary_list"],
            "top_10_customers": data["top_10_customers"],
            "selected_category": category or "",
            "selected_condition": condition or "",
            "start_date": parsed_start.strftime("%Y-%m-%d"),
            "end_date": parsed_end.strftime("%Y-%m-%d"),
        },
    )