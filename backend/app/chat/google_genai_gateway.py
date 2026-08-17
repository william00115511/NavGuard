from __future__ import annotations

from typing import Any, Sequence

from google.genai import types

from app.chat.gemini_chat_service import (
    ConversationMessage,
    GeminiGatewayError,
    ModelReply,
    ToolCall,
)


SYSTEM_INSTRUCTION = """
你是 Safeway 夜間步行路線助理，使用繁體中文。先收集起點與終點；使用者未表達
安全偏好時，priority_alpha 使用預設值 0.6。只有資訊齊全時才能呼叫
calculate_safe_route。不得自行編造座標、路線、距離、安全分數、點位或事件。
工具回傳後只能根據工具資料摘要，不得修改任何數值，也不得宣稱路線絕對安全。
每次提供路線都要附上工具回傳的 disclaimer 與 warnings。若使用者正遭遇立即
危險，停止一般導航並建議聯絡當地緊急服務、前往明亮且有人員的公共場所。
""".strip()


CALCULATE_SAFE_ROUTE_TOOL = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="calculate_safe_route",
            description="起點與終點確認後，計算夜間步行安全路線與最快路線。",
            parameters_json_schema={
                "type": "object",
                "properties": {
                    "origin": {
                        "type": "string",
                        "description": "使用者確認的起點；使用目前位置時填 current_location。",
                    },
                    "destination": {
                        "type": "string",
                        "description": "使用者確認的終點地址或地標。",
                    },
                    "priority_alpha": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 1,
                        "default": 0.6,
                        "description": "安全優先權重；未表態時使用 0.6。",
                    },
                },
                "required": ["origin", "destination"],
                "additionalProperties": False,
            },
        )
    ]
)


class GoogleGenAIGateway:
    def __init__(self, client: Any, model: str) -> None:
        self._client = client
        self._model = model
        self._config = types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            tools=[CALCULATE_SAFE_ROUTE_TOOL],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            temperature=0.2,
        )

    async def generate(
        self,
        history: Sequence[ConversationMessage],
    ) -> ModelReply:
        try:
            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents=self._to_contents(history),
                config=self._config,
            )
        except Exception as exc:
            raise GeminiGatewayError("Gemini request failed") from exc

        function_calls = response.function_calls or []
        tool_calls = tuple(
            ToolCall(
                id=function_call.id or f"call-{index}",
                name=function_call.name or "",
                arguments=dict(function_call.args or {}),
            )
            for index, function_call in enumerate(function_calls, start=1)
        )
        raw_content = response.candidates[0].content if response.candidates else None
        return ModelReply(
            text=response.text or "",
            tool_calls=tool_calls,
            raw_content=raw_content,
        )

    @staticmethod
    def _to_contents(
        history: Sequence[ConversationMessage],
    ) -> list[types.Content]:
        contents: list[types.Content] = []
        for message in history:
            if isinstance(message.raw_content, types.Content):
                contents.append(message.raw_content)
            elif message.kind == "user":
                contents.append(
                    types.Content(
                        role="user",
                        parts=[types.Part.from_text(text=message.text)],
                    )
                )
            elif message.kind == "assistant":
                contents.append(
                    types.Content(
                        role="model",
                        parts=[types.Part.from_text(text=message.text)],
                    )
                )
            elif message.kind == "tool_call" and message.tool_call is not None:
                contents.append(
                    types.Content(
                        role="model",
                        parts=[
                            types.Part.from_function_call(
                                name=message.tool_call.name,
                                args=message.tool_call.arguments,
                            )
                        ],
                    )
                )
            elif message.kind == "tool_response":
                contents.append(
                    types.Content(
                        role="user",
                        parts=[
                            types.Part.from_function_response(
                                name=message.tool_name,
                                response=message.tool_response or {},
                            )
                        ],
                    )
                )
        return contents
