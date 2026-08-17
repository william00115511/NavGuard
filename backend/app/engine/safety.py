"""Edge 安全分數計算與正規化（AGENTS.md §4.2 / §4.3 / §4.7）。

    raw_score(edge) = mean over samples of
        Σ ( weight × decay(distance, radius_m) × sign × confidence )
    safety(edge)    = 1 / (1 + exp(-k × raw_score(edge)))

正規化刻意不用 min-max：min-max 的基準會隨每次請求加入的動態點位漂移，
同一條路在不同請求會得到不同分數，既無法解釋也無法測試（§4.3）。
固定 k 的 squashing 函式讓分數跨請求可比較。
"""

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Sequence

from app.config import SAFETY_SIGMOID_K
from app.data.schema import CategoryConfig, PointRecord
from app.engine.geo import haversine_m
from app.engine.graph import Edge, EdgeKey, RoadGraph
from inner_interface import LatLng


def decay(distance_m: float, radius_m: float) -> float:
    """線性衰減：距離 0 時為 1，距離達到 radius_m（或以上）時為 0。"""
    if radius_m <= 0 or distance_m >= radius_m:
        return 0.0
    return 1.0 - (distance_m / radius_m)


def sigmoid_safety(raw_score: float, k: float = SAFETY_SIGMOID_K) -> float:
    """把任意實數的 raw_score 壓到 0~1（1 最安全），跨請求可比較（§4.3）。"""
    return 1.0 / (1.0 + math.exp(-k * raw_score))


def filter_active_points(points: Sequence[PointRecord], now: datetime | None = None) -> list[PointRecord]:
    """過濾掉已過期的動態點位；靜態點位 expires_at 恆為 None，永遠保留。"""
    now = now or datetime.now(timezone.utc)
    active = []
    for p in points:
        if p.expires_at is None:
            active.append(p)
            continue
        try:
            expires_at = datetime.fromisoformat(p.expires_at)
        except ValueError:
            # 時間格式壞掉的點位無法判斷時效，保守起見不採計（§1 原則 3：
            # 寧可少算一個點位並降低 confidence，也不要當成永久有效）。
            continue
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at > now:
            active.append(p)
    return active


@dataclass(frozen=True)
class ScoringProfile:
    """依實際有覆蓋的類別調整後的計分設定（§4.7）。

    某個靜態類別在這個區域完全沒有資料時，做法是把它的權重從公式移除、
    其餘權重按比例放大回原本的總量，並產生一則 warning——而不是把「沒資料」
    當成「沒有風險／沒有照明」（§1 原則 3）。

    動態類別（火警、臨時人潮…）本來就是事件驅動、平常沒有點位是正常狀態，
    不列入覆蓋率計算，權重也不縮放。
    """

    categories: dict[str, CategoryConfig]
    effective_weight: dict[str, float]
    covered_static: list[str]
    missing_static: list[str]
    warnings: list[str]

    @property
    def static_coverage_ratio(self) -> float:
        total = len(self.covered_static) + len(self.missing_static)
        return len(self.covered_static) / total if total else 1.0


def build_scoring_profile(
    categories: dict[str, CategoryConfig],
    static_points: Sequence[PointRecord],
) -> ScoringProfile:
    present = {p.category for p in static_points}
    static_names = [name for name, c in categories.items() if c.kind == "static"]

    covered = [name for name in static_names if name in present]
    missing = [name for name in static_names if name not in present]

    covered_weight = sum(categories[name].weight for name in covered)
    total_weight = sum(categories[name].weight for name in static_names)
    # 重新正規化：讓剩下的類別扛起原本的總權重，避免缺一類就整體分數偏低。
    scale = (total_weight / covered_weight) if covered_weight > 0 else 1.0

    effective_weight = {name: categories[name].weight for name in categories}
    for name in covered:
        effective_weight[name] = categories[name].weight * scale
    for name in missing:
        effective_weight[name] = 0.0

    warnings = [
        f"{categories[name].display_name}資料在此區沒有覆蓋，未納入評分" for name in missing
    ]
    return ScoringProfile(
        categories=categories,
        effective_weight=effective_weight,
        covered_static=covered,
        missing_static=missing,
        warnings=warnings,
    )


def point_contribution(sample: LatLng, point: PointRecord, profile: ScoringProfile) -> float:
    """單一點位對單一取樣點的加權影響；類別未登記或超出半徑時為 0。"""
    category = profile.categories.get(point.category)
    if category is None:
        return 0.0
    weight = profile.effective_weight.get(point.category, category.weight)
    if weight == 0.0:
        return 0.0
    decay_factor = decay(haversine_m(sample, LatLng(lat=point.lat, lng=point.lng)), category.radius_m)
    if decay_factor <= 0:
        return 0.0
    return weight * decay_factor * category.sign * point.confidence


def raw_score_at(sample: LatLng, points: Iterable[PointRecord], profile: ScoringProfile) -> float:
    """某個取樣點周圍所有點位的加權原始分數（未正規化）。"""
    return sum(point_contribution(sample, point, profile) for point in points)


def raw_edge_score(edge: Edge, points: Iterable[PointRecord], profile: ScoringProfile) -> float:
    """沿 edge 取樣後取平均（§4.2）。"""
    points = list(points)
    return sum(raw_score_at(s, points, profile) for s in edge.samples) / len(edge.samples)


class EdgeSafetyIndex:
    """靜態分數啟動時算一次並快取，每次請求只疊加動態點位的影響（§4.1）。"""

    def __init__(self, graph: RoadGraph, static_points: Sequence[PointRecord], profile: ScoringProfile):
        self._graph = graph
        self._profile = profile
        self._static_raw: dict[EdgeKey, float] = {
            edge.key: raw_edge_score(edge, static_points, profile) for edge in graph.edges
        }

    @property
    def profile(self) -> ScoringProfile:
        return self._profile

    def safety_scores(self, dynamic_points: Sequence[PointRecord] = ()) -> dict[EdgeKey, float]:
        """回傳每條 edge 正規化後的 0~1 safety，1 最安全。"""
        raw = dict(self._static_raw)
        for point in dynamic_points:
            category = self._profile.categories.get(point.category)
            if category is None:
                continue
            location = LatLng(lat=point.lat, lng=point.lng)
            for edge in self._graph.edges:
                # 任一取樣點與 samples[0] 的距離都不超過 edge 長度，所以這個
                # 條件成立時整條 edge 必定在影響半徑外，可以直接跳過（§4.1）。
                if haversine_m(location, edge.samples[0]) > category.radius_m + edge.distance_m:
                    continue
                delta = sum(point_contribution(s, point, self._profile) for s in edge.samples)
                if delta:
                    raw[edge.key] += delta / len(edge.samples)
        return {key: sigmoid_safety(value) for key, value in raw.items()}
