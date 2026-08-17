from app.maps.simplify import simplify_to_max_points
from app.maps.url_builder import MAX_WAYPOINTS, build_google_maps_url
from inner_interface import LatLng


def test_short_path_untouched():
    points = [LatLng(lat=0, lng=0), LatLng(lat=1, lng=1), LatLng(lat=2, lng=2)]
    assert simplify_to_max_points(points, max_points=9) == points


def test_long_noisy_path_is_bounded():
    points = [LatLng(lat=i * 0.0001, lng=(0.00001 if i % 2 == 0 else -0.00001)) for i in range(200)]
    simplified = simplify_to_max_points(points, max_points=9)
    assert len(simplified) <= 9
    assert simplified[0] == points[0]
    assert simplified[-1] == points[-1]


def test_google_maps_url_never_exceeds_max_waypoints():
    points = [LatLng(lat=i * 0.0001, lng=(0.00001 if i % 2 == 0 else -0.00001)) for i in range(200)]
    url = build_google_maps_url(points)
    assert url.startswith("https://www.google.com/maps/dir/?api=1")
    assert "travelmode=walking" in url
    if "waypoints=" in url:
        waypoints_param = url.split("waypoints=")[1].split("&")[0]
        assert waypoints_param.count("|") + 1 <= MAX_WAYPOINTS


def test_url_requires_at_least_two_points():
    import pytest

    with pytest.raises(ValueError):
        build_google_maps_url([LatLng(lat=0, lng=0)])
