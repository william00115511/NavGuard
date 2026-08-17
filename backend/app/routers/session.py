"""POST /api/session（ForAI.md 4.5.1）。

單純轉呼叫 ChatService.create_session()；session 是否存在、何時過期完全
由 ChatService 實作管理，這裡只補上 API 回應需要的 created_at 時間戳。
"""

from datetime import datetime, timezone

from fastapi import APIRouter

from app.chat.dependencies import get_chat_service
from app.schemas import CreateSessionResponse

router = APIRouter(prefix="/api", tags=["session"])


@router.post("/session", response_model=CreateSessionResponse)
async def create_session() -> CreateSessionResponse:
    session_id = get_chat_service().create_session()
    return CreateSessionResponse(session_id=session_id, created_at=datetime.now(timezone.utc))
