"""ChatService 的唯一組裝點。

依 `CHAT_SERVICE_BACKEND` 選擇 Fake 或 Gemini；routers/ 不需知道實作細節。
"""

from google import genai
from google.oauth2 import service_account

from app.chat.fake_chat_service import FakeChatService
from app.chat.gemini_chat_service import GeminiChatService
from app.chat.google_genai_gateway import GoogleGenAIGateway
from app.config import Settings
from app.engine.route_engine import get_route_engine
from app.geocoding.google_places_geocoder import GooglePlacesGeocoder
from interfaces import ChatService

_chat_service: ChatService | None = None


def get_chat_service() -> ChatService:
    global _chat_service
    if _chat_service is None:
        settings = Settings()
        backend = settings.chat_service_backend.lower()
        if backend == "fake":
            _chat_service = FakeChatService(client_count=settings.client_count)
        elif backend == "gemini":
            credentials_path = settings.google_application_credentials
            if not credentials_path.is_file():
                raise RuntimeError(
                    "CHAT_SERVICE_BACKEND=gemini requires a Vertex AI service "
                    f"account JSON key at {credentials_path}"
                )
            credentials = service_account.Credentials.from_service_account_file(
                str(credentials_path),
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
            )
            maps_api_key = settings.maps_api_key.get_secret_value()
            if not maps_api_key or maps_api_key == "YOUR_API_KEY_HERE":
                raise RuntimeError("CHAT_SERVICE_BACKEND=gemini requires MAPS_API_KEY")
            client = genai.Client(
                vertexai=True,
                project=credentials.project_id,
                location=settings.vertex_location,
                credentials=credentials,
            )
            gateway = GoogleGenAIGateway(client=client, model=settings.gemini_model)
            route_engine = get_route_engine()
            route_engine.set_geocoder(GooglePlacesGeocoder(api_key=maps_api_key))
            _chat_service = GeminiChatService(
                gateway=gateway,
                route_engine=route_engine,
                session_ttl_seconds=settings.session_ttl_seconds,
                max_history_messages=settings.max_history_messages,
                client_count=settings.client_count,
            )
        else:
            raise RuntimeError("CHAT_SERVICE_BACKEND must be either 'fake' or 'gemini'")
    return _chat_service
