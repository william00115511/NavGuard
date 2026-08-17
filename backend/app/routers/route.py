"""POST /api/route/calculate（AGENTS.md §6.3，除錯／進階模式）。

不經過 Gemini，直接呼叫 RouteEngine，方便前後端分開測試，也可保留作日後
「進階模式」使用。§6.3：這個端點的 dynamic_hazards 直接吃座標，因為
geocoding 屬於 Function Calling handler 的職責，不屬於引擎。
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter

from app.data.store import get_data_store
from app.engine.route_engine import get_route_engine
from app.errors import NO_ROUTE_FOUND, OUT_OF_COVERAGE
from app.schemas import (
    DynamicHazardIn,
    RouteCalculateRequest,
    RouteCalculateResponse,
    hazard_to_out,
    selected_route_to_out,
)
from interfaces import DynamicHazard, NoRouteFoundError, OutOfCoverageError

router = APIRouter(prefix="/api", tags=["route"])

_FALLBACK_TTL_HOURS = 3.0


def _to_hazard(raw: DynamicHazardIn) -> DynamicHazard:
    """把 API 輸入補齊成內部型別；expires_at 只在這裡決定一次，後面不再重算。"""
    expires_at = raw.expires_at
    if expires_at is None:
        ttl_hours = raw.valid_hours
        if ttl_hours is None:
            category = get_data_store().categories.get(raw.category)
            ttl_hours = (category.default_ttl_hours if category else None) or _FALLBACK_TTL_HOURS
        expires_at = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)
    elif expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    return DynamicHazard(
        category=raw.category,
        location=raw.location.to_latlng(),
        summary=raw.summary,
        expires_at=expires_at,
        confidence=raw.confidence,
        source_url=raw.source_url,
    )


@router.post(
    "/route/calculate", response_model=RouteCalculateResponse, response_model_exclude_none=True
)
async def calculate_route(request: RouteCalculateRequest) -> RouteCalculateResponse:
    try:
        result = await get_route_engine().calculate_route(
            origin=request.origin.to_latlng(),
            destination=request.destination.to_latlng(),
            priority_alpha=request.priority_alpha,
            dynamic_hazards=[_to_hazard(h) for h in request.dynamic_hazards],
        )
    except OutOfCoverageError as exc:
        # §6.5：業務邏輯失敗回 HTTP 200 + status error，請求本身是有效的。
        return RouteCalculateResponse(status="error", error_code=OUT_OF_COVERAGE, message=str(exc))
    except NoRouteFoundError as exc:
        return RouteCalculateResponse(status="error", error_code=NO_ROUTE_FOUND, message=str(exc))

    return RouteCalculateResponse(
        status="ok",
        route=selected_route_to_out(result),
        dynamic_hazards_considered=[hazard_to_out(h) for h in result.dynamic_hazards_considered],
        google_maps_url=result.google_maps_url,
        disclaimer=result.disclaimer,
    )
