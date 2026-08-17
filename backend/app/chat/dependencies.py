"""ChatService 的唯一組裝點。

依 `CHAT_SERVICE_BACKEND` 選擇 Fake 或 Gemini；routers/ 不需知道實作細節。
"""

from google import genai

from app.chat.fake_chat_service import FakeChatService
from app.chat.gemini_chat_service import GeminiChatService
from app.chat.google_genai_gateway import GoogleGenAIGateway
from app.config import Settings
from app.engine.route_engine import get_route_engine
from interfaces import ChatService

_chat_service: ChatService | None = None


def get_chat_service() -> ChatService:
    global _chat_service
    if _chat_service is None:
        settings = Settings()
        backend = settings.chat_service_backend.lower()
        if backend == "fake":
            _chat_service = FakeChatService()
        elif backend == "gemini":
            api_key = settings.gemini_api_key.get_secret_value()
            if not api_key or api_key == "YOUR_API_KEY_HERE":
                raise RuntimeError("CHAT_SERVICE_BACKEND=gemini requires GEMINI_API_KEY")
            gateway = GoogleGenAIGateway(
                client=genai.Client(api_key=api_key),
                model=settings.gemini_model,
            )
            _chat_service = GeminiChatService(
                gateway=gateway,
                route_engine=get_route_engine(),
                session_ttl_seconds=settings.session_ttl_seconds,
                max_history_messages=settings.max_history_messages,
            )
        else:
            raise RuntimeError("CHAT_SERVICE_BACKEND must be either 'fake' or 'gemini'")
    return _chat_service
