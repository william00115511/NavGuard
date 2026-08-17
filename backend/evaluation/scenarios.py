"""隨機從路網節點取樣起訖點對，供批次評估使用。

只從既有 RoadGraph.nodes 挑，天生就在路網覆蓋範圍內，不需要另外做覆蓋範圍
檢查。隨機取樣（而非手動挑案例）是刻意的：要證明「我們的路線比較安全」，
樣本不能是精心挑選讓自己贏的案例，固定 seed 只是為了讓報告可重現，不是為了
挑出對我們有利的組合。

這裡只用直線距離做初步過濾；兩點在路網圖上是否連通留給 runner.py 呼叫路徑
引擎時處理——不連通的案例會被記錄成失敗案例，而不是讓取樣迴圈卡住重試。
"""

import random
from dataclasses import dataclass

from app.engine.geo import haversine_m
from app.engine.graph import RoadGraph
from evaluation.config import (
    MAX_SAMPLE_ATTEMPTS_PER_SCENARIO,
    MAX_SCENARIO_DISTANCE_M,
    MIN_SCENARIO_DISTANCE_M,
)
from interfaces import LatLng


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    origin: LatLng
    destination: LatLng
    straight_line_distance_m: float


def sample_scenarios(
    graph: RoadGraph,
    n: int,
    seed: int,
    min_distance_m: float = MIN_SCENARIO_DISTANCE_M,
    max_distance_m: float = MAX_SCENARIO_DISTANCE_M,
) -> list[Scenario]:
    """固定 (graph, n, seed) 永遠取樣出同一批起訖點——報告要能重現，取樣過程
    不能每次跑都不一樣。取樣不到 n 組時回傳目前已取樣到的（呼叫端自行決定
    要不要視為錯誤），不會無窮迴圈：超過 n * MAX_SAMPLE_ATTEMPTS_PER_SCENARIO
    次嘗試就停止。"""
    rng = random.Random(seed)
    node_ids = list(graph.nodes.keys())
    if len(node_ids) < 2:
        raise ValueError("路網節點數不足以取樣起訖點對")

    scenarios: list[Scenario] = []
    seen_pairs: set[tuple[str, str]] = set()
    attempts = 0
    max_total_attempts = n * MAX_SAMPLE_ATTEMPTS_PER_SCENARIO

    while len(scenarios) < n and attempts < max_total_attempts:
        attempts += 1
        origin_id, destination_id = rng.sample(node_ids, 2)
        pair_key = (min(origin_id, destination_id), max(origin_id, destination_id))
        if pair_key in seen_pairs:
            continue

        origin = graph.nodes[origin_id]
        destination = graph.nodes[destination_id]
        distance = haversine_m(origin, destination)
        if not (min_distance_m <= distance <= max_distance_m):
            continue

        seen_pairs.add(pair_key)
        scenarios.append(
            Scenario(
                scenario_id=f"s{len(scenarios):03d}",
                origin=origin,
                destination=destination,
                straight_line_distance_m=distance,
            )
        )

    return scenarios
