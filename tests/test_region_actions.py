import tempfile
import unittest
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

from src.eda_provider import EDAProvider
from src.policy_executor import PolicyExecutor
from src.routing_toolkit import RoutingMetrics, DRCViolation


class MockEDAProvider(EDAProvider):
    """Mock provider for region action tests."""

    def __init__(self):
        self.calls: List[Dict] = []

    def run_baseline_flow(self, def_file: str, guide_file: Optional[str] = None) -> str:
        return def_file

    def run_incremental_route(
        self, def_file: str, net_list=None, output_name=None, timeout=None
    ) -> str:
        self.calls.append({"method": "run_incremental_route", "def_file": def_file})
        return def_file

    def run_detailed_route(self, def_file: str, output_name=None) -> str:
        self.calls.append({"method": "run_detailed_route", "def_file": def_file})
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
        self.calls.append({"method": "rip_up_nets", "def_file": def_file, "nets": net_list})
        return def_file

    def set_routing_blockage(
        self, def_file: str, bbox, layers, output_name=None, hardness="hard"
    ) -> str:
        self.calls.append(
            {
                "method": "set_routing_blockage",
                "def_file": def_file,
                "bbox": bbox,
                "layers": layers,
                "hardness": hardness,
            }
        )
        return def_file


class TestRegionActions(unittest.TestCase):
    def test_set_soft_guidance_rips_routes_and_removes_blockages(self):
        provider = MockEDAProvider()
        executor = PolicyExecutor(provider)

        work_dir = Path(tempfile.mkdtemp(prefix="test_region_"))
        def_path = work_dir / "in.def"
        def_path.write_text(
            "VERSION 5.8 ;\nDESIGN test ;\nNETS 1 ;\n- net1 ( a A ) ( b B )\n  "
            "+ ROUTED Metal2 ( 1000 1000 ) ( 2000 * )\n  ;\nEND NETS\nEND DESIGN\n"
        )

        policy = {
            "routing_policy": {
                "priority_actions": [
                    {
                        "action": "set_soft_guidance",
                        "parameters": {
                            "net_name": "net1",
                            "guide_points": [[1000, 1000], [2000, 2000]],
                            "layer": "M3",
                            "margin_nm": 500,
                        },
                    }
                ]
            }
        }
        new_def, report = executor.execute_policy(
            str(def_path), policy, [], {}
        )

        self.assertEqual(len(report), 1)
        self.assertTrue(report[0]["success"])
        # Should rip up the net, add temporary blockages, route, then relax.
        self.assertIn({"method": "rip_up_nets", "def_file": str(def_path), "nets": ["net1"]}, provider.calls)
        blockage_calls = [c for c in provider.calls if c["method"] == "set_routing_blockage"]
        self.assertEqual(len(blockage_calls), 1)
        # M2, M4, M5 should be blocked (not M3)
        self.assertEqual(set(blockage_calls[0]["layers"]), {"M2", "M4", "M5"})

    def test_set_routing_blockage_hardness_passed_through(self):
        provider = MockEDAProvider()
        executor = PolicyExecutor(provider)
        policy = {
            "routing_policy": {
                "priority_actions": [
                    {
                        "action": "set_routing_blockage",
                        "parameters": {
                            "bbox": [0, 0, 1000, 1000],
                            "layers": ["M3"],
                            "hardness": "soft",
                        },
                    }
                ]
            }
        }
        executor.execute_policy("/tmp/in.def", policy, [], {})

        blockage_calls = [c for c in provider.calls if c["method"] == "set_routing_blockage"]
        self.assertEqual(blockage_calls[0]["hardness"], "soft")


if __name__ == "__main__":
    unittest.main()
