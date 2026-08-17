"""固定回傳 route_ready 假資料的 ChatService。

讓 Dev A 在 Dev B 完成 Gemini Function Calling 邏輯前，先把 API Router、
client_id 轉發、錯誤處理串起來測試（AGENTS.md §8.3）。

Session 生命週期由 ChatService 自己管理（§6.6）：這裡示範最小可用的
「記憶體 dict + TTL + LRU 上限」，真正的 GeminiChatService 會在同一個位置
額外保存對話歷史與 dynamic hazards，但那些對 API Router 完全不透明。
"""

from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.engine.route_engine import get_route_engine
from app.errors import NO_ROUTE_FOUND, OUT_OF_COVERAGE
from interfaces import (
    ChatResult,
    ChatService,
    ChatStatus,
    LatLng,
    NoRouteFoundError,
    OutOfCoverageError,
)

SESSION_TTL = timedelta(minutes=30)
DEFAULT_MAX_ACTIVE_SESSIONS = 50

# 展示範圍內的固定起訖點，讓假回覆也跑一次真實引擎（而不是硬編路徑數值）。
# 座標對應信義區真實 OSM 路網（見 tests/test_pathfinding.py 開頭的節點說明）。
_DEMO_ORIGIN = LatLng(lat=25.01848, lng=121.557416)
_DEMO_DESTINATION = LatLng(lat=25.04478, lng=121.584105)


@dataclass
class _Session:
    created_at: datetime
    user_location: Optional[LatLng] = None


class FakeChatService(ChatService):
    """不呼叫 Gemini；用固定起訖點跑一次真實路徑引擎當假資料。"""

    def __init__(self, *, max_active_sessions: int = DEFAULT_MAX_ACTIVE_SESSIONS) -> None:
        self._max_active_sessions = max_active_sessions
        self._sessions: OrderedDict[str, _Session] = OrderedDict()

    async def clear_context(self, client_id: str) -> None:
        self._sessions.pop(client_id, None)

    async def handle_message(
        self,
        client_id: str,
        message: str,
        user_location: Optional[LatLng] = None,
        priority_alpha: Optional[float] = None,
    ) -> ChatResult:
        session = self._get_or_create_session(client_id)
        if user_location is not None:
            session.user_location = user_location

        origin = session.user_location or _DEMO_ORIGIN
        try:
            result = await get_route_engine().calculate_route(
                origin=origin,
                destination=_DEMO_DESTINATION,
                priority_alpha=priority_alpha if priority_alpha is not None else 0.6,
            )
        except OutOfCoverageError:
            return ChatResult(
                status=ChatStatus.ERROR,
                reply_text="你的位置目前不在這次展示涵蓋的範圍內，這個版本只支援台北市信義區一帶。",
                error_code=OUT_OF_COVERAGE,
            )
        except NoRouteFoundError:
            return ChatResult(
                status=ChatStatus.ERROR,
                reply_text="抱歉，這兩個地點之間在目前的路網資料上沒有可以連通的步行路線。",
                error_code=NO_ROUTE_FOUND,
            )

        return ChatResult(status=ChatStatus.ROUTE_READY, route_result=result)

    def _get_or_create_session(self, client_id: str) -> _Session:
        self._evict_expired()
        session = self._sessions.get(client_id)
        if session is not None:
            self._sessions.move_to_end(client_id)
            return session

        if len(self._sessions) >= self._max_active_sessions:
            self._sessions.popitem(last=False)
        session = _Session(created_at=datetime.now(timezone.utc))
        self._sessions[client_id] = session
        return session

    def _evict_expired(self) -> None:
        """§6.6：記憶體 session 加 TTL，避免長時間執行後無限增長。"""
        cutoff = datetime.now(timezone.utc) - SESSION_TTL
        expired = [sid for sid, s in self._sessions.items() if s.created_at < cutoff]
        for sid in expired:
            del self._sessions[sid]
