"""
tests/test_agent_controller.py

Unit tests for RoutingAgent controller internals.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

from src.agent_controller import RoutingAgent
from src.routing_toolkit import RoutingMetrics
from src.visual_renderer import VisualRenderer
from src.heuristic_policy import HeuristicPolicyGenerator


class _MockToolkit:
    """Minimal toolkit stand-in for controller tests."""

    def __init__(self):
        self.work_dir = ""


class TestRoutingAgentCheckpoints(unittest.TestCase):
    def test_baseline_checkpoint_tag(self):
        """Baseline checkpoint (iteration -1) must be named iter_-0001."""
        with tempfile.TemporaryDirectory() as tmpdir:
            agent = RoutingAgent(
                toolkit=_MockToolkit(),
                renderer=VisualRenderer(output_dir=tmpdir, resolution=64),
                vlm=HeuristicPolicyGenerator(),
                work_dir=tmpdir,
                max_iterations=1,
            )
            metrics = RoutingMetrics()
            dummy_def = os.path.join(tmpdir, "dummy.def")
            Path(dummy_def).write_text("VERSION 5.8 ;\nEND DESIGN\n")
            agent._save_checkpoint(-1, dummy_def, metrics)

            checkpoint_dir = Path(tmpdir) / "checkpoints"
            self.assertTrue((checkpoint_dir / "iter_-0001.json").exists())
            self.assertTrue((checkpoint_dir / "iter_-0001.def").exists())
            # Ensure the negative formatting does not collide with iteration 1.
            self.assertFalse((checkpoint_dir / "iter_-001.json").exists())

    def test_positive_checkpoint_tag(self):
        """Positive iteration checkpoints keep zero-padded naming."""
        with tempfile.TemporaryDirectory() as tmpdir:
            agent = RoutingAgent(
                toolkit=_MockToolkit(),
                renderer=VisualRenderer(output_dir=tmpdir, resolution=64),
                vlm=HeuristicPolicyGenerator(),
                work_dir=tmpdir,
                max_iterations=1,
            )
            metrics = RoutingMetrics()
            dummy_def = os.path.join(tmpdir, "dummy.def")
            Path(dummy_def).write_text("VERSION 5.8 ;\nEND DESIGN\n")
            agent._save_checkpoint(7, dummy_def, metrics)

            checkpoint_dir = Path(tmpdir) / "checkpoints"
            self.assertTrue((checkpoint_dir / "iter_0007.json").exists())


if __name__ == "__main__":
    unittest.main()
