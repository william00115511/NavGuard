"""ChatService 的唯一組裝點。

目前綁定 FakeChatService；Dev B 完成 GeminiChatService 後，只需要把
這裡換成 `GeminiChatService()`，routers/ 底下完全不用改（第6節解耦介面）。
"""

from app.chat.fake_chat_service import FakeChatService
from inner_interface import ChatService

_chat_service: ChatService | None = None


def get_chat_service() -> ChatService:
    global _chat_service
    if _chat_service is None:
        _chat_service = FakeChatService()
    return _chat_service
