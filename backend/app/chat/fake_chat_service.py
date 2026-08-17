"""固定回傳 route_ready 假資料的 ChatService。

讓 Dev A 在 Dev B 完成 Gemini Function Calling 邏輯前，先把 API Router、
client_id 轉發、錯誤處理串起來測試（AGENTS.md §8.3）。

Session 生命週期由 ChatService 自己管理（§6.6）：這裡示範最小可用的
「記憶體 dict + TTL」，真正的 GeminiChatService 會在同一個位置額外保存
對話歷史與 dynamic hazards，但那些對 API Router 完全不透明。client_id
固定是 "1".."N"（見 §6.1），所以直接一次配好 N 個 session，不需要容量
逐出機制。
"""

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
DEFAULT_CLIENT_COUNT = 50

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

    def __init__(self, *, client_count: int = DEFAULT_CLIENT_COUNT) -> None:
        self._sessions: dict[str, _Session] = {
            str(i): _Session(created_at=datetime.now(timezone.utc))
            for i in range(1, client_count + 1)
        }

    async def clear_context(self, client_id: str) -> None:
        session = self._sessions.get(client_id)
        if session is None:
            return
        session.created_at = datetime.now(timezone.utc)
        session.user_location = None

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
        session = self._sessions.get(client_id)
        if session is None:
            # 理論上不會發生（client_id 固定是 "1".."N"，已在建構時配好），
            # 保底自動建立（AGENTS.md §6.1）。
            session = _Session(created_at=datetime.now(timezone.utc))
            self._sessions[client_id] = session
        elif datetime.now(timezone.utc) - session.created_at > SESSION_TTL:
            session.created_at = datetime.now(timezone.utc)
            session.user_location = None
        return session
