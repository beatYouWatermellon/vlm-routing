#!/usr/bin/env python3
"""
scripts/run_agent.py

Main execution script: run the VLM routing agent on an ISPD benchmark.
"""

import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.routing_toolkit import RoutingToolkit
from src.visual_renderer import VisualRenderer
from src.vlm_policy import VLMPolicyGenerator
from src.heuristic_policy import HeuristicPolicyGenerator
from src.agent_controller import RoutingAgent
from src.ispd_evaluator import ISPDEvaluator
from src.utils import load_config


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

  # Disable fine-grained actions and use only coarse actions
  python scripts/run_agent.py --benchmark ispd18_test1 --no-fine-actions

  # Use a specific EDA provider
  python scripts/run_agent.py --benchmark ispd18_test1 --eda-provider openroad
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
        "--max-iter", type=int, default=None,
        help="Maximum optimization iterations (overrides config)"
    )
    parser.add_argument(
        "--patience", type=int, default=None,
        help="Early stopping patience (overrides config)"
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
    parser.add_argument(
        "--enable-fine-actions",
        action="store_true",
        default=None,
        help="Enable fine-grained segment/via/net actions"
    )
    parser.add_argument(
        "--no-fine-actions",
        action="store_true",
        help="Disable fine-grained actions (use legacy coarse actions only)"
    )
    parser.add_argument(
        "--enable-local-eval",
        action="store_true",
        default=None,
        help="Enable local DRC evaluation for small fine-grained edits"
    )
    parser.add_argument(
        "--heuristic-policy",
        action="store_true",
        help="Use deterministic heuristic policy instead of VLM (no API key needed)"
    )
    parser.add_argument(
        "--eda-provider", default=None,
        help="EDA provider to use (currently only 'openroad' is supported)"
    )

    args = parser.parse_args()

    # Load configuration and apply CLI overrides
    config = load_config("config/agent_config.yaml")
    agent_cfg = config.get("agent", {})
    vlm_cfg = config.get("vlm", {})

    max_iter = args.max_iter if args.max_iter is not None else agent_cfg.get("max_iterations", 15)
    patience = args.patience if args.patience is not None else agent_cfg.get("patience", 5)

    enable_fine = agent_cfg.get("enable_fine_actions", True)
    if args.enable_fine_actions is True:
        enable_fine = True
    if args.no_fine_actions:
        enable_fine = False

    enable_local_eval = agent_cfg.get("enable_local_eval", False)
    if args.enable_local_eval is True:
        enable_local_eval = True

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
    print(f"  VLM:   {args.vlm_model or vlm_cfg.get('model', 'default')}")
    print(f"  Fine actions: {enable_fine}")
    print(f"  Local eval: {enable_local_eval}")
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

    if args.heuristic_policy:
        vlm = HeuristicPolicyGenerator()
    else:
        vlm = VLMPolicyGenerator(
            model=args.vlm_model or vlm_cfg.get("model"),
            client_type=args.vlm_client or vlm_cfg.get("client_type"),
        )

    agent = RoutingAgent(
        toolkit=toolkit,
        renderer=renderer,
        vlm=vlm,
        work_dir=str(work_dir),
        max_iterations=max_iter,
        patience=patience,
        enable_fine_actions=enable_fine,
        local_edit_threshold=agent_cfg.get("local_edit_threshold_nets", 3),
        enable_local_eval=enable_local_eval,
    )

    print("\n[Start] Running optimization...\n")
    best_def, best_metrics = agent.optimize(
        initial_def=str(def_file),
        guide_file=str(guide_file) if guide_file.exists() else None,
    )

    baseline_metrics = None
    baseline_json = work_dir / "checkpoints" / "iter_-0001.json"
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
