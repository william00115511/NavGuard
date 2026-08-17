"""HTTP API 的 Pydantic request/response models（ForAI.md 4.5節）。"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from pydantic import BaseModel, Field

from inner_interface import RouteResult


class CreateSessionResponse(BaseModel):
    session_id: str
    created_at: datetime


class ChatRequest(BaseModel):
    session_id: str
    message: str


class LatLngIn(BaseModel):
    lat: float
    lng: float


class DynamicHazardIn(BaseModel):
    location_description: Optional[str] = None
    location: Optional[LatLngIn] = None
    category: str
    effect: str
    confidence: float = 1.0
    valid_hours: Optional[float] = None
    summary: str = ""


class DynamicHazardOut(BaseModel):
    category: str
    summary: str
    expires_at: Optional[str] = None


class RouteOut(BaseModel):
    distance_m: float
    avg_safety_score: float
    alpha_used: float
    passed_landmarks: dict[str, int]
    dynamic_hazards_considered: list[DynamicHazardOut]
    google_maps_url: str


class ChatResponse(BaseModel):
    session_id: str
    status: str
    reply_text: str
    route: Optional[RouteOut] = None
    error_code: Optional[str] = None


class RouteCalculateRequest(BaseModel):
    origin: LatLngIn
    destination: LatLngIn
    priority_alpha: float = Field(ge=0.0, le=1.0)
    dynamic_hazards: list[DynamicHazardIn] = []


def route_result_to_out(route: RouteResult) -> RouteOut:
    now = datetime.now(timezone.utc)
    hazards_out = []
    for hazard in route.dynamic_hazards_considered:
        expires_at = None
        if hazard.valid_hours is not None:
            expires_at = (now + timedelta(hours=hazard.valid_hours)).isoformat()
        hazards_out.append(
            DynamicHazardOut(category=hazard.category, summary=hazard.summary, expires_at=expires_at)
        )

    return RouteOut(
        distance_m=route.distance_m,
        avg_safety_score=route.avg_safety_score,
        alpha_used=route.alpha_used,
        passed_landmarks=route.passed_landmarks,
        dynamic_hazards_considered=hazards_out,
        google_maps_url=route.google_maps_url,
    )
