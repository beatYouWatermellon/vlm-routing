#!/usr/bin/env python3
"""
scripts/run_baseline.py

Run the OpenROAD default baseline routing flow to produce a comparison baseline.
"""

import sys
import argparse
from pathlib import Path

import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.routing_toolkit import RoutingToolkit
from src.ispd_evaluator import ISPDEvaluator


def main():
    parser = argparse.ArgumentParser(description="Run OpenROAD baseline routing")
    parser.add_argument("--benchmark", required=True, help="ISPD benchmark name")
    parser.add_argument("--data-dir", default="./data/ispd2018")
    parser.add_argument("--output-dir", default="./outputs/baseline")
    parser.add_argument("--openroad", default="openroad")
    parser.add_argument("--scoring-mode", default="simplified")
    args = parser.parse_args()

    data_dir = Path(args.data_dir) / args.benchmark
    lef_file = data_dir / f"{args.benchmark}.lef"
    def_file = data_dir / f"{args.benchmark}.def"
    guide_file = data_dir / f"{args.benchmark}.guide"

    output_dir = Path(args.output_dir) / args.benchmark
    output_dir.mkdir(parents=True, exist_ok=True)

    toolkit = RoutingToolkit(
        openroad_exe=args.openroad,
        work_dir=str(output_dir),
        lef_file=str(lef_file),
    )

    print(f"Running baseline for {args.benchmark}...")
    routed_def = toolkit.run_baseline_flow(
        str(def_file),
        str(guide_file) if guide_file.exists() else None,
    )

    metrics = toolkit.extract_metrics(routed_def)

    print("\nBaseline Results:")
    print(f"  DRC: {metrics.drc_total}")
    print(f"  WL:  {metrics.wirelength:.2f} um")
    print(f"  Via: {metrics.via_count}")
    print(f"  DEF: {routed_def}")

    evaluator = ISPDEvaluator(str(output_dir / "evaluation"))
    score = evaluator.compute_score(metrics, args.scoring_mode)
    print(f"  Score: {score:.2f}")


if __name__ == "__main__":
    main()
