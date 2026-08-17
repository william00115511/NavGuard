from app.data.schema import CategoryConfig, PointRecord
from app.engine.safety import decay, filter_active_points, raw_edge_safety_score
from inner_interface import LatLng

_CATEGORIES = {
    "street_light": CategoryConfig(name="street_light", effect="positive", weight=1.0, radius_m=30, kind="static"),
    "danger_zone": CategoryConfig(name="danger_zone", effect="negative", weight=2.0, radius_m=80, kind="static"),
}


def test_decay_is_1_at_zero_distance_and_0_beyond_radius():
    assert decay(0, 30) == 1.0
    assert decay(30, 30) == 0.0
    assert decay(31, 30) == 0.0
    assert decay(15, 30) == 0.5


def test_decay_zero_radius_never_contributes():
    assert decay(0, 0) == 0.0


def test_positive_point_increases_score():
    light = PointRecord(
        id="l1", category="street_light", lat=0.0, lng=0.0,
        source="test", source_type="static_local",
    )
    score_at_light = raw_edge_safety_score(LatLng(lat=0.0, lng=0.0), [light], _CATEGORIES)
    assert score_at_light > 0


def test_negative_point_decreases_score():
    danger = PointRecord(
        id="d1", category="danger_zone", lat=0.0, lng=0.0,
        source="test", source_type="static_local",
    )
    score_at_danger = raw_edge_safety_score(LatLng(lat=0.0, lng=0.0), [danger], _CATEGORIES)
    assert score_at_danger < 0


def test_far_away_point_has_no_effect():
    light = PointRecord(
        id="l1", category="street_light", lat=0.0, lng=0.0,
        source="test", source_type="static_local",
    )
    far = LatLng(lat=10.0, lng=10.0)
    assert raw_edge_safety_score(far, [light], _CATEGORIES) == 0.0


def test_filter_active_points_drops_expired():
    static_point = PointRecord(
        id="s1", category="street_light", lat=0, lng=0, source="t", source_type="static_local", expires_at=None,
    )
    expired = PointRecord(
        id="e1", category="danger_zone", lat=0, lng=0, source="t", source_type="dynamic_realtime",
        expires_at="2000-01-01T00:00:00+00:00",
    )
    active = PointRecord(
        id="a1", category="danger_zone", lat=0, lng=0, source="t", source_type="dynamic_realtime",
        expires_at="2999-01-01T00:00:00+00:00",
    )
    result = filter_active_points([static_point, expired, active])
    ids = {p.id for p in result}
    assert ids == {"s1", "a1"}
