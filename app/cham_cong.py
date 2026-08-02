import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from config import supabase  # Supabase client instance

logger = logging.getLogger(__name__)

# --- BẢO BỎ ĐƯỜNG DẪN TEMPLATES TUYỆT ĐỐI ---
# Tìm thư mục gốc dự án (chứa thư mục templates)
# BASE_DIR ở đây trỏ về thư mục cha chứa file mã nguồn hiện tại
BASE_DIR = Path(__file__).resolve().parent

# Nếu cham_cong.py nằm trong thư mục con (ví dụ: routers/cham_cong.py), dùng parent.parent
# Nếu cham_cong.py nằm ngay thư mục gốc, dùng BASE_DIR / "templates"
TEMPLATES_DIR = BASE_DIR.parent / "templates" if (BASE_DIR.parent / "templates").exists() else BASE_DIR / "templates"

if not TEMPLATES_DIR.exists():
    logger.error(f"CRITICAL: Không tìm thấy thư mục templates tại: {TEMPLATES_DIR}")

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter(prefix="/cham-cong", tags=["Chấm Công Lắp Đặt"])


class DuyetPhieuSchema(BaseModel):
    trang_thai: str = Field(..., description="Trạng thái: 'Đã duyệt', 'Từ chối', hoặc 'Chờ duyệt'")
    ghi_chu_duyet: Optional[str] = Field(default="", description="Ghi chú từ QTV")


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def list_cham_cong(request: Request):
    """
    Hiển thị danh sách 5 phiếu chấm công gần nhất của tháng hiện tại
    """
    try:
        now = datetime.now()
        first_day_of_month = datetime(now.year, now.month, 1, tzinfo=timezone.utc).isoformat()

        res_cc = (
            supabase.table('cham_cong')
            .select('*')
            .gte('thoi_gian', first_day_of_month)
            .order('id', desc=True)
            .limit(5)
            .execute()
        )
        
        # --- LÀM SẠCH DỮ LIỆU ĐỂ TRÁNH LỖI UNDEFINED ---
        recent_installations = []
        if res_cc.data:
            for item in res_cc.data:
                # Chuyển đổi item sang dict và lọc bỏ các giá trị Undefined hoặc lỗi serialize
                clean_item = {}
                for k, v in dict(item).items():
                    # Nếu giá trị là Undefined hoặc kiểu dữ liệu không chuẩn, chuyển thành chuỗi rỗng hoặc None
                    if str(type(v)).find("Undefined") != -1:
                        clean_item[k] = None
                    else:
                        clean_item[k] = v
                recent_installations.append(clean_item)
                
    except Exception as e:
        print(f"[DEBUG ERROR] Lỗi truy vấn danh sách chấm công: {str(e)}")
        return HTMLResponse(content=f"<h3>Lỗi hệ thống: {str(e)}</h3>", status_code=500)


@router.get("/detail/{item_id}", response_class=HTMLResponse)
async def detail_cham_cong(request: Request, item_id: int):
    """
    Hiển thị giao diện chi tiết phiếu chấm công (Lấy trực tiếp cột combo từ bảng cham_cong)
    """
    print(f"\n=================== [DEBUG] HIT /cham-cong/detail/{item_id} ===================")
    now = datetime.now()
    first_day_of_month = datetime(now.year, now.month, 1, tzinfo=timezone.utc).isoformat()
    try:
        # 1. Lấy toàn bộ thông tin phiếu chấm công theo ID (đã bao gồm cột combo)
        res = (
            supabase.table('cham_cong')
            .select('*')
            .gte('thoi_gian', first_day_of_month)  # Lọc từ ngày 1 của tháng hiện tại
            .order('id', desc=True)                # Mới nhất xếp trước
            .limit(5)                              # Chỉ lấy tối đa 5 đơn
            .execute()
        )
        data_list = res.data if res.data else []
        
        if not data_list:
            print(f"[DEBUG] Không tìm thấy phiếu chấm công ID: {item_id}")
            return RedirectResponse(url="/cham-cong", status_code=status.HTTP_303_SEE_OTHER)

        item = dict(data_list[0])
        print(f"[DEBUG] Dữ liệu item lấy từ DB: {item}")
        print(f"[DEBUG] Số lượng máy (combo): {item.get('combo')}")

        # Parse datetime an toàn
        if item.get('thoi_gian'):
            try:
                time_str = str(item['thoi_gian']).replace('Z', '')
                if '+' in time_str:
                    time_str = time_str.split('+')[0]
                item['thoi_gian'] = datetime.fromisoformat(time_str)
            except Exception as parse_err:
                print(f"[DEBUG] Lỗi parse datetime: {parse_err}")
                item['thoi_gian'] = None

        template_name = "cham_cong_detail.html"
        target_template = TEMPLATES_DIR / template_name
        
        if not target_template.exists():
            print(f"[DEBUG ERROR] Không tìm thấy file template tại đường dẫn: {target_template}")
            return HTMLResponse(content=f"<h3>Lỗi Template: Không tìm thấy file {template_name}</h3>", status_code=500)

        return templates.TemplateResponse(
            request=request,
            name=template_name,
            context={
                "item": item
            }
        )

    except Exception as e:
        print(f"[DEBUG EXCEPTION CAUGHT] Lỗi ngoại lệ tại detail_cham_cong ({item_id}): {str(e)}")
        import traceback
        traceback.print_exc()
        return RedirectResponse(url="/cham-cong", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/api/duyet/{item_id}")
async def duyet_phieu(item_id: int, payload: DuyetPhieuSchema):
    """
    API duyệt hoặc từ chối phiếu chấm công (Dành cho Admin/QTV)
    """
    if payload.trang_thai not in ['Đã duyệt', 'Từ chối', 'Chờ duyệt']:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"success": False, "message": "Trạng thái không hợp lệ"}
        )

    try:
        update_data = {
            "trang_thai": payload.trang_thai,
            "ghi_chu_duyet": payload.ghi_chu_duyet.strip() if payload.ghi_chu_duyet else ""
        }

        res = supabase.table('cham_cong').update(update_data).eq('id', item_id).execute()
        updated_data = res.data if res.data else []

        if updated_data:
            return {
                "success": True,
                "message": f"Đã cập nhật trạng thái phiếu thành '{payload.trang_thai}'"
            }

        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"success": False, "message": "Không tìm thấy phiếu chấm công để cập nhật"}
        )

    except Exception as e:
        logger.error(f"Lỗi khi duyệt phiếu ID {item_id}: {str(e)}")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"success": False, "message": f"Lỗi hệ thống: {str(e)}"}
        )