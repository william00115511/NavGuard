"""HTTP API 的 Pydantic request/response models（AGENTS.md §6）。

這一層只做「內部 dataclass ⇄ JSON」的轉換，不含任何業務邏輯：所有數值都由
引擎算好，這裡不重算（§1 原則 2 的同一個精神——單一計算來源）。
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from interfaces import DynamicHazard, LatLng, Route, RouteMetrics, RouteResult, RouteWarning


class LatLngIn(BaseModel):
    lat: float = Field(ge=-90.0, le=90.0)
    lng: float = Field(ge=-180.0, le=180.0)

    def to_latlng(self) -> LatLng:
        return LatLng(lat=self.lat, lng=self.lng)


# ---------- §6.2 POST /api/chat ----------


class ChatRequest(BaseModel):
    # 前端自行產生並持久化的識別碼（例如裝置安裝時建立一次的 UUID）；不需要
    # 先呼叫任何「建立 session」端點交換 ID（§6.6 修訂）。
    client_id: str = Field(min_length=1)
    message: str
    user_location: Optional[LatLngIn] = None
    # 安全優先權重，由前端 UI（例如滑桿）提供；不經 Gemini 判斷（AGENTS.md §5.1）。
    # 選填，未帶時後端使用預設值。
    priority_alpha: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class DynamicHazardOut(BaseModel):
    category: str
    summary: str
    confidence: float
    expires_at: datetime
    source_url: Optional[str] = None


class RouteWarningOut(BaseModel):
    """結構化警告碼，前端依 code 自行決定文案／樣式，不解析後端組好的中文句子。"""

    code: str
    category: Optional[str] = None
    summary: Optional[str] = None


class RouteMetricsOut(BaseModel):
    distance_m: float
    duration_min_est: float
    avg_safety_score: float
    passed_landmarks: dict[str, int]
    detour_vs_fastest_min: float
    data_coverage: list[str]
    # 沒有該類資料時是 null，不是 0（§1 原則 3）。
    lit_coverage_ratio: Optional[float] = None
    help_points_within_50m: Optional[int] = None
    police_within_150m: Optional[int] = None


class RouteOut(BaseModel):
    path_coordinates: list[list[float]]  # [[lat, lng], ...]，前端直接畫 polyline
    alpha_used: float
    confidence: str
    metrics: RouteMetricsOut
    warnings: list[RouteWarningOut]


class ChatResponse(BaseModel):
    status: str
    # 只有 collecting_info（追問）與 error（錯誤說明）才有值；route_ready 不帶
    # reply_text，路線結果改由下面的結構化欄位表達（§5.1 修訂）。
    reply_text: Optional[str] = None
    error_code: Optional[str] = None
    # 以下只有 status == "route_ready" 時才有值。
    disclaimer: Optional[str] = None
    route: Optional[RouteOut] = None
    dynamic_hazards_considered: Optional[list[DynamicHazardOut]] = None
    google_maps_url: Optional[str] = None


# ---------- POST /api/chat/clear ----------


class ClearContextRequest(BaseModel):
    client_id: str = Field(min_length=1)


class ClearContextResponse(BaseModel):
    status: str = "ok"


# ---------- §6.3 POST /api/route/calculate ----------


class DynamicHazardIn(BaseModel):
    """§6.3：這個端點直接吃座標，geocoding 屬於 Function Calling handler 的職責。

    effect 不在這裡指定——正負面一律查 categories.json（§5.4 規則 1）。
    """

    category: str
    location: LatLngIn
    summary: str = ""
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    expires_at: Optional[datetime] = None
    valid_hours: Optional[float] = Field(default=None, gt=0.0)
    source_url: Optional[str] = None


class RouteCalculateRequest(BaseModel):
    origin: LatLngIn
    destination: LatLngIn
    priority_alpha: float = Field(default=0.6, ge=0.0, le=1.0)
    dynamic_hazards: list[DynamicHazardIn] = []


class RouteCalculateResponse(BaseModel):
    """成功時是 §6.2 的 route 部分；業務失敗時依 §6.5 仍是 HTTP 200 + status error。"""

    status: str
    error_code: Optional[str] = None
    message: Optional[str] = None
    route: Optional[RouteOut] = None
    dynamic_hazards_considered: Optional[list[DynamicHazardOut]] = None
    google_maps_url: Optional[str] = None
    disclaimer: Optional[str] = None


# ---------- 轉換 ----------


def metrics_to_out(metrics: RouteMetrics) -> RouteMetricsOut:
    return RouteMetricsOut(**vars(metrics))


def warning_to_out(warning: RouteWarning) -> RouteWarningOut:
    return RouteWarningOut(code=warning.code, category=warning.category, summary=warning.summary)


def route_to_out(route: Route) -> RouteOut:
    return RouteOut(
        path_coordinates=[[p.lat, p.lng] for p in route.path_coordinates],
        alpha_used=route.alpha_used,
        confidence=route.confidence.value,
        metrics=metrics_to_out(route.metrics),
        warnings=[warning_to_out(w) for w in route.warnings],
    )


def hazard_to_out(hazard: DynamicHazard) -> DynamicHazardOut:
    return DynamicHazardOut(
        category=hazard.category,
        summary=hazard.summary,
        confidence=hazard.confidence,
        expires_at=hazard.expires_at,
        source_url=hazard.source_url,
    )


def selected_route_to_out(result: RouteResult) -> RouteOut:
    """§4/§6.2 修訂：引擎內部仍算兩條路線（供 detour 對照與 §7 URL 簡化用），
    但 API 只對外回傳前端安全參數（priority_alpha）對應的那一條。"""
    selected = next(r for r in result.routes if r.id == result.selected_route_id)
    return route_to_out(selected)
