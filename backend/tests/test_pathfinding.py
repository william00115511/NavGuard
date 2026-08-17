from app.data.store import DataStore
from app.engine.graph import RoadGraph
from app.engine.pathfinding import compute_path


def _load():
    return RoadGraph.load(), DataStore.load()


def test_safety_priority_avoids_danger_zone_column():
    graph, ds = _load()
    fast = compute_path(graph, "n_0_0", "n_5_2", ds.static_points, ds.categories, alpha=0.0)
    safe = compute_path(graph, "n_0_0", "n_5_2", ds.static_points, ds.categories, alpha=1.0)

    assert fast is not None and safe is not None
    # 示範資料中，n_3_1 是 danger_zone 節點；safety 優先的路徑應避開它
    assert "n_3_1" not in safe.node_path
    assert safe.avg_safety_score >= fast.avg_safety_score


def test_same_origin_and_destination_is_trivial_path():
    graph, ds = _load()
    result = compute_path(graph, "n_0_0", "n_0_0", ds.static_points, ds.categories, alpha=0.5)
    assert result.node_path == ["n_0_0"]
    assert result.distance_m == 0
