"""極簡 in-memory session 註冊表：只負責『session_id 存不存在』與 created_at。

對話歷史、Gemini 內部 session 狀態等由 ChatService 實作（Dev B）自行管理，
這裡完全不碰，維持第6節 ChatService/RouteEngine 的解耦邊界。
"""

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class SessionInfo:
    session_id: str
    created_at: datetime


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, SessionInfo] = {}

    def register(self, session_id: str) -> SessionInfo:
        info = SessionInfo(session_id=session_id, created_at=datetime.now(timezone.utc))
        self._sessions[session_id] = info
        return info

    def exists(self, session_id: str) -> bool:
        return session_id in self._sessions


_store: SessionStore | None = None


def get_session_store() -> SessionStore:
    global _store
    if _store is None:
        _store = SessionStore()
    return _store
