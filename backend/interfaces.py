"""Dev A ⇄ Dev B 的共用契約（AGENTS.md §8.2）。

這個檔案是兩邊唯一的溝通型別，任一方都不需要知道對方的內部實作：
Dev B 只依賴 `RouteEngine`，Dev A 只依賴 `ChatService`。

介面採 async，因為兩邊實際都會做 I/O（Gemini API、geocoding、檔案讀取），
FastAPI 也是 async 框架，避免日後回頭改簽章。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Sequence

# ---------- 共用資料結構 ----------


@dataclass(frozen=True)
class LatLng:
    lat: float
    lng: float


@dataclass(frozen=True)
class DynamicHazard:
    """對應 §3.4 / §5.4：Gemini 即時搜尋到的時效性點位。

    注意 location 已是座標——geocoding 由 Function Calling handler 先呼叫
    RouteEngine.geocode() 完成，引擎不再處理文字地點。
    effect 不在此處指定，一律由 categories.json 決定（§5.4 規則 1）。
    """

    category: str
    location: LatLng
    summary: str
    expires_at: datetime
    confidence: float = 1.0
    source_url: Optional[str] = None


@dataclass(frozen=True)
class RouteMetrics:
    """對應 §4.6。無資料的欄位用 None，不得填 0。"""

    distance_m: float
    duration_min_est: float
    avg_safety_score: float
    passed_landmarks: dict[str, int]
    detour_vs_fastest_min: float
    data_coverage: list[str]
    lit_coverage_ratio: Optional[float] = None
    help_points_within_50m: Optional[int] = None
    police_within_150m: Optional[int] = None


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass(frozen=True)
class Route:
    id: str  # "safest" | "fastest"
    label: str
    path_coordinates: list[LatLng]
    alpha_used: float
    metrics: RouteMetrics
    confidence: Confidence
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RouteResult:
    """對應 §4 引擎輸出 + §7 Google Maps URL"""

    selected_route_id: str
    routes: list[Route]  # 至少含 safest 與 fastest
    dynamic_hazards_considered: list[DynamicHazard]
    google_maps_url: str
    disclaimer: str


class ChatStatus(str, Enum):
    COLLECTING_INFO = "collecting_info"
    ROUTE_READY = "route_ready"
    ERROR = "error"


@dataclass(frozen=True)
class ChatResult:
    """對應 §6.2 /api/chat 回應"""

    status: ChatStatus
    reply_text: str
    route_result: Optional[RouteResult] = None
    error_code: Optional[str] = None


# ---------- 領域例外 ----------
# 這些是「業務邏輯失敗」，依 §6.5 最終會被轉成 HTTP 200 + status: "error"，
# 只有 SessionNotFoundError 屬於系統層級（HTTP 404）。


class SessionNotFoundError(Exception):
    """session_id 不存在或已過期時，由 handle_message 拋出。

    Session 生命週期（建立、對話歷史、dynamic hazards 暫存、過期清除）
    完全由 ChatService 實作自行管理（記憶體 dict + TTL，§6.6）；API Router
    端不維護自己的 session registry，只單純轉呼叫 create_session /
    handle_message，接到這個例外時轉成 HTTP 404 SESSION_NOT_FOUND。
    """


class OutOfCoverageError(Exception):
    """起訖點超出路網覆蓋範圍（§4.7 / §9.1）。不做外插，直接失敗。"""


class NoRouteFoundError(Exception):
    """起訖點都在覆蓋範圍內，但在路網圖上不連通。"""


# ---------- ABC #1：API Router ⇄ Gemini 邊界 ----------
# 由 Dev B 實作；Dev A 的路由層只依賴這個介面，不需知道 Gemini 細節。


class ChatService(ABC):

    @abstractmethod
    async def create_session(self, user_location: Optional[LatLng] = None) -> str:
        """建立新的對話 session，回傳 session_id。"""
        raise NotImplementedError

    @abstractmethod
    async def handle_message(
        self,
        session_id: str,
        message: str,
        user_location: Optional[LatLng] = None,
        priority_alpha: Optional[float] = None,
    ) -> ChatResult:
        """處理一則使用者訊息（內部含 Gemini 對話與 Function Calling），
        回傳 collecting_info / route_ready / error 三種結果之一。
        session_id 不存在時 raise SessionNotFoundError。

        priority_alpha（安全優先權重）由前端 UI 直接提供，不經 Gemini 判斷；
        未帶時使用預設值（見實作，AGENTS.md §5.1）。"""
        raise NotImplementedError


# ---------- ABC #2：Gemini ⇄ 路徑計算/資料層 邊界 ----------
# 由 Dev A 實作；Dev B 的 Function Calling handler 只依賴這個介面。


class RouteEngine(ABC):

    @abstractmethod
    async def geocode(
        self,
        place_description: str,
        bias: Optional[LatLng] = None,
    ) -> Optional[LatLng]:
        """文字地點轉座標（§9.1）。bias 用於偏向使用者附近的結果。
        查無結果回傳 None。"""
        raise NotImplementedError

    @abstractmethod
    async def calculate_route(
        self,
        origin: LatLng,
        destination: LatLng,
        priority_alpha: float = 0.6,
        dynamic_hazards: Sequence[DynamicHazard] = (),
    ) -> RouteResult:
        """§4 安全路徑計算 + §7 Google Maps URL 產生。
        內部同時算 α=0 的最快路線作為對照，兩條都放進 routes。
        起訖點超出路網覆蓋範圍時 raise OutOfCoverageError。"""
        raise NotImplementedError
