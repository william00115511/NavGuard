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

LOCATION_AVAILABLE_NOTE = (
    "系統狀態：目前已取得使用者的目前位置。若使用者的訊息中沒有明確指定起點，"
    "直接以目前位置作為起點（calculate_safe_route 的 origin 填 current_location），"
    "不要詢問使用者是否要以目前位置出發。若使用者訊息中已明確指定其他起點，"
    "則以使用者指定的起點為準。"
)

LOCATION_UNAVAILABLE_NOTE = (
    "系統狀態：目前尚未取得使用者的目前位置（定位權限未開啟或裝置未提供座標）。"
    "詢問起點時，不要提出「要不要用目前位置出發」這類選項，也不得將 origin 填為 "
    "current_location，因為系統目前沒有這個資料可用。請直接請使用者提供明確的"
    "起點地址或地標。"
)


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
        self._configs = {
            has_location: self._build_config(has_location)
            for has_location in (True, False)
        }

    @staticmethod
    def _build_config(has_user_location: bool) -> types.GenerateContentConfig:
        location_note = LOCATION_AVAILABLE_NOTE if has_user_location else LOCATION_UNAVAILABLE_NOTE
        return types.GenerateContentConfig(
            system_instruction=f"{SYSTEM_INSTRUCTION}\n\n{location_note}",
            tools=[CALCULATE_SAFE_ROUTE_TOOL],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            temperature=0.2,
        )

    async def generate(
        self,
        history: Sequence[ConversationMessage],
        *,
        has_user_location: bool = False,
    ) -> ModelReply:
        try:
            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents=self._to_contents(history),
                config=self._configs[has_user_location],
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
