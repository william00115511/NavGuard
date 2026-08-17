import asyncio
from collections import deque
from typing import Sequence

import pytest

from app.chat.gemini_chat_service import (
    ConversationMessage,
    GeminiChatService,
    ModelReply,
    ToolCall,
)
from interfaces import (
    ChatStatus,
    LatLng,
    Route,
    RouteEngine,
    RouteMetrics,
    RouteResult,
    SessionNotFoundError,
)


def run(coro):
    return asyncio.run(coro)


def new_session(service: GeminiChatService, **kwargs) -> str:
    return run(service.create_session(**kwargs))


def make_route_result(alpha: float = 0.6) -> RouteResult:
    metrics = RouteMetrics(
        distance_m=1240,
        duration_min_est=16,
        avg_safety_score=0.78,
        passed_landmarks={"street_light": 14},
        detour_vs_fastest_min=2,
        data_coverage=["street_light"],
    )
    return RouteResult(
        selected_route_id="safest",
        routes=[
            Route(
                id="safest",
                label="推薦的較安全路線",
                path_coordinates=[
                    LatLng(lat=25.0478, lng=121.5319),
                    LatLng(lat=25.0170, lng=121.5340),
                ],
                alpha_used=alpha,
                metrics=metrics,
            )
        ],
        dynamic_hazards_considered=[],
        google_maps_url="https://www.google.com/maps/dir/?api=1",
        disclaimer="此建議無法保證安全。",
    )


class ScriptedGateway:
    def __init__(self, replies: Sequence[ModelReply | Exception]) -> None:
        self._replies = deque(replies)
        self.histories: list[tuple[ConversationMessage, ...]] = []
        self.has_user_location_calls: list[bool] = []

    async def generate(
        self,
        history: Sequence[ConversationMessage],
        *,
        has_user_location: bool = False,
    ) -> ModelReply:
        self.histories.append(tuple(history))
        self.has_user_location_calls.append(has_user_location)
        reply = self._replies.popleft()
        if isinstance(reply, Exception):
            raise reply
        return reply


class FakeRouteEngine(RouteEngine):
    def __init__(self) -> None:
        self.calculate_calls: list[tuple[LatLng, LatLng, float]] = []
        self.geocode_biases: list[LatLng | None] = []

    async def geocode(
        self,
        place_description: str,
        bias: LatLng | None = None,
    ) -> LatLng | None:
        self.geocode_biases.append(bias)
        return {
            "台北車站": LatLng(lat=25.0478, lng=121.5319),
            "公館夜市": LatLng(lat=25.0170, lng=121.5340),
        }.get(place_description)

    async def calculate_route(
        self,
        origin: LatLng,
        destination: LatLng,
        priority_alpha: float = 0.6,
        dynamic_hazards=(),
    ) -> RouteResult:
        self.calculate_calls.append((origin, destination, priority_alpha))
        return make_route_result(priority_alpha)


class FailingGeocodeEngine(FakeRouteEngine):
    async def geocode(
        self,
        place_description: str,
        bias: LatLng | None = None,
    ) -> LatLng | None:
        raise RuntimeError("geocoder unavailable")


class FailingRouteEngine(FakeRouteEngine):
    async def calculate_route(
        self,
        origin: LatLng,
        destination: LatLng,
        priority_alpha: float = 0.6,
        dynamic_hazards=(),
    ) -> RouteResult:
        raise RuntimeError("route engine unavailable")


def route_call(**overrides) -> ModelReply:
    arguments = {
        "origin": "台北車站",
        "destination": "公館夜市",
    }
    arguments.update(overrides)
    return ModelReply(
        tool_calls=(
            ToolCall(
                id="call-1",
                name="calculate_safe_route",
                arguments=arguments,
            ),
        )
    )


def test_handle_message_without_create_session_raises_session_not_found() -> None:
    """§6.1：session_id 必須先由 create_session() 配發，直接用未知的 ID 對話要失敗。"""
    service = GeminiChatService(ScriptedGateway([]), FakeRouteEngine())

    with pytest.raises(SessionNotFoundError):
        run(service.handle_message("sess_never_issued", "我從台北車站出發"))


def test_new_session_starts_a_conversation() -> None:
    gateway = ScriptedGateway([ModelReply(text="請問目的地是哪裡？")])
    service = GeminiChatService(gateway, FakeRouteEngine())
    session_id = new_session(service)

    result = run(service.handle_message(session_id, "我從台北車站出發"))

    assert result.status is ChatStatus.COLLECTING_INFO
    assert result.reply_text == "請問目的地是哪裡？"


def test_expired_session_raises_session_not_found() -> None:
    """過期的 session_id 是系統層級錯誤（§6.1），前端要重新呼叫 create_session
    換一個新的 session_id，不是靜默重置成新對話。"""
    now = [100.0]
    gateway = ScriptedGateway([ModelReply(text="第一次回覆")])
    service = GeminiChatService(
        gateway,
        FakeRouteEngine(),
        session_ttl_seconds=60,
        clock=lambda: now[0],
    )
    session_id = new_session(service)
    run(service.handle_message(session_id, "規劃路線"))
    now[0] = 300  # 超過 TTL

    with pytest.raises(SessionNotFoundError):
        run(service.handle_message(session_id, "規劃路線"))


def test_reap_expired_sessions_removes_only_stale_sessions() -> None:
    """§6.6：定時回收只清掉過期的 session，未過期的仍然可以正常對話。"""
    now = [100.0]
    gateway = ScriptedGateway([ModelReply(text="還在")])
    service = GeminiChatService(
        gateway,
        FakeRouteEngine(),
        session_ttl_seconds=60,
        clock=lambda: now[0],
    )
    stale_session_id = new_session(service)
    now[0] = 110.0
    fresh_session_id = new_session(service)
    now[0] = 165.0  # stale 已過 TTL（65s > 60s），fresh 還沒（55s ≤ 60s）

    reaped = run(service.reap_expired_sessions())

    assert reaped == 1
    with pytest.raises(SessionNotFoundError):
        run(service.handle_message(stale_session_id, "還在嗎"))
    result = run(service.handle_message(fresh_session_id, "還在嗎"))
    assert result.status is ChatStatus.COLLECTING_INFO


def test_clear_context_resets_conversation_history() -> None:
    gateway = ScriptedGateway([ModelReply(text="第一次"), ModelReply(text="第二次")])
    service = GeminiChatService(gateway, FakeRouteEngine())
    session_id = new_session(service)

    run(service.handle_message(session_id, "第一次訊息"))
    run(service.clear_context(session_id))
    run(service.handle_message(session_id, "第二次訊息"))

    assert [m.kind for m in gateway.histories[-1]] == ["user"]


def test_clear_context_on_unknown_session_id_is_a_noop() -> None:
    service = GeminiChatService(ScriptedGateway([]), FakeRouteEngine())
    run(service.clear_context("never-seen-before"))  # 不應該丟例外


def test_model_question_returns_collecting_info() -> None:
    gateway = ScriptedGateway([ModelReply(text="請問目的地是哪裡？")])
    service = GeminiChatService(gateway, FakeRouteEngine())
    session_id = new_session(service)

    result = run(service.handle_message(session_id, "我從台北車站出發"))

    assert result.status is ChatStatus.COLLECTING_INFO
    assert result.reply_text == "請問目的地是哪裡？"


def test_route_ready_does_not_make_a_second_gemini_call() -> None:
    """route_ready 的數值資料已經結構化（route_result），不需要再打一次 Gemini
    把它轉成文字，reply_text 也不再帶值（§5.1 修訂）。"""
    gateway = ScriptedGateway([route_call()])
    service = GeminiChatService(gateway, FakeRouteEngine())
    session_id = new_session(service)

    result = run(service.handle_message(session_id, "規劃"))

    assert result.status is ChatStatus.ROUTE_READY
    assert result.route_result is not None
    assert result.reply_text is None
    assert len(gateway.histories) == 1


def test_frontend_priority_alpha_is_passed_to_route_engine() -> None:
    gateway = ScriptedGateway([route_call()])
    engine = FakeRouteEngine()
    service = GeminiChatService(gateway, engine)
    session_id = new_session(service)

    result = run(service.handle_message(session_id, "幫我規劃路線", priority_alpha=0.8))

    assert result.status is ChatStatus.ROUTE_READY
    assert result.route_result is not None
    assert engine.calculate_calls[0][2] == 0.8


def test_priority_alpha_defaults_when_frontend_omits_it() -> None:
    gateway = ScriptedGateway([route_call()])
    engine = FakeRouteEngine()
    service = GeminiChatService(gateway, engine)
    session_id = new_session(service)

    result = run(service.handle_message(session_id, "幫我規劃路線"))

    assert result.status is ChatStatus.ROUTE_READY
    assert engine.calculate_calls[0][2] == 0.6


def test_gemini_supplied_priority_alpha_is_rejected() -> None:
    gateway = ScriptedGateway([route_call(priority_alpha=0.9)])
    service = GeminiChatService(gateway, FakeRouteEngine())
    session_id = new_session(service)

    result = run(service.handle_message(session_id, "幫我規劃路線"))

    assert result.status is ChatStatus.ERROR
    assert result.error_code == "INVALID_TOOL_ARGUMENTS"


def test_current_location_uses_session_location_and_geocode_bias() -> None:
    user_location = LatLng(lat=25.033, lng=121.5654)
    gateway = ScriptedGateway([route_call(origin="current_location")])
    engine = FakeRouteEngine()
    service = GeminiChatService(gateway, engine)
    session_id = new_session(service)

    result = run(
        service.handle_message(session_id, "從目前位置出發", user_location=user_location)
    )

    assert result.status is ChatStatus.ROUTE_READY
    assert engine.calculate_calls[0][0] == user_location
    assert engine.geocode_biases == [user_location]
    assert gateway.has_user_location_calls == [True]


def test_gateway_is_told_when_no_user_location_is_available() -> None:
    gateway = ScriptedGateway([ModelReply(text="請問你的起點是哪裡？")])
    service = GeminiChatService(gateway, FakeRouteEngine())
    session_id = new_session(service)

    run(service.handle_message(session_id, "幫我規劃路線"))

    assert gateway.has_user_location_calls == [False]


def test_geocoding_failure_is_recorded_for_next_turn() -> None:
    gateway = ScriptedGateway(
        [
            route_call(origin="不存在"),
            ModelReply(text="請提供更完整的起點。"),
        ]
    )
    service = GeminiChatService(gateway, FakeRouteEngine())
    session_id = new_session(service)

    first = run(service.handle_message(session_id, "規劃"))
    second = run(service.handle_message(session_id, "起點在北門"))

    assert first.error_code == "GEOCODING_FAILED"
    assert second.status is ChatStatus.COLLECTING_INFO
    assert any(message.kind == "tool_response" for message in gateway.histories[1])


@pytest.mark.parametrize("engine", [FailingGeocodeEngine(), FailingRouteEngine()])
def test_system_dependency_failure_is_not_misreported_as_business_error(engine) -> None:
    gateway = ScriptedGateway([route_call()])
    service = GeminiChatService(gateway, engine)
    session_id = new_session(service)

    with pytest.raises(RuntimeError):
        run(service.handle_message(session_id, "規劃"))


def test_history_pruning_keeps_complete_tool_turns() -> None:
    gateway = ScriptedGateway([route_call(), route_call()])
    service = GeminiChatService(gateway, FakeRouteEngine(), max_history_messages=4)
    session_id = new_session(service)

    run(service.handle_message(session_id, "第一次"))
    run(service.handle_message(session_id, "第二次"))

    assert gateway.histories[1][0].kind == "user"
    assert len(gateway.histories[1]) <= 4
