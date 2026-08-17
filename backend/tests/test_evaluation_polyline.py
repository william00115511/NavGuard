import pytest

from evaluation.polyline import decode_polyline, encode_polyline
from interfaces import LatLng


def test_decode_known_google_example():
    """Google 官方文件範例：https://developers.google.com/maps/documentation/utilities/polylinealgorithm"""
    points = decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@")
    assert len(points) == 3
    assert points[0].lat == pytest.approx(38.5, abs=1e-5)
    assert points[0].lng == pytest.approx(-120.2, abs=1e-5)
    assert points[1].lat == pytest.approx(40.7, abs=1e-5)
    assert points[1].lng == pytest.approx(-120.95, abs=1e-5)
    assert points[2].lat == pytest.approx(43.252, abs=1e-5)
    assert points[2].lng == pytest.approx(-126.453, abs=1e-5)


def test_encode_decode_round_trip():
    points = [
        LatLng(lat=25.0330, lng=121.5654),
        LatLng(lat=25.0478, lng=121.5319),
        LatLng(lat=25.0170, lng=121.5340),
    ]
    decoded = decode_polyline(encode_polyline(points))
    assert len(decoded) == len(points)
    for original, round_tripped in zip(points, decoded):
        assert round_tripped.lat == pytest.approx(original.lat, abs=1e-5)
        assert round_tripped.lng == pytest.approx(original.lng, abs=1e-5)


def test_decode_empty_string_returns_empty_list():
    assert decode_polyline("") == []


def test_decode_truncated_string_raises():
    with pytest.raises(ValueError):
        decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`")
