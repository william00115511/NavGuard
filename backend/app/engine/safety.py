"""Edge 安全分數計算與正規化（AGENTS.md §4.2 / §4.3 / §4.7）。

    raw_score(edge) = mean over samples of
        Σ ( weight × decay(distance, radius_m) × sign × confidence )
    safety(edge)    = 1 / (1 + exp(-k × raw_score(edge)))

正規化刻意不用 min-max：min-max 的基準會隨每次請求加入的動態點位漂移，
同一條路在不同請求會得到不同分數，既無法解釋也無法測試（§4.3）。
固定 k 的 squashing 函式讓分數跨請求可比較。
"""

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Sequence

from app.config import SAFETY_SIGMOID_K
from app.data.schema import CategoryConfig, PointRecord
from app.engine.geo import haversine_m
from app.engine.graph import Edge, EdgeKey, RoadGraph
from interfaces import LatLng, RouteWarning

_METERS_PER_DEGREE_LAT = 111_320.0


def decay(distance_m: float, radius_m: float) -> float:
    """線性衰減：距離 0 時為 1，距離達到 radius_m（或以上）時為 0。"""
    if radius_m <= 0 or distance_m >= radius_m:
        return 0.0
    return 1.0 - (distance_m / radius_m)


def sigmoid_safety(raw_score: float, k: float = SAFETY_SIGMOID_K) -> float:
    """把任意實數的 raw_score 壓到 0~1（1 最安全），跨請求可比較（§4.3）。"""
    return 1.0 / (1.0 + math.exp(-k * raw_score))


def risk_exposure(negative_raw_score: float, k: float = SAFETY_SIGMOID_K) -> float:
    """Normalize negative-only point influence to 0..1 for routing penalties.

    Safety is intentionally still reported as a 0..1 sigmoid score.  Routing,
    however, must retain the magnitude of negative evidence separately so that
    nearby lights or help points cannot cancel a marked hazard and so that a
    hazard can cost more than the unweighted street distance.
    """
    magnitude = max(0.0, -negative_raw_score)
    return 1.0 - math.exp(-k * magnitude)


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
    warnings: list[RouteWarning]

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

    # code="missing_data_category"：category 帶原始類別 key，display_name 由前端自行查表。
    warnings = [RouteWarning(code="missing_data_category", category=name) for name in missing]
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


_GridBuckets = dict[tuple[int, int], list[PointRecord]]


@dataclass(frozen=True)
class _CategoryGrid:
    """單一類別的點位空間索引，格子邊長取自該類別的 radius_m（見 metrics.py 同款設計）。"""

    buckets: _GridBuckets
    lat_deg: float
    lng_deg: float


def _build_grid(points: Sequence[PointRecord], radius_m: float) -> _CategoryGrid:
    if not points:
        return _CategoryGrid({}, 1.0, 1.0)
    ref_lat = sum(p.lat for p in points) / len(points)
    lat_deg = max(radius_m, 1.0) / _METERS_PER_DEGREE_LAT
    meters_per_deg_lng = _METERS_PER_DEGREE_LAT * max(math.cos(math.radians(ref_lat)), 1e-6)
    lng_deg = max(radius_m, 1.0) / meters_per_deg_lng
    buckets: _GridBuckets = defaultdict(list)
    for p in points:
        buckets[(math.floor(p.lat / lat_deg), math.floor(p.lng / lng_deg))].append(p)
    return _CategoryGrid(buckets, lat_deg, lng_deg)


def _nearby_points(sample: LatLng, grid: _CategoryGrid) -> list[PointRecord]:
    gx = math.floor(sample.lat / grid.lat_deg)
    gy = math.floor(sample.lng / grid.lng_deg)
    candidates: list[PointRecord] = []
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            candidates.extend(grid.buckets.get((gx + dx, gy + dy), ()))
    return candidates


def _build_category_grids(
    points: Sequence[PointRecord], categories: dict[str, CategoryConfig]
) -> dict[str, _CategoryGrid]:
    by_category: dict[str, list[PointRecord]] = defaultdict(list)
    for p in points:
        by_category[p.category].append(p)
    return {
        name: _build_grid(pts, categories[name].radius_m)
        for name, pts in by_category.items()
        if name in categories
    }


def _raw_edge_score_indexed(edge: Edge, grids: dict[str, _CategoryGrid], profile: ScoringProfile) -> float:
    """跟 raw_edge_score 算法相同，但點位查詢走空間索引而非全點位掃描。

    展示範圍換成信義區真實 OSM 路網後，edge 數量（近 2 萬）跟點位數量
    （路燈近 3 萬筆）都比手動網格圖大兩三個數量級；全掃描是
    edges × samples × points 量級，啟動會拖到無法接受，所以只有這條
    （EdgeSafetyIndex 內部用的）熱路徑走索引版本——raw_score_at／
    raw_edge_score 兩個公開函式維持原本簽章，給測試跟其他呼叫端用。
    """
    total = 0.0
    for sample in edge.samples:
        for grid in grids.values():
            for point in _nearby_points(sample, grid):
                total += point_contribution(sample, point, profile)
    return total / len(edge.samples)


class EdgeSafetyIndex:
    """靜態分數啟動時算一次並快取，每次請求只疊加動態點位的影響（§4.1）。"""

    def __init__(self, graph: RoadGraph, static_points: Sequence[PointRecord], profile: ScoringProfile):
        self._graph = graph
        self._profile = profile
        grids = _build_category_grids(static_points, profile.categories)
        self._static_raw: dict[EdgeKey, float] = {
            edge.key: _raw_edge_score_indexed(edge, grids, profile) for edge in graph.edges
        }
        negative_points = [
            point
            for point in static_points
            if (category := profile.categories.get(point.category)) is not None
            and category.effect == "negative"
        ]
        negative_grids = _build_category_grids(negative_points, profile.categories)
        self._static_negative_raw: dict[EdgeKey, float] = {
            edge.key: _raw_edge_score_indexed(edge, negative_grids, profile)
            for edge in graph.edges
        }

    @property
    def profile(self) -> ScoringProfile:
        return self._profile

    def safety_scores(self, dynamic_points: Sequence[PointRecord] = ()) -> dict[EdgeKey, float]:
        """回傳每條 edge 正規化後的 0~1 safety，1 最安全。"""
        safety, _risk = self.routing_scores(dynamic_points)
        return safety

    def routing_scores(
        self, dynamic_points: Sequence[PointRecord] = ()
    ) -> tuple[dict[EdgeKey, float], dict[EdgeKey, float]]:
        """Return reportable safety and independent negative risk per edge.

        Positive and negative points still jointly determine the user-facing
        safety score.  The second map contains only negative exposure and is
        used by pathfinding as an additional cost, preventing positive density
        from masking a known hazard.
        """
        raw = dict(self._static_raw)
        negative_raw = dict(self._static_negative_raw)
        if dynamic_points:
            # 動態點位一次對話最多幾筆，直接建索引查詢即可，不需要再額外的
            # bounding-box 早退邏輯。
            dynamic_grids = _build_category_grids(dynamic_points, self._profile.categories)
            negative_dynamic_points = [
                point
                for point in dynamic_points
                if (category := self._profile.categories.get(point.category)) is not None
                and category.effect == "negative"
            ]
            negative_dynamic_grids = _build_category_grids(
                negative_dynamic_points, self._profile.categories
            )
            for edge in self._graph.edges:
                delta = _raw_edge_score_indexed(edge, dynamic_grids, self._profile)
                if delta:
                    raw[edge.key] += delta
                negative_delta = _raw_edge_score_indexed(
                    edge, negative_dynamic_grids, self._profile
                )
                if negative_delta:
                    negative_raw[edge.key] += negative_delta
        return (
            {key: sigmoid_safety(value) for key, value in raw.items()},
            {key: risk_exposure(value) for key, value in negative_raw.items()},
        )
