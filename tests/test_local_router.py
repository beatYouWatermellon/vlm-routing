import tempfile
import unittest
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

from src.local_router import LocalRouter, LocalCheckResult
from src.eda_provider import EDAProvider
from src.routing_toolkit import RoutingMetrics, DRCViolation


class MockEDAProvider(EDAProvider):
    """Minimal EDAProvider mock for LocalRouter tests."""

    def __init__(self, drc_violations=None):
        self.drc_violations = drc_violations or []

    def run_baseline_flow(self, def_file: str, guide_file: Optional[str] = None) -> str:
        return def_file

    def run_incremental_route(
        self, def_file: str, net_list=None, output_name=None, timeout=None
    ) -> str:
        return def_file

    def run_detailed_route(self, def_file: str, output_name=None) -> str:
        return def_file

    def extract_drc_report(self, def_file: str):
        return self.drc_violations

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


class TestLocalRouter(unittest.TestCase):
    def setUp(self):
        self.sample_def = """VERSION 5.8 ;
DIVIDERCHAR "/" ;
BUSBITCHARS "[]" ;
DESIGN test ;
UNITS DISTANCE MICRONS 2000 ;
DIEAREA ( 0 0 ) ( 10000 10000 ) ;
NETS 2 ;
- net1 ( 1000 1000 ) ( 2000 1000 )
  + ROUTED Metal2 ( 1000 1000 ) ( 2000 * )
  ;
- net2 ( 5000 5000 ) ( 6000 5000 )
  + ROUTED Metal2 ( 5000 5000 ) ( 6000 * )
  ;
END NETS
END DESIGN
"""
        self.work_dir = Path(tempfile.mkdtemp(prefix="test_local_router_"))
        self.def_path = self.work_dir / "test.def"
        self.def_path.write_text(self.sample_def)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.work_dir, ignore_errors=True)

    def test_evaluate_edit_passes_when_local_drc_improves(self):
        # Two violations: one inside net1's region, one far away.
        violations = [
            DRCViolation("v1", "spacing", "M2", (0, 0, 100, 100), (50, 50), ["net1"]),
            DRCViolation("v2", "spacing", "M2", (5000, 5000, 5100, 5100), (5050, 5050), ["net2"]),
        ]
        provider = MockEDAProvider(drc_violations=violations)
        router = LocalRouter(provider, str(self.work_dir / "local"))

        result = router.evaluate_edit(str(self.def_path), ["net1"])

        self.assertIsInstance(result, LocalCheckResult)
        # Only the violation inside net1's expanded region counts.
        self.assertEqual(result.local_drc_total, 1)
        self.assertTrue(result.passed)

    def test_evaluate_edit_fails_when_local_drc_increases(self):
        previous = [
            DRCViolation("v1", "spacing", "M2", (0, 0, 100, 100), (50, 50), ["net1"]),
        ]
        current = [
            DRCViolation("v1", "spacing", "M2", (0, 0, 100, 100), (50, 50), ["net1"]),
            DRCViolation("v2", "spacing", "M2", (1500, 900, 1600, 1000), (1550, 950), ["net1"]),
        ]
        provider = MockEDAProvider(drc_violations=current)
        router = LocalRouter(provider, str(self.work_dir / "local"))

        result = router.evaluate_edit(
            str(self.def_path), ["net1"], previous_region_violations=previous
        )

        self.assertEqual(result.local_drc_delta, 1)
        self.assertFalse(result.passed)

    def test_region_bbox_computation(self):
        provider = MockEDAProvider()
        router = LocalRouter(provider, str(self.work_dir / "local"))
        nets = {
            "net1": type("Net", (), {"bbox": (1000, 1000, 2000, 2000)})(),
        }
        bbox = router._compute_region_bbox(nets, ["net1"], margin_nm=500)
        self.assertEqual(bbox, (500, 500, 2500, 2500))


if __name__ == "__main__":
    unittest.main()
