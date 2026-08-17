import pytest

from app.data.store import DataStore
from app.engine.geo import haversine_m
from app.engine.graph import Edge, RoadGraph
from app.engine.pathfinding import compute_path, find_node_path, straight_line_heuristic
from app.engine.safety import EdgeSafetyIndex, build_scoring_profile, filter_active_points
from app.config import EDGE_SAMPLE_INTERVAL_M
from app.engine.geo import sample_along
from interfaces import LatLng, OutOfCoverageError

# 信義區真實 OSM 路網（backend/data/road_network.json，見
# backend/scripts/build_road_network.py）上兩個相距約 4.9 公里的節點，
# 中間路徑會經過 _DANGER_NODE_ID。這三個 id 是產生當下的 OSM node id，
# 之後重跑腳本更新路網時若剛好被 OSM 合併/刪除，要跟著換成新的 id。
_ORIGIN_ID = "osm_638299155"  # 25.01848, 121.557416（信義區西南側）
_DESTINATION_ID = "osm_1304689120"  # 25.04478, 121.584105（信義區東北側）
_DANGER_NODE_ID = "osm_7782558246"  # 25.030474, 121.565463（台北 101 一帶，danger_zone.json 示範點所在）


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
    result = compute_path(graph, safety, _ORIGIN_ID, _DESTINATION_ID, alpha=0.0)
    baseline = _shortest_distance_path(graph, _ORIGIN_ID, _DESTINATION_ID)

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
            graph, safety, _ORIGIN_ID, _DESTINATION_ID, alpha,
            straight_line_heuristic(graph, _DESTINATION_ID, alpha),
        )
        dijkstra = find_node_path(graph, safety, _ORIGIN_ID, _DESTINATION_ID, alpha, heuristic=None)
        assert astar == dijkstra, f"alpha={alpha} 時 A* 與 Dijkstra 結果不一致"


def test_safety_priority_avoids_danger_zone(demo):
    graph, safety = demo
    fast = compute_path(graph, safety, _ORIGIN_ID, _DESTINATION_ID, alpha=0.0)
    safe = compute_path(graph, safety, _ORIGIN_ID, _DESTINATION_ID, alpha=1.0)

    # 示範資料中 _DANGER_NODE_ID 附近有 danger_zone；safety 優先的路徑應避開它
    assert _DANGER_NODE_ID not in safe.node_path
    assert safe.avg_safety_score >= fast.avg_safety_score


def test_same_origin_and_destination_is_trivial_path(demo):
    graph, safety = demo
    result = compute_path(graph, safety, _ORIGIN_ID, _ORIGIN_ID, alpha=0.5)
    assert result.node_path == [_ORIGIN_ID]
    assert result.distance_m == 0


def test_nearest_node_rejects_locations_outside_coverage():
    """§4.7：超出路網範圍要回錯誤，不能靜默吸附到最近節點。"""
    graph = RoadGraph.load()
    assert graph.nearest_node(LatLng(lat=25.01848, lng=121.557416)) == _ORIGIN_ID
    with pytest.raises(OutOfCoverageError):
        graph.nearest_node(LatLng(lat=35.681, lng=139.767))  # 東京車站
