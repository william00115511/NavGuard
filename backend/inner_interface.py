from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Sequence

@dataclass(frozen=True)
class LatLng:
    lat: float
    lng: float


@dataclass(frozen=True)
class DynamicHazard:
    """對應第 2.5 / 4.2b 節：Gemini 即時搜尋到的時效性點位"""
    category: str
    location: LatLng
    effect: str            # "positive" | "negative"
    confidence: float = 1.0
    valid_hours: Optional[float] = None
    summary: str = ""


@dataclass(frozen=True)
class RouteResult:
    """對應第 3.5 節路徑引擎輸出 + 第 5 節 Google Maps URL"""
    path_coordinates: list[LatLng]
    distance_m: float
    avg_safety_score: float
    alpha_used: float
    passed_landmarks: dict[str, int]
    dynamic_hazards_considered: list[DynamicHazard]
    google_maps_url: str


class ChatStatus(str, Enum):
    COLLECTING_INFO = "collecting_info"
    ROUTE_READY = "route_ready"
    ERROR = "error"


@dataclass(frozen=True)
class ChatResult:
    """對應第 4.5.2 節 /api/chat 回應"""
    status: ChatStatus
    reply_text: str
    route: Optional[RouteResult] = None
    error_code: Optional[str] = None

class ChatService(ABC):

    @abstractmethod
    def create_session(self) -> str:
        """建立新的對話 session，回傳 session_id。"""
        raise NotImplementedError

    @abstractmethod
    def handle_message(self, session_id: str, message: str) -> ChatResult:
        """
        處理一則使用者訊息（內部含 Gemini 對話與 Function Calling），
        回傳 collecting_info / route_ready / error 三種結果之一。
        """
        raise NotImplementedError

class RouteEngine(ABC):

    @abstractmethod
    def geocode(self, place_description: str) -> Optional[LatLng]:
        """文字地點轉座標（第 4.4 節），查無結果回傳 None。"""
        raise NotImplementedError

    @abstractmethod
    def calculate_route(
        self,
        origin: LatLng,
        destination: LatLng,
        priority_alpha: float,
        dynamic_hazards: Sequence[DynamicHazard] = (),
    ) -> RouteResult:
        """
        對應第 3 節安全路徑計算 + 第 5 節 Google Maps URL 產生。
        dynamic_hazards 為本次對話中透過 report_dynamic_hazard
        回報、尚未過期的動態點位（第 2.5 節）。
        """
        raise NotImplementedError
