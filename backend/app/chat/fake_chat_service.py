"""固定回傳 route_ready 假資料的 ChatService。

讓 Dev A 在 Dev B 完成 Gemini Function Calling 邏輯前，先把 API Router、
session_id 轉發、錯誤處理串起來測試（AGENTS.md §8.3）。

Session 生命週期由 ChatService 自己管理（§6.6）：這裡示範最小可用的
「記憶體 dict + TTL」，真正的 GeminiChatService 會在同一個位置額外保存
對話歷史與 dynamic hazards，但那些對 API Router 完全不透明。session_id
由 create_session() 動態配發（見 §6.1），不是固定範圍。
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import uuid4

from app.engine.route_engine import get_route_engine
from app.errors import NO_ROUTE_FOUND, OUT_OF_COVERAGE
from interfaces import (
    ChatResult,
    ChatService,
    ChatStatus,
    LatLng,
    NoRouteFoundError,
    OutOfCoverageError,
    RouteEndpoint,
    SessionNotFoundError,
)

SESSION_TTL = timedelta(minutes=30)

# 展示範圍內的固定起訖點，讓假回覆也跑一次真實引擎（而不是硬編路徑數值）。
# 座標對應信義區真實 OSM 路網（見 tests/test_pathfinding.py 開頭的節點說明）。
# _DEMO_ORIGIN 沒有 name：跟使用者 GPS 定位一樣視為非地標的純座標
# （AGENTS.md §6.2 修訂）；_DEMO_DESTINATION 帶固定假名稱，模擬 destination
# 一律有意義、需要名稱顯示的情境。
_DEMO_ORIGIN = RouteEndpoint(lat=25.01848, lng=121.557416)
_DEMO_DESTINATION = RouteEndpoint(lat=25.04478, lng=121.584105, name="示範終點（信義區）")


@dataclass
class _Session:
    created_at: datetime
    user_location: Optional[LatLng] = None


class FakeChatService(ChatService):
    """不呼叫 Gemini；用固定起訖點跑一次真實路徑引擎當假資料。"""

    def __init__(self) -> None:
        self._sessions: dict[str, _Session] = {}

    async def create_session(self, user_location: Optional[LatLng] = None) -> str:
        session_id = f"sess_{uuid4().hex[:12]}"
        self._sessions[session_id] = _Session(
            created_at=datetime.now(timezone.utc),
            user_location=user_location,
        )
        return session_id

    async def clear_context(self, session_id: str) -> None:
        session = self._sessions.get(session_id)
        if session is None:
            return
        session.created_at = datetime.now(timezone.utc)
        session.user_location = None

    async def reap_expired_sessions(self) -> int:
        now = datetime.now(timezone.utc)
        expired = [
            sid for sid, session in self._sessions.items() if now - session.created_at > SESSION_TTL
        ]
        for session_id in expired:
            del self._sessions[session_id]
        return len(expired)

    async def handle_message(
        self,
        session_id: str,
        message: str,
        user_location: Optional[LatLng] = None,
        priority_alpha: Optional[float] = None,
    ) -> ChatResult:
        session = self._sessions.get(session_id)
        if session is None or datetime.now(timezone.utc) - session.created_at > SESSION_TTL:
            self._sessions.pop(session_id, None)
            raise SessionNotFoundError(f"session_id 不存在或已過期: {session_id}")
        if user_location is not None:
            session.user_location = user_location

        # session.user_location 是 GPS 座標，跟 _DEMO_ORIGIN 一樣視為非地標的
        # 純座標，不補名稱（AGENTS.md §6.2 修訂）。
        origin = (
            RouteEndpoint(lat=session.user_location.lat, lng=session.user_location.lng)
            if session.user_location is not None
            else _DEMO_ORIGIN
        )
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
