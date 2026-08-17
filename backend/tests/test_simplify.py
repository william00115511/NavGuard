import pytest

from app.maps.simplify import (
    distance_to_polyline,
    simplify_preserving_divergence,
    simplify_to_max_points,
)
from app.maps.url_builder import MAX_WAYPOINTS, build_google_maps_url
from inner_interface import LatLng


def _zigzag(count: int) -> list[LatLng]:
    return [LatLng(lat=i * 0.0001, lng=(0.00001 if i % 2 == 0 else -0.00001)) for i in range(count)]


def test_short_path_untouched():
    points = [LatLng(lat=0, lng=0), LatLng(lat=1, lng=1), LatLng(lat=2, lng=2)]
    assert simplify_to_max_points(points, max_points=9) == points


def test_long_noisy_path_is_bounded():
    points = _zigzag(200)
    simplified = simplify_to_max_points(points, max_points=9)
    assert len(simplified) <= 9
    assert simplified[0] == points[0]
    assert simplified[-1] == points[-1]


def test_divergence_point_survives_simplification():
    """§7.3：偏離最快路線最遠的轉折點必須被保留，否則 Google Maps 會導回原路。"""
    reference = [LatLng(lat=i * 0.001, lng=0.0) for i in range(41)]
    detour = list(reference)
    detour[20] = LatLng(lat=0.020, lng=0.004)  # 刻意繞開的那個轉彎

    simplified = simplify_preserving_divergence(detour, reference, max_points=11)
    assert len(simplified) <= 11
    assert detour[20] in simplified


def test_divergence_simplification_is_bounded_and_keeps_endpoints():
    points = _zigzag(200)
    reference = [LatLng(lat=i * 0.0001, lng=0.0) for i in range(200)]
    simplified = simplify_preserving_divergence(points, reference, max_points=11)
    assert len(simplified) <= 11
    assert simplified[0] == points[0]
    assert simplified[-1] == points[-1]


def test_distance_to_polyline_clamps_to_segment_ends():
    polyline = [LatLng(lat=0.0, lng=0.0), LatLng(lat=0.0, lng=1.0)]
    # 落在線段延長線上的點，距離要以端點計算，不能算成 0
    assert distance_to_polyline(LatLng(lat=0.0, lng=2.0), polyline) == pytest.approx(1.0)


def test_google_maps_url_never_exceeds_max_waypoints():
    url = build_google_maps_url(_zigzag(200))
    assert url.startswith("https://www.google.com/maps/dir/?api=1")
    assert "travelmode=walking" in url
    if "waypoints=" in url:
        waypoints_param = url.split("waypoints=")[1].split("&")[0]
        assert waypoints_param.count("|") + 1 <= MAX_WAYPOINTS


def test_single_point_path_builds_a_valid_url():
    """起訖點吸附到同一個節點不是錯誤，不該讓整個請求失敗。"""
    url = build_google_maps_url([LatLng(lat=25.0, lng=121.5)])
    assert "origin=25.0,121.5" in url and "destination=25.0,121.5" in url


def test_empty_path_is_rejected():
    with pytest.raises(ValueError):
        build_google_maps_url([])
