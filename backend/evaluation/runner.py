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

from app.config import Settings, WALK_SPEED_MPS
from app.data.store import get_data_store
from app.engine.graph import get_road_graph
from app.engine.metrics import build_metrics
from app.engine.route_engine import LocalDataRouteEngine
from app.engine.safety import ScoringProfile, build_scoring_profile, filter_active_points
from evaluation.config import (
    DEFAULT_N_SCENARIOS,
    DEFAULT_SEED,
    FAILURES_CSV_PATH,
    RESULTS_CSV_PATH,
    SAFEST_ROUTE_ALPHA,
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
    "detour_vs_fastest_min",
)


@dataclass(frozen=True)
class Failure:
    scenario_id: str
    reason: str


def _metrics_row(prefix: str, metrics: RouteMetrics, profile: ScoringProfile) -> dict[str, object]:
    row: dict[str, object] = {f"{prefix}_{name}": getattr(metrics, name) for name in _ROUTE_METRIC_FIELDS}
    # AGENTS.md §4.6 修訂：danger_zone 也改回傳具體點位列表，不再計入
    # passed_landmarks，評估報表改對列表 len()（0 代表這次路線沒經過，
    # 不是「這區沒有危險點位資料」，跟 police/help 兩欄位一致）。
    row[f"{prefix}_danger_zone_passed"] = len(metrics.danger_zones) if metrics.danger_zones is not None else 0
    # AGENTS.md §4.6 修訂：route_ready 改回傳具體點位列表而非數量，評估報表
    # 仍需要純數字做統計比較，這裡自己對列表 len()（None 代表無資料，維持
    # §1 原則 3 的「缺資料不填 0」語意）。
    row[f"{prefix}_convenience_stores_within_80m"] = (
        len(metrics.convenience_stores) if metrics.convenience_stores is not None else None
    )
    row[f"{prefix}_police_within_150m"] = (
        len(metrics.police_stations) if metrics.police_stations is not None else None
    )
    # passed_landmarks 涵蓋除了 danger_zone/police_station/convenience_store
    # 以外的所有類別（目前主要是正面的 street_light，以及負面的
    # night_club_hazard／underpass_hazard，見 categories.json）。計分公式
    # （app/engine/safety.py）本來就只認正負號、不認具體類別名稱（§3.3），
    # 這裡比照辦理：用 profile.categories[name].effect 分流加總成正/負兩欄，
    # 不額外為新類別硬編碼欄位，新增一個負面類別也不用改這裡的程式碼。
    positive_count = 0
    negative_count = 0
    for name, count in metrics.passed_landmarks.items():
        category = profile.categories.get(name)
        if category is None:
            continue
        if category.effect == "positive":
            positive_count += count
        else:
            negative_count += count
    row[f"{prefix}_other_positive_landmarks_passed"] = positive_count
    row[f"{prefix}_other_negative_landmarks_passed"] = negative_count
    return row


def _default_google_client() -> GoogleRoutesClient:
    api_key = Settings().maps_api_key.get_secret_value()
    if not api_key or api_key == "YOUR_API_KEY_HERE":
        raise RuntimeError(
            "跑真的批次評估需要 MAPS_API_KEY（.env）；"
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
                scenario.origin, scenario.destination, priority_alpha=SAFEST_ROUTE_ALPHA
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
        row.update(_metrics_row("google", google_metrics, profile))
        row.update(_metrics_row("our_fastest", our_fastest.metrics, profile))
        row.update(_metrics_row("our_safest", our_safest.metrics, profile))
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
