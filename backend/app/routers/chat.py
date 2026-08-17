"""POST /api/chat（ForAI.md 4.5.2，核心端點）。

路由層不保存任何 session 狀態，單純把請求轉給 ChatService；
session_id 是否有效由 ChatService.handle_message 判斷，
不存在時依約定拋出 inner_interface.SessionNotFoundError，
這裡接住並轉成 4.5.4 節定義的系統層級 404。
"""

from fastapi import APIRouter

from app.chat.dependencies import get_chat_service
from app.errors import UpstreamError
from app.errors import SessionNotFoundError as ApiSessionNotFoundError
from app.schemas import ChatRequest, ChatResponse, route_result_to_out
from inner_interface import SessionNotFoundError as ChatSessionNotFoundError

router = APIRouter(prefix="/api", tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    try:
        result = get_chat_service().handle_message(request.session_id, request.message)
    except ChatSessionNotFoundError as exc:
        raise ApiSessionNotFoundError(f"session_id 不存在: {request.session_id}") from exc
    except Exception as exc:  # Gemini API 逾時、路徑引擎例外等（4.5.4節：系統層級 500）
        raise UpstreamError(f"處理訊息時發生錯誤: {exc}") from exc

    return ChatResponse(
        session_id=request.session_id,
        status=result.status.value,
        reply_text=result.reply_text,
        route=route_result_to_out(result.route) if result.route is not None else None,
        error_code=result.error_code,
    )
