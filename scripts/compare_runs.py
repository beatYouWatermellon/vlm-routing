#!/usr/bin/env python3
"""
scripts/compare_runs.py

Compare multiple agent/baseline runs across ISPD benchmarks.

The first directory is treated as the reference baseline.  For every other
run we print the absolute metrics and the delta versus the reference.

Example:
    python scripts/compare_runs.py \
        ./outputs/baseline \
        ./outputs/agent_coarse \
        ./outputs/agent_fine \
        --benchmarks ispd18_test1 ispd18_test3 ispd18_test4 ispd18_test5
"""

import sys
import json
import argparse
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent.parent))


def load_report(work_dir: Path, benchmark: str) -> Optional[Dict]:
    """Load an ISPD evaluation report if it exists."""
    report_path = work_dir / benchmark / "evaluation" / f"{benchmark}_report.json"
    if report_path.exists():
        with open(report_path, "r") as f:
            return json.load(f)
    return None


def _fmt(value, fmt: str = "{:}") -> str:
    if value is None:
        return "N/A"
    if isinstance(value, (int, float)):
        return fmt.format(value)
    return str(value)


def _delta(reference: Optional[Dict], current: Optional[Dict]) -> Dict[str, str]:
    """Return formatted deltas for DRC, WL, Via and score."""
    if reference is None or current is None:
        return {"drc": "N/A", "wl": "N/A", "via": "N/A", "score": "N/A"}

    rd = reference.get("drc_total", 0)
    cd = current.get("drc_total", 0)
    rw = reference.get("wirelength_um", 0)
    cw = current.get("wirelength_um", 0)
    rv = reference.get("via_count", 0)
    cv = current.get("via_count", 0)
    rs = reference.get("score", 0)
    cs = current.get("score", 0)

    def pct(delta: float, base: float) -> str:
        if base > 0:
            return f"{delta / base * 100:+.2f}%"
        return "0.00%"

    return {
        "drc": f"{cd - rd:+d} ({pct(cd - rd, rd)})",
        "wl": f"{cw - rw:+.2f} ({pct(cw - rw, rw)})",
        "via": f"{cv - rv:+d} ({pct(cv - rv, rv)})",
        "score": f"{cs - rs:+.2f} ({pct(cs - rs, rs)})",
    }


def build_row(
    benchmark: str,
    reports: List[Optional[Dict]],
    labels: List[str],
) -> List[str]:
    """Return table cells for one benchmark across all runs."""
    cells = [benchmark]

    reference = None
    if reports and reports[0] is not None:
        reference = reports[0].get("baseline") or reports[0].get("optimized")

    for report in reports:
        if report is None:
            cells.extend(["N/A"] * 4)
            continue
        optimized = report.get("optimized", {})
        cells.append(_fmt(optimized.get("drc_total"), "{:d}"))
        cells.append(_fmt(optimized.get("wirelength_um"), "{:.2f}"))
        cells.append(_fmt(optimized.get("via_count"), "{:d}"))
        cells.append(_fmt(optimized.get("score"), "{:.2f}"))

    # Delta columns for every non-reference run.
    for report in reports[1:]:
        if report is None:
            cells.extend(["N/A"] * 4)
            continue
        current = report.get("optimized") or report.get("baseline")
        d = _delta(reference, current)
        cells.extend([d["drc"], d["wl"], d["via"], d["score"]])

    return cells


def build_header(labels: List[str]) -> Tuple[List[str], List[str]]:
    """Return (header, separator alignment spec)."""
    header = ["Benchmark"]
    align = ["<"]  # left-align benchmark name

    for label in labels:
        header.extend([f"{label}_DRC", f"{label}_WL", f"{label}_Via", f"{label}_Score"])
        align.extend([">", ">", ">", ">"])

    if len(labels) > 1:
        for label in labels[1:]:
            header.extend(
                [f"Δ{label}_DRC", f"Δ{label}_WL", f"Δ{label}_Via", f"Δ{label}_Score"]
            )
            align.extend([">", ">", ">", ">"])

    return header, align


def main():
    parser = argparse.ArgumentParser(
        description="Compare multiple routing runs across ISPD benchmarks"
    )
    parser.add_argument(
        "run_dirs",
        nargs="+",
        help="Output directories to compare (first is reference baseline)",
    )
    parser.add_argument(
        "--labels",
        nargs="+",
        default=None,
        help="Human-readable labels for each run directory",
    )
    parser.add_argument(
        "--benchmarks",
        nargs="+",
        default=["ispd18_test1", "ispd18_test3", "ispd18_test4", "ispd18_test5"],
        help="Benchmark names to include in the comparison",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional JSON file to write the comparison table",
    )
    args = parser.parse_args()

    run_dirs = [Path(d) for d in args.run_dirs]
    labels = args.labels or [d.name for d in run_dirs]

    if len(labels) != len(run_dirs):
        print("[ERROR] Number of labels must match number of run directories")
        sys.exit(1)

    header, align = build_header(labels)
    table: List[List[str]] = [header]

    for benchmark in args.benchmarks:
        reports = [load_report(d, benchmark) for d in run_dirs]
        table.append(build_row(benchmark, reports, labels))

    # Compute column widths
    widths = [
        max(len(row[i]) for row in table) for i in range(len(header))
    ]

    def format_row(row: List[str]) -> str:
        parts = []
        for cell, w, a in zip(row, widths, align):
            if a == "<":
                parts.append(cell.ljust(w))
            else:
                parts.append(cell.rjust(w))
        return "| " + " | ".join(parts) + " |"

    separator = "+-" + "-+-".join("-" * w for w in widths) + "-+"

    print(separator)
    for i, row in enumerate(table):
        print(format_row(row))
        if i == 0:
            print(separator)
    print(separator)

    # Optional JSON output
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        result = {
            "labels": labels,
            "benchmarks": args.benchmarks,
            "header": header,
            "rows": table[1:],
        }
        with open(output_path, "w") as f:
            json.dump(result, f, indent=2)
        print(f"\nComparison table written to: {output_path}")


if __name__ == "__main__":
    main()
