import logging
import time
from typing import Dict, Set, Optional
from config import supabase_admin

logger = logging.getLogger(__name__)

# Bộ nhớ đệm lưu phân quyền: {"page_key": {"super admin", "admin", ...}}
PERMISSIONS_CACHE: Dict[str, Set[str]] = {}

# Trạng thái quản lý Cache RAM
IS_CACHE_LOADED: bool = False
LAST_CACHE_UPDATE: float = 0.0
CACHE_TTL_SECONDS: int = 300  # Tự động nạp lại cache từ CSDL sau mỗi 5 phút (300 giây)

ROLE_LEVELS: Dict[str, int] = {
    "user": 1,
    "admin": 2,
    "super admin": 3,
    "system admin": 4
}


def load_permissions_cache(force: bool = False) -> None:
    """
    Tải và cập nhật lại cache phân quyền từ CSDL Supabase vào bộ nhớ RAM.
    :param force: Đặt True khi muốn bắt buộc làm mới RAM lập tức (ví dụ: sau khi Admin bấm Lưu).
    """
    global PERMISSIONS_CACHE, IS_CACHE_LOADED, LAST_CACHE_UPDATE

    now = time.time()
    # Nếu cache đã được tải và chưa hết hạn TTL (và không ép buộc) thì giữ nguyên
    if not force and IS_CACHE_LOADED and (now - LAST_CACHE_UPDATE < CACHE_TTL_SECONDS):
        return

    try:
        res = supabase_admin.table("page_permissions").select("page_key, allowed_roles").execute()
        new_cache: Dict[str, Set[str]] = {}

        for row in res.data or []:
            key = row.get("page_key")
            roles = [str(r).strip().lower() for r in (row.get("allowed_roles") or [])]
            if key:
                new_cache[str(key).strip()] = set(roles)

        PERMISSIONS_CACHE = new_cache
        IS_CACHE_LOADED = True
        LAST_CACHE_UPDATE = now
        logger.info("✅ Đã cập nhật Cache Phân quyền thành công!")
    except Exception as e:
        logger.error(f"❌ Lỗi tải Cache Phân quyền từ CSDL: {e}")
        # Đánh dấu đã nạp để tránh lặp lại truy vấn CSDL liên tục khi gặp lỗi
        IS_CACHE_LOADED = True


def can_access(page_key: str, user: Optional[dict]) -> bool:
    """
    Kiểm tra User hiện tại có quyền truy cập page_key hay không dựa trên:
    1. System Admin luôn có toàn quyền truy cập (Bypass).
    2. Khớp trực tiếp vai trò trong danh sách allowed_roles.
    3. Kế thừa cấp bậc (User level >= level thấp nhất trong allowed_roles).
    """
    if not user or not isinstance(user, dict):
        return False

    page_key_clean = str(page_key or "").strip()
    user_role = str(user.get("role") or "").strip().lower()
    user_level = ROLE_LEVELS.get(user_role, 1)

    # 1. System Admin (Cấp 4) luôn có toàn quyền truy cập
    if user_role == "system admin" or user_level >= ROLE_LEVELS["system admin"]:
        return True

    # 2. Tự động kiểm tra / nạp Cache nếu chưa khởi tạo hoặc đã hết hạn TTL
    if not IS_CACHE_LOADED or (time.time() - LAST_CACHE_UPDATE >= CACHE_TTL_SECONDS):
        load_permissions_cache()

    allowed_roles = PERMISSIONS_CACHE.get(page_key_clean, set())
    if not allowed_roles:
        return False

    # 3. Khớp trực tiếp vai trò trong danh sách được phép
    if user_role in allowed_roles:
        return True

    # 4. Kế thừa cấp bậc: User có level >= level thấp nhất trong các role được phép
    allowed_levels = [ROLE_LEVELS.get(r, 1) for r in allowed_roles if r in ROLE_LEVELS]
    if allowed_levels and user_level >= min(allowed_levels):
        return True

    return False