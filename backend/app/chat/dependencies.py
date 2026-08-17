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
            _chat_service = FakeChatService()
        elif backend == "gemini":
            maps_api_key = (
                settings.maps_api_key.get_secret_value()
                or settings.geocoding_api_key.get_secret_value()
                or settings.routes_api_key.get_secret_value()
            )
            if not maps_api_key or maps_api_key == "YOUR_API_KEY_HERE":
                raise RuntimeError(
                    "CHAT_SERVICE_BACKEND=gemini requires MAPS_API_KEY (or GEOCODING_API_KEY / ROUTES_API_KEY)"
                )

            api_key = settings.gemini_api_key.get_secret_value()
            credentials_path = settings.google_application_credentials
            if api_key and api_key != "YOUR_API_KEY_HERE":
                # Cloud Run 的既有部署路徑：key 由 server-side 環境變數注入，
                # 不寫入 image，也不會傳給 Flutter。
                client = genai.Client(api_key=api_key)
            elif credentials_path.is_file():
                credentials = service_account.Credentials.from_service_account_file(
                    str(credentials_path),
                    scopes=["https://www.googleapis.com/auth/cloud-platform"],
                )
                client = genai.Client(
                    vertexai=True,
                    project=credentials.project_id,
                    location=settings.vertex_location,
                    credentials=credentials,
                )
            else:
                raise RuntimeError(
                    "CHAT_SERVICE_BACKEND=gemini requires GEMINI_API_KEY or a Vertex AI service "
                    f"account JSON key at {credentials_path}"
                )

            gateway = GoogleGenAIGateway(
                client=client,
                model=settings.gemini_model,
                fallback_models=settings.gemini_fallback_models,
            )
            route_engine = get_route_engine()
            route_engine.set_geocoder(GooglePlacesGeocoder(api_key=maps_api_key))
            _chat_service = GeminiChatService(
                gateway=gateway,
                route_engine=route_engine,
                session_ttl_seconds=settings.session_ttl_seconds,
                max_history_messages=settings.max_history_messages,
            )
        else:
            raise RuntimeError("CHAT_SERVICE_BACKEND must be either 'fake' or 'gemini'")
    return _chat_service
