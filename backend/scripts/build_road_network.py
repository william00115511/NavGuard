"""從 OpenStreetMap（Overpass API）擷取步行路網，轉成路徑計算引擎讀取的
`{nodes, edges}` 格式（AGENTS.md §3.5），覆蓋 `backend/data/road_network.json`。

§3.5 主要方案是用 `osmnx` 預先擷取存 GraphML／pickle；但那會多引入
networkx／shapely／geopandas 一整包地理空間依賴。這支腳本改用專案已有的
httpx 直接打 Overpass API，換取同樣真實的路網拓樸，但不新增依賴，
`app/engine/graph.py` 的 `RoadGraph.load()` 也完全不用改（輸出檔案 schema
跟原本手動整理的示範網格一致）。

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
import time
from pathlib import Path
from typing import Any

import httpx
import truststore

truststore.inject_into_ssl()

SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent
DATA_DIR = BACKEND_DIR / "data"
ROAD_NETWORK_PATH = DATA_DIR / "road_network.json"

_HTTP_TIMEOUT = 60.0
_USER_AGENT = "Safeway-NightWalkSafety-DataIngest/0.1 (offline conversion script)"
# 公用 Overpass 主機常常忙碌，準備幾個鏡像站輪流試（跟 ingest_open_data.py 同一份清單）。
OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
]

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

# 比照 osmnx network_type="walk" 的預設篩選：只排除明顯不可步行／不存在的
# 分類（快速道路、施工中、規劃中、廢棄……），其餘 highway=* 一律視為可通行，
# 含人行道、巷弄、樓梯等——夜間安全路徑本來就該考慮這些小路。
_EXCLUDED_HIGHWAY_PATTERN = "abandoned|bus_guideway|construction|cycleway|motor|no|planned|platform|proposed|raceway"


def _build_query(districts: list[str]) -> str:
    # 注意：district area 不能直接寫成 `area(area.city)["name"="X"]`——那個寫法
    # 不會真的按「屬於 area.city 範圍內」過濾，同名行政區會全部混進來（例如
    # 「信義區」臺北市、基隆市都有，會把基隆信義區的路網也抓進來）。正確做法
    # 是先用 `relation[...](area.city)` 過濾出屬於臺北市的 relation，
    # 再用 `map_to_area` 轉成後續查詢可用的 area。
    district_names = "|".join(districts)
    return (
        "[out:json][timeout:120];\n"
        'area["name"="臺北市"]["boundary"="administrative"]->.city;\n'
        f'relation["boundary"="administrative"]["name"~"^({district_names})$"](area.city)->.rel;\n'
        ".rel map_to_area->.districts;\n"
        'way["highway"]["area"!~"yes"]["access"!~"private"]'
        f'["highway"!~"{_EXCLUDED_HIGHWAY_PATTERN}"](area.districts);\n'
        "(._;>;);\n"
        "out body;"
    )


def fetch_elements(client: httpx.Client, districts: list[str]) -> list[dict[str, Any]]:
    query = _build_query(districts)
    print(f"查詢範圍：{'、'.join(districts)}")

    elements = None
    last_error: Exception | None = None
    for attempt in range(3):
        for url in OVERPASS_URLS:
            try:
                print(f"  查詢 Overpass：{url}（第 {attempt + 1} 輪）")
                response = client.post(
                    url,
                    data={"data": query},
                    headers={"User-Agent": _USER_AGENT},
                    timeout=150.0,
                )
                response.raise_for_status()
                elements = response.json()["elements"]
                break
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
                continue
        if elements is not None:
            break
        time.sleep(5 * (attempt + 1))

    if elements is None:
        raise RuntimeError(f"所有 Overpass 端點都失敗了，最後一次錯誤：{last_error}")
    return elements


def build_graph(elements: list[dict[str, Any]]) -> dict[str, Any]:
    """把 Overpass 回傳的 node/way element 轉成 `{nodes, edges}`。

    node id 沿用 OSM node id（加 `osm_` 前綴，跟舊格式的 `n_x_y` 區隔），edge
    是每條 way 裡相鄰兩個 node 的線段。這樣圖上的節點除了真實路口，也含
    way 本身的形狀頂點，路徑幾何會比「只留路口」精細；取樣／計分邏輯
    （app/engine/safety.py）不區分節點種類，不受影響。
    """
    node_coords: dict[int, tuple[float, float]] = {
        el["id"]: (el["lat"], el["lon"]) for el in elements if el["type"] == "node"
    }

    edge_keys: set[tuple[int, int]] = set()
    edges: list[dict[str, str]] = []
    used_node_ids: set[int] = set()
    for el in elements:
        if el["type"] != "way":
            continue
        way_nodes = el.get("nodes", [])
        for a, b in zip(way_nodes, way_nodes[1:]):
            if a not in node_coords or b not in node_coords or a == b:
                continue
            key = (a, b) if a < b else (b, a)
            if key in edge_keys:
                continue
            edge_keys.add(key)
            used_node_ids.add(a)
            used_node_ids.add(b)
            edges.append({"from": f"osm_{a}", "to": f"osm_{b}"})

    nodes = [
        {"id": f"osm_{node_id}", "lat": round(lat, 6), "lng": round(lng, 6)}
        for node_id, (lat, lng) in node_coords.items()
        if node_id in used_node_ids
    ]
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

    with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
        elements = fetch_elements(client, args.district)

    graph = build_graph(elements)
    if not graph["nodes"] or not graph["edges"]:
        raise SystemExit("Overpass 沒回傳任何路段，可能是行政區名稱查不到或查詢逾時，不覆蓋本地檔案。")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ROAD_NETWORK_PATH.write_text(json.dumps(graph, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已寫入 {ROAD_NETWORK_PATH.relative_to(BACKEND_DIR)}：{len(graph['nodes'])} 個節點、{len(graph['edges'])} 條邊")


if __name__ == "__main__":
    main()
