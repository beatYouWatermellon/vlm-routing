#!/usr/bin/env python3
"""
scripts/run_evaluation_suite.py

Run baseline + heuristic-agent on a set of ISPD benchmarks and produce a
comparison table.  This script is useful for proving algorithm effectiveness
without requiring VLM API keys.

Example:
    python scripts/run_evaluation_suite.py \
        --data-dir ./data/ispd2018 \
        --benchmarks ispd18_test1 ispd18_test3 ispd18_test4 \
        --output-dir ./outputs/evaluation \
        --max-iter 5
"""

import sys
import json
import argparse
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent.parent))


def run_baseline(
    benchmark: str, data_dir: Path, output_dir: Path, openroad: str = "openroad"
) -> Optional[Dict]:
    """Run the baseline flow and return the report dict."""
    cmd = [
        sys.executable,
        "scripts/run_baseline.py",
        "--benchmark",
        benchmark,
        "--data-dir",
        str(data_dir),
        "--output-dir",
        str(output_dir / "baseline"),
        "--openroad",
        openroad,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[ERROR] Baseline failed for {benchmark}:\n{result.stderr}")
        return None

    report_path = (
        output_dir / "baseline" / benchmark / "evaluation" / f"{benchmark}_report.json"
    )
    if not report_path.exists():
        return None
    with open(report_path) as f:
        return json.load(f)


def run_agent(
    benchmark: str,
    data_dir: Path,
    output_dir: Path,
    max_iter: int,
    patience: int,
    resolution: int,
    openroad: str = "openroad",
) -> Optional[Dict]:
    """Run the heuristic agent and return the evaluation report."""
    cmd = [
        sys.executable,
        "scripts/run_agent.py",
        "--benchmark",
        benchmark,
        "--data-dir",
        str(data_dir),
        "--work-dir",
        str(output_dir / "agent"),
        "--heuristic-policy",
        "--max-iter",
        str(max_iter),
        "--patience",
        str(patience),
        "--resolution",
        str(resolution),
        "--openroad",
        openroad,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[ERROR] Agent failed for {benchmark}:\n{result.stderr}")
        return None

    report_path = (
        output_dir / "agent" / benchmark / "evaluation" / f"{benchmark}_report.json"
    )
    if not report_path.exists():
        return None
    with open(report_path) as f:
        return json.load(f)


def pct(delta: float, base: float) -> str:
    if base == 0:
        return "N/A"
    return f"{delta / base * 100:+.2f}"


def format_table(results: List[Tuple[str, Optional[Dict], Optional[Dict]]]) -> str:
    """Return a Markdown table comparing baseline vs agent."""
    header = (
        "| Benchmark | Baseline DRC | Agent DRC | Δ DRC | Baseline WL | Agent WL | Δ WL% | "
        "Baseline Via | Agent Via | Δ Via | Baseline Score | Agent Score | Δ Score% |"
    )
    sep = (
        "|-----------|-------------:|----------:|------:|------------:|---------:|------:|"
        "-------------:|----------:|------:|---------------:|------------:|---------:|"
    )
    lines = [header, sep]

    for benchmark, base_report, agent_report in results:
        if base_report is None or agent_report is None:
            lines.append(
                f"| {benchmark} | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A |"
            )
            continue

        b = base_report["optimized"]
        a = agent_report["optimized"]

        drc_delta = a["drc_total"] - b["drc_total"]
        wl_delta = a["wirelength_um"] - b["wirelength_um"]
        via_delta = a["via_count"] - b["via_count"]
        score_delta = a["score"] - b["score"]

        lines.append(
            f"| {benchmark} | "
            f"{b['drc_total']} | {a['drc_total']} | {drc_delta:+d} | "
            f"{b['wirelength_um']:.2f} | {a['wirelength_um']:.2f} | {pct(wl_delta, b['wirelength_um'])} | "
            f"{b['via_count']} | {a['via_count']} | {via_delta:+d} | "
            f"{b['score']:.2f} | {a['score']:.2f} | {pct(score_delta, b['score'])} |"
        )

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Run baseline + heuristic agent on ISPD benchmarks"
    )
    parser.add_argument("--data-dir", default="./data/ispd2018")
    parser.add_argument(
        "--benchmarks",
        nargs="+",
        default=["ispd18_test1", "ispd18_test3", "ispd18_test4", "ispd18_test5"],
    )
    parser.add_argument("--output-dir", default="./outputs/evaluation")
    parser.add_argument("--max-iter", type=int, default=5)
    parser.add_argument("--patience", type=int, default=2)
    parser.add_argument("--resolution", type=int, default=256)
    parser.add_argument("--openroad", default="openroad")
    parser.add_argument("--json", default=None, help="Write results JSON to this path")
    parser.add_argument(
        "--skip-agent-if-clean",
        action="store_true",
        help="Skip agent run when baseline is already DRC-clean",
    )
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    results: List[Tuple[str, Optional[Dict], Optional[Dict]]] = []

    for benchmark in args.benchmarks:
        print(f"\n{'='*60}")
        print(f"Benchmark: {benchmark}")
        print(f"{'='*60}")

        base_report = run_baseline(benchmark, data_dir, output_dir, args.openroad)
        if base_report is None:
            results.append((benchmark, None, None))
            continue

        b = base_report["optimized"]
        print(
            f"Baseline: DRC={b['drc_total']} WL={b['wirelength_um']:.2f} "
            f"Via={b['via_count']} Score={b['score']:.2f}"
        )

        agent_report = None
        if args.skip_agent_if_clean and b["drc_total"] == 0:
            print("Baseline is DRC-clean; skipping agent run.")
            agent_report = base_report
        else:
            agent_report = run_agent(
                benchmark,
                data_dir,
                output_dir,
                args.max_iter,
                args.patience,
                args.resolution,
                args.openroad,
            )

        if agent_report:
            a = agent_report["optimized"]
            print(
                f"Agent:    DRC={a['drc_total']} WL={a['wirelength_um']:.2f} "
                f"Via={a['via_count']} Score={a['score']:.2f}"
            )

        results.append((benchmark, base_report, agent_report))

    print("\n" + "=" * 60)
    print("Comparison Table")
    print("=" * 60)
    table = format_table(results)
    print(table)

    if args.json:
        json_path = Path(args.json)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        with open(json_path, "w") as f:
            json.dump(
                {
                    "benchmarks": args.benchmarks,
                    "results": [
                        {
                            "benchmark": b,
                            "baseline": base_report,
                            "agent": agent_report,
                        }
                        for b, base_report, agent_report in results
                    ],
                },
                f,
                indent=2,
            )
        print(f"\nResults written to: {json_path}")


if __name__ == "__main__":
    main()
