import logging
import time
from typing import Any, Dict, Optional, Set
from config import supabase_admin

logger = logging.getLogger(__name__)

# Cấu hình cấp bậc Role
ROLE_LEVELS: Dict[str, int] = {
    "user": 1,
    "admin": 2,
    "super admin": 3,
    "system admin": 4
}

# Bộ nhớ đệm RAM
PERMISSIONS_CACHE: Dict[str, Any] = {}
IS_CACHE_LOADED: bool = False
LAST_CACHE_UPDATE: float = 0
CACHE_TTL_SECONDS: int = 300  # 5 phút TTL


def load_permissions_cache(force: bool = False) -> None:
    """Nạp ma trận phân quyền từ CSDL vào RAM Cache."""
    global PERMISSIONS_CACHE, IS_CACHE_LOADED, LAST_CACHE_UPDATE
    
    now = time.time()
    if IS_CACHE_LOADED and not force and (now - LAST_CACHE_UPDATE < CACHE_TTL_SECONDS):
        return

    try:
        res = supabase_admin.table("page_permissions").select("page_key, allowed_roles, allowed_departments").execute()
        new_cache = {}
        
        if res.data:
            for item in res.data:
                page_key = str(item.get("page_key") or "").strip()
                if not page_key:
                    continue
                
                roles = {str(r).strip().lower() for r in (item.get("allowed_roles") or [])}
                depts = {str(d).strip().upper() for d in (item.get("allowed_departments") or ["ALL"])}
                
                # 💡 Lưu dạng Dictionary chứa cả Roles và Departments
                new_cache[page_key] = {
                    "roles": roles,
                    "departments": depts
                }

        PERMISSIONS_CACHE = new_cache
        IS_CACHE_LOADED = True
        LAST_CACHE_UPDATE = now
        logger.info(f"✅ Đã tải {len(PERMISSIONS_CACHE)} trang phân quyền vào Cache RAM.")
    except Exception as e:
        logger.error(f"❌ Lỗi nạp permissions cache: {e}")


def can_access(page_key: str, user: Optional[dict]) -> bool:
    """Kiểm tra quyền truy cập trang qua Cache RAM (RBAC + Data Scope)."""
    if not user or not isinstance(user, dict):
        return False

    page_key_clean = str(page_key or "").strip()
    user_role = str(user.get("role") or "").strip().lower()
    user_dept = str(user.get("department") or "KTSC").strip().upper()
    user_level = ROLE_LEVELS.get(user_role, 1)

    # 1. System Admin luôn có toàn quyền
    if user_role == "system admin" or user_level >= ROLE_LEVELS["system admin"]:
        return True

    # 2. Nạp Cache nếu chưa tải
    if not IS_CACHE_LOADED:
        load_permissions_cache()

    perm = PERMISSIONS_CACHE.get(page_key_clean)
    if not perm:
        return False

    # 💡 XỬ LÝ AN TOÀN KHI PERM LÀ SET (CACHE CỦ) HOẶC DICT (CACHE MỚI)
    if isinstance(perm, set):
        allowed_roles = perm
        allowed_depts = {"ALL"}
    elif isinstance(perm, dict):
        allowed_roles = perm.get("roles", set())
        allowed_depts = perm.get("departments", {"ALL"})
    else:
        return False

    # 3. Kiểm tra Phạm vi Phòng Ban (Department Scope)
    if user_role != "super admin" and "ALL" not in allowed_depts:
        if user_dept not in allowed_depts:
            return False

    # 4. Kiểm tra Cấp bậc Vai trò (Role Level)
    if user_role in allowed_roles:
        return True

    allowed_levels = [ROLE_LEVELS.get(r, 1) for r in allowed_roles if r in ROLE_LEVELS]
    if allowed_levels and user_level >= min(allowed_levels):
        return True

    return False

def parse_int(val, default_val: int = 0) -> int:
    """Parse giá trị sang int an toàn, trả về default_val nếu lỗi hoặc rỗng."""
    try:
        if val is None or str(val).strip() == "":
            return default_val
        return int(val)
    except (ValueError, TypeError):
        return default_val

def get_staff_department_map() -> dict:
    """Lấy danh sách ánh xạ từ tên nhân viên/username sang phòng ban từ bảng quan_tri_vien."""
    try:
        res = supabase_admin.table("quan_tri_vien").select("ho_ten, username, department").execute()
        mapping = {}
        if res.data:
            for row in res.data:
                dept = str(row.get("department") or "").strip().lower()
                ho_ten = str(row.get("ho_ten") or "").strip().lower()
                username = str(row.get("username") or "").strip().lower()
                
                if ho_ten:
                    mapping[ho_ten] = dept
                if username:
                    mapping[username] = dept
        return mapping
    except Exception as e:
        logger.error(f"❌ Lỗi lấy mapping phòng ban nhân viên: {e}")
        return {}


def can_modify_warranty_record(user: dict, record: dict) -> bool:
    """
    Kiểm tra quyền Sửa/Xóa phiếu bảo hành:
    - Super Admin / System Admin: Toàn quyền.
    - Tác giả tạo phiếu: Được phép.
    - Người cùng phòng ban với tác giả: Được phép (Tra cứu tự động qua bảng quan_tri_vien).
    """
    if not user or not record:
        return False

    role = str(user.get("role") or "").strip().lower()
    if role in ["super admin", "system admin"]:
        return True

    # 1. Kiểm tra chính chủ (Tác giả)
    current_user_display = str(
        user.get("ho_ten") or user.get("display_name") or user.get("username") or user.get("email") or ""
    ).strip().lower()
    
    creator = str(record.get("created_by") or record.get("staff_name") or "").strip().lower()
    
    if current_user_display and creator and current_user_display == creator:
        return True

    # 2. Kiểm tra cùng phòng ban (Tra cứu tự động từ bảng quan_tri_vien dựa vào staff_name)
    user_dept = str(user.get("department") or "").strip().lower()
    
    if not user_dept:
        # Nếu user hiện tại không có department sẵn trong session, thử lấy từ metadata hoặc quan_tri_vien
        meta = user.get("user_metadata", {})
        user_dept = str(meta.get("department") or "").strip().lower()

    if user_dept:
        # Lấy bảng ánh xạ nhân viên -> phòng ban
        staff_dept_map = get_staff_department_map()
        
        # Phòng ban của người tạo phiếu chính là phòng ban tra được từ staff_name của record
        record_dept = staff_dept_map.get(creator, "")
        
        if record_dept and user_dept == record_dept:
            return True

    return False