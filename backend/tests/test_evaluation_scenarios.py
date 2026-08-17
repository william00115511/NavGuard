from app.engine.graph import RoadGraph
from evaluation.scenarios import sample_scenarios
from interfaces import LatLng


def _grid_graph(rows: int = 6, cols: int = 6, spacing_deg: float = 0.001) -> RoadGraph:
    nodes = {
        f"n{r}_{c}": LatLng(lat=r * spacing_deg, lng=c * spacing_deg) for r in range(rows) for c in range(cols)
    }
    return RoadGraph(nodes=nodes, edges=[])


def test_sample_scenarios_is_deterministic_with_fixed_seed():
    graph = _grid_graph()
    first = sample_scenarios(graph, n=5, seed=1, min_distance_m=1.0, max_distance_m=1_000_000.0)
    second = sample_scenarios(graph, n=5, seed=1, min_distance_m=1.0, max_distance_m=1_000_000.0)
    assert [(s.origin, s.destination) for s in first] == [(s.origin, s.destination) for s in second]


def test_sample_scenarios_respects_distance_bounds():
    graph = _grid_graph(rows=10, cols=10, spacing_deg=0.001)
    scenarios = sample_scenarios(graph, n=20, seed=7, min_distance_m=200.0, max_distance_m=400.0)
    assert scenarios
    for scenario in scenarios:
        assert 200.0 <= scenario.straight_line_distance_m <= 400.0


def test_sample_scenarios_does_not_repeat_pairs():
    graph = _grid_graph()
    scenarios = sample_scenarios(graph, n=10, seed=3, min_distance_m=1.0, max_distance_m=1_000_000.0)
    pairs = {
        (round(s.origin.lat, 6), round(s.origin.lng, 6), round(s.destination.lat, 6), round(s.destination.lng, 6))
        for s in scenarios
    }
    assert len(pairs) == len(scenarios)


def test_sample_scenarios_stops_instead_of_looping_forever_when_unsatisfiable():
    graph = _grid_graph(rows=2, cols=2, spacing_deg=0.00001)
    scenarios = sample_scenarios(graph, n=50, seed=1, min_distance_m=1_000_000.0, max_distance_m=2_000_000.0)
    assert scenarios == []
