import calendar
import io
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any
from urllib.parse import quote

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from app.auth import require_login
from config import supabase

logger = logging.getLogger(__name__)

# Cấu hình Templates & Router
templates = Jinja2Templates(directory="templates")
router = APIRouter(prefix="/admin/report", tags=["Báo cáo & Thống kê"])

ALLOWED_ADMIN_ROLES = {"Admin", "Super Admin", "System Admin", "Manager"}
NUMERIC_COLS = [
    "quang_duong", "combo", "device_cost", "distance_cost",
    "tho_phu_cost", "di_tinh_cost", "tien_ngoai_gio",
    "phu_phi_phat_sinh", "thanh_tien"
]


# =========================================================
# HELPER: THỜI GIAN & LỌC D DỮ LIỆU
# =========================================================

def _parse_month_range(month_str: str) -> tuple[str, str]:
    """Parse chuỗi tháng (MM/YYYY hoặc YYYY) thành mốc thời gian ISO start/end."""
    try:
        clean_str = str(month_str).strip()
        if "/" in clean_str:
            sel_dt = datetime.strptime(clean_str, "%m/%Y")
            start_d = sel_dt.date().replace(day=1)
            last_day = calendar.monthrange(sel_dt.year, sel_dt.month)[1]
            end_d = sel_dt.date().replace(day=last_day)
        else:
            year = int(clean_str)
            start_d = datetime(year, 1, 1).date()
            end_d = datetime(year, 12, 31).date()
    except Exception:
        now = datetime.now()
        start_d = now.date().replace(day=1)
        last_day = calendar.monthrange(now.year, now.month)[1]
        end_d = now.date().replace(day=last_day)

    start_ts = f"{start_d.strftime('%Y-%m-%d')}T00:00:00"
    end_ts = f"{end_d.strftime('%Y-%m-%d')}T23:59:59"
    return start_ts, end_ts


def get_filtered_report_data(
    month: str,
    user_role: str,
    username: str,
    selected_nv: str = "Tất cả",
    selected_tt: str = "Tất cả",
    search: str = ""
) -> pd.DataFrame:
    """Query dữ liệu từ Supabase theo chuẩn Schema và chuyển đổi sang Pandas DataFrame."""
    start_ts, end_ts = _parse_month_range(month)

    # 1. Query Database
    query = (
        supabase.table("cham_cong")
        .select("*")
        .gte("thoi_gian", start_ts)
        .lte("thoi_gian", end_ts)
    )

    # Phân quyền & Lọc theo Nhân viên
    if user_role not in ALLOWED_ADMIN_ROLES:
        query = query.eq("username", username)
    elif selected_nv and selected_nv != "Tất cả":
        query = query.or_(f"username.eq.{selected_nv},ten.eq.{selected_nv}")

    # Lọc Trạng thái
    if selected_tt and selected_tt != "Tất cả":
        query = query.eq("trang_thai", selected_tt)

    res = query.order("id", desc=True).execute()
    data = res.data or []

    if not data:
        return pd.DataFrame()

    df = pd.DataFrame(data)

    # 2. Chuẩn hóa Cột dữ liệu & Chuỗi hiển thị
    df["Tên"] = df["ten"].fillna(df["username"]).fillna("N/A")

    if "thoi_gian" in df.columns:
        df["Thời Gian"] = pd.to_datetime(df["thoi_gian"], utc=True).dt.tz_convert("Asia/Ho_Chi_Minh")
        df["Thời Gian Str"] = df["Thời Gian"].dt.strftime("%d/%m/%Y %H:%M")
    else:
        df["Thời Gian Str"] = ""

    # Ép kiểu dữ liệu số hàng loạt (Vectorized)
    for col in NUMERIC_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
        else:
            df[col] = 0.0

    # Chuẩn hóa văn bản
    string_cols = {
        "so_hoa_don": "",
        "noi_dung": "",
        "trang_thai": "Chờ duyệt",
        "ghi_chu_duyet": "",
        "hinh_anh": ""
    }
    for col, default_val in string_cols.items():
        if col in df.columns:
            df[col] = df[col].fillna(default_val)
        else:
            df[col] = default_val

    # 3. Lọc Từ khóa Tìm kiếm (Search)
    if search and search.strip():
        search_lower = search.lower().strip()
        mask = (
            df["so_hoa_don"].astype(str).str.lower().str.contains(search_lower) |
            df["noi_dung"].astype(str).str.lower().str.contains(search_lower) |
            df["Tên"].astype(str).str.lower().str.contains(search_lower)
        )
        df = df[mask]

    return df


# =========================================================
# HELPER: TẠO FILE EXCEL CHI TIẾT
# =========================================================

def _build_excel_report(df_display: pd.DataFrame, month: str) -> io.BytesIO:
    """Khởi tạo Workbook Excel định dạng chuẩn bao gồm bảng chi tiết và tổng hợp."""
    df_export = df_display.sort_values("Thời Gian").copy() if "Thời Gian" in df_display.columns else df_display.copy()
    df_export["STT"] = range(1, len(df_export) + 1)
    df_export["Ngày"] = df_export["Thời Gian"].dt.strftime("%d/%m/%Y") if "Thời Gian" in df_export.columns else ""
    df_export["Quãng đường Str"] = df_export["quang_duong"].apply(lambda x: f"{float(x):g} Km" if x > 0 else "0 Km")

    out = io.BytesIO()
    with pd.ExcelWriter(out, engine="xlsxwriter") as writer:
        wb = writer.book
        ws = wb.add_worksheet("BaoCaoChiTiet")

        # Định dạng Styles
        fmt_title = wb.add_format({"bold": True, "font_size": 14, "font_name": "Segoe UI", "align": "center", "valign": "vcenter", "bg_color": "#1F4E79", "font_color": "white", "border": 1})
        fmt_header = wb.add_format({"bold": True, "font_size": 10, "font_name": "Segoe UI", "align": "center", "valign": "vcenter", "bg_color": "#2F5597", "font_color": "white", "border": 1, "text_wrap": True})
        fmt_center = wb.add_format({"border": 1, "align": "center", "valign": "vcenter", "font_name": "Segoe UI"})
        fmt_left = wb.add_format({"border": 1, "align": "left", "valign": "vcenter", "font_name": "Segoe UI"})
        fmt_money = wb.add_format({"border": 1, "align": "right", "valign": "vcenter", "num_format": "#,##0", "font_name": "Segoe UI"})
        
        fmt_total_lbl = wb.add_format({"bold": True, "border": 1, "align": "right", "valign": "vcenter", "bg_color": "#D9EAD3", "font_name": "Segoe UI"})
        fmt_total_money = wb.add_format({"bold": True, "border": 1, "align": "right", "valign": "vcenter", "num_format": "#,##0", "bg_color": "#D9EAD3", "font_name": "Segoe UI"})

        fmt_policy_title = wb.add_format({"bold": True, "font_size": 10, "font_name": "Segoe UI", "bg_color": "#FFF2CC", "border": 1, "align": "left", "valign": "vcenter"})
        fmt_policy_box = wb.add_format({"font_size": 9, "font_name": "Segoe UI", "bg_color": "#FFF2CC", "border": 1, "valign": "top", "text_wrap": True})

        fmt_sum_header = wb.add_format({"bold": True, "font_size": 10, "font_name": "Segoe UI", "align": "center", "valign": "vcenter", "bg_color": "#E2EFDA", "border": 1, "text_wrap": True})
        fmt_sum_total = wb.add_format({"bold": True, "font_size": 10, "font_name": "Segoe UI", "align": "left", "valign": "vcenter", "bg_color": "#D9EAD3", "border": 1})
        fmt_sum_money_total = wb.add_format({"bold": True, "font_size": 10, "font_name": "Segoe UI", "align": "right", "valign": "vcenter", "num_format": "#,##0", "bg_color": "#D9EAD3", "border": 1})

        # 1. Render Bảng Chi Tiết (Cột A -> P)
        headers = [
            "STT", "Ngày", "Số HĐ", "Nhân viên", "Nội dung / Địa chỉ", "Số máy", "Quãng đường",
            "Tiền máy", "Tiền KM", "Thợ phụ", "Đi tỉnh", "Ngoài giờ", "Phụ phí", "Thành tiền", "Ghi chú", "Trạng thái"
        ]

        ws.merge_range("A1:P2", f"BẢNG TỔNG HỢP CHI TIẾT CÔNG LẮP ĐẶT & BẢO HÀNH - THÁNG {month}", fmt_title)

        for c_idx, h_text in enumerate(headers):
            ws.write(2, c_idx, h_text, fmt_header)

        start_row = 3
        for r_idx, row in df_export.reset_index(drop=True).iterrows():
            curr = start_row + r_idx
            ws.write(curr, 0, row["STT"], fmt_center)
            ws.write(curr, 1, row["Ngày"], fmt_center)
            ws.write(curr, 2, row["so_hoa_don"], fmt_center)
            ws.write(curr, 3, row["Tên"], fmt_left)
            ws.write(curr, 4, row["noi_dung"], fmt_left)
            ws.write(curr, 5, row["combo"], fmt_center)
            ws.write(curr, 6, row["Quãng đường Str"], fmt_center)
            
            # Tiền tệ
            ws.write(curr, 7, row["device_cost"], fmt_money)
            ws.write(curr, 8, row["distance_cost"], fmt_money)
            ws.write(curr, 9, row["tho_phu_cost"], fmt_money)
            ws.write(curr, 10, row["di_tinh_cost"], fmt_money)
            ws.write(curr, 11, row["tien_ngoai_gio"], fmt_money)
            ws.write(curr, 12, row["phu_phi_phat_sinh"], fmt_money)
            ws.write(curr, 13, row["thanh_tien"], fmt_money)
            
            ws.write(curr, 14, row["ghi_chu_duyet"], fmt_left)
            ws.write(curr, 15, row["trang_thai"], fmt_center)

        # Dòng Tổng Cộng Bảng Chính
        total_row = start_row + len(df_export)
        excel_start_line = start_row + 1
        excel_end_line = total_row

        ws.merge_range(total_row, 0, total_row, 6, "TỔNG CỘNG:", fmt_total_lbl)
        for col_idx in range(7, 14):
            col_letter = chr(65 + col_idx)
            ws.write_formula(total_row, col_idx, f"=SUM({col_letter}{excel_start_line}:{col_letter}{excel_end_line})", fmt_total_money)
            
        ws.write(total_row, 14, "", fmt_total_lbl)
        ws.write(total_row, 15, "", fmt_total_lbl)

        # 2. Khung Chính Sách (Cột R -> X)
        policy_text = (
            "1. ĐƠN GIÁ CÔNG LẮP ĐẶT THEO KHOẢNG CÁCH (NỘI THÀNH):\n"
            "   • Dưới 20km  : 30.000 đ/máy\n"
            "   • 21km - 30km: 50.000 đ/máy\n"
            "   • 31km - 40km: 70.000 đ/máy\n"
            "   • 41km - 50km: 80.000 đ/máy\n"
            "   • Trên 51km  : 80.000 đ + 5.000 đ cho mỗi km vượt mức\n\n"
            "2. LOẠI MÁY ĐẶC THÙ:\n"
            "   • Máy khổ lớn: 80.000 đ/máy\n"
            "   • Máy ép nhiệt: 80.000 đ (<20km) | 50.000 đ (>20km)\n\n"
            "3. PHỤ CẤP ĐI TỈNH XA:\n"
            "   • Công đi tỉnh: 500.000 đ/ngày | Khách sạn: 350.000 đ | Ăn uống: 200.000 đ"
        )
        ws.merge_range("R1:X1", "📌 CHÍNH SÁCH PHỤ CẤP LẮP ĐẶT & BẢO HÀNH", fmt_policy_title)
        ws.merge_range("R2:X10", policy_text, fmt_policy_box)

        # 3. Bảng Tổng Hợp Theo Nhân Viên (Cột R -> X)
        df_approved = df_export[df_export["trang_thai"] == "Đã duyệt"]
        if df_approved.empty:
            df_approved = df_export

        summary_df = df_approved.groupby("Tên", as_index=False).agg(
            tong_don=("STT", "count"),
            tien_may=("device_cost", "sum"),
            tien_km=("distance_cost", "sum"),
            tho_phu=("tho_phu_cost", "sum"),
            di_tinh=("di_tinh_cost", "sum"),
            ngoai_gio=("tien_ngoai_gio", "sum"),
            phu_phi=("phu_phi_phat_sinh", "sum"),
            tong_tien=("thanh_tien", "sum")
        )

        sum_start_row = 11
        sum_headers = ["NHÂN VIÊN", "SỐ ĐƠN", "TIỀN MÁY", "TIỀN KM", "THỢ PHỤ", "PHỤ CẤP KHÁC", "TỔNG THỰC LĨNH"]
        for idx, h_name in enumerate(sum_headers):
            ws.write(sum_start_row, 17 + idx, h_name, fmt_sum_header)

        r_idx = sum_start_row + 1
        for _, s_row in summary_df.iterrows():
            phu_cap_khac = s_row["di_tinh"] + s_row["ngoai_gio"] + s_row["phu_phi"]
            ws.write(r_idx, 17, s_row["Tên"], fmt_left)
            ws.write(r_idx, 18, s_row["tong_don"], fmt_center)
            ws.write(r_idx, 19, s_row["tien_may"], fmt_money)
            ws.write(r_idx, 20, s_row["tien_km"], fmt_money)
            ws.write(r_idx, 21, s_row["tho_phu"], fmt_money)
            ws.write(r_idx, 22, phu_cap_khac, fmt_money)
            ws.write(r_idx, 23, s_row["tong_tien"], fmt_money)
            r_idx += 1

        # Dòng Tổng Cộng Bảng Nhân Viên
        ws.write(r_idx, 17, "TỔNG CỘNG", fmt_sum_total)
        ws.write(r_idx, 18, summary_df["tong_don"].sum(), fmt_sum_total)
        ws.write(r_idx, 19, summary_df["tien_may"].sum(), fmt_sum_money_total)
        ws.write(r_idx, 20, summary_df["tien_km"].sum(), fmt_sum_money_total)
        ws.write(r_idx, 21, summary_df["tho_phu"].sum(), fmt_sum_money_total)
        
        tong_phu_cap = summary_df["di_tinh"].sum() + summary_df["ngoai_gio"].sum() + summary_df["phu_phi"].sum()
        ws.write(r_idx, 22, tong_phu_cap, fmt_sum_money_total)
        ws.write(r_idx, 23, summary_df["tong_tien"].sum(), fmt_sum_money_total)

        # 4. Cấu hình Độ Rộng Cột
        col_widths = [5, 12, 12, 18, 32, 8, 12, 12, 12, 12, 12, 12, 12, 15, 25, 12]
        for idx, width in enumerate(col_widths):
            ws.set_column(idx, idx, width)

        ws.set_column("Q:Q", 3)
        ws.set_column("R:R", 18)
        ws.set_column("S:S", 9)
        ws.set_column("T:W", 13)
        ws.set_column("X:X", 16)

    out.seek(0)
    return out


# =========================================================
# ROUTERS / ENDPOINTS
# =========================================================

@router.get("", response_class=HTMLResponse)
def report_page(request: Request, current_user: dict = Depends(require_login)):
    """Render giao diện HTML trang báo cáo."""
    return templates.TemplateResponse(
        "admin_report.html",
        {"request": request, "current_user": current_user, "admin": current_user}
    )


@router.get("/api/data")
def get_report_api_data(
    month: str = Query(...),
    selected_nv: Optional[str] = Query("Tất cả"),
    selected_tt: Optional[str] = Query("Tất cả"),
    search: Optional[str] = Query(""),
    current_user: dict = Depends(require_login),
):
    """
    API lấy dữ liệu bảng, chỉ số KPIs & biểu đồ.
    Đã chuyển sang hàm đồng bộ ('def') để tối ưu ThreadPool cho Pandas.
    """
    user_role = current_user.get("role", "User")
    username = current_user.get("username", "")

    df_filtered = get_filtered_report_data(month, user_role, username, selected_nv, selected_tt, search or "")

    if df_filtered.empty:
        return JSONResponse({
            "is_empty": True,
            "kpis": {"rev_sum": 0, "pending_sum": 0, "approved_count": 0, "total_orders": 0},
            "chart_data": [],
            "items": []
        })

    # 1. Tính toán KPIs bằng Pandas Mask
    total_orders = len(df_filtered)
    is_approved = df_filtered["trang_thai"] == "Đã duyệt"
    is_pending = df_filtered["trang_thai"] == "Chờ duyệt"

    df_approved = df_filtered[is_approved]
    approved_count = len(df_approved)

    rev_sum = float(df_approved["thanh_tien"].sum()) if not df_approved.empty else 0.0
    pending_sum = float(df_filtered[is_pending]["thanh_tien"].sum()) if not df_filtered[is_pending].empty else 0.0

    # 2. Biểu đồ Doanh thu (Chỉ dành cho Admin)
    chart_data = []
    if user_role in ALLOWED_ADMIN_ROLES and not df_approved.empty:
        chart_grouped = (
            df_approved.groupby("Tên", as_index=False)
            .agg(so_don=("id", "count"), doanh_thu=("thanh_tien", "sum"))
        )
        chart_data = chart_grouped.to_dict(orient="records")

    # 3. Chuẩn hóa nhanh danh sách bản ghi (Vectorized JSON Mapping)
    df_items = df_filtered.copy()
    df_items.insert(0, "stt", range(1, len(df_items) + 1))
    df_items.rename(columns={"Tên": "ten", "Thời Gian Str": "thoi_gian_str"}, inplace=True)

    items = df_items.to_dict(orient="records")

    return {
        "is_empty": False,
        "kpis": {
            "rev_sum": rev_sum,
            "pending_sum": pending_sum,
            "approved_count": approved_count,
            "total_orders": total_orders
        },
        "chart_data": chart_data,
        "items": items,
    }


@router.get("/api/employees")
def get_employee_list(
    month: str = Query(...),
    current_user: dict = Depends(require_login)
):
    """API nạp danh sách nhân viên cho Dropdown lọc (Truy vấn tối ưu gọn nhẹ)."""
    user_role = current_user.get("role", "User")
    if user_role not in ALLOWED_ADMIN_ROLES:
        return {"employees": []}

    start_ts, end_ts = _parse_month_range(month)

    # Truy vấn tối giản chỉ lấy 2 cột ten và username
    res = (
        supabase.table("cham_cong")
        .select("ten, username")
        .gte("thoi_gian", start_ts)
        .lte("thoi_gian", end_ts)
        .execute()
    )
    
    data = res.data or []
    if not data:
        return {"employees": ["Tất cả"]}

    # Trích xuất danh sách tên duy nhất
    names = {item.get("ten") or item.get("username") for item in data if item.get("ten") or item.get("username")}
    return {"employees": ["Tất cả"] + sorted(list(names))}


@router.get("/export-excel")
def export_report_excel(
    month: Optional[str] = Query(None),
    selected_nv: Optional[str] = Query("Tất cả"),
    selected_tt: Optional[str] = Query("Tất cả"),
    search: Optional[str] = Query(""),
    current_user: dict = Depends(require_login),
):
    """Endpoint xuất file Excel báo cáo chi tiết."""
    target_month = month or datetime.now().strftime("%m/%Y")

    user_role = current_user.get("role", "User")
    username = current_user.get("username", "")

    df_display = get_filtered_report_data(target_month, user_role, username, selected_nv, selected_tt, search or "")

    if df_display.empty:
        raise HTTPException(status_code=400, detail="Không tìm thấy dữ liệu phù hợp để xuất Excel")

    excel_stream = _build_excel_report(df_display, target_month)
    
    clean_month = str(target_month).replace('/', '_')
    filename = f"Bao_Cao_Cham_Cong_{clean_month}.xlsx"
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