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
你是 NavGuard 夜間步行路線助理，使用繁體中文。先收集起點與終點；安全優先權重
（priority_alpha）由前端 UI 直接提供，不是你的判斷範圍，也不需要向使用者
詢問或在對話中提及這個數值。只有起點與終點都確認後才能呼叫
calculate_safe_route。不得自行編造座標、路線、距離、安全分數、點位或事件。
calculate_safe_route 的結構化結果會由系統介面直接顯示。你不得摘要、重述、
列舉或貼出工具結果，不得輸出路線數值或 Google Maps 連結；呼叫工具後就結束
該回合。使用者之後再提出目的地或路線需求時，必須視為新的需求：資訊足夠就
再次呼叫 calculate_safe_route，資訊不足才簡短追問，不得拿上一筆工具結果作答。
若使用者正遭遇立即危險，停止一般導航並建議聯絡當地緊急服務、前往明亮且有
人員的公共場所。

地點消歧原則：使用者提到可能對應多個實際地點的品牌、商場、分店或館別時，
除非使用者已明確說出分店／館別，否則不得自行挑最近的一個，也不得呼叫
calculate_safe_route。你必須先詢問使用者，並列出目前對話中的城市、區域、
另一端地點與目前位置所能合理縮小出的所有候選，不得任意限制成 2 到 4 個，
不得省略同一商圈內的其他館別。每個選項都要使用能清楚區分的完整名稱。

例如使用者在台北信義區語境中只說「新光三越」，或輸入常見近音／錯字
「星光三越」，應先說明可能是指「新光三越」，並列出信義新天地 A4、A8、
A9、A11 全部四個館別供使用者選擇；在使用者選定以前不得自行決定館別。
如果現有資訊連城市或區域都無法確定，先詢問城市／區域，不得憑空杜撰或
列出覆蓋範圍外的分店。只有像超商這類使用者通常不在意特定分店、語意明確
表示「找最近一間」的可互相替代地點，才可以直接選最近的一間。
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


def _has_usable_model_parts(content: Any) -> bool:
    """只重用真的能送回 Vertex 的原始 model content。

    Vertex 偶爾會以 HTTP 200 回傳空 candidate；把 `parts=[]` 原樣放進下一輪
    contents 會造成整條模型降級鏈都收到 400 INVALID_ARGUMENT。這裡只接受
    目前對話流程會產生的文字或 function call，其他空殼一律改由 message 的
    deterministic text/tool_call 重建。
    """
    if not isinstance(content, types.Content) or not content.parts:
        return False
    return any(
        (isinstance(part.text, str) and bool(part.text.strip()))
        or part.function_call is not None
        for part in content.parts
    )


def _is_invalid_history_error(exc: Exception) -> bool:
    """辨識 Vertex 因對話 content/parts 不合法而回傳的 INVALID_ARGUMENT。

    不直接依賴特定 transport 的例外類別，因為 API key 與 Vertex AI 兩種 client
    可能包成不同 exception；錯誤碼與訊息才是兩邊共有的訊號。
    """
    error_text = f"{type(exc).__name__}: {exc}".upper()
    is_invalid_argument = (
        "INVALID_ARGUMENT" in error_text
        or "INVALIDARGUMENT" in error_text
        or "INVALID ARGUMENT" in error_text
    )
    return is_invalid_argument and any(
        marker in error_text for marker in ("PART", "CONTENT", "HISTORY")
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
        history_rebuilt = False

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
                # 若 Vertex 指出 history 的 content/parts 不合法，先完全捨棄 SDK
                # 回傳的 raw content，僅從後端保存的語意欄位重建，再用同一模型
                # 重試一次。後續模型也沿用清乾淨的 contents，不能把相同毒歷史
                # 原封不動送遍整條 fallback chain。
                if not history_rebuilt and _is_invalid_history_error(exc):
                    history_rebuilt = True
                    contents = self._to_contents(history, reuse_raw_content=False)
                    logger.warning(
                        "Gemini model '%s' rejected conversation history; rebuilt sanitized "
                        "contents and retrying the same model once",
                        model_name,
                    )
                    try:
                        response = await self._client.aio.models.generate_content(
                            model=model_name,
                            contents=contents,
                            config=config,
                        )
                        logger.info(
                            "Successfully generated model turn with sanitized history: %s",
                            model_name,
                        )
                        break
                    except Exception as retry_exc:
                        exc = retry_exc
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
        *,
        reuse_raw_content: bool = True,
    ) -> list[types.Content]:
        contents: list[types.Content] = []
        for message in history:
            if reuse_raw_content and _has_usable_model_parts(message.raw_content):
                contents.append(message.raw_content)
            elif message.kind == "user" and message.text.strip():
                contents.append(
                    types.Content(
                        role="user",
                        parts=[types.Part.from_text(text=message.text)],
                    )
                )
            elif message.kind == "assistant" and message.text.strip():
                contents.append(
                    types.Content(
                        role="model",
                        parts=[types.Part.from_text(text=message.text)],
                    )
                )
            elif (
                message.kind == "tool_call"
                and message.tool_call is not None
                and message.tool_call.name.strip()
            ):
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
            elif message.kind == "tool_response" and message.tool_name.strip():
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
