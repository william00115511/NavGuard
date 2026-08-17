import pytest

from app.data.store import DataStore
from app.engine.geo import haversine_m
from app.engine.graph import Edge, RoadGraph
from app.engine.pathfinding import compute_path, find_node_path, straight_line_heuristic
from app.engine.safety import EdgeSafetyIndex, build_scoring_profile, filter_active_points
from app.config import EDGE_SAMPLE_INTERVAL_M
from app.engine.geo import sample_along
from interfaces import LatLng, OutOfCoverageError


@pytest.fixture(scope="module")
def demo():
    graph = RoadGraph.load()
    store = DataStore.load()
    points = filter_active_points(store.static_points)
    profile = build_scoring_profile(store.categories, points)
    return graph, EdgeSafetyIndex(graph, points, profile).safety_scores()


def _shortest_distance_path(graph: RoadGraph, origin: str, destination: str) -> list[str]:
    """獨立算一次純距離最短路徑，當作 α=0 的對照基準。"""
    flat_safety = {edge.key: 0.0 for edge in graph.edges}
    return find_node_path(graph, flat_safety, origin, destination, alpha=0.0)


def test_alpha_zero_equals_shortest_distance_path(demo):
    """驗收清單：α=0 時結果等同最短路徑。

    §4.4 的成本必須乘上邊長才會成立——少了長度項時演算法會改成最小化
    「edge 數量」，α=0 就不再是最快路線。
    """
    graph, safety = demo
    result = compute_path(graph, safety, "n_0_0", "n_5_2", alpha=0.0)
    baseline = _shortest_distance_path(graph, "n_0_0", "n_5_2")

    baseline_distance = sum(
        haversine_m(graph.nodes[a], graph.nodes[b]) for a, b in zip(baseline, baseline[1:])
    )
    assert result.distance_m == pytest.approx(baseline_distance)


def test_cost_prefers_short_distance_over_few_edges():
    """一條長邊 vs 多條短邊繞路：α=0 必須選距離短的那條，不管 edge 數量。"""
    nodes = {
        "A": LatLng(25.0, 121.5),
        "B": LatLng(25.0, 121.51),
        "c1": LatLng(25.002, 121.502),
        "c2": LatLng(25.003, 121.505),
        "c3": LatLng(25.002, 121.508),
    }
    pairs = [("A", "B"), ("A", "c1"), ("c1", "c2"), ("c2", "c3"), ("c3", "B")]
    edges = [
        Edge(
            from_id=a,
            to_id=b,
            distance_m=haversine_m(nodes[a], nodes[b]),
            samples=sample_along(nodes[a], nodes[b], EDGE_SAMPLE_INTERVAL_M),
        )
        for a, b in pairs
    ]
    graph = RoadGraph(nodes=nodes, edges=edges)
    safety = {edge.key: 0.5 for edge in edges}

    result = compute_path(graph, safety, "A", "B", alpha=0.0)
    assert result.node_path == ["A", "B"]


def test_astar_and_dijkstra_agree(demo):
    """驗收清單：A* 與 Dijkstra 在同一組測資上回傳相同路徑（heuristic admissible）。"""
    graph, safety = demo
    for alpha in (0.0, 0.3, 0.6, 1.0):
        astar = find_node_path(
            graph, safety, "n_0_0", "n_5_2", alpha, straight_line_heuristic(graph, "n_5_2", alpha)
        )
        dijkstra = find_node_path(graph, safety, "n_0_0", "n_5_2", alpha, heuristic=None)
        assert astar == dijkstra, f"alpha={alpha} 時 A* 與 Dijkstra 結果不一致"


def test_safety_priority_avoids_danger_zone(demo):
    graph, safety = demo
    fast = compute_path(graph, safety, "n_0_0", "n_5_2", alpha=0.0)
    safe = compute_path(graph, safety, "n_0_0", "n_5_2", alpha=1.0)

    # 示範資料中 n_3_1 附近有 danger_zone；safety 優先的路徑應避開它
    assert "n_3_1" not in safe.node_path
    assert safe.avg_safety_score >= fast.avg_safety_score


def test_same_origin_and_destination_is_trivial_path(demo):
    graph, safety = demo
    result = compute_path(graph, safety, "n_0_0", "n_0_0", alpha=0.5)
    assert result.node_path == ["n_0_0"]
    assert result.distance_m == 0


def test_nearest_node_rejects_locations_outside_coverage():
    """§4.7：超出路網範圍要回錯誤，不能靜默吸附到最近節點。"""
    graph = RoadGraph.load()
    assert graph.nearest_node(LatLng(lat=25.0481, lng=121.5311)) == "n_0_0"
    with pytest.raises(OutOfCoverageError):
        graph.nearest_node(LatLng(lat=35.681, lng=139.767))  # 東京車站
