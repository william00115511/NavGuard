import asyncio
from types import SimpleNamespace

from app.chat.gemini_chat_service import ConversationMessage
from app.chat.google_genai_gateway import GoogleGenAIGateway


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


def make_response(*, text="", function_calls=None):
    return SimpleNamespace(
        text=text,
        function_calls=function_calls or [],
        candidates=[SimpleNamespace(content=SimpleNamespace(role="model", parts=[]))],
    )


def test_gateway_declares_route_tool_with_optional_alpha() -> None:
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
    assert declaration["parameters_json_schema"]["properties"]["priority_alpha"]["default"] == 0.6


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
