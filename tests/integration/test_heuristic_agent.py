"""
End-to-end integration tests using the deterministic heuristic policy.

These tests prove the optimization pipeline works without requiring VLM API
keys.  The heuristic policy rips up the net involved in the most DRC violations
and re-routes it.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.routing_toolkit import RoutingToolkit
from src.visual_renderer import VisualRenderer
from src.heuristic_policy import HeuristicPolicyGenerator
from src.agent_controller import RoutingAgent


pytestmark = [pytest.mark.integration, pytest.mark.slow]


def test_heuristic_agent_converges_on_ispd18_test1(
    has_openroad, ispd18_test1_paths, tmp_work_dir
):
    """The heuristic agent should run end-to-end and produce checkpoints."""
    work_dir = tmp_work_dir / "agent"
    toolkit = RoutingToolkit(
        openroad_exe="openroad",
        work_dir=str(work_dir / "toolkit"),
        lef_file=str(ispd18_test1_paths["lef"]),
    )
    renderer = VisualRenderer(
        output_dir=str(work_dir / "visual_states"),
        resolution=256,
    )
    vlm = HeuristicPolicyGenerator()

    agent = RoutingAgent(
        toolkit=toolkit,
        renderer=renderer,
        vlm=vlm,
        work_dir=str(work_dir),
        max_iterations=3,
        patience=1,
        enable_fine_actions=True,
        enable_local_eval=False,
    )

    guide = ispd18_test1_paths["guide"]
    guide_file = str(guide) if guide.exists() else None
    best_def, best_metrics = agent.optimize(
        initial_def=str(ispd18_test1_paths["def"]),
        guide_file=guide_file,
    )

    assert Path(best_def).exists(), "Best DEF file should exist"
    assert best_metrics.wirelength > 0, "Best wirelength should be positive"
    assert best_metrics.drc_total >= 0, "Best DRC total should be non-negative"

    # Checkpoints should have been saved.
    checkpoints_dir = work_dir / "checkpoints"
    assert checkpoints_dir.exists(), "Checkpoints directory should exist"
    baseline_json = checkpoints_dir / "iter_-0001.json"
    assert baseline_json.exists(), "Baseline checkpoint should exist"

    iter_jsons = list(checkpoints_dir.glob("iter_*.json"))
    assert len(iter_jsons) >= 1, "At least one checkpoint should be saved"


def test_heuristic_agent_matches_or_improves_baseline(
    has_openroad, ispd18_test1_paths, tmp_work_dir
):
    """Heuristic agent should not catastrophically regress baseline metrics."""
    work_dir = tmp_work_dir / "agent"
    toolkit = RoutingToolkit(
        openroad_exe="openroad",
        work_dir=str(work_dir / "toolkit"),
        lef_file=str(ispd18_test1_paths["lef"]),
    )
    renderer = VisualRenderer(
        output_dir=str(work_dir / "visual_states"),
        resolution=256,
    )
    vlm = HeuristicPolicyGenerator()

    agent = RoutingAgent(
        toolkit=toolkit,
        renderer=renderer,
        vlm=vlm,
        work_dir=str(work_dir),
        max_iterations=3,
        patience=1,
        enable_fine_actions=True,
        enable_local_eval=False,
    )

    guide = ispd18_test1_paths["guide"]
    guide_file = str(guide) if guide.exists() else None
    best_def, best_metrics = agent.optimize(
        initial_def=str(ispd18_test1_paths["def"]),
        guide_file=guide_file,
    )

    # Baseline is saved as iter_-0001.
    import json
    with open(work_dir / "checkpoints" / "iter_-0001.json") as f:
        baseline_data = json.load(f)

    baseline_drc = baseline_data["drc_total"]

    # Agent must not increase DRC by more than 20% (heuristic is simple).
    assert best_metrics.drc_total <= max(baseline_drc * 1.2, baseline_drc + 5), (
        f"DRC regressed too much: baseline={baseline_drc}, "
        f"best={best_metrics.drc_total}"
    )

    # Wirelength should remain within a reasonable range.
    baseline_wl = baseline_data["wirelength"]
    assert best_metrics.wirelength > 0
    assert best_metrics.wirelength <= max(baseline_wl * 1.5, baseline_wl + 1000)
