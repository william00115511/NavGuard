"""讀 results.csv / failures.csv，彙整統計並產出 report.md + report.html。

統計方法刻意保守、誠實：
- 用配對差異（同一組起訖點 our_safest 與 google 的差）而非兩組獨立平均相減，
  避免起訖點難度不同造成的變異蓋過真正的差異。
- 平均安全分數差異附 bootstrap 95% 信賴區間，不是只給一個看起來漂亮的平均值。
- 一定同時揭露代價面（our_safest 比 our_fastest 多走幾分鐘），不能只講好處。
- 失敗案例（Google 無路線／起訖點不連通）獨立列出，不悄悄從分母移除。
"""

from __future__ import annotations

import csv
import html
import random
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from evaluation.config import (
    BOOTSTRAP_CONFIDENCE,
    BOOTSTRAP_ITERATIONS,
    FAILURES_CSV_PATH,
    MAX_SCENARIO_DISTANCE_M,
    MIN_SCENARIO_DISTANCE_M,
    REPORT_HTML_PATH,
    REPORT_MD_PATH,
    RESULTS_CSV_PATH,
    SAFEST_ROUTE_ALPHA,
)

_PREFIXES = ("google", "our_fastest", "our_safest")
_PREFIX_LABELS = {
    "google": "Google 路線",
    "our_fastest": "我們的最快路線（α=0）",
    "our_safest": f"我們的安全路線（α={SAFEST_ROUTE_ALPHA}）",
}

_REQUIRED_FLOAT_SUFFIXES = ("distance_m", "duration_min_est", "avg_safety_score", "detour_vs_fastest_min")
_OPTIONAL_FLOAT_SUFFIXES = ("lit_coverage_ratio",)
_OPTIONAL_INT_SUFFIXES = ("convenience_stores_within_80m", "police_within_150m")
_REQUIRED_INT_SUFFIXES = (
    "danger_zone_passed",
    "other_positive_landmarks_passed",
    "other_negative_landmarks_passed",
)


# ---------- 讀檔 ----------


def load_results(path: Path = RESULTS_CSV_PATH) -> list[dict]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open(encoding="utf-8") as f:
        rows = []
        for raw in csv.DictReader(f):
            row: dict[str, object] = {
                "scenario_id": raw["scenario_id"],
                "straight_line_distance_m": float(raw["straight_line_distance_m"]),
            }
            for prefix in _PREFIXES:
                for suffix in _REQUIRED_FLOAT_SUFFIXES:
                    row[f"{prefix}_{suffix}"] = float(raw[f"{prefix}_{suffix}"])
                for suffix in _OPTIONAL_FLOAT_SUFFIXES:
                    value = raw[f"{prefix}_{suffix}"]
                    row[f"{prefix}_{suffix}"] = float(value) if value != "" else None
                for suffix in _OPTIONAL_INT_SUFFIXES:
                    value = raw[f"{prefix}_{suffix}"]
                    row[f"{prefix}_{suffix}"] = int(value) if value != "" else None
                for suffix in _REQUIRED_INT_SUFFIXES:
                    row[f"{prefix}_{suffix}"] = int(raw[f"{prefix}_{suffix}"])
            rows.append(row)
        return rows


def load_failures(path: Path = FAILURES_CSV_PATH) -> list[tuple[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader, None)
        return [(row[0], row[1]) for row in reader if row]


# ---------- 統計 ----------


def bootstrap_mean_ci(
    values: list[float],
    iterations: int = BOOTSTRAP_ITERATIONS,
    confidence: float = BOOTSTRAP_CONFIDENCE,
    seed: int = 0,
) -> tuple[float, float]:
    """純 stdlib 的 percentile bootstrap 信賴區間，不引入 scipy/numpy。seed 固定
    只是讓報告可重現，不影響信賴區間的統計意義。"""
    if len(values) < 2:
        only = values[0] if values else 0.0
        return (only, only)
    rng = random.Random(seed)
    n = len(values)
    means = [statistics.mean(values[rng.randrange(n)] for _ in range(n)) for _ in range(iterations)]
    means.sort()
    alpha = (1 - confidence) / 2
    lower_idx = int(alpha * iterations)
    upper_idx = min(int((1 - alpha) * iterations), iterations - 1)
    return (means[lower_idx], means[upper_idx])


@dataclass(frozen=True)
class Summary:
    n_success: int
    n_failed: int
    mean_safety: dict[str, float]
    median_safety: dict[str, float]
    stdev_safety: dict[str, float]
    win_rate_safest_vs_google: float
    win_rate_safest_vs_fastest: float
    mean_delta_safety_vs_google: float
    delta_safety_ci: tuple[float, float]
    mean_delta_danger_zone_vs_google: float
    mean_delta_other_positive_landmarks_vs_google: float
    mean_delta_other_negative_landmarks_vs_google: float
    mean_delta_lit_coverage_vs_google: Optional[float]
    mean_delta_convenience_stores_vs_google: Optional[float]
    mean_delta_police_vs_google: Optional[float]
    mean_detour_min_safest_vs_fastest: float
    safety_gain_per_extra_minute: Optional[float]


def _paired_deltas(rows: list[dict], a_prefix: str, b_prefix: str, suffix: str) -> list[float]:
    deltas = []
    for row in rows:
        a = row[f"{a_prefix}_{suffix}"]
        b = row[f"{b_prefix}_{suffix}"]
        if a is None or b is None:
            continue
        deltas.append(a - b)
    return deltas


def summarize(rows: list[dict], n_failed: int = 0) -> Summary:
    if not rows:
        raise ValueError("沒有成功案例可以彙整統計，先確認 results.csv 是否為空")

    safety = {p: [row[f"{p}_avg_safety_score"] for row in rows] for p in _PREFIXES}
    mean_safety = {p: statistics.mean(v) for p, v in safety.items()}
    median_safety = {p: statistics.median(v) for p, v in safety.items()}
    stdev_safety = {p: (statistics.stdev(v) if len(v) > 1 else 0.0) for p, v in safety.items()}

    safety_deltas_vs_google = _paired_deltas(rows, "our_safest", "google", "avg_safety_score")
    win_rate_vs_google = sum(1 for d in safety_deltas_vs_google if d > 0) / len(safety_deltas_vs_google)

    safety_deltas_vs_fastest = _paired_deltas(rows, "our_safest", "our_fastest", "avg_safety_score")
    win_rate_vs_fastest = sum(1 for d in safety_deltas_vs_fastest if d > 0) / len(safety_deltas_vs_fastest)

    danger_deltas = _paired_deltas(rows, "our_safest", "google", "danger_zone_passed")
    other_positive_deltas = _paired_deltas(rows, "our_safest", "google", "other_positive_landmarks_passed")
    other_negative_deltas = _paired_deltas(rows, "our_safest", "google", "other_negative_landmarks_passed")
    lit_deltas = _paired_deltas(rows, "our_safest", "google", "lit_coverage_ratio")
    convenience_deltas = _paired_deltas(rows, "our_safest", "google", "convenience_stores_within_80m")
    police_deltas = _paired_deltas(rows, "our_safest", "google", "police_within_150m")

    detour_minutes = [row["our_safest_detour_vs_fastest_min"] for row in rows]
    mean_detour = statistics.mean(detour_minutes)
    mean_delta_safety = statistics.mean(safety_deltas_vs_google)

    return Summary(
        n_success=len(rows),
        n_failed=n_failed,
        mean_safety=mean_safety,
        median_safety=median_safety,
        stdev_safety=stdev_safety,
        win_rate_safest_vs_google=win_rate_vs_google,
        win_rate_safest_vs_fastest=win_rate_vs_fastest,
        mean_delta_safety_vs_google=mean_delta_safety,
        delta_safety_ci=bootstrap_mean_ci(safety_deltas_vs_google),
        mean_delta_danger_zone_vs_google=statistics.mean(danger_deltas) if danger_deltas else 0.0,
        mean_delta_other_positive_landmarks_vs_google=(
            statistics.mean(other_positive_deltas) if other_positive_deltas else 0.0
        ),
        mean_delta_other_negative_landmarks_vs_google=(
            statistics.mean(other_negative_deltas) if other_negative_deltas else 0.0
        ),
        mean_delta_lit_coverage_vs_google=(statistics.mean(lit_deltas) if lit_deltas else None),
        mean_delta_convenience_stores_vs_google=(statistics.mean(convenience_deltas) if convenience_deltas else None),
        mean_delta_police_vs_google=(statistics.mean(police_deltas) if police_deltas else None),
        mean_detour_min_safest_vs_fastest=mean_detour,
        safety_gain_per_extra_minute=(mean_delta_safety / mean_detour if mean_detour > 1e-6 else None),
    )


# ---------- Markdown ----------


def _fmt_optional(value: Optional[float], fmt: str) -> str:
    return fmt.format(value) if value is not None else "無資料"


def render_markdown(summary: Summary, failures: list[tuple[str, str]]) -> str:
    ci_lo, ci_hi = summary.delta_safety_ci
    lines = [
        "# NavGuard vs Google Maps 安全性驗證報告",
        "",
        f"- 成功比對樣本數：{summary.n_success}（另有 {summary.n_failed} 組未納入，見文末「未納入案例」）",
        f"- 起訖點：從路網隨機取樣，直線距離介於 {MIN_SCENARIO_DISTANCE_M:.0f}m–{MAX_SCENARIO_DISTANCE_M:.0f}m",
        f"- 我們的安全路線使用 α={SAFEST_ROUTE_ALPHA}；我們的最快路線 α=0 作為代價面對照組",
        "- 三條路線的安全分數都由 app/engine/safety.py 與 app/engine/metrics.py 既有、未修改的公式計算，"
        "細節見文末「方法論」",
        "",
        "## 核心結論",
        "",
        f"- **{summary.win_rate_safest_vs_google:.0%}** 的案例中，我們的安全路線平均安全分數高於 Google 路線",
        f"- 平均安全分數差異（我們的安全路線 − Google）：**{summary.mean_delta_safety_vs_google:+.3f}**"
        f"（95% bootstrap 信賴區間：[{ci_lo:+.3f}, {ci_hi:+.3f}]，{BOOTSTRAP_ITERATIONS} 次重抽樣）",
        f"- 危險點位通過數（我們的安全路線 − Google，平均）：{summary.mean_delta_danger_zone_vs_google:+.2f}",
        f"- **代價面**：我們的安全路線平均比最快路線多走 {summary.mean_detour_min_safest_vs_fastest:.1f} 分鐘"
        + (
            f"，換算每多走 1 分鐘，安全分數平均提升 {summary.safety_gain_per_extra_minute:.4f}"
            if summary.safety_gain_per_extra_minute is not None
            else ""
        ),
        "",
        "## 三條路線的安全分數分布",
        "",
        "| 路線 | 平均 | 中位數 | 標準差 |",
        "|---|---|---|---|",
    ]
    for prefix in _PREFIXES:
        lines.append(
            f"| {_PREFIX_LABELS[prefix]} | {summary.mean_safety[prefix]:.3f} | "
            f"{summary.median_safety[prefix]:.3f} | {summary.stdev_safety[prefix]:.3f} |"
        )
    lines += [
        "",
        "## 其他指標差異（我們的安全路線 − Google，平均值）",
        "",
        "| 指標 | 差異 |",
        "|---|---|",
        f"| 危險點位通過數（負面） | {summary.mean_delta_danger_zone_vs_google:+.2f} |",
        f"| 其他負面類別通過數（如夜店鬧事區、地下道死角） | "
        f"{summary.mean_delta_other_negative_landmarks_vs_google:+.2f} |",
        f"| 其他正面類別通過數（如路燈） | {summary.mean_delta_other_positive_landmarks_vs_google:+.2f} |",
        f"| 路燈覆蓋率（30m 內有路燈的採樣點比例） | {_fmt_optional(summary.mean_delta_lit_coverage_vs_google, '{:+.3f}')} |",
        f"| 80m 內超商數（正面） | {_fmt_optional(summary.mean_delta_convenience_stores_vs_google, '{:+.2f}')} |",
        f"| 150m 內警局數（正面） | {_fmt_optional(summary.mean_delta_police_vs_google, '{:+.2f}')} |",
        "",
        "## 方法論",
        "",
        "1. 起訖點從既有路網圖節點隨機取樣（固定 seed 可重現），刻意不手動挑選案例，避免選出對我們有利的組合。",
        "2. 我們的兩條路線直接呼叫 `LocalDataRouteEngine.calculate_route()`（未修改），跟正式 API 走同一套引擎。",
        "3. Google 路線來自 Google Routes API（walking mode）解碼後的座標序列，用跟我們建路網 edge 完全相同的"
        "取樣方式切成一串「偽 edge」，再套用跟我們自己路線**完全相同**的 `raw_edge_score` / `sigmoid_safety` /"
        " `build_metrics`（app/engine/safety.py、app/engine/metrics.py），沒有另外為 Google 路線寫一套算分邏輯。",
        "4. 除了危險點位／超商／警局三個具名類別，`categories.json` 其餘類別（目前是正面的路燈、負面的"
        "夜店鬧事區與地下道死角）改用 `RouteMetrics.passed_landmarks` 依 `effect`（正/負）分流加總，"
        "跟計分公式一樣只認正負號、不寫死具體類別名稱——之後在 `categories.json` 新增類別，這裡不用改程式碼。",
        "5. 安全分數差異採配對比較（同一組起訖點兩條路線相減），而非兩組獨立平均相減，避免起訖點難易度不同"
        "造成的變異蓋過真正的差異。",
        "",
        "**限制與提醒**：安全分數是輔助決策依據，不是安全保證；本報告只反映本次資料集（路燈／可求助據點／"
        "危險點位／警局）與取樣範圍下的結果，不代表任何路線在真實情境中絕對安全或絕對危險。",
    ]

    if failures:
        lines += ["", f"## 未納入案例（{len(failures)} 組）", "", "| scenario_id | 原因 |", "|---|---|"]
        for scenario_id, reason in failures:
            lines.append(f"| {scenario_id} | {reason} |")

    return "\n".join(lines) + "\n"


# ---------- HTML（自包含，inline SVG，無外部依賴） ----------


def _svg_bar_chart(labels: list[str], values: list[float], value_fmt: str = "{:.3f}") -> str:
    width = 520
    bar_height = 30
    gap = 14
    left_margin = 190
    right_margin = 70
    top_margin = 8
    chart_width = width - left_margin - right_margin
    max_value = max(max(values), 1e-6)
    height = top_margin + len(values) * (bar_height + gap) + gap

    parts = [
        f'<svg viewBox="0 0 {width} {height}" width="100%" style="max-width:{width}px" '
        f'xmlns="http://www.w3.org/2000/svg" role="img">'
    ]
    for i, (label, value) in enumerate(zip(labels, values)):
        y = top_margin + gap + i * (bar_height + gap)
        bar_w = max(2.0, (value / max_value) * chart_width)
        parts.append(
            f'<text x="{left_margin - 10}" y="{y + bar_height / 2 + 4:.1f}" text-anchor="end" '
            f'font-size="13" fill="#1f2933">{html.escape(label)}</text>'
            f'<rect x="{left_margin}" y="{y}" width="{bar_w:.1f}" height="{bar_height}" '
            f'fill="#2f6f4f" rx="4"/>'
            f'<text x="{left_margin + bar_w + 8:.1f}" y="{y + bar_height / 2 + 4:.1f}" '
            f'font-size="13" fill="#1f2933">{value_fmt.format(value)}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def _svg_win_rate_bar(win_rate: float) -> str:
    width = 520
    height = 56
    bar_x = 20
    bar_w = width - 40
    fill_w = max(2.0, bar_w * win_rate)
    return (
        f'<svg viewBox="0 0 {width} {height}" width="100%" style="max-width:{width}px" '
        f'xmlns="http://www.w3.org/2000/svg" role="img">'
        f'<rect x="{bar_x}" y="16" width="{bar_w}" height="24" fill="#e4e7eb" rx="12"/>'
        f'<rect x="{bar_x}" y="16" width="{fill_w:.1f}" height="24" fill="#2f6f4f" rx="12"/>'
        f'<text x="{bar_x}" y="52" font-size="13" fill="#1f2933">0%</text>'
        f'<text x="{bar_x + bar_w}" y="52" font-size="13" fill="#1f2933" text-anchor="end">100%</text>'
        f'<text x="{bar_x + bar_w / 2:.1f}" y="33" font-size="13" font-weight="600" fill="#ffffff" '
        f'text-anchor="middle">{win_rate:.0%}</text>'
        f"</svg>"
    )


def render_html(summary: Summary, failures: list[tuple[str, str]]) -> str:
    ci_lo, ci_hi = summary.delta_safety_ci
    safety_labels = [_PREFIX_LABELS[p] for p in _PREFIXES]
    safety_values = [summary.mean_safety[p] for p in _PREFIXES]

    metrics_rows = "".join(
        f"<tr><td>{label}</td><td>{value}</td></tr>"
        for label, value in (
            ("危險點位通過數（負面，平均差異）", f"{summary.mean_delta_danger_zone_vs_google:+.2f}"),
            (
                "其他負面類別通過數（夜店鬧事區、地下道死角等，平均差異）",
                f"{summary.mean_delta_other_negative_landmarks_vs_google:+.2f}",
            ),
            (
                "其他正面類別通過數（路燈等，平均差異）",
                f"{summary.mean_delta_other_positive_landmarks_vs_google:+.2f}",
            ),
            ("路燈覆蓋率（平均差異）", _fmt_optional(summary.mean_delta_lit_coverage_vs_google, "{:+.3f}")),
            ("80m 內超商數（正面，平均差異）", _fmt_optional(summary.mean_delta_convenience_stores_vs_google, "{:+.2f}")),
            ("150m 內警局數（正面，平均差異）", _fmt_optional(summary.mean_delta_police_vs_google, "{:+.2f}")),
        )
    )

    failures_section = ""
    if failures:
        failure_rows = "".join(
            f"<tr><td>{html.escape(scenario_id)}</td><td>{html.escape(reason)}</td></tr>"
            for scenario_id, reason in failures
        )
        failures_section = f"""
        <h2>未納入案例（{len(failures)} 組）</h2>
        <table><thead><tr><th>scenario_id</th><th>原因</th></tr></thead>
        <tbody>{failure_rows}</tbody></table>
        """

    trade_off = (
        f"每多走 1 分鐘，安全分數平均提升 {summary.safety_gain_per_extra_minute:.4f}"
        if summary.safety_gain_per_extra_minute is not None
        else "本批樣本平均detour為 0，無法換算比率"
    )

    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<title>NavGuard vs Google Maps 安全性驗證報告</title>
<style>
  body {{ font-family: -apple-system, "Segoe UI", "Noto Sans TC", sans-serif; background:#f7f8fa;
         color:#1f2933; margin:0; padding:32px 16px; }}
  main {{ max-width: 760px; margin: 0 auto; }}
  h1 {{ font-size: 1.6rem; margin-bottom: 4px; }}
  h2 {{ font-size: 1.15rem; margin-top: 36px; border-top: 1px solid #e4e7eb; padding-top: 20px; }}
  .meta {{ color:#52606d; font-size: 0.92rem; line-height:1.6; }}
  .cards {{ display:flex; gap:12px; flex-wrap:wrap; margin: 20px 0; }}
  .card {{ flex:1; min-width:180px; background:#fff; border:1px solid #e4e7eb; border-radius:10px;
           padding:14px 16px; }}
  .card .label {{ font-size:0.82rem; color:#52606d; }}
  .card .value {{ font-size:1.4rem; font-weight:600; margin-top:4px; }}
  table {{ width:100%; border-collapse: collapse; margin-top: 8px; font-size:0.92rem; }}
  th, td {{ text-align:left; padding:8px 10px; border-bottom:1px solid #e4e7eb; }}
  th {{ color:#52606d; font-weight:600; }}
  .chart-wrap {{ background:#fff; border:1px solid #e4e7eb; border-radius:10px; padding:16px; margin-top:12px; }}
  .disclaimer {{ font-size:0.85rem; color:#52606d; background:#fff; border:1px solid #e4e7eb;
                border-radius:10px; padding:14px 16px; margin-top:36px; }}
</style>
</head>
<body>
<main>
  <h1>NavGuard vs Google Maps 安全性驗證報告</h1>
  <p class="meta">
    成功比對樣本數 {summary.n_success}（另有 {summary.n_failed} 組未納入）·
    起訖點隨機取樣，直線距離 {MIN_SCENARIO_DISTANCE_M:.0f}m–{MAX_SCENARIO_DISTANCE_M:.0f}m ·
    我們的安全路線 α={SAFEST_ROUTE_ALPHA}
  </p>

  <div class="cards">
    <div class="card">
      <div class="label">安全路線勝過 Google 路線的比例</div>
      <div class="value">{summary.win_rate_safest_vs_google:.0%}</div>
    </div>
    <div class="card">
      <div class="label">平均安全分數差異（我們 − Google）</div>
      <div class="value">{summary.mean_delta_safety_vs_google:+.3f}</div>
    </div>
    <div class="card">
      <div class="label">平均代價</div>
      <div class="value">+{summary.mean_detour_min_safest_vs_fastest:.1f} 分鐘</div>
    </div>
  </div>

  <h2>安全路線勝率（our_safest.avg_safety_score &gt; google.avg_safety_score）</h2>
  <div class="chart-wrap">{_svg_win_rate_bar(summary.win_rate_safest_vs_google)}</div>

  <h2>三條路線的平均安全分數</h2>
  <div class="chart-wrap">{_svg_bar_chart(safety_labels, safety_values)}</div>
  <p class="meta">
    95% bootstrap 信賴區間（我們的安全路線 − Google）：[{ci_lo:+.3f}, {ci_hi:+.3f}]，
    {BOOTSTRAP_ITERATIONS} 次重抽樣
  </p>

  <h2>其他指標差異（我們的安全路線 − Google，平均值）</h2>
  <table><thead><tr><th>指標</th><th>差異</th></tr></thead><tbody>{metrics_rows}</tbody></table>

  <h2>代價面（誠實揭露）</h2>
  <p class="meta">
    我們的安全路線平均比最快路線多走 {summary.mean_detour_min_safest_vs_fastest:.1f} 分鐘；{trade_off}。
  </p>

  {failures_section}

  <div class="disclaimer">
    方法論：Google 路線與我們自己的路線套用完全相同的評分公式
    （app/engine/safety.py、app/engine/metrics.py，未修改），差異只來自路線本身，不是評分方式不同。
    起訖點隨機取樣，未手動挑選案例。安全分數是輔助決策依據，不是安全保證，本報告不代表任何路線在真實情境
    中絕對安全或絕對危險。
  </div>
</main>
</body>
</html>
"""


def write_report(
    md_path: Path = REPORT_MD_PATH,
    html_path: Path = REPORT_HTML_PATH,
    results_path: Path = RESULTS_CSV_PATH,
    failures_path: Path = FAILURES_CSV_PATH,
) -> Summary:
    rows = load_results(results_path)
    failures = load_failures(failures_path)
    summary = summarize(rows, n_failed=len(failures))

    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(render_markdown(summary, failures), encoding="utf-8")
    html_path.write_text(render_html(summary, failures), encoding="utf-8")
    return summary
