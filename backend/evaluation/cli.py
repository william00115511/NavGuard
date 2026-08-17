"""CLI 入口：跑批次評估、產報告。

用法（在 backend/ 目錄下執行，才能 import app.* / interfaces）：
    python -m evaluation.cli run --n 30 --seed 42
    python -m evaluation.cli report
"""

from __future__ import annotations

import argparse
import asyncio

from evaluation.config import DEFAULT_N_SCENARIOS, DEFAULT_SEED
from evaluation.report import write_report
from evaluation.runner import run_batch, write_failures_csv, write_results_csv


def _run(args: argparse.Namespace) -> None:
    rows, failures = asyncio.run(run_batch(n=args.n, seed=args.seed))
    write_results_csv(rows)
    write_failures_csv(failures)
    print(f"完成：{len(rows)} 組成功比對，{len(failures)} 組未納入。已寫入 evaluation/output/results.csv")
    if failures:
        reasons: dict[str, int] = {}
        for failure in failures:
            key = failure.reason.split(":", 1)[0]
            reasons[key] = reasons.get(key, 0) + 1
        print("未納入原因統計：")
        for reason, count in sorted(reasons.items(), key=lambda kv: -kv[1]):
            print(f"  {reason}: {count}")


def _report(args: argparse.Namespace) -> None:
    summary = write_report()
    print("報告已產出：evaluation/output/report.md、evaluation/output/report.html")
    print(
        f"樣本數 {summary.n_success}，勝率 {summary.win_rate_safest_vs_google:.0%}，"
        f"平均安全分數差異 {summary.mean_delta_safety_vs_google:+.3f}"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="evaluation.cli", description="NavGuard vs Google Maps 安全性驗證")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="跑一批起訖點的路線比對，寫入 output/results.csv")
    run_parser.add_argument("--n", type=int, default=DEFAULT_N_SCENARIOS, help="取樣起訖點組數")
    run_parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="取樣用的隨機種子")
    run_parser.set_defaults(func=_run)

    report_parser = subparsers.add_parser("report", help="從 output/results.csv 產出 report.md / report.html")
    report_parser.set_defaults(func=_report)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
