"""POST /api/chat（AGENTS.md §6.2，核心端點）。

路由層不保存任何 session 狀態，單純把請求轉給 ChatService；session_id 是否
有效由 ChatService.handle_message 判斷，不存在時依約定拋出
inner_interface.SessionNotFoundError，這裡接住並轉成 §6.5 的系統層級 404。

業務層的失敗（聽不懂地點、超出覆蓋範圍…）不會走例外，而是由 ChatService
回一個 status=error 的 ChatResult，依 §6.5 以 HTTP 200 回傳。
"""

import asyncio

from fastapi import APIRouter

from app.chat.dependencies import get_chat_service
from app.errors import ApiError, InternalError, UpstreamTimeoutError
from app.errors import SessionNotFoundError as ApiSessionNotFoundError
from app.schemas import ChatRequest, ChatResponse, hazard_to_out, route_to_out
from interfaces import ChatStatus
from interfaces import SessionNotFoundError as ChatSessionNotFoundError

router = APIRouter(prefix="/api", tags=["chat"])


@router.post("/chat", response_model=ChatResponse, response_model_exclude_none=True)
async def chat(request: ChatRequest) -> ChatResponse:
    user_location = request.user_location.to_latlng() if request.user_location else None
    try:
        result = await get_chat_service().handle_message(
            request.session_id, request.message, user_location, request.priority_alpha
        )
    except ChatSessionNotFoundError as exc:
        raise ApiSessionNotFoundError(f"session_id 不存在: {request.session_id}") from exc
    except (asyncio.TimeoutError, TimeoutError) as exc:
        raise UpstreamTimeoutError("Gemini 或 geocoding 回應逾時") from exc
    except ApiError:
        # 已經分類過的系統層級錯誤原樣往上拋，不要被下面的 500 蓋掉。
        raise
    except Exception as exc:
        raise InternalError(f"處理訊息時發生錯誤: {exc}") from exc

    response = ChatResponse(
        session_id=request.session_id,
        status=result.status.value,
        reply_text=result.reply_text,
        error_code=result.error_code,
    )
    if result.status is ChatStatus.ROUTE_READY and result.route_result is not None:
        route_result = result.route_result
        response.disclaimer = route_result.disclaimer
        response.selected_route_id = route_result.selected_route_id
        response.routes = [route_to_out(r) for r in route_result.routes]
        response.dynamic_hazards_considered = [
            hazard_to_out(h) for h in route_result.dynamic_hazards_considered
        ]
        response.google_maps_url = route_result.google_maps_url
    return response
