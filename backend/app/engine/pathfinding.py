"""綜合成本函數與最短路徑演算法（AGENTS.md §4.4 / §4.5）。

    edge_cost = length_m × ( (1 - α) + α × (1 - safety(edge)) )

成本**必須乘上邊長**：若 cost 是每條 edge 的固定值而非「每公尺代價」，
演算法會偏好「edge 數量少」的路徑而非「距離短」的路徑——一條長幹道是一條
edge，十條短巷也是十條 edge。乘上長度之後，α=0 就精確等同最短距離路線，
這正是我們拿來當對照組的「最快路線」（§4.4）。
"""

import heapq
from dataclasses import dataclass
from typing import Callable, Optional, Sequence

from app.engine.geo import haversine_m
from app.engine.graph import Edge, EdgeKey, RoadGraph
from interfaces import LatLng


@dataclass(frozen=True)
class PathComputation:
    node_path: list[str]
    path_coordinates: list[LatLng]
    edges: list[Edge]
    distance_m: float
    avg_safety_score: float


def edge_cost(distance_m: float, safety: float, alpha: float) -> float:
    """§4.4 綜合成本；safety ≤ 1 時每公尺成本的下界是 (1 - α)。"""
    return distance_m * ((1 - alpha) + alpha * (1 - safety))


def find_node_path(
    graph: RoadGraph,
    safety_by_edge: dict[EdgeKey, float],
    origin: str,
    destination: str,
    alpha: float,
    heuristic: Optional[Callable[[str], float]] = None,
) -> Optional[list[str]]:
    """A*（heuristic 為 None 時退化成 Dijkstra）找成本最低路徑。"""
    if origin == destination:
        return [origin]

    h = heuristic or (lambda _node: 0.0)
    best_cost = {origin: 0.0}
    previous: dict[str, str] = {}
    queue: list[tuple[float, str]] = [(h(origin), origin)]
    visited: set[str] = set()

    while queue:
        _priority, node = heapq.heappop(queue)
        if node in visited:
            continue
        visited.add(node)
        if node == destination:
            break

        for edge in graph.adjacency.get(node, []):
            neighbor = edge.other_end(node)
            cost = best_cost[node] + edge_cost(edge.distance_m, safety_by_edge[edge.key], alpha)
            if cost < best_cost.get(neighbor, float("inf")):
                best_cost[neighbor] = cost
                previous[neighbor] = node
                heapq.heappush(queue, (cost + h(neighbor), neighbor))

    if destination not in best_cost:
        return None

    path = [destination]
    while path[-1] != origin:
        path.append(previous[path[-1]])
    path.reverse()
    return path


def straight_line_heuristic(graph: RoadGraph, goal: str, alpha: float) -> Callable[[str], float]:
    """§4.5：h(n) = haversine(n, goal) × (1 - α)。

    乘上 (1 - α) 才 admissible——那是每公尺成本的下界，少了它 A* 會高估
    而可能回傳非最佳路徑。
    """
    goal_location = graph.nodes[goal]

    def h(node: str) -> float:
        return haversine_m(graph.nodes[node], goal_location) * (1 - alpha)

    return h


def compute_path(
    graph: RoadGraph,
    safety_by_edge: dict[EdgeKey, float],
    origin_node: str,
    destination_node: str,
    alpha: float,
    heuristic: Optional[Callable[[str], float]] = None,
) -> Optional[PathComputation]:
    """找路徑並整理出後續計算 metrics 需要的 edge 序列與長度加權平均安全分數。"""
    if heuristic is None:
        heuristic = straight_line_heuristic(graph, destination_node, alpha)

    node_path = find_node_path(graph, safety_by_edge, origin_node, destination_node, alpha, heuristic)
    if node_path is None:
        return None

    edges = [_edge_between(graph, a, b) for a, b in zip(node_path, node_path[1:])]
    total_distance = sum(e.distance_m for e in edges)

    # §4.6：avg_safety_score 以長度加權，否則一串短巷會蓋過一條長幹道。
    if total_distance > 0:
        avg_safety = sum(safety_by_edge[e.key] * e.distance_m for e in edges) / total_distance
    else:
        avg_safety = safety_by_edge[edges[0].key] if edges else 0.5

    return PathComputation(
        node_path=node_path,
        path_coordinates=[graph.nodes[node_id] for node_id in node_path],
        edges=edges,
        distance_m=total_distance,
        avg_safety_score=avg_safety,
    )


def _edge_between(graph: RoadGraph, a: str, b: str) -> Edge:
    for edge in graph.adjacency[a]:
        if edge.other_end(a) == b:
            return edge
    raise KeyError(f"路網圖中沒有 {a} 與 {b} 之間的邊")


def path_sample_points(edges: Sequence[Edge]) -> list[LatLng]:
    """路徑沿線的所有取樣點，供 §4.6 的覆蓋率類 metric 使用。"""
    return [sample for edge in edges for sample in edge.samples]
