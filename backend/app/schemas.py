"""HTTP API 的 Pydantic request/response models（AGENTS.md §6）。

這一層只做「內部 dataclass ⇄ JSON」的轉換，不含任何業務邏輯：所有數值都由
引擎算好，這裡不重算（§1 原則 2 的同一個精神——單一計算來源）。
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from interfaces import DynamicHazard, LatLng, Route, RouteMetrics, RouteResult


class LatLngIn(BaseModel):
    lat: float = Field(ge=-90.0, le=90.0)
    lng: float = Field(ge=-180.0, le=180.0)

    def to_latlng(self) -> LatLng:
        return LatLng(lat=self.lat, lng=self.lng)


# ---------- §6.1 POST /api/session ----------


class CreateSessionRequest(BaseModel):
    # 選填，用於 geocoding bias 與 origin: "current_location" 的解析。
    user_location: Optional[LatLngIn] = None


class CreateSessionResponse(BaseModel):
    session_id: str
    created_at: datetime


# ---------- §6.2 POST /api/chat ----------


class ChatRequest(BaseModel):
    session_id: str
    message: str
    user_location: Optional[LatLngIn] = None


class DynamicHazardOut(BaseModel):
    category: str
    summary: str
    confidence: float
    expires_at: datetime
    source_url: Optional[str] = None


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
    id: str
    label: str
    path_coordinates: list[list[float]]  # [[lat, lng], ...]，前端直接畫 polyline
    alpha_used: float
    confidence: str
    metrics: RouteMetricsOut
    reasons: list[str]
    warnings: list[str]


class RouteResultOut(BaseModel):
    selected_route_id: str
    routes: list[RouteOut]
    dynamic_hazards_considered: list[DynamicHazardOut]
    google_maps_url: str
    disclaimer: str


class ChatResponse(BaseModel):
    session_id: str
    status: str
    reply_text: str
    error_code: Optional[str] = None
    # 以下只有 status == "route_ready" 時才有值。
    disclaimer: Optional[str] = None
    selected_route_id: Optional[str] = None
    routes: Optional[list[RouteOut]] = None
    dynamic_hazards_considered: Optional[list[DynamicHazardOut]] = None
    google_maps_url: Optional[str] = None


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
    """成功時是 §6.2 的 routes 部分；業務失敗時依 §6.5 仍是 HTTP 200 + status error。"""

    status: str
    error_code: Optional[str] = None
    message: Optional[str] = None
    selected_route_id: Optional[str] = None
    routes: Optional[list[RouteOut]] = None
    dynamic_hazards_considered: Optional[list[DynamicHazardOut]] = None
    google_maps_url: Optional[str] = None
    disclaimer: Optional[str] = None


# ---------- 轉換 ----------


def metrics_to_out(metrics: RouteMetrics) -> RouteMetricsOut:
    return RouteMetricsOut(**vars(metrics))


def route_to_out(route: Route) -> RouteOut:
    return RouteOut(
        id=route.id,
        label=route.label,
        path_coordinates=[[p.lat, p.lng] for p in route.path_coordinates],
        alpha_used=route.alpha_used,
        confidence=route.confidence.value,
        metrics=metrics_to_out(route.metrics),
        reasons=route.reasons,
        warnings=route.warnings,
    )


def hazard_to_out(hazard: DynamicHazard) -> DynamicHazardOut:
    return DynamicHazardOut(
        category=hazard.category,
        summary=hazard.summary,
        confidence=hazard.confidence,
        expires_at=hazard.expires_at,
        source_url=hazard.source_url,
    )


def route_result_to_out(result: RouteResult) -> RouteResultOut:
    return RouteResultOut(
        selected_route_id=result.selected_route_id,
        routes=[route_to_out(r) for r in result.routes],
        dynamic_hazards_considered=[hazard_to_out(h) for h in result.dynamic_hazards_considered],
        google_maps_url=result.google_maps_url,
        disclaimer=result.disclaimer,
    )
