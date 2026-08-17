import asyncio
from types import SimpleNamespace

from google.genai import types

from app.chat.gemini_chat_service import ConversationMessage
from app.chat.google_genai_gateway import GoogleGenAIGateway, SYSTEM_INSTRUCTION


def run(coro):
    return asyncio.run(coro)


class FakeModels:
    def __init__(self, response) -> None:
        self.response = response
        self.calls: list[dict] = []

    async def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class FakeClient:
    def __init__(self, response) -> None:
        self.models = FakeModels(response)
        self.aio = SimpleNamespace(models=self.models)


class ScriptedModels:
    def __init__(self, results) -> None:
        self.results = iter(results)
        self.calls: list[dict] = []

    async def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        result = next(self.results)
        if isinstance(result, Exception):
            raise result
        return result


class ScriptedClient:
    def __init__(self, results) -> None:
        self.models = ScriptedModels(results)
        self.aio = SimpleNamespace(models=self.models)


def make_response(*, text="", function_calls=None):
    parts = [types.Part.from_text(text=text)] if text else []
    return SimpleNamespace(
        text=text,
        function_calls=function_calls or [],
        candidates=[SimpleNamespace(content=SimpleNamespace(role="model", parts=parts))],
    )


def test_gateway_declares_route_tool_without_alpha() -> None:
    function_call = SimpleNamespace(
        id="call-1",
        name="calculate_safe_route",
        args={"origin": "台北車站", "destination": "公館夜市"},
    )
    client = FakeClient(make_response(function_calls=[function_call]))
    gateway = GoogleGenAIGateway(client, "gemini-2.5-flash")

    reply = run(gateway.generate([ConversationMessage(kind="user", text="規劃路線")]))

    assert reply.tool_calls[0].name == "calculate_safe_route"
    config = client.models.calls[0]["config"].model_dump(by_alias=False)
    declaration = config["tools"][0]["function_declarations"][0]
    assert declaration["parameters_json_schema"]["required"] == ["origin", "destination"]
    assert "priority_alpha" not in declaration["parameters_json_schema"]["properties"]


def test_system_instruction_forbids_rendering_route_payload_as_chat_text() -> None:
    assert "不得摘要、重述" in SYSTEM_INSTRUCTION
    assert "不得輸出路線數值或 Google Maps 連結" in SYSTEM_INSTRUCTION
    assert "再次呼叫 calculate_safe_route" in SYSTEM_INSTRUCTION


def test_system_instruction_requires_all_ambiguous_place_candidates() -> None:
    assert "所有候選" in SYSTEM_INSTRUCTION
    assert "不得任意限制成 2 到 4 個" in SYSTEM_INSTRUCTION
    assert "信義新天地 A4、A8、" in SYSTEM_INSTRUCTION
    assert "A9、A11 全部四個館別" in SYSTEM_INSTRUCTION
    assert "星光三越" in SYSTEM_INSTRUCTION


def test_gateway_serializes_function_response() -> None:
    client = FakeClient(make_response(text="已完成。"))
    gateway = GoogleGenAIGateway(client, "gemini-2.5-flash")

    reply = run(
        gateway.generate(
            [
                ConversationMessage(kind="user", text="規劃"),
                ConversationMessage(
                    kind="tool_response",
                    tool_name="calculate_safe_route",
                    tool_response={"selected_route_id": "safest"},
                ),
            ]
        )
    )

    assert reply.text == "已完成。"
    contents = client.models.calls[0]["contents"]
    assert contents[-1].parts[0].function_response.name == "calculate_safe_route"


def test_gateway_rebuilds_empty_raw_model_content_from_fallback_text() -> None:
    contents = GoogleGenAIGateway._to_contents(
        [
            ConversationMessage(
                kind="assistant",
                text="請再提供起點與終點。",
                raw_content=types.Content(role="model", parts=[]),
            ),
            ConversationMessage(kind="user", text="A4 到台北市政府"),
        ]
    )

    assert contents[0].role == "model"
    assert contents[0].parts
    assert contents[0].parts[0].text == "請再提供起點與終點。"
    assert contents[1].parts[0].text == "A4 到台北市政府"


def test_gateway_drops_legacy_empty_messages_before_request() -> None:
    contents = GoogleGenAIGateway._to_contents(
        [
            ConversationMessage(
                kind="assistant",
                raw_content=types.Content(role="model", parts=[]),
            ),
            ConversationMessage(kind="user", text="有效訊息"),
        ]
    )

    assert len(contents) == 1
    assert contents[0].role == "user"
    assert contents[0].parts[0].text == "有效訊息"


def test_invalid_parts_history_is_rebuilt_before_model_fallback() -> None:
    client = ScriptedClient(
        [
            RuntimeError(
                "400 INVALID_ARGUMENT: request must include at least one parts field"
            ),
            make_response(text="清理後成功。"),
        ]
    )
    gateway = GoogleGenAIGateway(
        client,
        "gemini-primary",
        fallback_models=["gemini-fallback"],
    )
    history = [
        ConversationMessage(
            kind="assistant",
            text="後端保存的安全文字",
            raw_content=types.Content(
                role="model",
                parts=[types.Part.from_text(text="SDK 原始內容")],
            ),
        ),
        ConversationMessage(kind="user", text="下一則訊息"),
    ]

    reply = run(gateway.generate(history))

    assert reply.text == "清理後成功。"
    assert [call["model"] for call in client.models.calls] == [
        "gemini-primary",
        "gemini-primary",
    ]
    assert client.models.calls[0]["contents"][0].parts[0].text == "SDK 原始內容"
    assert client.models.calls[1]["contents"][0].parts[0].text == "後端保存的安全文字"


def test_fallback_model_receives_sanitized_history_after_retry_failure() -> None:
    client = ScriptedClient(
        [
            RuntimeError(
                "400 INVALID_ARGUMENT: request must include at least one parts field"
            ),
            RuntimeError("primary model temporarily unavailable"),
            make_response(text="備援成功。"),
        ]
    )
    gateway = GoogleGenAIGateway(
        client,
        "gemini-primary",
        fallback_models=["gemini-fallback"],
    )
    history = [
        ConversationMessage(
            kind="assistant",
            text="乾淨歷史",
            raw_content=types.Content(
                role="model",
                parts=[types.Part.from_text(text="造成拒絕的原始歷史")],
            ),
        ),
        ConversationMessage(kind="user", text="繼續"),
    ]

    reply = run(gateway.generate(history))

    assert reply.text == "備援成功。"
    assert [call["model"] for call in client.models.calls] == [
        "gemini-primary",
        "gemini-primary",
        "gemini-fallback",
    ]
    assert client.models.calls[2]["contents"][0].parts[0].text == "乾淨歷史"
