"""路線層級的可解釋 metrics、confidence 與 reasons（AGENTS.md §4.6 / §4.7）。

這些是**報告用**指標，不參與路徑選擇。無資料的欄位一律填 None，不填 0
（§1 原則 3：沒有資料不等於沒有風險）。
"""

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
from interfaces import Confidence, LatLng, RouteMetrics

_LIT_CATEGORY = "street_light"
_HELP_CATEGORY = "help_point"
_POLICE_CATEGORY = "police_station"


def _points_of(points: Sequence[PointRecord], category: str) -> list[PointRecord]:
    return [p for p in points if p.category == category]


def _count_near(samples: Sequence[LatLng], points: Sequence[PointRecord], radius_m: float) -> int:
    """沿線 radius_m 內的點位數（同一個點位只算一次）。"""
    return sum(
        1
        for p in points
        if any(haversine_m(s, LatLng(lat=p.lat, lng=p.lng)) <= radius_m for s in samples)
    )


def _coverage_ratio(samples: Sequence[LatLng], points: Sequence[PointRecord], radius_m: float) -> float:
    """採樣點中，radius_m 內至少有一個該類點位的比例。"""
    if not samples:
        return 0.0
    hit = sum(
        1
        for s in samples
        if any(haversine_m(s, LatLng(lat=p.lat, lng=p.lng)) <= radius_m for p in points)
    )
    return hit / len(samples)


def build_metrics(
    computation: PathComputation,
    points: Sequence[PointRecord],
    profile: ScoringProfile,
    detour_vs_fastest_min: float,
) -> RouteMetrics:
    samples = path_sample_points(computation.edges) or list(computation.path_coordinates)

    passed_landmarks: dict[str, int] = {}
    for name, category in profile.categories.items():
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
        # 三個 metric 都遵守同一條規則：該類別沒有任何資料時回 None 而非 0。
        lit_coverage_ratio=(
            round(_coverage_ratio(samples, _points_of(points, _LIT_CATEGORY), LIT_COVERAGE_RADIUS_M), 3)
            if _LIT_CATEGORY in present
            else None
        ),
        help_points_within_50m=(
            _count_near(samples, _points_of(points, _HELP_CATEGORY), HELP_POINT_RADIUS_M)
            if _HELP_CATEGORY in present
            else None
        ),
        police_within_150m=(
            _count_near(samples, _points_of(points, _POLICE_CATEGORY), POLICE_RADIUS_M)
            if _POLICE_CATEGORY in present
            else None
        ),
    )


def decide_confidence(profile: ScoringProfile, points: Sequence[PointRecord]) -> Confidence:
    """§4.7：依靜態資料覆蓋比例與動態點位佔比決定。

    動態點位（未經查證的即時新聞）佔比越高，整體結論越不可靠，
    所以即使靜態覆蓋完整也要降級。
    """
    coverage = profile.static_coverage_ratio
    dynamic_count = sum(1 for p in points if p.source_type == "dynamic_realtime")
    dynamic_ratio = dynamic_count / len(points) if points else 0.0

    if coverage < 0.5 or dynamic_ratio > 0.5:
        return Confidence.LOW
    if coverage >= 0.8 and dynamic_ratio <= 0.2:
        return Confidence.HIGH
    return Confidence.MEDIUM


def build_reasons(safest: RouteMetrics, fastest: RouteMetrics, profile: ScoringProfile) -> list[str]:
    """產生給使用者看的理由。

    只陳述可驗證的事實（數量、分鐘數），不做安全承諾——§1 原則 1 禁止
    「絕對安全」「保證安全」這類文案，所以這裡一律用「較多／較少」的說法。
    """
    reasons: list[str] = []

    if safest.help_points_within_50m:
        reasons.append(f"沿途 50 公尺內有 {safest.help_points_within_50m} 個可求助據點")
    if safest.police_within_150m:
        reasons.append(f"沿途 150 公尺內有 {safest.police_within_150m} 處警察局")
    if safest.lit_coverage_ratio is not None:
        reasons.append(f"約 {safest.lit_coverage_ratio:.0%} 的路段 30 公尺內有路燈資料")

    delta_min = safest.duration_min_est - fastest.duration_min_est
    if delta_min > 0.1 and safest.avg_safety_score > fastest.avg_safety_score:
        reasons.append(
            f"比最快路線多走約 {delta_min:.0f} 分鐘，換得較高的照明與可求助據點密度"
        )
    elif abs(delta_min) <= 0.1:
        reasons.append("與最快路線幾乎同長，不需要額外繞路")

    for name in profile.missing_static:
        reasons.append(f"（{profile.categories[name].display_name}資料缺漏，未納入以上評估）")

    return reasons
