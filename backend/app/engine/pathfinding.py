"""綜合成本函數、最短路徑演算法與路徑統計摘要（ForAI.md 3.1 / 3.3 / 3.4 / 3.5）。"""

import heapq
from dataclasses import dataclass
from typing import Optional, Sequence

from app.data.schema import CategoryConfig, PointRecord
from app.engine.geo import haversine_m, midpoint
from app.engine.graph import RoadGraph
from app.engine.safety import raw_edge_safety_score
from inner_interface import LatLng


@dataclass(frozen=True)
class ScoredEdge:
    from_id: str
    to_id: str
    distance_m: float
    safety_score: float  # 已正規化到 0~1，1 最安全


@dataclass(frozen=True)
class PathComputation:
    node_path: list[str]
    path_coordinates: list[LatLng]
    distance_m: float
    avg_safety_score: float
    passed_landmarks: dict[str, int]


def _normalize(values: Sequence[float]) -> list[float]:
    """min-max 正規化到 0~1；全部相同時無法區分優劣，回傳中性值 0.5。"""
    if not values:
        return []
    lo, hi = min(values), max(values)
    if hi - lo < 1e-9:
        return [0.5 for _ in values]
    return [(v - lo) / (hi - lo) for v in values]


def build_scored_edges(
    graph: RoadGraph,
    points: Sequence[PointRecord],
    categories: dict[str, CategoryConfig],
) -> list[ScoredEdge]:
    """對每條 edge 取中點算安全分數，正規化到 0~1（3.1/3.2節）。"""
    raw: list[tuple[str, str, float, float]] = []
    seen: set[tuple[str, str]] = set()
    for from_id, neighbors in graph.adjacency.items():
        for edge in neighbors:
            key = tuple(sorted((from_id, edge.to_id)))
            if key in seen:
                continue
            seen.add(key)
            mid = midpoint(graph.nodes[from_id], graph.nodes[edge.to_id])
            raw_score = raw_edge_safety_score(mid, points, categories)
            raw.append((from_id, edge.to_id, edge.distance_m, raw_score))

    normalized_safety = _normalize([r[3] for r in raw])
    return [
        ScoredEdge(from_id=f, to_id=t, distance_m=d, safety_score=s)
        for (f, t, d, _raw), s in zip(raw, normalized_safety)
    ]


def _build_cost_adjacency(
    scored_edges: list[ScoredEdge], alpha: float
) -> dict[str, list[tuple[str, float, float, float]]]:
    """回傳 node_id -> [(neighbor_id, edge_cost, distance_m, safety_score), ...]，雙向。

    edge_cost = (1-α)×normalized_distance + α×(1-safety_score)（3.3節）
    """
    normalized_distance = _normalize([e.distance_m for e in scored_edges])
    adjacency: dict[str, list[tuple[str, float, float, float]]] = {}
    for edge, norm_dist in zip(scored_edges, normalized_distance):
        cost = (1 - alpha) * norm_dist + alpha * (1 - edge.safety_score)
        adjacency.setdefault(edge.from_id, []).append((edge.to_id, cost, edge.distance_m, edge.safety_score))
        adjacency.setdefault(edge.to_id, []).append((edge.from_id, cost, edge.distance_m, edge.safety_score))
    return adjacency


def _dijkstra(
    adjacency: dict[str, list[tuple[str, float, float, float]]],
    origin: str,
    destination: str,
) -> Optional[list[str]]:
    """在加權圖上找 cost 最低路徑。圖小（黑客松規模），用 Dijkstra 足夠且保證正確（3.4節）。"""
    if origin == destination:
        return [origin]

    best_cost = {origin: 0.0}
    previous: dict[str, str] = {}
    queue: list[tuple[float, str]] = [(0.0, origin)]
    visited: set[str] = set()

    while queue:
        cost, node = heapq.heappop(queue)
        if node in visited:
            continue
        visited.add(node)
        if node == destination:
            break

        for neighbor, edge_cost, _distance, _safety in adjacency.get(node, []):
            new_cost = cost + edge_cost
            if new_cost < best_cost.get(neighbor, float("inf")):
                best_cost[neighbor] = new_cost
                previous[neighbor] = node
                heapq.heappush(queue, (new_cost, neighbor))

    if destination not in best_cost:
        return None

    path = [destination]
    while path[-1] != origin:
        path.append(previous[path[-1]])
    path.reverse()
    return path


def _passed_landmarks(
    node_path: Sequence[LatLng],
    points: Sequence[PointRecord],
    categories: dict[str, CategoryConfig],
) -> dict[str, int]:
    """統計路徑沿線（各點位自身 radius_m 範圍內）經過幾個各類別點位。"""
    counts: dict[str, int] = {}
    for point in points:
        category = categories.get(point.category)
        if category is None:
            continue
        point_loc = LatLng(lat=point.lat, lng=point.lng)
        passed = any(haversine_m(node, point_loc) <= category.radius_m for node in node_path)
        if passed:
            counts[point.category] = counts.get(point.category, 0) + 1
    return counts


def compute_path(
    graph: RoadGraph,
    origin_node: str,
    destination_node: str,
    points: Sequence[PointRecord],
    categories: dict[str, CategoryConfig],
    alpha: float,
) -> Optional[PathComputation]:
    """整合 3.1~3.5 節：建加權圖 → 找最短成本路徑 → 統計摘要。"""
    scored_edges = build_scored_edges(graph, points, categories)
    cost_adjacency = _build_cost_adjacency(scored_edges, alpha)

    node_path = _dijkstra(cost_adjacency, origin_node, destination_node)
    if node_path is None:
        return None

    total_distance = 0.0
    safety_scores: list[float] = []
    for a, b in zip(node_path, node_path[1:]):
        edge = next(e for e in cost_adjacency[a] if e[0] == b)
        _neighbor, _cost, distance_m, safety_score = edge
        total_distance += distance_m
        safety_scores.append(safety_score)

    avg_safety = sum(safety_scores) / len(safety_scores) if safety_scores else 1.0
    path_coordinates = [graph.nodes[node_id] for node_id in node_path]

    return PathComputation(
        node_path=node_path,
        path_coordinates=path_coordinates,
        distance_m=total_distance,
        avg_safety_score=avg_safety,
        passed_landmarks=_passed_landmarks(path_coordinates, points, categories),
    )
