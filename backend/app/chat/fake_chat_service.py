"""固定回傳 route_ready 假資料的 ChatService。

讓 Dev A 在 Dev B 完成 Gemini Function Calling 邏輯前，先把 API Router、
錯誤處理串起來測試（ForAI.md 第6節「平行開發方式」）。

Session 生命週期（建立、過期）由 ChatService 自己管理，這裡用一個
process 內的 set 模擬；真正的 GeminiChatService 會換成 dict + TTL，
並額外保存對話歷史與 dynamic hazards，但那些對 Dev A／API Router
完全不透明。
"""

import uuid

from app.engine.route_engine import LocalDataRouteEngine
from inner_interface import ChatResult, ChatService, ChatStatus, LatLng, SessionNotFoundError


class FakeChatService(ChatService):
    """不呼叫 Gemini；用固定起訖點跑一次真實路徑引擎當假資料。"""

    def __init__(self) -> None:
        self._route_engine = LocalDataRouteEngine()
        self._sessions: set[str] = set()

    def create_session(self) -> str:
        session_id = f"sess_{uuid.uuid4().hex[:8]}"
        self._sessions.add(session_id)
        return session_id

    def handle_message(self, session_id: str, message: str) -> ChatResult:
        if session_id not in self._sessions:
            raise SessionNotFoundError(f"session_id 不存在: {session_id}")

        route = self._route_engine.calculate_route(
            origin=LatLng(lat=25.048, lng=121.531),
            destination=LatLng(lat=25.013, lng=121.535),
            priority_alpha=0.7,
        )
        return ChatResult(
            status=ChatStatus.ROUTE_READY,
            reply_text=f"（測試用假回覆）已收到訊息「{message}」，幫你規劃了一條示範路線。",
            route=route,
        )
