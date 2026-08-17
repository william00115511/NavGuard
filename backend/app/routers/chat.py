"""POST /api/chat 與 POST /api/chat/clear（AGENTS.md §6.2 / §6.6，核心端點）。

路由層不保存任何 session 狀態，單純把請求轉給 ChatService；client_id 是否
已經有對話歷史（第一次出現時自動建立）完全由 ChatService 判斷（§6.6），這裡
不需要處理「session 不存在」這種系統層級錯誤。

業務層的失敗（聽不懂地點、超出覆蓋範圍…）不會走例外，而是由 ChatService
回一個 status=error 的 ChatResult，依 §6.5 以 HTTP 200 回傳。
"""

import asyncio

from fastapi import APIRouter

from app.chat.dependencies import get_chat_service
from app.errors import ApiError, InternalError, UpstreamTimeoutError
from app.schemas import (
    ChatRequest,
    ChatResponse,
    ClearContextRequest,
    ClearContextResponse,
    hazard_to_out,
    selected_route_to_out,
)
from interfaces import ChatStatus

router = APIRouter(prefix="/api", tags=["chat"])


@router.post("/chat", response_model=ChatResponse, response_model_exclude_none=True)
async def chat(request: ChatRequest) -> ChatResponse:
    user_location = request.user_location.to_latlng() if request.user_location else None
    try:
        result = await get_chat_service().handle_message(
            request.client_id, request.message, user_location, request.priority_alpha
        )
    except (asyncio.TimeoutError, TimeoutError) as exc:
        raise UpstreamTimeoutError("Gemini 或 geocoding 回應逾時") from exc
    except ApiError:
        # 已經分類過的系統層級錯誤原樣往上拋，不要被下面的 500 蓋掉。
        raise
    except Exception as exc:
        raise InternalError(f"處理訊息時發生錯誤: {exc}") from exc

    response = ChatResponse(
        status=result.status.value,
        reply_text=result.reply_text,
        error_code=result.error_code,
    )
    if result.status is ChatStatus.ROUTE_READY and result.route_result is not None:
        route_result = result.route_result
        response.disclaimer = route_result.disclaimer
        response.route = selected_route_to_out(route_result)
        response.dynamic_hazards_considered = [
            hazard_to_out(h) for h in route_result.dynamic_hazards_considered
        ]
        response.google_maps_url = route_result.google_maps_url
    return response


@router.post("/chat/clear", response_model=ClearContextResponse)
async def clear_context(request: ClearContextRequest) -> ClearContextResponse:
    await get_chat_service().clear_context(request.client_id)
    return ClearContextResponse()
