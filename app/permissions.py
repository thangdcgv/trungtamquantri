import logging
from typing import Dict, Set
from config import supabase,supabase_admin

logger = logging.getLogger(__name__)

# Bộ nhớ đệm lưu phân quyền: {"page_key": {"super admin", "admin", ...}}
PERMISSIONS_CACHE: Dict[str, Set[str]] = {}

ROLE_LEVELS: Dict[str, int] = {
    "user": 1,
    "admin": 2,
    "super admin": 3,
    "system admin": 4
}


def load_permissions_cache():
    """Tải và cập nhật lại cache phân quyền từ CSDL Supabase vào bộ nhớ RAM."""
    global PERMISSIONS_CACHE
    try:
        res = supabase_admin.table("page_permissions").select("page_key, allowed_roles").execute()
        new_cache = {}
        for row in res.data or []:
            key = row.get("page_key")
            roles = [str(r).strip().lower() for r in (row.get("allowed_roles") or [])]
            if key:
                new_cache[key] = set(roles)
        PERMISSIONS_CACHE = new_cache
        logger.info("✅ Đã cập nhật Cache Phân quyền thành công!")
    except Exception as e:
        logger.error(f"❌ Lỗi tải Cache Phân quyền từ CSDL: {e}")


def can_access(page_key: str, user: dict) -> bool:
    """
    Kiểm tra User hiện tại có quyền truy cập page_key hay không dựa trên cấp bậc:
    user (1) < admin (2) < super admin (3) < system admin (4)
    """
    if not user or not isinstance(user, dict):
        return False
    
    user_role = str(user.get("role") or "").strip().lower()
    user_level = ROLE_LEVELS.get(user_role, 0)

    # 1. System Admin (Cấp cao nhất) luôn luôn có quyền truy cập
    if user_role == "system admin" or user_level >= ROLE_LEVELS["system admin"]:
        return True

    # 2. Tự động tải Cache nếu chưa được khởi tạo trong bộ nhớ
    if not PERMISSIONS_CACHE:
        load_permissions_cache()

    allowed_roles = PERMISSIONS_CACHE.get(page_key, set())
    if not allowed_roles:
        return False

    # 3. Khớp trực tiếp role trong danh sách được phép
    if user_role in allowed_roles:
        return True

    # 4. Kế thừa cấp bậc (User có level >= level thấp nhất được phép)
    allowed_levels = [ROLE_LEVELS.get(r, 0) for r in allowed_roles if r in ROLE_LEVELS]
    if allowed_levels and user_level >= min(allowed_levels):
        return True

    return False