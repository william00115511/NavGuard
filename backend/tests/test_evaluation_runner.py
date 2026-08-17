"""端到端跑一次 runner（用 FakeGoogleRoutesClient，不打真的 Google API），確保
整條管線（取樣 -> 引擎 -> Google 路線評分 -> CSV）接得起來。真實資料集
（app/data/points/street_light.json 等）會被載入，所以 n 故意設得很小，避免
測試變慢。"""

import asyncio
import csv

from evaluation.google_routes_client import FakeGoogleRoutesClient
from evaluation.runner import run_batch, write_failures_csv, write_results_csv


def test_run_batch_produces_rows_with_expected_columns(tmp_path):
    rows, failures = asyncio.run(run_batch(n=2, seed=1, google_client=FakeGoogleRoutesClient()))

    assert isinstance(failures, list)
    assert rows, "至少要有一組成功案例才能驗證欄位"

    row = rows[0]
    for prefix in ("google", "our_fastest", "our_safest"):
        assert f"{prefix}_avg_safety_score" in row
        assert f"{prefix}_distance_m" in row
        assert f"{prefix}_danger_zone_passed" in row
    assert 0.0 <= row["google_avg_safety_score"] <= 1.0
    assert 0.0 <= row["our_safest_avg_safety_score"] <= 1.0


def test_write_results_and_failures_csv_round_trip(tmp_path):
    rows, failures = asyncio.run(run_batch(n=2, seed=2, google_client=FakeGoogleRoutesClient()))

    results_path = tmp_path / "results.csv"
    failures_path = tmp_path / "failures.csv"
    write_results_csv(rows, path=results_path)
    write_failures_csv(failures, path=failures_path)

    assert results_path.exists()
    with results_path.open(encoding="utf-8") as f:
        written_rows = list(csv.DictReader(f))
    assert len(written_rows) == len(rows)

    assert failures_path.exists()
    with failures_path.open(encoding="utf-8") as f:
        header = next(csv.reader(f))
    assert header == ["scenario_id", "reason"]
