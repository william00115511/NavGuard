"""POST /api/route/calculate（ForAI.md 4.5.3，除錯用路徑計算端點）。

不經過 Gemini，直接呼叫 RouteEngine，方便前後端分開測試，
也可保留作日後「進階模式」使用。
"""

from fastapi import APIRouter

from app.engine.route_engine import LocalDataRouteEngine
from app.schemas import RouteCalculateRequest, RouteOut, route_result_to_out
from inner_interface import DynamicHazard, LatLng

router = APIRouter(prefix="/api", tags=["route"])

_route_engine = LocalDataRouteEngine()


@router.post("/route/calculate", response_model=RouteOut)
async def calculate_route(request: RouteCalculateRequest) -> RouteOut:
    hazards = [
        DynamicHazard(
            category=h.category,
            location=LatLng(lat=h.location.lat, lng=h.location.lng),
            effect=h.effect,
            confidence=h.confidence,
            valid_hours=h.valid_hours,
            summary=h.summary,
        )
        for h in request.dynamic_hazards
        if h.location is not None
    ]

    route = _route_engine.calculate_route(
        origin=LatLng(lat=request.origin.lat, lng=request.origin.lng),
        destination=LatLng(lat=request.destination.lat, lng=request.destination.lng),
        priority_alpha=request.priority_alpha,
        dynamic_hazards=hazards,
    )
    return route_result_to_out(route)
