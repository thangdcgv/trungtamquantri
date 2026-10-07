import asyncio
import logging
from typing import Set
from fastapi import WebSocket
from datetime import timezone, timedelta

VN_TZ = timezone(timedelta(hours=7))
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

    async def disconnect(self, websocket: WebSocket):
        """Ngắt kết nối an toàn và giải phóng tài nguyên socket"""
        self.active_connections.discard(websocket)
        try:
            # Thử đóng kết nối nếu socket vẫn còn mở
            await websocket.close()
        except Exception:
            pass  # Nếu client đã tự đóng trước đó thì bỏ qua
            
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

        targets = list(self.active_connections)
        results = await asyncio.gather(
            *(self._send_safe(ws, message) for ws in targets),
            return_exceptions=True,
        )

        # Thu gom các socket bị lỗi kết nối để tiến hành dọn dẹp
        for result in results:
            if isinstance(result, WebSocket):
                await self.disconnect(result)


manager = ConnectionManager()