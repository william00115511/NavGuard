"""批次評估主流程：對每組取樣起訖點，同時算出「我們的安全路線」「我們的最快
路線」「Google 實際會導航的路線」三條路線的同一組 metrics，寫成
output/results.csv（成功案例）與 output/failures.csv（失敗案例，不悄悄丟棄）。

三條路線的 metrics 都來自同一個 app/engine/metrics.py 的 build_metrics()：
我們自己的兩條路線是引擎（app/engine/route_engine.py）內部呼叫的結果，
Google 路線則是把解碼後的 polyline 餵進 evaluation/path_scoring.py 的
path_computation_from_polyline() 之後一樣呼叫 build_metrics()。這是整份報告
「兩條路線用同一把尺」的唯一入口。
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.config import DEFAULT_ALPHA, Settings, WALK_SPEED_MPS
from app.data.store import get_data_store
from app.engine.graph import get_road_graph
from app.engine.metrics import build_metrics
from app.engine.route_engine import LocalDataRouteEngine
from app.engine.safety import build_scoring_profile, filter_active_points
from evaluation.config import (
    DEFAULT_N_SCENARIOS,
    DEFAULT_SEED,
    FAILURES_CSV_PATH,
    RESULTS_CSV_PATH,
)
from evaluation.google_routes_client import (
    CachingGoogleRoutesClient,
    GoogleRoutesClient,
    HttpGoogleRoutesClient,
)
from evaluation.path_scoring import path_computation_from_polyline
from evaluation.scenarios import sample_scenarios
from interfaces import NoRouteFoundError, OutOfCoverageError, RouteMetrics

_ROUTE_METRIC_FIELDS = (
    "distance_m",
    "duration_min_est",
    "avg_safety_score",
    "lit_coverage_ratio",
    "help_points_within_50m",
    "police_within_150m",
    "detour_vs_fastest_min",
)


@dataclass(frozen=True)
class Failure:
    scenario_id: str
    reason: str


def _metrics_row(prefix: str, metrics: RouteMetrics) -> dict[str, object]:
    row: dict[str, object] = {f"{prefix}_{name}": getattr(metrics, name) for name in _ROUTE_METRIC_FIELDS}
    row[f"{prefix}_danger_zone_passed"] = metrics.passed_landmarks.get("danger_zone", 0)
    return row


def _default_google_client() -> GoogleRoutesClient:
    api_key = Settings().routes_api_key.get_secret_value()
    if not api_key or api_key == "YOUR_API_KEY_HERE":
        raise RuntimeError(
            "跑真的批次評估需要 ROUTES_API_KEY（.env）；"
            "測試或 dry-run 請改用 evaluation.google_routes_client.FakeGoogleRoutesClient"
        )
    return CachingGoogleRoutesClient(HttpGoogleRoutesClient(api_key=api_key))


async def run_batch(
    n: int = DEFAULT_N_SCENARIOS,
    seed: int = DEFAULT_SEED,
    google_client: Optional[GoogleRoutesClient] = None,
) -> tuple[list[dict], list[Failure]]:
    graph = get_road_graph()
    data_store = get_data_store()
    static_points = filter_active_points(data_store.static_points)
    profile = build_scoring_profile(data_store.categories, static_points)
    engine = LocalDataRouteEngine(data_store=data_store, graph=graph)

    if google_client is None:
        google_client = _default_google_client()

    scenarios = sample_scenarios(graph, n=n, seed=seed)

    rows: list[dict] = []
    failures: list[Failure] = []

    for scenario in scenarios:
        try:
            result = await engine.calculate_route(
                scenario.origin, scenario.destination, priority_alpha=DEFAULT_ALPHA
            )
        except OutOfCoverageError as exc:
            failures.append(Failure(scenario.scenario_id, f"out_of_coverage: {exc}"))
            continue
        except NoRouteFoundError as exc:
            failures.append(Failure(scenario.scenario_id, f"no_route_found: {exc}"))
            continue

        our_fastest = next(r for r in result.routes if r.id == "fastest")
        our_safest = next(r for r in result.routes if r.id == "safest")

        google_result = await google_client.compute_walking_route(scenario.origin, scenario.destination)
        if google_result is None:
            failures.append(Failure(scenario.scenario_id, "google_no_route"))
            continue

        google_computation = path_computation_from_polyline(
            list(google_result.polyline), static_points, profile
        )
        # 跟 our_safest 的 detour_vs_fastest_min 用同一個對照組（我們自己算出的
        # 最快路線），才能同一張表裡直接比較「多花幾分鐘」。
        detour_min = (
            (google_computation.distance_m - our_fastest.metrics.distance_m) / WALK_SPEED_MPS / 60
        )
        google_metrics = build_metrics(
            google_computation, static_points, profile, detour_vs_fastest_min=detour_min
        )

        row: dict[str, object] = {
            "scenario_id": scenario.scenario_id,
            "origin_lat": scenario.origin.lat,
            "origin_lng": scenario.origin.lng,
            "destination_lat": scenario.destination.lat,
            "destination_lng": scenario.destination.lng,
            "straight_line_distance_m": round(scenario.straight_line_distance_m, 1),
        }
        row.update(_metrics_row("google", google_metrics))
        row.update(_metrics_row("our_fastest", our_fastest.metrics))
        row.update(_metrics_row("our_safest", our_safest.metrics))
        rows.append(row)

    return rows, failures


def write_results_csv(rows: list[dict], path: Path = RESULTS_CSV_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        if not rows:
            f.write("")
            return
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_failures_csv(failures: list[Failure], path: Path = FAILURES_CSV_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["scenario_id", "reason"])
        for failure in failures:
            writer.writerow([failure.scenario_id, failure.reason])
