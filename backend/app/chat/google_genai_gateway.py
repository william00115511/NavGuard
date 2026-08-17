from __future__ import annotations

import logging
from typing import Any, Sequence

from google.genai import types

from app.chat.gemini_chat_service import (
    ConversationMessage,
    GeminiGatewayError,
    ModelReply,
    ToolCall,
)

logger = logging.getLogger(__name__)


SYSTEM_INSTRUCTION = """
你是 Safeway 夜間步行路線助理，使用繁體中文。先收集起點與終點；安全優先權重
（priority_alpha）由前端 UI 直接提供，不是你的判斷範圍，也不需要向使用者
詢問或在對話中提及這個數值。只有起點與終點都確認後才能呼叫
calculate_safe_route。不得自行編造座標、路線、距離、安全分數、點位或事件。
工具回傳後只能根據工具資料摘要，不得修改任何數值，也不得宣稱路線絕對安全。
每次提供路線都要附上工具回傳的 disclaimer 與 warnings。若使用者正遭遇立即
危險，停止一般導航並建議聯絡當地緊急服務、前往明亮且有人員的公共場所。

地點消歧原則：使用者提到連鎖品牌或有多間分店的地標（百貨公司、超商、
連鎖店等）時，先自行判斷現有資訊是否已經足夠指向單一明確地點——例如
對話中另一端的地址明顯只靠近某一間分店、使用者已提過所在城市或區域、
或分店之間距離夠遠、走路路線有明顯差異，足以用地理位置排除其他分店。
只要能合理判斷，就直接選用該分店繼續進行（呼叫工具時把地點說清楚，
例如「大遠百（台北店）」），不要為了消歧而多問一輪。

注意：分店距離近不等於可以隨便選一間。像超商這種分店之間高度可互相
替代的地標，即使沒有其他線索，選最近的一間通常就是使用者的意圖。但
像百貨公司在同一商圈內的不同館別（例如新光三越信義新天地的 A9、A11
等），使用者講出具體地標時通常是衝著特定館別去的，即使各館距離很近、
走路路線差異不大，也不能只靠距離判斷該去哪一館——這種情況下如果沒有
其他線索能縮小到單一館別，仍然要詢問使用者，並只列出你判斷最可能的
少數幾個選項（2 到 4 個即可），不要把所有分店都列出來要使用者自己選。
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
            description=(
                "起點與終點確認後，計算夜間步行安全路線與最快路線。安全優先權重"
                "（priority_alpha）由前端直接提供，不是這個工具的參數，也不由 Gemini 決定。"
            ),
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
                },
                "required": ["origin", "destination"],
                "additionalProperties": False,
            },
        )
    ]
)


def _response_text(content: types.Content | None) -> str:
    """等同 response.text，但直接讀 parts，不觸發 SDK 對 non-text parts 的警告 log
    （工具呼叫的回應本來就沒有純文字 part，是預期狀況，不需要每次都印警告）。"""
    if content is None or content.parts is None:
        return ""
    return "".join(
        part.text
        for part in content.parts
        if isinstance(part.text, str) and not (isinstance(part.thought, bool) and part.thought)
    )


class GoogleGenAIGateway:
    def __init__(
        self,
        client: Any,
        model: str,
        fallback_models: Sequence[str] = (),
    ) -> None:
        self._client = client
        # 優先使用主要 model，後續接上備用 fallback models（去重）
        self._models = [model] + [m for m in fallback_models if m and m != model]
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
        contents = self._to_contents(history)
        config = self._configs[has_user_location]
        response = None
        last_exc: Exception | None = None

        for model_name in self._models:
            try:
                response = await self._client.aio.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=config,
                )
                logger.info("Successfully generated model turn with: %s", model_name)
                break
            except Exception as exc:
                logger.warning(
                    "Gemini model '%s' failed (error: %s). Trying next fallback model...",
                    model_name,
                    exc,
                )
                last_exc = exc

        if response is None:
            raise GeminiGatewayError("All Gemini models in fallback chain failed") from last_exc

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
            text=_response_text(raw_content),
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
