"""路線層級的可解釋 metrics（AGENTS.md §4.6）。

這些是**報告用**指標，不參與路徑選擇。無資料的欄位一律填 None，不填 0
（§1 原則 3：沒有資料不等於沒有風險）。
"""

import math
from collections import defaultdict
from typing import Sequence

from app.config import (
    HELP_POINT_RADIUS_M,
    LIT_COVERAGE_RADIUS_M,
    POLICE_RADIUS_M,
    WALK_SPEED_MPS,
)
from app.data.schema import PointRecord
from app.engine.geo import haversine_m
from app.engine.pathfinding import PathComputation, path_sample_points
from app.engine.safety import ScoringProfile
from interfaces import LatLng, NearbyPoint, RouteMetrics

_LIT_CATEGORY = "street_light"
_HELP_CATEGORY = "help_point"
_POLICE_CATEGORY = "police_station"

_METERS_PER_DEGREE_LAT = 111_320.0

_GridBuckets = dict[tuple[int, int], list[PointRecord]]


def _points_of(points: Sequence[PointRecord], category: str) -> list[PointRecord]:
    return [p for p in points if p.category == category]


def _build_grid(points: Sequence[PointRecord], radius_m: float) -> tuple[_GridBuckets, float, float]:
    """把點位依 radius_m 大小分桶，避免每個取樣點都要跟全部點位算距離。

    格子邊長取自查詢半徑，所以取樣點所在格的 3x3 鄰域必定涵蓋半徑內的所有
    點位；鄰域外的點位不可能落在半徑內，可以直接跳過，不必算 haversine。
    """
    if not points:
        return {}, 1.0, 1.0
    ref_lat = sum(p.lat for p in points) / len(points)
    lat_deg = max(radius_m, 1.0) / _METERS_PER_DEGREE_LAT
    meters_per_deg_lng = _METERS_PER_DEGREE_LAT * max(math.cos(math.radians(ref_lat)), 1e-6)
    lng_deg = max(radius_m, 1.0) / meters_per_deg_lng
    buckets: _GridBuckets = defaultdict(list)
    for p in points:
        buckets[(math.floor(p.lat / lat_deg), math.floor(p.lng / lng_deg))].append(p)
    return buckets, lat_deg, lng_deg


def _nearby_points(sample: LatLng, buckets: _GridBuckets, lat_deg: float, lng_deg: float) -> list[PointRecord]:
    """取樣點所在網格週圍 3x3 格內的候選點位；仍需用 haversine 精確篩選半徑。"""
    gx = math.floor(sample.lat / lat_deg)
    gy = math.floor(sample.lng / lng_deg)
    candidates: list[PointRecord] = []
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            candidates.extend(buckets.get((gx + dx, gy + dy), ()))
    return candidates


def _count_near(samples: Sequence[LatLng], points: Sequence[PointRecord], radius_m: float) -> int:
    """沿線 radius_m 內的點位數（同一個點位只算一次）。"""
    return len(_records_near(samples, points, radius_m))


def _records_near(samples: Sequence[LatLng], points: Sequence[PointRecord], radius_m: float) -> list[PointRecord]:
    """沿線 radius_m 內的具體點位（同一個點位只算一次），供前端畫 marker 用。"""
    buckets, lat_deg, lng_deg = _build_grid(points, radius_m)
    hit_ids: set[str] = set()
    result: list[PointRecord] = []
    for s in samples:
        for p in _nearby_points(s, buckets, lat_deg, lng_deg):
            if p.id in hit_ids:
                continue
            if haversine_m(s, LatLng(lat=p.lat, lng=p.lng)) <= radius_m:
                hit_ids.add(p.id)
                result.append(p)
    return result


def _to_nearby_point(p: PointRecord) -> NearbyPoint:
    return NearbyPoint(id=p.id, lat=p.lat, lng=p.lng, name=p.meta.get("name"))


def _coverage_ratio(samples: Sequence[LatLng], points: Sequence[PointRecord], radius_m: float) -> float:
    """採樣點中，radius_m 內至少有一個該類點位的比例。"""
    if not samples:
        return 0.0
    buckets, lat_deg, lng_deg = _build_grid(points, radius_m)
    hit = sum(
        1
        for s in samples
        if any(
            haversine_m(s, LatLng(lat=p.lat, lng=p.lng)) <= radius_m
            for p in _nearby_points(s, buckets, lat_deg, lng_deg)
        )
    )
    return hit / len(samples)


def build_metrics(
    computation: PathComputation,
    points: Sequence[PointRecord],
    profile: ScoringProfile,
    detour_vs_fastest_min: float,
) -> RouteMetrics:
    samples = path_sample_points(computation.edges) or list(computation.path_coordinates)

    # 警局／可求助據點下面改回傳具體點位列表，不再在這裡重複統計數量
    # （§4.6 修訂：數量可由前端對列表 len()，不需要後端另外算一次）。
    passed_landmarks: dict[str, int] = {}
    for name, category in profile.categories.items():
        if name in (_POLICE_CATEGORY, _HELP_CATEGORY):
            continue
        count = _count_near(samples, _points_of(points, name), category.radius_m)
        if count:
            passed_landmarks[name] = count

    present = {p.category for p in points}
    data_coverage = [name for name in profile.categories if name in present]

    return RouteMetrics(
        distance_m=round(computation.distance_m, 1),
        duration_min_est=round(computation.distance_m / WALK_SPEED_MPS / 60, 1),
        avg_safety_score=round(computation.avg_safety_score, 3),
        passed_landmarks=passed_landmarks,
        detour_vs_fastest_min=round(detour_vs_fastest_min, 1),
        data_coverage=data_coverage,
        # 三個 metric 都遵守同一條規則：該類別沒有任何資料時回 None 而非 0／空陣列。
        lit_coverage_ratio=(
            round(_coverage_ratio(samples, _points_of(points, _LIT_CATEGORY), LIT_COVERAGE_RADIUS_M), 3)
            if _LIT_CATEGORY in present
            else None
        ),
        help_points=(
            [_to_nearby_point(p) for p in _records_near(samples, _points_of(points, _HELP_CATEGORY), HELP_POINT_RADIUS_M)]
            if _HELP_CATEGORY in present
            else None
        ),
        police_stations=(
            [_to_nearby_point(p) for p in _records_near(samples, _points_of(points, _POLICE_CATEGORY), POLICE_RADIUS_M)]
            if _POLICE_CATEGORY in present
            else None
        ),
    )
