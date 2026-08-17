from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from time import monotonic
from typing import Any, Callable, Protocol, Sequence
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.errors import GEOCODING_FAILED, NO_ROUTE_FOUND, OUT_OF_COVERAGE
from interfaces import (
    ChatResult,
    ChatService,
    ChatStatus,
    LatLng,
    NoRouteFoundError,
    OutOfCoverageError,
    RouteEngine,
    RouteResult,
    SessionNotFoundError,
)


class GeminiGatewayError(RuntimeError):
    """Gemini request failed before a valid model turn was produced."""


DEFAULT_PRIORITY_ALPHA = 0.6


class CalculateRouteArguments(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    origin: str = Field(min_length=1)
    destination: str = Field(min_length=1)


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ModelReply:
    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    raw_content: Any | None = None


@dataclass(frozen=True)
class ConversationMessage:
    kind: str
    text: str = ""
    tool_call: ToolCall | None = None
    tool_name: str = ""
    tool_response: dict[str, Any] | None = None
    raw_content: Any | None = None


class GeminiGateway(Protocol):
    async def generate(
        self,
        history: Sequence[ConversationMessage],
        *,
        has_user_location: bool = False,
    ) -> ModelReply:
        """Generate the next model turn for the supplied conversation."""


@dataclass
class _SessionState:
    history: list[ConversationMessage] = field(default_factory=list)
    user_location: LatLng | None = None
    last_access_at: float = 0.0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)


class GeminiChatService(ChatService):
    def __init__(
        self,
        gateway: GeminiGateway,
        route_engine: RouteEngine,
        *,
        session_ttl_seconds: float = 30 * 60,
        max_history_messages: int = 20,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if session_ttl_seconds <= 0:
            raise ValueError("session_ttl_seconds must be positive")
        if max_history_messages < 4:
            raise ValueError("max_history_messages must be at least 4")
        self._gateway = gateway
        self._route_engine = route_engine
        self._session_ttl_seconds = session_ttl_seconds
        self._max_history_messages = max_history_messages
        self._clock = clock
        self._sessions: dict[str, _SessionState] = {}

    async def create_session(self, user_location: LatLng | None = None) -> str:
        session_id = f"sess_{uuid4().hex[:12]}"
        self._sessions[session_id] = _SessionState(
            user_location=user_location,
            last_access_at=self._clock(),
        )
        return session_id

    async def clear_context(self, session_id: str) -> None:
        session = self._sessions.get(session_id)
        if session is None:
            return
        session.history.clear()
        session.user_location = None
        session.last_access_at = self._clock()

    async def handle_message(
        self,
        session_id: str,
        message: str,
        user_location: LatLng | None = None,
        priority_alpha: float | None = None,
    ) -> ChatResult:
        session = self._sessions.get(session_id)
        if session is None or self._is_expired(session):
            self._sessions.pop(session_id, None)
            raise SessionNotFoundError(f"session_id 不存在或已過期: {session_id}")
        if not message.strip():
            return self._error("INVALID_MESSAGE", "請輸入路線需求。")

        async with session.lock:
            if user_location is not None:
                session.user_location = user_location
            session.last_access_at = self._clock()
            resolved_alpha = (
                priority_alpha if priority_alpha is not None else DEFAULT_PRIORITY_ALPHA
            )
            return await self._handle_session_message(session, message.strip(), resolved_alpha)

    async def reap_expired_sessions(self) -> int:
        """§6.6：定時回收，由背景排程呼叫，與 handle_message 的存取觸發式
        過期檢查互補——就算沒有任何請求進來，閒置的 session 也會被定期清掉。"""
        expired = [sid for sid, session in self._sessions.items() if self._is_expired(session)]
        for session_id in expired:
            del self._sessions[session_id]
        return len(expired)

    async def _handle_session_message(
        self,
        session: _SessionState,
        message: str,
        priority_alpha: float,
    ) -> ChatResult:
        self._append_history(session, ConversationMessage(kind="user", text=message))
        has_user_location = session.user_location is not None

        try:
            reply = await self._gateway.generate(
                session.history,
                has_user_location=has_user_location,
            )
        except (GeminiGatewayError, TimeoutError):
            return self._error("GEMINI_UNAVAILABLE", "目前無法理解路線需求，請稍後再試。")

        if not reply.tool_calls:
            text = reply.text.strip() or "請再提供起點與終點。"
            self._append_history(
                session,
                ConversationMessage(kind="assistant", text=text, raw_content=reply.raw_content),
            )
            return ChatResult(status=ChatStatus.COLLECTING_INFO, reply_text=text)

        tool_call = reply.tool_calls[0]
        self._append_history(
            session,
            ConversationMessage(kind="tool_call", tool_call=tool_call, raw_content=reply.raw_content),
        )
        if tool_call.name != "calculate_safe_route":
            self._record_tool_error(session, tool_call, "UNSUPPORTED_TOOL")
            return self._error("UNSUPPORTED_TOOL", "目前無法執行這個路線操作。")

        try:
            arguments = CalculateRouteArguments.model_validate(tool_call.arguments)
        except ValidationError:
            self._record_tool_error(session, tool_call, "INVALID_TOOL_ARGUMENTS")
            return self._error(
                "INVALID_TOOL_ARGUMENTS",
                "路線需求格式不完整，請重新提供起點與終點。",
            )

        try:
            origin = await self._resolve_origin(arguments.origin, session)
            destination = await self._route_engine.geocode(
                arguments.destination,
                bias=session.user_location,
            )
        except Exception:
            self._record_tool_error(session, tool_call, GEOCODING_FAILED)
            raise
        if origin is None or destination is None:
            self._record_tool_error(session, tool_call, GEOCODING_FAILED)
            return self._error(
                GEOCODING_FAILED,
                "找不到其中一個地點，請提供更完整的地址或地標。",
            )

        try:
            route = await self._route_engine.calculate_route(
                origin=origin,
                destination=destination,
                priority_alpha=priority_alpha,
            )
        except OutOfCoverageError:
            self._record_tool_error(session, tool_call, OUT_OF_COVERAGE)
            return self._error(OUT_OF_COVERAGE, "這個版本尚未涵蓋其中一個地點。")
        except NoRouteFoundError:
            self._record_tool_error(session, tool_call, NO_ROUTE_FOUND)
            return self._error(NO_ROUTE_FOUND, "目前的路網資料找不到可連通的步行路線。")
        except Exception:
            self._record_tool_error(session, tool_call, "ROUTE_ENGINE_FAILED")
            raise

        self._append_history(
            session,
            ConversationMessage(
                kind="tool_response",
                tool_name=tool_call.name,
                tool_response=self._route_result_to_json(route),
            ),
        )

        # route_ready 不需要 Gemini 把數值轉成文字（那份文字已經被結構化 route_result
        # 取代，見 AGENTS.md §5.1 修訂），所以這裡不再多打一次 Gemini 拿 reply_text。
        return ChatResult(status=ChatStatus.ROUTE_READY, route_result=route)

    async def _resolve_origin(
        self,
        origin: str,
        session: _SessionState,
    ) -> LatLng | None:
        if origin == "current_location":
            return session.user_location
        return await self._route_engine.geocode(origin, bias=session.user_location)

    def _append_history(
        self,
        session: _SessionState,
        message: ConversationMessage,
    ) -> None:
        session.history.append(message)
        self._trim_complete_turns(session)

    def _trim_complete_turns(self, session: _SessionState) -> None:
        while len(session.history) > self._max_history_messages:
            next_user_index = next(
                (
                    index
                    for index, item in enumerate(session.history[1:], start=1)
                    if item.kind == "user"
                ),
                None,
            )
            if next_user_index is None:
                return
            del session.history[:next_user_index]

    def _record_tool_error(
        self,
        session: _SessionState,
        tool_call: ToolCall,
        error_code: str,
    ) -> None:
        self._append_history(
            session,
            ConversationMessage(
                kind="tool_response",
                tool_name=tool_call.name,
                tool_response={"error_code": error_code},
            ),
        )

    def _is_expired(self, session: _SessionState) -> bool:
        return self._clock() - session.last_access_at > self._session_ttl_seconds

    @staticmethod
    def _route_result_to_json(route: RouteResult) -> dict[str, Any]:
        def encode(value: Any) -> Any:
            if isinstance(value, Enum):
                return value.value
            if isinstance(value, datetime):
                return value.isoformat()
            raise TypeError(f"unsupported route result value: {type(value).__name__}")

        return json.loads(json.dumps(asdict(route), default=encode))

    @staticmethod
    def _error(error_code: str, reply_text: str) -> ChatResult:
        return ChatResult(
            status=ChatStatus.ERROR,
            reply_text=reply_text,
            error_code=error_code,
        )
