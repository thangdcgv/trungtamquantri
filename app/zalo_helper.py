from datetime import datetime
import requests
import logging
from config import ZALO_OA_ACCESS_TOKEN, ZALO_APP_ID, ZALO_SECRET_KEY, REFRESH_TOKEN

logger = logging.getLogger("uvicorn.error")

_zalo_token_cache = {
    "access_token": ZALO_OA_ACCESS_TOKEN,
    "expires_at": 0
}

def get_zalo_access_token() -> str:
    """Tự động refresh token Zalo, trả về token hợp lệ"""
    now = datetime.now().timestamp()
    if _zalo_token_cache["access_token"] and now < _zalo_token_cache["expires_at"] - 300:
        return _zalo_token_cache["access_token"]

    if not (ZALO_APP_ID and ZALO_SECRET_KEY and REFRESH_TOKEN):
        logger.warning("⚠️ Thiếu thông số refresh Zalo — dùng token tĩnh")
        return _zalo_token_cache["access_token"]

    try:
        res = requests.post(
            "https://oauth.zaloapp.com/v4/oa/access_token",
            headers={"secret_key": ZALO_SECRET_KEY},
            data={
                "app_id": ZALO_APP_ID,
                "grant_type": "refresh_token",
                "refresh_token": REFRESH_TOKEN
            },
            timeout=10
        )
        data = res.json()
        if data.get("access_token"):
            _zalo_token_cache["access_token"] = data["access_token"]
            _zalo_token_cache["expires_at"] = now + data.get("expires_in", 90000)
            if data.get("refresh_token"):
                logger.warning(f"🔄 REFRESH_TOKEN MỚI — cập nhật .env: {data['refresh_token']}")
            return data["access_token"]
        logger.error(f"Refresh token thất bại: {data}")
    except Exception as e:
        logger.error(f"Lỗi gọi refresh token: {e}")

    return _zalo_token_cache["access_token"]