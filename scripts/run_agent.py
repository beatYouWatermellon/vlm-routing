#!/usr/bin/env python3
"""
scripts/run_agent.py

Main execution script: run the VLM routing agent on an ISPD benchmark.
"""

import sys
import argparse
from pathlib import Path

import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.routing_toolkit import RoutingToolkit
from src.visual_renderer import VisualRenderer
from src.vlm_policy import VLMPolicyGenerator
from src.agent_controller import RoutingAgent
from src.ispd_evaluator import ISPDEvaluator


def main():
    parser = argparse.ArgumentParser(
        description="VLM Routing Agent - ISPD Benchmark Optimization",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # ISPD 2018 test1
  python scripts/run_agent.py --benchmark ispd18_test1 --data-dir ./data/ispd2018

  # ISPD 2019 test1 with custom iterations
  python scripts/run_agent.py --benchmark ispd19_test1 --data-dir ./data/ispd2019 --max-iter 20

  # Use Claude instead of Gemini
  python scripts/run_agent.py --benchmark ispd18_test1 --vlm-model claude-3-5-sonnet-20241022
        """
    )

    parser.add_argument(
        "--benchmark", required=True,
        help="ISPD benchmark name (e.g., ispd18_test1, ispd19_test1)"
    )
    parser.add_argument(
        "--data-dir", default="./data/ispd2018",
        help="ISPD data root directory"
    )
    parser.add_argument(
        "--work-dir", default="./outputs/agent_runs",
        help="Working output directory"
    )
    parser.add_argument(
        "--max-iter", type=int, default=15,
        help="Maximum optimization iterations"
    )
    parser.add_argument(
        "--patience", type=int, default=5,
        help="Early stopping patience"
    )
    parser.add_argument(
        "--openroad", default="openroad",
        help="OpenROAD executable path"
    )
    parser.add_argument(
        "--vlm-model", default=None,
        help="VLM model name (defaults to ANTHROPIC_MODEL / OPENAI_MODEL / gemini-2.5-flash)"
    )
    parser.add_argument(
        "--vlm-client", default=None,
        help="VLM client type (gemini|anthropic|openai)"
    )
    parser.add_argument(
        "--resolution", type=int, default=512,
        help="Visualization resolution"
    )
    parser.add_argument(
        "--scoring-mode", default="simplified",
        choices=["simplified", "ispd2019", "ispd2018"],
        help="ISPD scoring mode"
    )

    args = parser.parse_args()

    data_dir = Path(args.data_dir) / args.benchmark
    lef_file = data_dir / f"{args.benchmark}.lef"
    def_file = data_dir / f"{args.benchmark}.def"
    guide_file = data_dir / f"{args.benchmark}.guide"

    if not def_file.exists():
        print(f"[ERROR] DEF file not found: {def_file}")
        print(
            f"  Expected structure: {args.data_dir}/{args.benchmark}/{args.benchmark}.def"
        )
        sys.exit(1)

    if not lef_file.exists():
        print(f"[WARNING] LEF file not found: {lef_file}")

    work_dir = Path(args.work_dir) / args.benchmark
    work_dir.mkdir(parents=True, exist_ok=True)

    print(f"{'='*60}")
    print(f"Benchmark: {args.benchmark}")
    print(f"  LEF:   {lef_file}")
    print(f"  DEF:   {def_file}")
    print(f"  Guide: {guide_file if guide_file.exists() else 'N/A'}")
    print(f"  Work:  {work_dir}")
    print(f"  VLM:   {args.vlm_model}")
    print(f"{'='*60}\n")

    print("[Init] Initializing components...")

    toolkit = RoutingToolkit(
        openroad_exe=args.openroad,
        work_dir=str(work_dir / "toolkit"),
        lef_file=str(lef_file),
    )

    renderer = VisualRenderer(
        output_dir=str(work_dir / "visual_states"),
        resolution=args.resolution,
    )

    vlm = VLMPolicyGenerator(
        model=args.vlm_model,
        client_type=args.vlm_client,
    )

    agent = RoutingAgent(
        toolkit=toolkit,
        renderer=renderer,
        vlm=vlm,
        work_dir=str(work_dir),
        max_iterations=args.max_iter,
        patience=args.patience,
    )

    print("\n[Start] Running optimization...\n")
    best_def, best_metrics = agent.optimize(
        initial_def=str(def_file),
        guide_file=str(guide_file) if guide_file.exists() else None,
    )

    baseline_metrics = None
    baseline_json = work_dir / "checkpoints" / "iter_-001.json"
    if baseline_json.exists():
        import json as _json
        with open(baseline_json) as f:
            baseline_data = _json.load(f)
        baseline_metrics = type(best_metrics)(**baseline_data)

    if baseline_metrics:
        print("\n[Eval] Running ISPD evaluation...")
        evaluator = ISPDEvaluator(str(work_dir / "evaluation"))
        evaluator.evaluate_and_compare(
            baseline_metrics=baseline_metrics,
            optimized_metrics=best_metrics,
            benchmark_name=args.benchmark,
            scoring_mode=args.scoring_mode,
        )

    print(f"\n{'='*60}")
    print("Optimization Complete!")
    print(f"{'='*60}")
    print(f"Best DEF: {best_def}")
    print(f"Final DRC: {best_metrics.drc_total}")
    print(f"Final WL:  {best_metrics.wirelength:.2f} um")
    print(f"Final Via: {best_metrics.via_count}")
    print(f"Output directory: {work_dir}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
