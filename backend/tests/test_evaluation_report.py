import pytest

from evaluation.report import bootstrap_mean_ci, render_markdown, summarize


def _row(
    scenario_id: str,
    google_safety: float,
    fastest_safety: float,
    safest_safety: float,
    danger_google: int = 0,
    danger_safest: int = 0,
    detour: float = 2.0,
) -> dict:
    def route(prefix: str, safety: float, danger: int, distance: float) -> dict:
        return {
            f"{prefix}_distance_m": distance,
            f"{prefix}_duration_min_est": distance / 80,
            f"{prefix}_avg_safety_score": safety,
            f"{prefix}_lit_coverage_ratio": 0.5,
            f"{prefix}_convenience_stores_within_80m": 1,
            f"{prefix}_police_within_150m": 0,
            f"{prefix}_detour_vs_fastest_min": 0.0,
            f"{prefix}_danger_zone_passed": danger,
        }

    row: dict = {"scenario_id": scenario_id, "straight_line_distance_m": 500.0}
    row.update(route("google", google_safety, danger_google, 520.0))
    row.update(route("our_fastest", fastest_safety, 0, 500.0))
    row.update(route("our_safest", safest_safety, danger_safest, 560.0))
    row["our_safest_detour_vs_fastest_min"] = detour
    return row


def test_summarize_win_rate_and_mean_delta():
    rows = [
        _row("s0", google_safety=0.5, fastest_safety=0.5, safest_safety=0.8, danger_google=1, danger_safest=0),
        _row("s1", google_safety=0.6, fastest_safety=0.6, safest_safety=0.65, danger_google=0, danger_safest=0),
        _row("s2", google_safety=0.7, fastest_safety=0.7, safest_safety=0.6, danger_google=0, danger_safest=1),
    ]

    summary = summarize(rows, n_failed=2)

    assert summary.n_success == 3
    assert summary.n_failed == 2
    assert summary.win_rate_safest_vs_google == pytest.approx(2 / 3)
    expected_delta = ((0.8 - 0.5) + (0.65 - 0.6) + (0.6 - 0.7)) / 3
    assert summary.mean_delta_safety_vs_google == pytest.approx(expected_delta)
    expected_danger_delta = ((0 - 1) + (0 - 0) + (1 - 0)) / 3
    assert summary.mean_delta_danger_zone_vs_google == pytest.approx(expected_danger_delta)


def test_summarize_requires_at_least_one_row():
    with pytest.raises(ValueError):
        summarize([])


def test_summarize_computes_safety_gain_per_extra_minute():
    rows = [_row("s0", google_safety=0.5, fastest_safety=0.5, safest_safety=0.7, detour=4.0)]
    summary = summarize(rows)
    assert summary.safety_gain_per_extra_minute == pytest.approx(0.2 / 4.0)


def test_bootstrap_ci_collapses_to_point_for_constant_values():
    lo, hi = bootstrap_mean_ci([0.1, 0.1, 0.1, 0.1], iterations=200)
    assert lo == pytest.approx(0.1)
    assert hi == pytest.approx(0.1)


def test_bootstrap_ci_is_reproducible_for_fixed_seed():
    values = [0.1, 0.3, -0.2, 0.4, 0.05]
    first = bootstrap_mean_ci(values, iterations=500, seed=0)
    second = bootstrap_mean_ci(values, iterations=500, seed=0)
    assert first == second


def test_bootstrap_ci_brackets_the_sample_mean_roughly():
    import statistics

    values = [0.1, 0.3, 0.2, 0.4, 0.15, 0.25]
    lo, hi = bootstrap_mean_ci(values, iterations=2000)
    assert lo <= statistics.mean(values) <= hi


def test_render_markdown_includes_failures_and_headline_numbers():
    rows = [_row("s0", 0.5, 0.5, 0.8), _row("s1", 0.6, 0.6, 0.7)]
    summary = summarize(rows, n_failed=1)

    text = render_markdown(summary, failures=[("s99", "google_no_route")])

    assert "s99" in text
    assert "google_no_route" in text
    assert f"{summary.win_rate_safest_vs_google:.0%}" in text
    assert "Safeway vs Google Maps" in text
