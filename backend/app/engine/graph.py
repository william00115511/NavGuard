"""道路網路圖的載入與拓樸（AGENTS.md §3.5 / §4.1）。

MVP 用備援方案：手動整理的簡化網格圖（見 data/road_network.json），
之後要換成 osmnx 擷取的 OSM 路網時，只需要換掉 `load()` 讀檔的來源，
節點/邊的介面（RoadGraph）不用改，pathfinding.py 也不用改。
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

from app.config import EDGE_SAMPLE_INTERVAL_M, MAX_SNAP_DISTANCE_M, ROAD_NETWORK_PATH
from app.engine.geo import haversine_m, sample_along
from inner_interface import LatLng, OutOfCoverageError

EdgeKey = tuple[str, str]


@dataclass(frozen=True)
class Edge:
    """一條無向路段。samples 是 §4.2 沿線取樣點，建圖時算好之後重複使用。"""

    from_id: str
    to_id: str
    distance_m: float
    samples: tuple[LatLng, ...]

    @property
    def key(self) -> EdgeKey:
        """無向邊的正規化識別碼，讓兩個方向指向同一筆分數。"""
        a, b = sorted((self.from_id, self.to_id))
        return (a, b)

    def other_end(self, node_id: str) -> str:
        return self.to_id if node_id == self.from_id else self.from_id


@dataclass
class RoadGraph:
    nodes: dict[str, LatLng]
    edges: list[Edge]
    adjacency: dict[str, list[Edge]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.adjacency:
            return
        # 道路預設雙向可通行：同一個 Edge 物件掛在兩端，兩邊共用同一份分數。
        self.adjacency = {node_id: [] for node_id in self.nodes}
        for edge in self.edges:
            self.adjacency[edge.from_id].append(edge)
            self.adjacency[edge.to_id].append(edge)

    @classmethod
    def load(cls, path: Path = ROAD_NETWORK_PATH) -> "RoadGraph":
        raw = json.loads(path.read_text(encoding="utf-8"))
        nodes = {n["id"]: LatLng(lat=n["lat"], lng=n["lng"]) for n in raw["nodes"]}

        edges: list[Edge] = []
        seen: set[EdgeKey] = set()
        for e in raw["edges"]:
            a, b = e["from"], e["to"]
            key: EdgeKey = (min(a, b), max(a, b))
            if key in seen:
                continue
            seen.add(key)
            edges.append(
                Edge(
                    from_id=a,
                    to_id=b,
                    distance_m=haversine_m(nodes[a], nodes[b]),
                    samples=sample_along(nodes[a], nodes[b], EDGE_SAMPLE_INTERVAL_M),
                )
            )
        return cls(nodes=nodes, edges=edges)

    def nearest_node(self, location: LatLng, max_distance_m: float = MAX_SNAP_DISTANCE_M) -> str:
        """把任意座標（geocoding 結果）吸附到圖上最近的節點。

        §4.7：超出路網範圍時回錯誤，不做外插——沒有這道檢查的話，一個
        東京的座標也會被靜默吸到台北的節點上。
        """
        node_id = min(self.nodes, key=lambda n: haversine_m(location, self.nodes[n]))
        distance = haversine_m(location, self.nodes[node_id])
        if distance > max_distance_m:
            raise OutOfCoverageError(
                f"座標 ({location.lat}, {location.lng}) 距離最近的路網節點 {distance:.0f} 公尺，"
                f"超出覆蓋範圍（上限 {max_distance_m:.0f} 公尺）"
            )
        return node_id


_graph: RoadGraph | None = None


def get_road_graph() -> RoadGraph:
    global _graph
    if _graph is None:
        _graph = RoadGraph.load()
    return _graph
