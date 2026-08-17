import pytest

from app.config import EDGE_SAMPLE_INTERVAL_M
from app.data.schema import CategoryConfig, PointRecord
from app.engine.geo import haversine_m, sample_along
from app.engine.graph import Edge
from app.engine.metrics import build_metrics
from app.engine.safety import build_scoring_profile, raw_edge_score, sigmoid_safety
from evaluation.path_scoring import path_computation_from_polyline
from interfaces import LatLng

_CATEGORIES = {
    "street_light": CategoryConfig(
        name="street_light", effect="positive", weight=1.0, radius_m=30, kind="static", label="路燈"
    ),
    "danger_zone": CategoryConfig(
        name="danger_zone", effect="negative", weight=2.0, radius_m=80, kind="static", label="風險區域"
    ),
}


def _point(point_id: str, category: str, lat: float = 0.0, lng: float = 0.0, **kwargs) -> PointRecord:
    return PointRecord(
        place_id=point_id, category=category, lat=lat, lng=lng, source="test", source_type="static_local", **kwargs
    )


def test_single_segment_matches_public_raw_edge_score():
    """path_scoring.py 自己重建的空間網格索引，算出來的分數要跟 safety.py
    公開、未經索引優化的 raw_edge_score 完全一致——索引只是效能優化，不能
    改變任何分數。"""
    points = [
        _point("l1", "street_light", lat=0.0001, lng=0.0),
        _point("d1", "danger_zone", lat=-0.0002, lng=0.0),
    ]
    profile = build_scoring_profile(_CATEGORIES, points)
    a = LatLng(lat=0.0, lng=0.0)
    b = LatLng(lat=0.0005, lng=0.0)

    computation = path_computation_from_polyline([a, b], points, profile)

    edge = Edge(from_id="g0", to_id="g1", distance_m=haversine_m(a, b), samples=sample_along(a, b, EDGE_SAMPLE_INTERVAL_M))
    expected_safety = sigmoid_safety(raw_edge_score(edge, points, profile))

    assert computation.distance_m == pytest.approx(haversine_m(a, b))
    assert computation.avg_safety_score == pytest.approx(expected_safety, abs=1e-9)
    assert computation.path_coordinates == [a, b]


def test_danger_zone_lowers_safety_score():
    """驗收清單：Google 路線多繞過一個危險點位時，分數應該要更低——用固定的
    profile 只改點位集合，跟 test_safety.py 的 test_adding_danger_point_lowers_score
    同款寫法。"""
    base = [
        _point("l1", "street_light", lat=0.0002, lng=0.0),
        _point("d1", "danger_zone", lat=1.0, lng=1.0),
    ]
    profile = build_scoring_profile(_CATEGORIES, base)
    with_extra_danger = base + [_point("d2", "danger_zone", lat=0.0002, lng=0.0002)]
    polyline = [LatLng(lat=0.0, lng=0.0), LatLng(lat=0.0004, lng=0.0)]

    safer = path_computation_from_polyline(polyline, base, profile)
    less_safe = path_computation_from_polyline(polyline, with_extra_danger, profile)

    assert less_safe.avg_safety_score < safer.avg_safety_score


def test_build_metrics_reuses_same_function_as_engine():
    """整份評估子專案的核心主張：Google 路線的 PathComputation 可以原封不動
    丟進 app/engine/metrics.py 的 build_metrics()，拿到跟我們自己路線同構的
    metrics。"""
    points = [_point("l1", "street_light", lat=0.0001, lng=0.0001)]
    profile = build_scoring_profile(_CATEGORIES, points)
    polyline = [LatLng(lat=0.0, lng=0.0), LatLng(lat=0.0003, lng=0.0003)]

    computation = path_computation_from_polyline(polyline, points, profile)
    metrics = build_metrics(computation, points, profile, detour_vs_fastest_min=1.5)

    assert metrics.distance_m == pytest.approx(computation.distance_m, abs=0.1)
    assert 0.0 <= metrics.avg_safety_score <= 1.0
    assert metrics.passed_landmarks.get("street_light", 0) >= 1
    assert metrics.detour_vs_fastest_min == 1.5


def test_multi_vertex_polyline_builds_one_pseudo_edge_per_segment():
    points = [_point("l1", "street_light", lat=0.0001, lng=0.0001)]
    profile = build_scoring_profile(_CATEGORIES, points)
    polyline = [
        LatLng(lat=0.0, lng=0.0),
        LatLng(lat=0.0002, lng=0.0),
        LatLng(lat=0.0002, lng=0.0002),
    ]

    computation = path_computation_from_polyline(polyline, points, profile)

    assert len(computation.edges) == 2
    expected_distance = haversine_m(polyline[0], polyline[1]) + haversine_m(polyline[1], polyline[2])
    assert computation.distance_m == pytest.approx(expected_distance)
