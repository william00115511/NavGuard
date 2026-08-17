"""Edge 安全分數計算（ForAI.md 3.2）。

edge_safety_score = Σ ( category.weight × decay(distance, category.radius_m) × sign × confidence )

這裡只算「原始分數」，跨 edge 的 0~1 正規化在 graph.py 建圖時一次做完
（正規化需要知道全圖的 min/max，單一 edge 算不出來）。
"""

from datetime import datetime, timezone
from typing import Sequence

from app.data.schema import CategoryConfig, PointRecord
from app.engine.geo import haversine_m
from inner_interface import LatLng


def decay(distance_m: float, radius_m: float) -> float:
    """線性衰減：距離 0 時為 1，距離達到 radius_m（或以上）時為 0。"""
    if radius_m <= 0 or distance_m >= radius_m:
        return 0.0
    return 1.0 - (distance_m / radius_m)


def filter_active_points(points: Sequence[PointRecord], now: datetime | None = None) -> list[PointRecord]:
    """過濾掉已過期的動態點位；靜態點位 expires_at 恆為 None，永遠保留。"""
    now = now or datetime.now(timezone.utc)
    active = []
    for p in points:
        if p.expires_at is None:
            active.append(p)
            continue
        expires_at = datetime.fromisoformat(p.expires_at)
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at > now:
            active.append(p)
    return active


def raw_edge_safety_score(
    location: LatLng,
    points: Sequence[PointRecord],
    categories: dict[str, CategoryConfig],
) -> float:
    """計算某個位置（edge 中點）周圍點位加權後的原始安全分數，未正規化。"""
    score = 0.0
    for point in points:
        category = categories.get(point.category)
        if category is None:
            continue
        distance = haversine_m(location, LatLng(lat=point.lat, lng=point.lng))
        decay_factor = decay(distance, category.radius_m)
        if decay_factor <= 0:
            continue
        score += category.weight * decay_factor * category.sign * point.confidence
    return score
