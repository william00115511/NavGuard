"""用 osmnx 從 OpenStreetMap 擷取步行路網（AGENTS.md §3.5 主要方案），轉成路徑
計算引擎讀取的 `{nodes, edges}` 格式，覆蓋 `backend/data/road_network.json`。

`app/engine/graph.py` 的 `RoadGraph.load()` 只依賴精簡的 `{nodes: [{id,lat,lng}],
edges: [{from,to}]}` JSON schema，不吃 osmnx 原生的 GraphML／pickle，所以這裡
多一步轉換——引擎本身完全不用改（沿用原本的 node id 格式 `osm_<OSM node id>`）。

用 `graph_from_bbox`（行政區的矩形外框）而不是 `graph_from_place`（精確行政區
多邊形）：後者在邊界附近會漏掉幾筆剛好落在行政區精確邊界外、但仍屬於這次
展示範圍的節點；`simplify=False` 保留 way 本身的形狀頂點（不只留路口），
路徑幾何更精細，取樣／計分邏輯（app/engine/safety.py）不區分節點種類，不受影響。

執行期系統不會呼叫這支腳本，展示範圍要換行政區、路網要更新時，由人
手動重跑覆蓋本地檔案即可。

用法：
    cd backend
    .venv/Scripts/python.exe scripts/build_road_network.py
    .venv/Scripts/python.exe scripts/build_road_network.py --district 信義區 大安區
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import osmnx as ox

SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent
DATA_DIR = BACKEND_DIR / "data"
ROAD_NETWORK_PATH = DATA_DIR / "road_network.json"

TAIPEI_DISTRICTS: tuple[str, ...] = (
    "中正區",
    "大同區",
    "中山區",
    "松山區",
    "大安區",
    "萬華區",
    "信義區",
    "士林區",
    "北投區",
    "內湖區",
    "南港區",
    "文山區",
)

ox.settings.requests_timeout = 180


def _combined_bbox(districts: list[str]) -> tuple[float, float, float, float]:
    """回傳涵蓋所有指定行政區的外框 (west, south, east, north)。"""
    west = south = float("inf")
    east = north = float("-inf")
    for district in districts:
        print(f"  查詢行政區範圍：{district}", flush=True)
        gdf = ox.geocode_to_gdf(f"{district}, 臺北市, 台灣")
        d_west, d_south, d_east, d_north = gdf.total_bounds
        west, south = min(west, d_west), min(south, d_south)
        east, north = max(east, d_east), max(north, d_north)
    return west, south, east, north


def build_graph(districts: list[str]) -> dict[str, Any]:
    bbox = _combined_bbox(districts)
    print(f"  查詢 bbox（west,south,east,north）：{bbox}", flush=True)
    graph = ox.graph_from_bbox(bbox=bbox, network_type="walk", simplify=False, retain_all=False)

    nodes = [
        {"id": f"osm_{node_id}", "lat": round(data["y"], 6), "lng": round(data["x"], 6)}
        for node_id, data in graph.nodes(data=True)
    ]

    edge_keys: set[tuple[int, int]] = set()
    edges: list[dict[str, str]] = []
    for u, v in graph.edges(keys=False):
        if u == v:
            continue
        key = (u, v) if u < v else (v, u)
        if key in edge_keys:
            continue
        edge_keys.add(key)
        edges.append({"from": f"osm_{key[0]}", "to": f"osm_{key[1]}"})

    return {"nodes": nodes, "edges": edges}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--district",
        nargs="+",
        choices=TAIPEI_DISTRICTS,
        default=["信義區"],
        metavar="DISTRICT",
        help="要涵蓋的行政區，可指定多個（例如 --district 信義區 大安區），預設只抓信義區",
    )
    args = parser.parse_args()

    graph = build_graph(args.district)
    if not graph["nodes"] or not graph["edges"]:
        raise SystemExit("osmnx 沒回傳任何路段，可能是行政區名稱查不到，不覆蓋本地檔案。")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ROAD_NETWORK_PATH.write_text(json.dumps(graph, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已寫入 {ROAD_NETWORK_PATH.relative_to(BACKEND_DIR)}：{len(graph['nodes'])} 個節點、{len(graph['edges'])} 條邊")


if __name__ == "__main__":
    main()
