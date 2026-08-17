"""道路網路圖的載入與拓樸（ForAI.md 2.5 / 3.1）。

MVP 用備援方案：手動整理的簡化網格圖（見 data/road_network.json），
之後要換成 osmnx 擷取的 OSM 路網時，只需要換掉 `load()` 讀檔的來源，
節點/邊的介面（RoadGraph）不用改，pathfinding.py 也不用改。
"""

import json
from dataclasses import dataclass
from pathlib import Path

from app.config import ROAD_NETWORK_PATH
from app.engine.geo import haversine_m
from inner_interface import LatLng


@dataclass(frozen=True)
class Edge:
    to_id: str
    distance_m: float


@dataclass
class RoadGraph:
    nodes: dict[str, LatLng]
    adjacency: dict[str, list[Edge]]

    @classmethod
    def load(cls, path: Path = ROAD_NETWORK_PATH) -> "RoadGraph":
        raw = json.loads(path.read_text(encoding="utf-8"))
        nodes = {n["id"]: LatLng(lat=n["lat"], lng=n["lng"]) for n in raw["nodes"]}
        adjacency: dict[str, list[Edge]] = {node_id: [] for node_id in nodes}
        for e in raw["edges"]:
            a, b = e["from"], e["to"]
            distance_m = haversine_m(nodes[a], nodes[b])
            # 道路預設雙向可通行
            adjacency[a].append(Edge(to_id=b, distance_m=distance_m))
            adjacency[b].append(Edge(to_id=a, distance_m=distance_m))
        return cls(nodes=nodes, adjacency=adjacency)

    def nearest_node(self, location: LatLng) -> str:
        """把任意座標（geocoding 結果）吸附到圖上最近的節點。"""
        return min(self.nodes, key=lambda node_id: haversine_m(location, self.nodes[node_id]))


_graph: RoadGraph | None = None


def get_road_graph() -> RoadGraph:
    global _graph
    if _graph is None:
        _graph = RoadGraph.load()
    return _graph
