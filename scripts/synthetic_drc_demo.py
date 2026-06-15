#!/usr/bin/env python3
"""
scripts/synthetic_drc_demo.py

Controlled experiment that proves the agent can reduce DRC violations.

1. Start from a clean OpenROAD baseline of ispd18_sample.
2. Intentionally corrupt one net (net1234) so that its Metal3 segment overlaps
   with net1233, creating a short.
3. Re-run detailed route and confirm DRC > 0.
4. Run the heuristic agent on the corrupted design.
5. Report before/after DRC and other metrics.
"""

import sys
import shutil
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.routing_toolkit import RoutingToolkit
from src.visual_renderer import VisualRenderer
from src.heuristic_policy import HeuristicPolicyGenerator
from src.agent_controller import RoutingAgent


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "ispd2018" / "ispd18_sample"
WORK_DIR = PROJECT_ROOT / "outputs" / "synthetic_drc_demo"
BENCHMARK = "ispd18_sample"


def run(cmd, **kwargs):
    print(f"$ {' '.join(str(c) for c in cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True, **kwargs)
    if result.returncode != 0:
        print(result.stdout)
        print(result.stderr)
        raise RuntimeError(f"Command failed: {cmd}")
    return result


def main():
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    lef = DATA_DIR / f"{BENCHMARK}.lef"
    original_def = DATA_DIR / f"{BENCHMARK}.def"
    guide = DATA_DIR / f"{BENCHMARK}.guide"

    print("=" * 70)
    print("Synthetic DRC Demonstration")
    print("=" * 70)

    # Step 1: baseline route
    print("\n[Step 1] Running clean baseline...")
    toolkit = RoutingToolkit(
        openroad_exe="openroad",
        work_dir=str(WORK_DIR / "toolkit"),
        lef_file=str(lef),
    )
    clean_def = toolkit.run_baseline_flow(
        str(original_def), str(guide) if guide.exists() else None
    )
    clean_metrics = toolkit.extract_metrics(clean_def)
    print(
        f"Clean baseline: DRC={clean_metrics.drc_total} "
        f"WL={clean_metrics.wirelength:.2f} Via={clean_metrics.via_count}"
    )

    # Step 2: corrupt net1234 to overlap with net1233
    print("\n[Step 2] Injecting a synthetic short...")
    from src.def_editor import DEFEditor

    from src.def_editor import DEFEditor

    corrupted_def = WORK_DIR / "corrupted_ispd18_sample.def"
    editor = DEFEditor(clean_def)
    # net1233 has a Metal3 segment at y=73150 from x=88200 to x=97800.
    # Add a Metal3 rectangle to net1234 that overlaps that segment.
    edited_def = editor.replace_net_routing(
        "net1234",
        "ROUTED Metal2 ( 95400 73530 ) ( * 74670 )\n"
        "      NEW Metal1 ( 95400 74670 ) ( 100200 * )\n"
        "      NEW Metal2 ( 100200 74670 ) ( * 84550 )\n"
        "      NEW Metal2 ( 100200 84550 ) ( 100600 * )\n"
        "      NEW Metal3 ( 93000 73150 ) RECT ( -2000 -100 2000 100 )",
    )
    shutil.copy(edited_def, corrupted_def)
    print(f"Corrupted DEF written to: {corrupted_def}")

    # Step 3: run detailed route on corrupted DEF and measure DRC
    print("\n[Step 3] Re-running detailed route on corrupted design...")
    corrupted_routed_def = toolkit.run_incremental_route(str(corrupted_def))
    corrupted_metrics = toolkit.extract_metrics(corrupted_routed_def)
    print(
        f"Corrupted design: DRC={corrupted_metrics.drc_total} "
        f"WL={corrupted_metrics.wirelength:.2f} Via={corrupted_metrics.via_count}"
    )

    if corrupted_metrics.drc_total == 0:
        print("[ERROR] Synthetic short was not detected as a DRC violation.")
        return 1

    # Step 4: run heuristic agent
    print("\n[Step 4] Running heuristic agent to repair the design...")
    agent_work = WORK_DIR / "agent"
    renderer = VisualRenderer(
        output_dir=str(agent_work / "visual_states"),
        resolution=256,
    )
    agent = RoutingAgent(
        toolkit=toolkit,
        renderer=renderer,
        vlm=HeuristicPolicyGenerator(),
        work_dir=str(agent_work),
        max_iterations=5,
        patience=2,
        enable_fine_actions=True,
        enable_local_eval=False,
    )
    best_def, best_metrics = agent.optimize(initial_def=str(corrupted_routed_def))
    print(
        f"Agent result: DRC={best_metrics.drc_total} "
        f"WL={best_metrics.wirelength:.2f} Via={best_metrics.via_count}"
    )

    # Step 5: summary
    print("\n" + "=" * 70)
    print("Summary")
    print("=" * 70)
    print(f"Clean baseline:   DRC={clean_metrics.drc_total}")
    print(f"After corruption: DRC={corrupted_metrics.drc_total}")
    print(f"After agent:      DRC={best_metrics.drc_total}")
    print(
        f"DRC reduction:    {corrupted_metrics.drc_total - best_metrics.drc_total} "
        f"({(corrupted_metrics.drc_total - best_metrics.drc_total) / max(corrupted_metrics.drc_total, 1) * 100:.1f}%)"
    )

    if best_metrics.drc_total < corrupted_metrics.drc_total:
        print("\n[PASS] Agent successfully reduced DRC violations.")
        return 0
    else:
        print("\n[FAIL] Agent did not reduce DRC violations.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
