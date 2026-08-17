"""LocalDataRouteEngine：串起 geocode + 資料層 + 路徑引擎 + Google Maps URL。

實作 inner_interface.RouteEngine，是 Dev B 的 Function Calling 處理常式
唯一需要依賴的介面；Dev B 開發階段可先用假物件（FakeRouteEngine）代替。
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Sequence

from app.data.schema import CategoryConfig, PointRecord
from app.data.store import DataStore, get_data_store
from app.engine.graph import RoadGraph, get_road_graph
from app.engine.pathfinding import compute_path
from app.engine.safety import filter_active_points
from app.errors import RouteNotFoundError
from app.geocoding.nominatim import geocode_with_nominatim
from app.maps.url_builder import build_google_maps_url
from inner_interface import DynamicHazard, LatLng, RouteEngine, RouteResult

_DEFAULT_HAZARD_TTL_HOURS = 6.0


def _hazard_to_point(hazard: DynamicHazard, categories: dict[str, CategoryConfig]) -> PointRecord:
    """把 Gemini 回報的 DynamicHazard（4.2b節）轉成統一點位格式（2.2/2.4節）。"""
    category = categories.get(hazard.category)
    ttl_hours = hazard.valid_hours
    if ttl_hours is None:
        ttl_hours = category.default_ttl_hours if category else _DEFAULT_HAZARD_TTL_HOURS
    expires_at = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)

    return PointRecord(
        id=f"dynamic_{uuid.uuid4().hex[:8]}",
        category=hazard.category,
        lat=hazard.location.lat,
        lng=hazard.location.lng,
        source="gemini_realtime_search",
        source_type="dynamic_realtime",
        expires_at=expires_at.isoformat(),
        confidence=hazard.confidence,
        meta={"summary": hazard.summary},
    )


class LocalDataRouteEngine(RouteEngine):
    def __init__(self, data_store: Optional[DataStore] = None, graph: Optional[RoadGraph] = None):
        self._data_store = data_store or get_data_store()
        self._graph = graph or get_road_graph()

    def geocode(self, place_description: str) -> Optional[LatLng]:
        return geocode_with_nominatim(place_description)

    def calculate_route(
        self,
        origin: LatLng,
        destination: LatLng,
        priority_alpha: float,
        dynamic_hazards: Sequence[DynamicHazard] = (),
    ) -> RouteResult:
        categories = self._data_store.categories

        dynamic_points = [_hazard_to_point(h, categories) for h in dynamic_hazards]
        active_dynamic_points = filter_active_points(dynamic_points)
        active_ids = {p.id for p in active_dynamic_points}

        all_points = filter_active_points(self._data_store.static_points) + active_dynamic_points

        origin_node = self._graph.nearest_node(origin)
        destination_node = self._graph.nearest_node(destination)

        computation = compute_path(
            self._graph, origin_node, destination_node, all_points, categories, priority_alpha
        )
        if computation is None:
            raise RouteNotFoundError(f"起訖點在路網圖上不連通：{origin} -> {destination}")

        considered_hazards = [
            hazard
            for hazard, point in zip(dynamic_hazards, dynamic_points)
            if point.id in active_ids
        ]

        return RouteResult(
            path_coordinates=computation.path_coordinates,
            distance_m=computation.distance_m,
            avg_safety_score=computation.avg_safety_score,
            alpha_used=priority_alpha,
            passed_landmarks=computation.passed_landmarks,
            dynamic_hazards_considered=considered_hazards,
            google_maps_url=build_google_maps_url(computation.path_coordinates),
        )
