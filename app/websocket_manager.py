import asyncio
import logging
from typing import Set
from fastapi import WebSocket
from datetime import timezone, timedelta

# Khai báo múi giờ Việt Nam (UTC+7)
VN_TZ = timezone(timedelta(hours=7))
# Khởi tạo logger tiêu chuẩn
logger = logging.getLogger("websocket_manager")


class ConnectionManager:

    def __init__(self):
        self.active_connections: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket):
        """Chấp nhận kết nối mới"""
        await websocket.accept()
        self.active_connections.add(websocket)
        logger.info(
            f"🔌 [WebSocket] Kết nối mới. Tổng: {len(self.active_connections)}"
        )

    def disconnect(self, websocket: WebSocket):
        """Ngắt kết nối an toàn"""
        self.active_connections.discard(
            websocket
        )  # discard không bắn lỗi nếu item không tồn tại
        logger.info(
            f"🔌 [WebSocket] Ngắt kết nối. Còn: {len(self.active_connections)}"
        )

    async def _send_safe(self, ws: WebSocket, message: dict) -> WebSocket | None:
        """Hàm phụ trợ gửi tin an toàn; trả về WebSocket gặp lỗi để loại bỏ"""
        try:
            await ws.send_json(message)
            return None
        except Exception:
            return ws

    async def broadcast(self, message: dict):
        """Gửi thông báo SONG SONG tới tất cả client cùng lúc"""
        if not self.active_connections:
            return

        # Tạo danh sách các tác vụ gửi song song
        targets = list(self.active_connections)
        results = await asyncio.gather(
            *(self._send_safe(ws, message) for ws in targets),
            return_exceptions=True,
        )

        # Thu gom các socket bị lỗi kết nối để tiến hành dọn dẹp
        for result in results:
            if isinstance(result, WebSocket):
                self.disconnect(result)


# Đối tượng dùng chung toàn cục
manager = ConnectionManager()