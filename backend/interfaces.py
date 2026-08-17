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
class NearbyPoint:
    """沿路線附近的一個具體點位（警局／可求助據點／危險點位），供前端直接畫 marker。

    只帶顯示需要的欄位，不是完整 PointRecord（source/confidence 等對前端沒意義）。
    place_id 直接沿用 PointRecord.place_id（Google 來源是真實 place_id，
    其餘類別是資料轉換腳本產生的穩定字串）。summary 只有 danger_zones 會用到
    （危險原因說明），其餘類別是 None。
    """

    place_id: str
    lat: float
    lng: float
    name: Optional[str] = None
    summary: Optional[str] = None


@dataclass(frozen=True)
class RouteWarning:
    """Structured warning so the frontend can localize/style without parsing backend prose.

    `category` is set for missing-data-coverage and unknown-hazard-category warnings;
    `summary` is set for expired-hazard warnings (carries the hazard's own summary text).
    """

    code: str
    category: Optional[str] = None
    summary: Optional[str] = None


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
    # 警局／可求助據點／危險點位沿線已有具體點位列表（見下方三欄），不再另外
    # 統計數量；需要數量時前端自己 len()。None 代表該類別在此區無資料
    # （§1 原則 3），不是 0 筆。三者也因此都排除在 passed_landmarks 之外。
    police_stations: Optional[list[NearbyPoint]] = None
    convenience_stores: Optional[list[NearbyPoint]] = None
    danger_zones: Optional[list[NearbyPoint]] = None


@dataclass(frozen=True)
class Route:
    id: str  # "safest" | "fastest"
    label: str
    path_coordinates: list[LatLng]
    alpha_used: float
    metrics: RouteMetrics


@dataclass(frozen=True)
class RouteResult:
    """對應 §4 引擎輸出 + §7 Google Maps URL。

    warnings 是跟 routes 平行的頂層欄位（§4.7／§6.2 修訂），不掛在個別
    Route 底下——兩條路線（safest／fastest）本來就共用同一份資料覆蓋狀況，
    沒有理由各自帶一份重複的 warnings。
    """

    selected_route_id: str
    routes: list[Route]  # 至少含 safest 與 fastest
    dynamic_hazards_considered: list[DynamicHazard]
    google_maps_url: str
    disclaimer: str
    warnings: list[RouteWarning] = field(default_factory=list)


class ChatStatus(str, Enum):
    COLLECTING_INFO = "collecting_info"
    ROUTE_READY = "route_ready"
    ERROR = "error"


@dataclass(frozen=True)
class ChatResult:
    """對應 §6.2 /api/chat 回應。

    reply_text is only set for collecting_info（追問）與 error（錯誤說明）——
    這兩種是天生需要對話式文字的情境。route_ready 不帶 reply_text：路線結果
    改由 route_result 內的結構化資料表達，前端自己組文案（見 §5.1 修訂）。
    """

    status: ChatStatus
    reply_text: Optional[str] = None
    route_result: Optional[RouteResult] = None
    error_code: Optional[str] = None


# ---------- 領域例外 ----------
# 這些是「業務邏輯失敗」，依 §6.5 最終會被轉成 HTTP 200 + status: "error"。


class OutOfCoverageError(Exception):
    """起訖點超出路網覆蓋範圍（§4.7 / §9.1）。不做外插，直接失敗。"""


class NoRouteFoundError(Exception):
    """起訖點都在覆蓋範圍內，但在路網圖上不連通。"""


# 這是系統層級錯誤（§6.5），不是業務邏輯失敗：session_id 從未存在，或已經
# 過期／被定時回收清掉。前端收到後應重新呼叫 create_session 換一個新的
# session_id，不是重試同一個請求。
class SessionNotFoundError(Exception):
    """session_id 不存在或已過期（§6.1）。"""


# ---------- ABC #1：API Router ⇄ Gemini 邊界 ----------
# 由 Dev B 實作；Dev A 的路由層只依賴這個介面，不需知道 Gemini 細節。


class ChatService(ABC):

    @abstractmethod
    async def create_session(self, user_location: Optional[LatLng] = None) -> str:
        """建立一個新的對話 session，回傳後端配發的 session_id（AGENTS.md §6.1）。

        前端必須先呼叫這個端點換到 session_id，才能開始 /api/chat 對話；
        session_id 不是前端自行產生的。"""
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

        session_id 必須是先前 create_session() 配發過的值。不存在或已經
        過期（超過 TTL 未使用，或已被定時回收）時 raise
        SessionNotFoundError，由呼叫方轉成系統層級 404（§6.5）；前端收到後
        應重新呼叫 create_session 換一個新的 session_id，不是重試原請求。

        priority_alpha（安全優先權重）由前端 UI 直接提供，不經 Gemini 判斷；
        未帶時使用預設值（見實作，AGENTS.md §5.1）。"""
        raise NotImplementedError

    @abstractmethod
    async def clear_context(self, session_id: str) -> None:
        """清除這個 session_id 的對話歷史，下一則訊息視為全新對話的開始。

        對不存在的 session_id 呼叫是安全的（no-op），不視為錯誤——這是使用者
        主動要求的清空動作，不需要 session 已存在才能執行。"""
        raise NotImplementedError

    @abstractmethod
    async def reap_expired_sessions(self) -> int:
        """定時回收已過期（超過 TTL 未使用）的 session，釋放記憶體（§6.6）。

        由背景排程呼叫，不是使用者觸發的動作；回傳這次實際回收掉的 session
        數量，供 log／監控使用。"""
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
