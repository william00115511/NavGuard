import asyncio
from collections import deque
from typing import Sequence

import pytest

from app.chat.gemini_chat_service import (
    ConversationMessage,
    GeminiChatService,
    GeminiGatewayError,
    ModelReply,
    ToolCall,
)
from interfaces import (
    ChatStatus,
    Confidence,
    LatLng,
    Route,
    RouteEngine,
    RouteMetrics,
    RouteResult,
    SessionNotFoundError,
)


def run(coro):
    return asyncio.run(coro)


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
                confidence=Confidence.MEDIUM,
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
        "priority_alpha": 0.8,
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


def test_session_ids_are_unique_and_unknown_session_raises() -> None:
    service = GeminiChatService(ScriptedGateway([]), FakeRouteEngine())

    first = run(service.create_session())
    second = run(service.create_session())

    assert first != second
    with pytest.raises(SessionNotFoundError):
        run(service.handle_message("missing", "規劃路線"))


def test_expired_session_raises_contract_exception() -> None:
    now = [100.0]
    service = GeminiChatService(
        ScriptedGateway([]),
        FakeRouteEngine(),
        session_ttl_seconds=60,
        clock=lambda: now[0],
    )
    session_id = run(service.create_session())
    now[0] = 161

    with pytest.raises(SessionNotFoundError):
        run(service.handle_message(session_id, "規劃路線"))


def test_model_question_returns_collecting_info() -> None:
    gateway = ScriptedGateway([ModelReply(text="請問目的地是哪裡？")])
    service = GeminiChatService(gateway, FakeRouteEngine())
    session_id = run(service.create_session())

    result = run(service.handle_message(session_id, "我從台北車站出發"))

    assert result.status is ChatStatus.COLLECTING_INFO
    assert result.reply_text == "請問目的地是哪裡？"


def test_route_tool_passes_continuous_alpha_and_returns_route_result() -> None:
    gateway = ScriptedGateway([route_call(), ModelReply(text="已完成路線規劃。")])
    engine = FakeRouteEngine()
    service = GeminiChatService(gateway, engine)
    session_id = run(service.create_session())

    result = run(service.handle_message(session_id, "幫我規劃路線"))

    assert result.status is ChatStatus.ROUTE_READY
    assert result.route_result is not None
    assert engine.calculate_calls[0][2] == 0.8
    assert gateway.histories[-1][-1].kind == "tool_response"


def test_current_location_uses_session_location_and_geocode_bias() -> None:
    user_location = LatLng(lat=25.033, lng=121.5654)
    gateway = ScriptedGateway(
        [
            route_call(origin="current_location", priority_alpha=0.6),
            ModelReply(text="已完成。"),
        ]
    )
    engine = FakeRouteEngine()
    service = GeminiChatService(gateway, engine)
    session_id = run(service.create_session(user_location))

    result = run(service.handle_message(session_id, "從目前位置出發"))

    assert result.status is ChatStatus.ROUTE_READY
    assert engine.calculate_calls[0][0] == user_location
    assert engine.geocode_biases == [user_location]
    assert gateway.has_user_location_calls == [True, True]


def test_gateway_is_told_when_no_user_location_is_available() -> None:
    gateway = ScriptedGateway([ModelReply(text="請問你的起點是哪裡？")])
    service = GeminiChatService(gateway, FakeRouteEngine())
    session_id = run(service.create_session())

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
    session_id = run(service.create_session())

    first = run(service.handle_message(session_id, "規劃"))
    second = run(service.handle_message(session_id, "起點在北門"))

    assert first.error_code == "GEOCODING_FAILED"
    assert second.status is ChatStatus.COLLECTING_INFO
    assert any(message.kind == "tool_response" for message in gateway.histories[1])


@pytest.mark.parametrize("engine", [FailingGeocodeEngine(), FailingRouteEngine()])
def test_system_dependency_failure_is_not_misreported_as_business_error(engine) -> None:
    gateway = ScriptedGateway([route_call()])
    service = GeminiChatService(gateway, engine)
    session_id = run(service.create_session())

    with pytest.raises(RuntimeError):
        run(service.handle_message(session_id, "規劃"))


def test_final_gemini_failure_keeps_calculated_route() -> None:
    gateway = ScriptedGateway([route_call(), GeminiGatewayError("timeout")])
    service = GeminiChatService(gateway, FakeRouteEngine())
    session_id = run(service.create_session())

    result = run(service.handle_message(session_id, "規劃"))

    assert result.status is ChatStatus.ROUTE_READY
    assert result.route_result is not None
    assert "1.2 公里" in result.reply_text
    assert "無法保證安全" in result.reply_text


def test_history_pruning_keeps_complete_tool_turns() -> None:
    gateway = ScriptedGateway(
        [
            route_call(),
            ModelReply(text="第一條完成。"),
            route_call(),
            ModelReply(text="第二條完成。"),
        ]
    )
    service = GeminiChatService(gateway, FakeRouteEngine(), max_history_messages=4)
    session_id = run(service.create_session())

    run(service.handle_message(session_id, "第一次"))
    run(service.handle_message(session_id, "第二次"))

    assert gateway.histories[2][0].kind == "user"
    assert len(gateway.histories[2]) <= 4
