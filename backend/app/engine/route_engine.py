"""LocalDataRouteEngine：串起 geocode + 資料層 + 路徑引擎 + Google Maps URL。

實作 inner_interface.RouteEngine（AGENTS.md §8.2），是 Dev B 的 Function
Calling 處理常式唯一需要依賴的介面；Dev B 開發階段可先用假物件
（FakeRouteEngine）代替。

每次請求的流程（§4.1）：
    取本次未過期的動態點位 → 只對受影響的 edge 疊加動態分數（靜態分數啟動時
    已快取）→ sigmoid 正規化 → 依 α 合成 edge cost → A* 找最低成本路徑
    → 同時以 α=0 算一條最快路線作為對照 → 回傳兩條路線 + metrics
    + reasons + warnings
"""

import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Optional, Sequence

from app.config import DEFAULT_ALPHA, DISCLAIMER, FALLBACK_DYNAMIC_CATEGORY, WALK_SPEED_MPS
from app.data.schema import CategoryConfig, PointRecord
from app.data.store import DataStore, get_data_store
from app.engine.graph import RoadGraph, get_road_graph
from app.engine.metrics import build_metrics, build_reasons, decide_confidence
from app.engine.pathfinding import PathComputation, compute_path
from app.engine.safety import EdgeSafetyIndex, build_scoring_profile, filter_active_points
from app.geocoding.nominatim import geocode_with_nominatim
from app.maps.url_builder import build_google_maps_url
from inner_interface import (
    DynamicHazard,
    LatLng,
    NoRouteFoundError,
    Route,
    RouteEngine,
    RouteResult,
)

_FALLBACK_TTL_HOURS = 3.0
_SAFEST_ROUTE_ID = "safest"
_FASTEST_ROUTE_ID = "fastest"


class LocalDataRouteEngine(RouteEngine):
    def __init__(self, data_store: Optional[DataStore] = None, graph: Optional[RoadGraph] = None):
        self._data_store = data_store or get_data_store()
        self._graph = graph or get_road_graph()
        self._static_points = filter_active_points(self._data_store.static_points)
        self._profile = build_scoring_profile(self._data_store.categories, self._static_points)
        # 靜態分數只在這裡算一次，之後每次請求只疊加動態點位（§4.1）。
        self._index = EdgeSafetyIndex(self._graph, self._static_points, self._profile)

    async def geocode(
        self,
        place_description: str,
        bias: Optional[LatLng] = None,
    ) -> Optional[LatLng]:
        return await geocode_with_nominatim(place_description, bias=bias)

    async def calculate_route(
        self,
        origin: LatLng,
        destination: LatLng,
        priority_alpha: float = DEFAULT_ALPHA,
        dynamic_hazards: Sequence[DynamicHazard] = (),
    ) -> RouteResult:
        alpha = min(1.0, max(0.0, priority_alpha))

        # 覆蓋範圍檢查先做：超出範圍時 nearest_node 會 raise OutOfCoverageError，
        # 不必先花時間算分數（§4.7）。
        origin_node = self._graph.nearest_node(origin)
        destination_node = self._graph.nearest_node(destination)

        considered, dynamic_points, hazard_warnings = self._prepare_hazards(dynamic_hazards)
        safety_by_edge = self._index.safety_scores(dynamic_points)
        all_points = self._static_points + dynamic_points

        fastest = compute_path(self._graph, safety_by_edge, origin_node, destination_node, alpha=0.0)
        safest = compute_path(self._graph, safety_by_edge, origin_node, destination_node, alpha=alpha)
        if fastest is None or safest is None:
            raise NoRouteFoundError(f"起訖點在路網圖上不連通：{origin_node} -> {destination_node}")

        warnings = self._profile.warnings + hazard_warnings
        confidence = decide_confidence(self._profile, all_points)

        fastest_metrics = build_metrics(fastest, all_points, self._profile, detour_vs_fastest_min=0.0)
        detour_min = (safest.distance_m - fastest.distance_m) / WALK_SPEED_MPS / 60
        safest_metrics = build_metrics(safest, all_points, self._profile, detour_min)

        routes = [
            Route(
                id=_SAFEST_ROUTE_ID,
                label="推薦的較安全路線",
                path_coordinates=safest.path_coordinates,
                alpha_used=alpha,
                metrics=safest_metrics,
                confidence=confidence,
                reasons=build_reasons(safest_metrics, fastest_metrics, self._profile),
                warnings=list(warnings),
            ),
            Route(
                id=_FASTEST_ROUTE_ID,
                label="最快路線",
                path_coordinates=fastest.path_coordinates,
                alpha_used=0.0,
                metrics=fastest_metrics,
                confidence=confidence,
                reasons=[],
                warnings=list(warnings),
            ),
        ]

        return RouteResult(
            selected_route_id=_SAFEST_ROUTE_ID,
            routes=routes,
            dynamic_hazards_considered=considered,
            google_maps_url=self._build_maps_url(safest, fastest),
            disclaimer=DISCLAIMER,
        )

    def _build_maps_url(self, safest: PathComputation, fastest: PathComputation) -> str:
        # §7.3：用最快路線當參考，強制保留安全路線偏離它最遠的轉折點，
        # 否則 Google Maps 會把簡化後的中繼點又連回原本的最短路線。
        return build_google_maps_url(safest.path_coordinates, fastest.path_coordinates)

    def _prepare_hazards(
        self, hazards: Sequence[DynamicHazard]
    ) -> tuple[list[DynamicHazard], list[PointRecord], list[str]]:
        """套用 §5.4 的後端處理規則，回傳（實際採用的 hazard、點位、warnings）。"""
        categories = self._data_store.categories
        warnings: list[str] = []
        considered: list[DynamicHazard] = []
        points: list[PointRecord] = []
        seen: set[tuple[str, float, float]] = set()
        now = datetime.now(timezone.utc)

        for hazard in hazards:
            resolved, warning = self._resolve_category(hazard, categories)
            if warning:
                warnings.append(warning)

            # 規則 5：同一批回報中重複的同地點同類別去重（約 1 公尺精度）。
            key = (resolved.category, round(resolved.location.lat, 5), round(resolved.location.lng, 5))
            if key in seen:
                continue
            seen.add(key)

            if resolved.expires_at <= now:
                warnings.append(f"即時資訊「{resolved.summary}」已過期，未納入本次計算")
                continue

            considered.append(resolved)
            points.append(self._hazard_to_point(resolved))

        return considered, points, warnings

    def _resolve_category(
        self, hazard: DynamicHazard, categories: dict[str, CategoryConfig]
    ) -> tuple[DynamicHazard, Optional[str]]:
        """規則 1/2：effect 一律查 categories.json；未知類別退回 dynamic_unknown。"""
        category = categories.get(hazard.category)
        if category is not None and category.kind == "dynamic":
            return hazard, None

        fallback = categories.get(FALLBACK_DYNAMIC_CATEGORY)
        ttl_hours = fallback.default_ttl_hours if fallback else _FALLBACK_TTL_HOURS
        resolved = replace(
            hazard,
            category=FALLBACK_DYNAMIC_CATEGORY,
            expires_at=min(
                hazard.expires_at,
                datetime.now(timezone.utc) + timedelta(hours=ttl_hours or _FALLBACK_TTL_HOURS),
            ),
        )
        return resolved, (
            f"即時事件類別「{hazard.category}」未登記在資料設定中，"
            f"已以低權重的未分類事件計入"
        )

    def _hazard_to_point(self, hazard: DynamicHazard) -> PointRecord:
        """把 DynamicHazard（§3.4 / §5.4）轉成統一點位格式（§3.2）。"""
        return PointRecord(
            id=f"dynamic_{uuid.uuid4().hex[:8]}",
            category=hazard.category,
            lat=hazard.location.lat,
            lng=hazard.location.lng,
            source=hazard.source_url or "gemini_realtime_search",
            source_type="dynamic_realtime",
            expires_at=hazard.expires_at.isoformat(),
            confidence=min(1.0, max(0.0, hazard.confidence)),
            meta={"summary": hazard.summary},
        )


_engine: LocalDataRouteEngine | None = None


def get_route_engine() -> LocalDataRouteEngine:
    """Process 內單例：靜態 edge 分數只算一次，之後所有請求共用（§4.1）。"""
    global _engine
    if _engine is None:
        _engine = LocalDataRouteEngine()
    return _engine
