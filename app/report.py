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

templates = Jinja2Templates(directory="templates")
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/report", tags=["Báo cáo & Thống kê"])
ALLOWED_ADMIN_ROLES = {"Admin", "Super Admin", "System Admin", "Manager"}


# =========================================================
# HELPER: QUERY & LỌC DỮ LIỆU TỪ SUPABASE
# =========================================================

def get_filtered_report_data(
    month: str,
    user_role: str,
    username: str,
    selected_nv: str = "Tất cả",
    selected_tt: str = "Tất cả",
    search: str = ""
) -> pd.DataFrame:
    """Query dữ liệu từ Supabase theo đúng Schema bảng cham_cong."""
    try:
        month_str = str(month).strip()
        if "/" in month_str:
            sel_dt = datetime.strptime(month_str, "%m/%Y")
            start_d = sel_dt.date().replace(day=1)
            last_day = calendar.monthrange(sel_dt.year, sel_dt.month)[1]
            end_d = sel_dt.date().replace(day=last_day)
            start_ts = f"{start_d.strftime('%Y-%m-%d')}T00:00:00"
            end_ts = f"{end_d.strftime('%Y-%m-%d')}T23:59:59"
        else:
            # Nếu chỉ truyền năm (VD: "2026")
            year = int(month_str)
            start_ts = f"{year}-01-01T00:00:00"
            end_ts = f"{year}-12-31T23:59:59"
    except Exception:
        # Mặc định lấy tháng hiện tại nếu parse lỗi
        now = datetime.now()
        start_d = now.date().replace(day=1)
        last_day = calendar.monthrange(now.year, now.month)[1]
        end_d = now.date().replace(day=last_day)
        start_ts = f"{start_d.strftime('%Y-%m-%d')}T00:00:00"
        end_ts = f"{end_d.strftime('%Y-%m-%d')}T23:59:59"

    # 1. Truy vấn Supabase
    query = supabase.table("cham_cong").select("*")
    query = query.gte("thoi_gian", start_ts).lte("thoi_gian", end_ts)

    # 2. Phân quyền & Lọc nhân viên
    if user_role not in ALLOWED_ADMIN_ROLES:
        query = query.eq("username", username)
    elif selected_nv and selected_nv != "Tất cả":
        # Lọc theo username hoặc ten hiển thị
        query = query.or_(f"username.eq.{selected_nv},ten.eq.{selected_nv}")

    # 3. Lọc trạng thái
    if selected_tt and selected_tt != "Tất cả":
        query = query.eq("trang_thai", selected_tt)

    res = query.order("id", desc=True).execute()
    data = res.data or []

    if not data:
        return pd.DataFrame()

    df = pd.DataFrame(data)

    # 4. Trích xuất tên hiển thị Nhân viên
    df["Tên"] = df.apply(lambda r: r.get("ten") or r.get("username") or "N/A", axis=1)

    # 5. Xử lý múi giờ
    if "thoi_gian" in df.columns:
        df["Thời Gian"] = pd.to_datetime(df["thoi_gian"], utc=True).dt.tz_convert("Asia/Ho_Chi_Minh")
        df["Thời Gian Str"] = df["Thời Gian"].dt.strftime("%d/%m/%Y %H:%M")

    # Chuẩn hóa các trường số
    numeric_cols = [
        "quang_duong", "combo", "thanh_tien", "device_cost", 
        "distance_cost", "tho_phu_cost", "di_tinh_cost", 
        "tien_ngoai_gio", "phu_phi_phat_sinh"
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
        else:
            df[col] = 0.0

    df["so_hoa_don"] = df.get("so_hoa_don", "").fillna("")
    df["noi_dung"] = df.get("noi_dung", "").fillna("")
    df["trang_thai"] = df.get("trang_thai", "Chờ duyệt").fillna("Chờ duyệt")
    df["ghi_chu_duyet"] = df.get("ghi_chu_duyet", "").fillna("")
    df["hinh_anh"] = df.get("hinh_anh", "").fillna("")

    # 6. Lọc từ khóa Tìm kiếm (Search)
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
    """Tạo workbook Excel chuyên nghiệp chứa đầy đủ bảng chi tiết và bảng tổng hợp nhân viên."""
    df_export = df_display.sort_values("Thời Gian").copy() if "Thời Gian" in df_display.columns else df_display.copy()
    df_export["STT"] = range(1, len(df_export) + 1)
    df_export["Ngày"] = df_export["Thời Gian"].dt.strftime("%d/%m/%Y") if "Thời Gian" in df_export.columns else ""
    df_export["Quãng đường Str"] = df_export["quang_duong"].apply(lambda x: f"{float(x):g} Km" if x > 0 else "0 Km")

    out = io.BytesIO()
    with pd.ExcelWriter(out, engine="xlsxwriter") as writer:
        wb = writer.book
        ws = wb.add_worksheet("BaoCaoChiTiet")

        # STYLES
        title_fmt = wb.add_format({
            "bold": True, "font_size": 14, "font_name": "Segoe UI",
            "align": "center", "valign": "vcenter",
            "bg_color": "#1F4E79", "font_color": "white", "border": 1
        })
        header_fmt = wb.add_format({
            "bold": True, "font_size": 10, "font_name": "Segoe UI",
            "align": "center", "valign": "vcenter",
            "bg_color": "#2F5597", "font_color": "white", "border": 1, "text_wrap": True
        })
        cell_center = wb.add_format({"border": 1, "align": "center", "valign": "vcenter", "font_name": "Segoe UI"})
        cell_left = wb.add_format({"border": 1, "align": "left", "valign": "vcenter", "font_name": "Segoe UI"})
        money_fmt = wb.add_format({"border": 1, "align": "right", "valign": "vcenter", "num_format": "#,##0", "font_name": "Segoe UI"})
        
        total_label_fmt = wb.add_format({"bold": True, "border": 1, "align": "right", "valign": "vcenter", "bg_color": "#D9EAD3", "font_name": "Segoe UI"})
        total_money_fmt = wb.add_format({"bold": True, "border": 1, "align": "right", "valign": "vcenter", "num_format": "#,##0", "bg_color": "#D9EAD3", "font_name": "Segoe UI"})

        policy_title_fmt = wb.add_format({
            "bold": True, "font_size": 10, "font_name": "Segoe UI",
            "bg_color": "#FFF2CC", "border": 1, "align": "left", "valign": "vcenter"
        })
        policy_box_fmt = wb.add_format({
            "font_size": 9, "font_name": "Segoe UI",
            "bg_color": "#FFF2CC", "border": 1, "valign": "top", "text_wrap": True
        })

        summary_header_fmt = wb.add_format({
            "bold": True, "font_size": 10, "font_name": "Segoe UI",
            "align": "center", "valign": "vcenter",
            "bg_color": "#E2EFDA", "border": 1, "text_wrap": True
        })
        summary_total_fmt = wb.add_format({
            "bold": True, "font_size": 10, "font_name": "Segoe UI",
            "align": "left", "valign": "vcenter",
            "bg_color": "#D9EAD3", "border": 1
        })
        summary_money_total_fmt = wb.add_format({
            "bold": True, "font_size": 10, "font_name": "Segoe UI",
            "align": "right", "valign": "vcenter", "num_format": "#,##0",
            "bg_color": "#D9EAD3", "border": 1
        })

        # 2. BẢNG CHÍNH CHI TIẾT (CỘT A - P)
        headers = [
            "STT", "Ngày", "Số HĐ", "Nhân viên", "Nội dung / Địa chỉ", "Số máy", "Quãng đường",
            "Tiền máy", "Tiền KM", "Thợ phụ", "Đi tỉnh", "Ngoài giờ", "Phụ phí", "Thành tiền", "Ghi chú", "Trạng thái"
        ]

        ws.merge_range("A1:P2", f"BẢNG TỔNG HỢP CHI TIẾT CÔNG LẮP ĐẶT & BẢO HÀNH - THÁNG {month}", title_fmt)

        for c_idx, h_text in enumerate(headers):
            ws.write(2, c_idx, h_text, header_fmt)

        start_row = 3
        for r_idx, row in df_export.reset_index(drop=True).iterrows():
            curr_row = start_row + r_idx
            ws.write(curr_row, 0, row["STT"], cell_center)
            ws.write(curr_row, 1, row["Ngày"], cell_center)
            ws.write(curr_row, 2, row["so_hoa_don"], cell_center)
            ws.write(curr_row, 3, row["Tên"], cell_left)
            ws.write(curr_row, 4, row["noi_dung"], cell_left)
            ws.write(curr_row, 5, row["combo"], cell_center)
            ws.write(curr_row, 6, row["Quãng đường Str"], cell_center)
            
            # Chi phí
            ws.write(curr_row, 7, row["device_cost"], money_fmt)
            ws.write(curr_row, 8, row["distance_cost"], money_fmt)
            ws.write(curr_row, 9, row["tho_phu_cost"], money_fmt)
            ws.write(curr_row, 10, row["di_tinh_cost"], money_fmt)
            ws.write(curr_row, 11, row["tien_ngoai_gio"], money_fmt)
            ws.write(curr_row, 12, row["phu_phi_phat_sinh"], money_fmt)
            ws.write(curr_row, 13, row["thanh_tien"], money_fmt)
            
            ws.write(curr_row, 14, row["ghi_chu_duyet"], cell_left)
            ws.write(curr_row, 15, row["trang_thai"], cell_center)

        # Dòng Tổng Cộng Bảng Chính (Tính toán chính xác index 1-based trong Excel)
        total_row = start_row + len(df_export)
        excel_start_line = start_row + 1  # Dòng 4
        excel_end_line = total_row         # Dòng cuối của dữ liệu

        ws.merge_range(total_row, 0, total_row, 6, "TỔNG CỘNG:", total_label_fmt)
        
        for col_idx in range(7, 14):
            col_letter = chr(65 + col_idx)
            ws.write_formula(total_row, col_idx, f"=SUM({col_letter}{excel_start_line}:{col_letter}{excel_end_line})", total_money_fmt)
            
        ws.write(total_row, 14, "", total_label_fmt)
        ws.write(total_row, 15, "", total_label_fmt)

        # 3. KHUNG CHÍNH SÁCH PHỤ CẤP (CỘT R - X)
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

        ws.merge_range("R1:X1", "📌 CHÍNH SÁCH PHỤ CẤP LẮP ĐẶT & BẢO HÀNH", policy_title_fmt)
        ws.merge_range("R2:X10", policy_text, policy_box_fmt)

        # 4. BẢNG TỔNG HỢP THEO NHÂN VIÊN (CỘT R - X)
        df_approved = df_export[df_export["trang_thai"] == "Đã duyệt"] if "trang_thai" in df_export.columns else df_export
        if df_approved.empty:
            df_approved = df_export

        summary_df = df_approved.groupby("Tên").agg(
            tong_don=("STT", "count"),
            tien_may=("device_cost", "sum"),
            tien_km=("distance_cost", "sum"),
            tho_phu=("tho_phu_cost", "sum"),
            di_tinh=("di_tinh_cost", "sum"),
            ngoai_gio=("tien_ngoai_gio", "sum"),
            phu_phi=("phu_phi_phat_sinh", "sum"),
            tong_tien=("thanh_tien", "sum")
        ).reset_index()

        sum_start_row = 11
        ws.write(sum_start_row, 17, "NHÂN VIÊN", summary_header_fmt)        # Cột R
        ws.write(sum_start_row, 18, "SỐ ĐƠN", summary_header_fmt)          # Cột S
        ws.write(sum_start_row, 19, "TIỀN MÁY", summary_header_fmt)        # Cột T
        ws.write(sum_start_row, 20, "TIỀN KM", summary_header_fmt)         # Cột U
        ws.write(sum_start_row, 21, "THỢ PHỤ", summary_header_fmt)         # Cột V
        ws.write(sum_start_row, 22, "PHỤ CẤP KHÁC", summary_header_fmt)    # Cột W
        ws.write(sum_start_row, 23, "TỔNG THỰC LĨNH", summary_header_fmt)  # Cột X

        r_idx = sum_start_row + 1
        for _, s_row in summary_df.iterrows():
            phu_cap_khac = s_row["di_tinh"] + s_row["ngoai_gio"] + s_row["phu_phi"]

            ws.write(r_idx, 17, s_row["Tên"], cell_left)
            ws.write(r_idx, 18, s_row["tong_don"], cell_center)
            ws.write(r_idx, 19, s_row["tien_may"], money_fmt)
            ws.write(r_idx, 20, s_row["tien_km"], money_fmt)
            ws.write(r_idx, 21, s_row["tho_phu"], money_fmt)
            ws.write(r_idx, 22, phu_cap_khac, money_fmt)
            ws.write(r_idx, 23, s_row["tong_tien"], money_fmt)
            r_idx += 1

        # Dòng TỔNG CỘNG Bảng Nhân Viên
        ws.write(r_idx, 17, "TỔNG CỘNG", summary_total_fmt)
        ws.write(r_idx, 18, summary_df["tong_don"].sum(), summary_total_fmt)
        ws.write(r_idx, 19, summary_df["tien_may"].sum(), summary_money_total_fmt)
        ws.write(r_idx, 20, summary_df["tien_km"].sum(), summary_money_total_fmt)
        ws.write(r_idx, 21, summary_df["tho_phu"].sum(), summary_money_total_fmt)
        
        tong_phu_cap_khac = summary_df["di_tinh"].sum() + summary_df["ngoai_gio"].sum() + summary_df["phu_phi"].sum()
        ws.write(r_idx, 22, tong_phu_cap_khac, summary_money_total_fmt)
        ws.write(r_idx, 23, summary_df["tong_tien"].sum(), summary_money_total_fmt)

        # 5. ĐIỀU CHỈNH KÍCH THƯỚC CỘT
        col_widths = [5, 12, 12, 18, 32, 8, 12, 12, 12, 12, 12, 12, 12, 15, 25, 12]
        for idx, width in enumerate(col_widths):
            ws.set_column(idx, idx, width)

        ws.set_column("Q:Q", 3)   # Cột đệm
        ws.set_column("R:R", 18)  # Tên Nhân viên
        ws.set_column("S:S", 9)   # Số đơn
        ws.set_column("T:W", 13)  # Tiền máy, KM, Thợ phụ, Phụ cấp
        ws.set_column("X:X", 16)  # Tổng thực lĩnh

    out.seek(0)
    return out


# =========================================================
# ROUTERS / ENDPOINTS
# =========================================================

@router.get("", response_class=HTMLResponse)
async def report_page(request: Request, current_user: dict = Depends(require_login)):
    """Giao diện trang báo cáo."""
    return templates.TemplateResponse(
        "admin_report.html",
        {"request": request, "current_user": current_user, "admin": current_user}
    )


@router.get("/api/data")
async def get_report_api_data(
    month: str = Query(...),
    selected_nv: Optional[str] = Query("Tất cả"),
    selected_tt: Optional[str] = Query("Tất cả"),
    search: Optional[str] = Query(""),
    current_user: dict = Depends(require_login),
):
    """API lấy dữ liệu bảng & KPIs & biểu đồ."""
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

    # KPIs
    total_orders = len(df_filtered)
    df_approved = df_filtered[df_filtered["trang_thai"] == "Đã duyệt"]
    approved_count = len(df_approved)
    rev_sum = float(df_approved["thanh_tien"].sum())
    pending_sum = float(df_filtered[df_filtered["trang_thai"] == "Chờ duyệt"]["thanh_tien"].sum())

    # Biểu đồ Doanh thu/Công theo Nhân viên
    chart_data = []
    if user_role in ALLOWED_ADMIN_ROLES and not df_approved.empty:
        chart_grouped = df_approved.groupby("Tên").agg(
            so_don=("id", "count"),
            doanh_thu=("thanh_tien", "sum")
        ).reset_index()
        chart_data = chart_grouped.to_dict(orient="records")

    # Mảng danh sách bản ghi
    items = []
    for idx, row in enumerate(df_filtered.to_dict(orient="records"), start=1):
        items.append({
            "stt": idx,
            "id": row.get("id"),
            "username": row.get("username", ""),
            "ten": row.get("Tên", ""),
            "thoi_gian_str": row.get("Thời Gian Str", ""),
            "so_hoa_don": row.get("so_hoa_don", ""),
            "noi_dung": row.get("noi_dung", ""),
            "quang_duong": float(row.get("quang_duong", 0)),
            "combo": int(row.get("combo", 0)),
            "device_cost": float(row.get("device_cost", 0)),
            "distance_cost": float(row.get("distance_cost", 0)),
            "tho_phu_cost": float(row.get("tho_phu_cost", 0)),
            "di_tinh_cost": float(row.get("di_tinh_cost", 0)),
            "tien_ngoai_gio": float(row.get("tien_ngoai_gio", 0)),
            "phu_phi_phat_sinh": float(row.get("phu_phi_phat_sinh", 0)),
            "thanh_tien": float(row.get("thanh_tien", 0)),
            "hinh_anh": row.get("hinh_anh", ""),
            "trang_thai": row.get("trang_thai", ""),
            "ghi_chu_duyet": row.get("ghi_chu_duyet", ""),
        })

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
async def get_employee_list(
    month: str = Query(...),
    current_user: dict = Depends(require_login)
):
    """API nạp danh sách nhân viên cho Dropdown lọc."""
    user_role = current_user.get("role", "User")
    if user_role not in ALLOWED_ADMIN_ROLES:
        return {"employees": []}

    df_raw = get_filtered_report_data(month, user_role, current_user.get("username", ""), "Tất cả", "Tất cả")
    if df_raw.empty:
        return {"employees": ["Tất cả"]}

    employees = ["Tất cả"] + sorted(df_raw["Tên"].unique().tolist())
    return {"employees": employees}


@router.get("/export-excel")
async def export_report_excel(
    month: Optional[str] = Query(None),
    selected_nv: Optional[str] = Query("Tất cả"),
    selected_tt: Optional[str] = Query("Tất cả"),
    search: Optional[str] = Query(""),
    current_user: dict = Depends(require_login),
):
    """Endpoint xuất file Excel báo cáo chi tiết."""
    if not month:
        month = datetime.now().strftime("%m/%Y")

    user_role = current_user.get("role", "User")
    username = current_user.get("username", "")

    # Lấy dữ liệu theo đúng filter đã chọn
    df_display = get_filtered_report_data(month, user_role, username, selected_nv, selected_tt, search or "")

    if df_display.empty:
        raise HTTPException(status_code=400, detail="Không tìm thấy dữ liệu phù hợp để xuất Excel")

    # Tạo stream file Excel
    excel_stream = _build_excel_report(df_display, month)
    
    filename = f"Bao_Cao_Cham_Cong_{str(month).replace('/', '_')}.xlsx"
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