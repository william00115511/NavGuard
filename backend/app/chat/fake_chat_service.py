"""固定回傳 route_ready 假資料的 ChatService。

讓 Dev A 在 Dev B 完成 Gemini Function Calling 邏輯前，先把 API Router、
session 轉發、錯誤處理串起來測試（AGENTS.md §8.3）。

Session 生命週期由 ChatService 自己管理（§6.6）：這裡示範最小可用的
「記憶體 dict + TTL」，真正的 GeminiChatService 會在同一個位置額外保存
對話歷史與 dynamic hazards，但那些對 API Router 完全不透明。
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.engine.route_engine import get_route_engine
from app.errors import NO_ROUTE_FOUND, OUT_OF_COVERAGE
from inner_interface import (
    ChatResult,
    ChatService,
    ChatStatus,
    LatLng,
    NoRouteFoundError,
    OutOfCoverageError,
    SessionNotFoundError,
)

SESSION_TTL = timedelta(minutes=30)

# 展示範圍內的固定起訖點，讓假回覆也跑一次真實引擎（而不是硬編路徑數值）。
_DEMO_ORIGIN = LatLng(lat=25.048, lng=121.531)
_DEMO_DESTINATION = LatLng(lat=25.013, lng=121.535)


@dataclass
class _Session:
    created_at: datetime
    user_location: Optional[LatLng] = None


class FakeChatService(ChatService):
    """不呼叫 Gemini；用固定起訖點跑一次真實路徑引擎當假資料。"""

    def __init__(self) -> None:
        self._sessions: dict[str, _Session] = {}

    async def create_session(self, user_location: Optional[LatLng] = None) -> str:
        self._evict_expired()
        session_id = f"sess_{uuid.uuid4().hex[:8]}"
        self._sessions[session_id] = _Session(
            created_at=datetime.now(timezone.utc), user_location=user_location
        )
        return session_id

    async def handle_message(
        self,
        session_id: str,
        message: str,
        user_location: Optional[LatLng] = None,
    ) -> ChatResult:
        self._evict_expired()
        session = self._sessions.get(session_id)
        if session is None:
            raise SessionNotFoundError(f"session_id 不存在或已過期: {session_id}")
        if user_location is not None:
            session.user_location = user_location

        origin = session.user_location or _DEMO_ORIGIN
        try:
            result = await get_route_engine().calculate_route(
                origin=origin, destination=_DEMO_DESTINATION
            )
        except OutOfCoverageError:
            return ChatResult(
                status=ChatStatus.ERROR,
                reply_text="你的位置目前不在這次展示涵蓋的範圍內，這個版本只支援台北車站到公館一帶。",
                error_code=OUT_OF_COVERAGE,
            )
        except NoRouteFoundError:
            return ChatResult(
                status=ChatStatus.ERROR,
                reply_text="抱歉，這兩個地點之間在目前的路網資料上沒有可以連通的步行路線。",
                error_code=NO_ROUTE_FOUND,
            )

        return ChatResult(
            status=ChatStatus.ROUTE_READY,
            reply_text=(
                f"（測試用假回覆）已收到訊息「{message}」，幫你規劃了一條示範路線。"
                f"{result.disclaimer}"
            ),
            route_result=result,
        )

    def _evict_expired(self) -> None:
        """§6.6：記憶體 session 加 TTL，避免長時間執行後無限增長。"""
        cutoff = datetime.now(timezone.utc) - SESSION_TTL
        expired = [sid for sid, s in self._sessions.items() if s.created_at < cutoff]
        for sid in expired:
            del self._sessions[sid]
