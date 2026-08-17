from app.data.schema import CategoryConfig, PointRecord
from app.engine.safety import (
    build_scoring_profile,
    decay,
    filter_active_points,
    raw_score_at,
    sigmoid_safety,
)
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
        id=point_id, category=category, lat=lat, lng=lng, source="test",
        source_type=kwargs.pop("source_type", "static_local"), **kwargs,
    )


def _profile(points):
    return build_scoring_profile(_CATEGORIES, points)


def test_decay_is_1_at_zero_distance_and_0_beyond_radius():
    assert decay(0, 30) == 1.0
    assert decay(30, 30) == 0.0
    assert decay(31, 30) == 0.0
    assert decay(15, 30) == 0.5


def test_decay_zero_radius_never_contributes():
    assert decay(0, 0) == 0.0


def test_positive_point_increases_score():
    points = [_point("l1", "street_light"), _point("d1", "danger_zone", lat=1.0)]
    score = raw_score_at(LatLng(lat=0.0, lng=0.0), points, _profile(points))
    assert score > 0


def test_negative_point_decreases_score():
    points = [_point("l1", "street_light", lat=1.0), _point("d1", "danger_zone")]
    score = raw_score_at(LatLng(lat=0.0, lng=0.0), points, _profile(points))
    assert score < 0


def test_adding_danger_point_lowers_score():
    """驗收清單：危險點位增加會降分。"""
    base = [_point("l1", "street_light"), _point("d1", "danger_zone", lat=1.0)]
    with_danger = base + [_point("d2", "danger_zone", lat=0.0002)]
    here = LatLng(lat=0.0, lng=0.0)
    profile = _profile(base)
    assert raw_score_at(here, with_danger, profile) < raw_score_at(here, base, profile)


def test_far_away_point_has_no_effect():
    points = [_point("l1", "street_light"), _point("d1", "danger_zone", lat=1.0)]
    far = LatLng(lat=10.0, lng=10.0)
    assert raw_score_at(far, points, _profile(points)) == 0.0


def test_sigmoid_is_fixed_and_comparable_across_requests():
    """§4.3：不用 min-max，所以同一個 raw_score 永遠對到同一個 safety。"""
    assert sigmoid_safety(0.0) == 0.5
    assert sigmoid_safety(5.0) > sigmoid_safety(1.0) > 0.5
    assert 0.0 < sigmoid_safety(-5.0) < 0.5


def test_missing_category_is_removed_and_others_renormalized():
    """§4.7：某類別完全沒資料時移除其權重、其餘重新正規化，而不是當成 0 風險。"""
    profile = _profile([_point("l1", "street_light")])
    assert profile.missing_static == ["danger_zone"]
    assert profile.effective_weight["danger_zone"] == 0.0
    # 剩下的 street_light 扛起原本 1.0 + 2.0 的總權重
    assert profile.effective_weight["street_light"] == 3.0
    assert profile.warnings == ["風險區域資料在此區沒有覆蓋，未納入評分"]


def test_full_coverage_keeps_original_weights():
    profile = _profile([_point("l1", "street_light"), _point("d1", "danger_zone")])
    assert profile.missing_static == []
    assert profile.effective_weight == {"street_light": 1.0, "danger_zone": 2.0}
    assert profile.warnings == []


def test_filter_active_points_drops_expired():
    static_point = _point("s1", "street_light")
    expired = _point("e1", "danger_zone", source_type="dynamic_realtime", expires_at="2000-01-01T00:00:00+00:00")
    active = _point("a1", "danger_zone", source_type="dynamic_realtime", expires_at="2999-01-01T00:00:00+00:00")
    result = filter_active_points([static_point, expired, active])
    assert {p.id for p in result} == {"s1", "a1"}


def test_filter_active_points_drops_unparsable_expiry():
    broken = _point("b1", "danger_zone", source_type="dynamic_realtime", expires_at="not-a-timestamp")
    assert filter_active_points([broken]) == []


def test_night_club_and_underpass_hazard_penalties():
    """驗證夜店醉漢區與地下道等負向點位能正確降低安全分數。"""
    categories = {
        "street_light": CategoryConfig(name="street_light", effect="positive", weight=1.0, radius_m=30, kind="static", label="路燈"),
        "night_club_hazard": CategoryConfig(name="night_club_hazard", effect="negative", weight=2.5, radius_m=50, kind="static", label="夜店酒吧鬧事區"),
        "underpass_hazard": CategoryConfig(name="underpass_hazard", effect="negative", weight=3.0, radius_m=60, kind="static", label="封閉地下道死角"),
    }
    points = [
        _point("l1", "street_light", lat=0.0, lng=0.0),
        _point("c1", "night_club_hazard", lat=0.0001, lng=0.0001),
        _point("u1", "underpass_hazard", lat=0.0002, lng=0.0002),
    ]
    profile = build_scoring_profile(categories, points)
    here = LatLng(lat=0.0, lng=0.0)
    score = raw_score_at(here, points, profile)
    assert score < 0, "負向點位總權重 (5.5) 大於路燈 (1.0)，原始分數應為負"
    assert sigmoid_safety(score) < 0.5
