import unittest
from typing import Dict, List, Optional
import numpy as np

from src.policy_executor import PolicyExecutor
from src.eda_provider import EDAProvider
from src.routing_toolkit import RoutingMetrics, DRCViolation


class MockEDAProvider(EDAProvider):
    """Minimal EDAProvider mock for unit tests."""

    def run_baseline_flow(self, def_file: str, guide_file: Optional[str] = None) -> str:
        return def_file

    def run_incremental_route(
        self, def_file: str, net_list=None, output_name=None, timeout=None
    ) -> str:
        return def_file

    def run_detailed_route(self, def_file: str, output_name=None) -> str:
        return def_file

    def extract_drc_report(self, def_file: str):
        return []

    def extract_metrics(self, def_file: str, drc_violations=None) -> RoutingMetrics:
        return RoutingMetrics()

    def extract_congestion_map(self, def_file: str, resolution: int = 256):
        return np.zeros((resolution, resolution))

    def extract_routing_layers(
        self, def_file: str, layers=None, resolution: int = 512
    ) -> Dict[str, np.ndarray]:
        return {layer: np.zeros((resolution, resolution)) for layer in (layers or [])}

    def rip_up_nets(self, def_file: str, net_list: List[str]) -> str:
        return def_file

    def set_routing_blockage(
        self, def_file: str, bbox, layers, output_name=None
    ) -> str:
        return def_file


class TestPolicyExecutor(unittest.TestCase):
    def test_terminate(self):
        provider = MockEDAProvider()
        executor = PolicyExecutor(provider)
        policy = {
            "routing_policy": {
                "iteration": 0,
                "strategy_type": "terminate",
                "priority_actions": [{"action": "terminate", "parameters": {}}],
                "termination_check": True,
            }
        }
        new_def, report = executor.execute_policy(
            "/tmp/in.def", policy, [], {}
        )
        self.assertEqual(new_def, "/tmp/in.def")
        self.assertEqual(len(report), 1)
        self.assertTrue(report[0]["success"])

    def test_no_actions(self):
        provider = MockEDAProvider()
        executor = PolicyExecutor(provider)
        policy = {"routing_policy": {"priority_actions": []}}
        new_def, report = executor.execute_policy(
            "/tmp/in.def", policy, [], {}
        )
        self.assertEqual(new_def, "/tmp/in.def")
        self.assertEqual(len(report), 0)


if __name__ == "__main__":
    unittest.main()
