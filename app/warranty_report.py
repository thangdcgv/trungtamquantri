import io
import logging
import re
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, StreamingResponse
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
    """Parse ngày từ string an toàn, tránh văng lỗi 500 khi nhập sai định dạng."""
    if not date_str:
        return default_date
    try:
        return datetime.strptime(str(date_str).strip()[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return default_date


def get_model_info(raw_name: str) -> Tuple[str, str]:
    """
    Trích xuất chuẩn [Thương hiệu / Loại thiết bị] + [Mã Model] dựa trên Thư viện từ khóa
    và Quy tắc nhận diện tự động từ Mã Model.
    """
    if not raw_name:
        return "Thiết bị khác", "0"

    raw_clean = str(raw_name).strip()
    upper_name = raw_clean.upper()

    BRAND_KEYWORDS = [
        "EPSON", "CANON", "HP", "PANTUM", "BROTHER", 
        "XEROX", "MÁY CẮT BẾ", "MAY CAT BE", "MÁY CẮT", 
        "VINFAST", "MOVE", "MIMAKI"
    ]

    found_brand = None
    for kw in BRAND_KEYWORDS:
        if kw in upper_name:
            if "CẮT" in kw or "CAT" in kw:
                found_brand = "Máy cắt bế"
            else:
                found_brand = kw.capitalize() if kw != "HP" else "HP"
            break

    clean_text = re.sub(
        r"(?i)\b(mực|muc|dye|uv|pigment|bảo hành|bao hanh|hộp|hop|đầu|dau|mới|moi|cũ|cu)\b", 
        "", 
        raw_clean
    )

    model_match = re.search(r"([A-Za-z]*\s*\d+[A-Za-z0-9\-]*)", clean_text)

    if model_match:
        raw_model_code = model_match.group(1).strip()
        model_code = re.sub(r"\s+", " ", raw_model_code).upper()

        if found_brand:
            model_code = re.sub(rf"(?i)^{re.escape(found_brand)}\s*", "", model_code).strip()

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

        num_match = re.search(r"\d+", model_code)
        series_id = num_match.group(0) if num_match else "0"
        display_name = f"{found_brand} {model_code}" if found_brand else model_code
            
        return display_name, series_id

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
    
    group_by_day = delta_days <= 45

    valid_records = []
    all_categories = set()

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

    sales_timeline: Dict[str, int] = {}
    model_counter: Dict[str, int] = {}
    model_condition_counts: Dict[Tuple[str, str], Dict] = {}
    customer_stats: Dict[Tuple[str, str], Dict] = {}

    active_count = 0
    expired_count = 0
    new_count = 0
    used_count = 0
    expired_list = []
    detailed_export_records = []

    for item in valid_records:
        if item["_machine_type"] == "✨ Máy mới":
            new_count += 1
        else:
            used_count += 1

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

        raw_model = item.get("model_name") or "Khác"
        disp_name, _ = get_model_info(raw_model)

        # Lưu bản ghi chuẩn bị xuất Excel
        detailed_export_records.append({
            "customer_name": str(item.get("customer_name") or "Khách lẻ"),
            "phone_number": str(item.get("phone_number") or ""),
            "purchase_date_str": p_date.strftime("%d/%m/%Y"),
            "category": str(item.get("category") or ""),
            "display_model": disp_name,
            "raw_model": raw_model,
            "serial_number": str(item.get("serial_number") or "N/A"),
            "condition": item["_machine_type"],
            "status_str": "Trong hạn" if is_warranty_active else "Hết hạn",
            "reason_str": reason_str,
            "policy_name": final_policy_name,
            "initial_counter": init_count,
            "current_counter": curr_count,
            "pages_printed": actual_printed,
            "exp_body_str": exp_body.strftime("%d/%m/%Y") if m_body > 0 else "K.Áp dụng",
            "exp_head_str": exp_head.strftime("%d/%m/%Y") if m_head > 0 and not is_laser else "K.Áp dụng",
            "exp_cart_str": exp_cart.strftime("%d/%m/%Y") if m_cart > 0 else "K.Áp dụng",
        })

        time_key = p_date.strftime("%d/%m") if group_by_day else p_date.strftime("%m/%Y")
        sales_timeline[time_key] = sales_timeline.get(time_key, 0) + 1

        model_counter[disp_name] = model_counter.get(disp_name, 0) + 1

        m_key = (disp_name, item["_machine_type"])
        if m_key not in model_condition_counts:
            model_condition_counts[m_key] = {
                "display_name": disp_name,
                "loai_may": item["_machine_type"],
                "count": 0,
            }
        model_condition_counts[m_key]["count"] += 1

        cust_name = str(item.get("customer_name") or "Khách lẻ").strip()
        phone = str(item.get("phone_number") or "").strip()
        cust_key = (cust_name, phone)
        if cust_key not in customer_stats:
            customer_stats[cust_key] = {"name": cust_name, "phone": phone or "N/A", "count": 0}
        customer_stats[cust_key]["count"] += 1

    if group_by_day:
        sorted_timeline = sorted(sales_timeline.items(), key=lambda x: datetime.strptime(x[0], "%d/%m"))
    else:
        sorted_timeline = sorted(sales_timeline.items(), key=lambda x: datetime.strptime(x[0], "%m/%Y"))

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
            "series_labels": [x[0] for x in sorted_top_models],
            "series_counts": [x[1] for x in sorted_top_models],
        },
        "expired_list": expired_list,
        "detailed_export_records": detailed_export_records,
        "model_summary_list": sorted(model_condition_counts.values(), key=lambda x: x["count"], reverse=True),
        "top_10_customers": sorted(customer_stats.values(), key=lambda x: x["count"], reverse=True)[:10],
    }


# =========================================================
# HELPER BUILD EXCEL BÁO CÁO BẢO HÀNH
# =========================================================

def _build_warranty_report_excel(
    detailed_records: List[Dict],
    start_date: date,
    end_date: date
) -> io.BytesIO:
    """Tạo Workbook Excel cho báo cáo bảo hành sản phẩm."""
    out = io.BytesIO()

    with pd.ExcelWriter(out, engine="xlsxwriter") as writer:
        wb = writer.book
        ws = wb.add_worksheet("BaoCaoBaoHanh")

        # Config Styles
        fmt_title = wb.add_format({
            "bold": True, "font_size": 15, "font_name": "Segoe UI",
            "align": "center", "valign": "vcenter", "bg_color": "#1F4E79",
            "font_color": "white", "border": 1
        })
        fmt_subtitle = wb.add_format({
            "italic": True, "font_size": 10, "font_name": "Segoe UI",
            "align": "center", "valign": "vcenter", "bg_color": "#D9EAD3", "border": 1
        })
        fmt_header = wb.add_format({
            "bold": True, "font_size": 10, "font_name": "Segoe UI",
            "align": "center", "valign": "vcenter", "bg_color": "#2F5597",
            "font_color": "white", "border": 1, "text_wrap": True
        })
        fmt_center = wb.add_format({"border": 1, "align": "center", "valign": "vcenter", "font_name": "Segoe UI"})
        fmt_left = wb.add_format({"border": 1, "align": "left", "valign": "vcenter", "font_name": "Segoe UI"})
        fmt_num = wb.add_format({"border": 1, "align": "right", "valign": "vcenter", "num_format": "#,##0", "font_name": "Segoe UI"})
        
        fmt_active = wb.add_format({"border": 1, "align": "center", "valign": "vcenter", "font_name": "Segoe UI", "bg_color": "#E2EFDA", "font_color": "#375623", "bold": True})
        fmt_expired = wb.add_format({"border": 1, "align": "center", "valign": "vcenter", "font_name": "Segoe UI", "bg_color": "#FCE4D6", "font_color": "#C65911", "bold": True})

        fmt_total_lbl = wb.add_format({"bold": True, "border": 1, "align": "right", "valign": "vcenter", "bg_color": "#D9EAD3", "font_name": "Segoe UI"})
        fmt_total_num = wb.add_format({"bold": True, "border": 1, "align": "right", "valign": "vcenter", "num_format": "#,##0", "bg_color": "#D9EAD3", "font_name": "Segoe UI"})

        headers = [
            "STT", "Tên Khách Hàng", "Số Điện Thoại", "Ngày Mua", "Danh Mục",
            "Model Gốc", "Số Serial", "Loại Máy", "Trạng Thái BH",
            "Chính Sách Áp Dụng", "Số Trang Đầu", "Số Trang Hiện Tại",
            "BH Cơ Máy", "BH Đầu Phun", "BH Hộp Mực"
        ]

        # Title Block
        ws.merge_range("A1:R2", "BÁO CÁO THỐNG KÊ CHI TIẾT TÌNH TRẠNG BẢO HÀNH", fmt_title)
        sub_text = f"Giai đoạn từ ngày: {start_date.strftime('%d/%m/%Y')} đến ngày: {end_date.strftime('%d/%m/%Y')}"
        ws.merge_range("A3:R3", sub_text, fmt_subtitle)

        # Header Row
        for col_idx, h_text in enumerate(headers):
            ws.write(3, col_idx, h_text, fmt_header)

        # Write Rows
        start_row = 4
        for idx, row in enumerate(detailed_records):
            curr_r = start_row + idx
            ws.write(curr_r, 0, idx + 1, fmt_center)
            ws.write(curr_r, 1, row["customer_name"], fmt_left)
            ws.write(curr_r, 2, row["phone_number"], fmt_center)
            ws.write(curr_r, 3, row["purchase_date_str"], fmt_center)
            ws.write(curr_r, 4, row["category"], fmt_left)
            ws.write(curr_r, 5, row["raw_model"], fmt_left)
            ws.write(curr_r, 6, row["serial_number"], fmt_center)
            ws.write(curr_r, 7, row["condition"], fmt_center)
            
            # Highlight Trạng Thái
            if row["status_str"] == "Trong hạn":
                ws.write(curr_r, 8, row["status_str"], fmt_active)
            else:
                ws.write(curr_r, 8, row["status_str"], fmt_expired)

            ws.write(curr_r, 9, row["policy_name"], fmt_left)
            
            ws.write(curr_r, 10, row["initial_counter"], fmt_num)
            ws.write(curr_r, 11, row["current_counter"], fmt_num)
            
            ws.write(curr_r, 12, row["exp_body_str"], fmt_center)
            ws.write(curr_r, 13, row["exp_head_str"], fmt_center)
            ws.write(curr_r, 14, row["exp_cart_str"], fmt_center)

        # Dòng Tổng
        tot_r = start_row + len(detailed_records)
        excel_start = start_row + 1
        excel_end = tot_r

        ws.merge_range(tot_r, 0, tot_r, 11, "TỔNG SỐ TRANG ĐÃ IN:", fmt_total_lbl)
        ws.write_formula(tot_r, 12, f"=SUM(M{excel_start}:M{excel_end})", fmt_total_num)
        ws.write_formula(tot_r, 13, f"=SUM(N{excel_start}:N{excel_end})", fmt_total_num)
        ws.write_formula(tot_r, 14, f"=SUM(O{excel_start}:O{excel_end})", fmt_total_num)
        ws.merge_range(tot_r, 15, tot_r, 17, "", fmt_total_lbl)

        # Set Column Widths
        col_widths = [6, 22, 14, 12, 16, 20, 18, 16, 14, 22, 14, 14, 14, 14, 14]
        for c_i, w in enumerate(col_widths):
            ws.set_column(c_i, c_i, w)

    out.seek(0)
    return out


# =========================================================
# ROUTES BÁO CÁO
# =========================================================

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


# 2. BÁO CÁO PHIẾU BẢO HÀNH CHI TIẾT (GIAO DIỆN WEB)
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

    if parsed_start > parsed_end:
        parsed_start, parsed_end = parsed_end, parsed_start

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


# 3. XUẤT BÁO CÁO BẢO HÀNH RA EXCEL (.XLSX)
@router.get("/export-excel")
async def export_warranty_report_excel(
    category: Optional[str] = Query(None),
    condition: Optional[str] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    user: dict = Depends(require_admin),
):
    """API xuất kết quả Báo cáo Thống kê Bảo hành ra file Excel."""
    today = date.today()
    parsed_start = parse_date_safe(start_date, today - timedelta(days=60))
    parsed_end = parse_date_safe(end_date, today)

    if parsed_start > parsed_end:
        parsed_start, parsed_end = parsed_end, parsed_start

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
        logger.error(f"Supabase Query Error Export Excel: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Đã xảy ra lỗi kết nối cơ sở dữ liệu khi xuất Excel."
        )

    data = process_warranty_data_by_range(
        records=res_rec.data or [],
        policies=res_pol.data or [],
        category_filter=category,
        condition_filter=condition,
        start_date=parsed_start,
        end_date=parsed_end,
    )

    detailed_records = data.get("detailed_export_records", [])

    if not detailed_records:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Không có dữ liệu báo cáo phù hợp với bộ lọc để xuất Excel."
        )

    excel_stream = _build_warranty_report_excel(detailed_records, parsed_start, parsed_end)

    filename = f"Bao_Cao_Bao_Hanh_{parsed_start.strftime('%Y%m%d')}_{parsed_end.strftime('%Y%m%d')}.xlsx"
    encoded_filename = quote(filename)

    return StreamingResponse(
        excel_stream,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename=\"{filename}\"; filename*=UTF-8''{encoded_filename}",
            "Access-Control-Expose-Headers": "Content-Disposition",
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0"
        }
    )