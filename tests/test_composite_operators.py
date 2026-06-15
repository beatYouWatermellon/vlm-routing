import unittest
from typing import Dict, List, Optional

import numpy as np

from src.composite_operators import CompositeOperators, CompositeResult
from src.eda_provider import EDAProvider
from src.routing_toolkit import RoutingMetrics, DRCViolation


class MockEDAProvider(EDAProvider):
    """Mock EDAProvider that records calls and returns deterministic results."""

    def __init__(self):
        self.calls: List[Dict] = []
        self._drc_violations: List[DRCViolation] = []
        self._metrics = RoutingMetrics()

    def set_next_metrics(self, metrics: RoutingMetrics):
        self._metrics = metrics

    def set_next_drc(self, violations: List[DRCViolation]):
        self._drc_violations = violations

    def run_baseline_flow(self, def_file: str, guide_file: Optional[str] = None) -> str:
        self.calls.append({"method": "run_baseline_flow", "def_file": def_file})
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
        self.calls.append({"method": "extract_drc_report", "def_file": def_file})
        return self._drc_violations

    def extract_metrics(self, def_file: str, drc_violations=None) -> RoutingMetrics:
        self.calls.append({"method": "extract_metrics", "def_file": def_file})
        return self._metrics

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
            {"method": "set_routing_blockage", "def_file": def_file, "bbox": bbox}
        )
        return def_file


class TestCompositeOperators(unittest.TestCase):
    def test_route_in_box_rips_and_reroutes(self):
        provider = MockEDAProvider()
        provider.set_next_metrics(RoutingMetrics(drc_total=2, wirelength=100.0))
        ops = CompositeOperators(provider)

        result = ops.route_in_box(
            "/tmp/in.def",
            bbox=(0, 0, 1000, 1000),
            target_nets=["net1", "net2"],
        )

        self.assertIsInstance(result, CompositeResult)
        self.assertTrue(result.success)
        self.assertIn({"method": "rip_up_nets", "def_file": "/tmp/in.def", "nets": ["net1", "net2"]}, provider.calls)
        self.assertIn({"method": "run_detailed_route", "def_file": "/tmp/in.def"}, provider.calls)

    def test_route_in_box_rolls_back_on_drc_spike(self):
        provider = MockEDAProvider()
        prev = RoutingMetrics(drc_total=10, wirelength=100.0)
        provider.set_next_metrics(RoutingMetrics(drc_total=30, wirelength=100.0))
        ops = CompositeOperators(provider)

        result = ops.route_in_box(
            "/tmp/in.def",
            bbox=(0, 0, 1000, 1000),
            target_nets=["net1"],
            previous_metrics=prev,
        )

        self.assertFalse(result.success)
        self.assertTrue(result.rolled_back)

    def test_route_repair_selects_involved_nets(self):
        provider = MockEDAProvider()
        provider.set_next_metrics(RoutingMetrics(drc_total=1, wirelength=50.0))
        ops = CompositeOperators(provider)

        violations = [
            DRCViolation("v1", "spacing", "M3", (0, 0, 100, 100), (50, 50), ["net1"]),
            DRCViolation("v2", "short", "M2", (0, 0, 100, 100), (50, 50), ["net2", "net3"]),
        ]

        result = ops.route_repair(
            "/tmp/in.def",
            violation_ids=["v2"],
            current_violations=violations,
            current_net_features={},
        )

        self.assertTrue(result.success)
        rip_calls = [c for c in provider.calls if c["method"] == "rip_up_nets"]
        self.assertEqual(len(rip_calls), 1)
        self.assertEqual(set(rip_calls[0]["nets"]), {"net2", "net3"})

    def test_optimize_congestion_adds_blockage(self):
        provider = MockEDAProvider()
        provider.set_next_metrics(RoutingMetrics(drc_total=2, wirelength=100.0))
        ops = CompositeOperators(provider)

        result = ops.optimize_congestion(
            "/tmp/in.def",
            hotspot_bbox=(0, 0, 1000, 1000),
            affected_nets=["net1"],
        )

        self.assertTrue(result.success)
        blockage_calls = [c for c in provider.calls if c["method"] == "set_routing_blockage"]
        self.assertEqual(len(blockage_calls), 1)


if __name__ == "__main__":
    unittest.main()
