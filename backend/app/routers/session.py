"""POST /api/session（AGENTS.md §6.1）。

單純轉呼叫 ChatService.create_session()；session 是否存在、何時過期、何時被
定時回收完全由 ChatService 實作管理（§6.6），這裡只補上 API 回應需要的
created_at。
"""

from datetime import datetime, timezone

from fastapi import APIRouter

from app.chat.dependencies import get_chat_service
from app.schemas import CreateSessionRequest, CreateSessionResponse

router = APIRouter(prefix="/api", tags=["session"])


@router.post("/session", response_model=CreateSessionResponse)
async def create_session(request: CreateSessionRequest | None = None) -> CreateSessionResponse:
    user_location = request.user_location.to_latlng() if request and request.user_location else None
    session_id = await get_chat_service().create_session(user_location)
    return CreateSessionResponse(session_id=session_id, created_at=datetime.now(timezone.utc))
