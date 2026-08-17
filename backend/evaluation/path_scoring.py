"""把任意座標序列（例如 Google 路線解碼後的 polyline）轉成跟我們自己路徑
引擎輸出同構的 PathComputation，讓 app/engine/metrics.py 的既有
build_metrics() 可以原封不動拿來評分——這是整個評估子專案「兩條路線套用
同一套公式」的關鍵：底下用來算分的 point_contribution / sigmoid_safety
是 app/engine/safety.py 裡 EdgeSafetyIndex 用的同一批公開函式，沒有另外
重寫任何評分邏輯。

這裡唯一「新寫」的東西是空間網格索引（_build_category_grids /
_nearby_points），純粹是效能優化——街燈資料有三萬多筆，若每個取樣點都要
掃過全部點位，30 個測試案例跑下來會慢到無法接受。索引寫法對照
app/engine/safety.py 內部的 EdgeSafetyIndex（同款設計，該檔案裡是 private
函式，這裡照著相同公式在評估子專案自己重建一份），但完全不影響任何分數的
算法，也不影響 point_contribution 本身的計算結果。
"""

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Sequence

from app.config import EDGE_SAMPLE_INTERVAL_M
from app.data.schema import CategoryConfig, PointRecord
from app.engine.geo import haversine_m, sample_along
from app.engine.graph import Edge
from app.engine.pathfinding import PathComputation
from app.engine.safety import ScoringProfile, point_contribution, sigmoid_safety
from interfaces import LatLng

_METERS_PER_DEGREE_LAT = 111_320.0

_GridBuckets = dict[tuple[int, int], list[PointRecord]]


@dataclass(frozen=True)
class _CategoryGrid:
    buckets: _GridBuckets
    lat_deg: float
    lng_deg: float


def _build_grid(points: Sequence[PointRecord], radius_m: float) -> _CategoryGrid:
    if not points:
        return _CategoryGrid({}, 1.0, 1.0)
    ref_lat = sum(p.lat for p in points) / len(points)
    lat_deg = max(radius_m, 1.0) / _METERS_PER_DEGREE_LAT
    meters_per_deg_lng = _METERS_PER_DEGREE_LAT * max(math.cos(math.radians(ref_lat)), 1e-6)
    lng_deg = max(radius_m, 1.0) / meters_per_deg_lng
    buckets: _GridBuckets = defaultdict(list)
    for p in points:
        buckets[(math.floor(p.lat / lat_deg), math.floor(p.lng / lng_deg))].append(p)
    return _CategoryGrid(buckets, lat_deg, lng_deg)


def _nearby_points(sample: LatLng, grid: _CategoryGrid) -> list[PointRecord]:
    gx = math.floor(sample.lat / grid.lat_deg)
    gy = math.floor(sample.lng / grid.lng_deg)
    candidates: list[PointRecord] = []
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            candidates.extend(grid.buckets.get((gx + dx, gy + dy), ()))
    return candidates


def _build_category_grids(
    points: Sequence[PointRecord], categories: dict[str, CategoryConfig]
) -> dict[str, _CategoryGrid]:
    by_category: dict[str, list[PointRecord]] = defaultdict(list)
    for p in points:
        by_category[p.category].append(p)
    return {
        name: _build_grid(pts, categories[name].radius_m)
        for name, pts in by_category.items()
        if name in categories
    }


def _raw_edge_score_indexed(edge: Edge, grids: dict[str, _CategoryGrid], profile: ScoringProfile) -> float:
    total = 0.0
    for sample in edge.samples:
        for grid in grids.values():
            for point in _nearby_points(sample, grid):
                total += point_contribution(sample, point, profile)
    return total / len(edge.samples)


def _build_pseudo_edges(polyline: Sequence[LatLng]) -> list[Edge]:
    """把座標序列相鄰兩點串成一段段「偽 edge」，取樣方式跟
    RoadGraph.load() 建真正的圖 edge 時完全一樣（app/engine/graph.py），
    確保 Google 路線跟我們自己的路線用同樣密度取樣、同樣公式算分。"""
    edges: list[Edge] = []
    for i, (a, b) in enumerate(zip(polyline, polyline[1:])):
        edges.append(
            Edge(
                from_id=f"g{i}",
                to_id=f"g{i + 1}",
                distance_m=haversine_m(a, b),
                samples=sample_along(a, b, EDGE_SAMPLE_INTERVAL_M),
            )
        )
    return edges


def path_computation_from_polyline(
    polyline: Sequence[LatLng],
    points: Sequence[PointRecord],
    profile: ScoringProfile,
) -> PathComputation:
    """polyline 至少要有兩個點；呼叫端（google_routes_client 回傳的解碼結果）
    保證這件事，這裡不重複防禦已經在系統邊界驗證過的輸入。"""
    edges = _build_pseudo_edges(polyline)
    grids = _build_category_grids(points, profile.categories)

    total_distance = sum(e.distance_m for e in edges)
    if total_distance <= 0:
        # 起訖點幾乎重合（Google 偶爾對極短路程回傳單一線段、兩端點座標相同）。
        only_score = sigmoid_safety(_raw_edge_score_indexed(edges[0], grids, profile))
        return PathComputation(
            node_path=[],
            path_coordinates=list(polyline),
            edges=edges,
            distance_m=total_distance,
            avg_safety_score=only_score,
        )

    weighted_sum = sum(
        sigmoid_safety(_raw_edge_score_indexed(edge, grids, profile)) * edge.distance_m for edge in edges
    )

    return PathComputation(
        node_path=[],
        path_coordinates=list(polyline),
        edges=edges,
        distance_m=total_distance,
        avg_safety_score=weighted_sum / total_distance,
    )
