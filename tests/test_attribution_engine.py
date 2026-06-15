import unittest

from src.attribution_engine import AttributionEngine
from src.routing_toolkit import RoutingMetrics, DRCViolation


class TestAttributionEngine(unittest.TestCase):
    def test_compute_delta(self):
        prev_metrics = RoutingMetrics(drc_total=5, wirelength=100.0, via_count=10)
        new_metrics = RoutingMetrics(drc_total=3, wirelength=102.0, via_count=11)

        prev_violations = [
            DRCViolation("v1", "spacing", "M3", (0, 0, 100, 100), (50, 50), ["net1"]),
            DRCViolation("v2", "short", "M2", (0, 0, 100, 100), (50, 50), ["net2"]),
            DRCViolation("v3", "spacing", "M3", (200, 200, 300, 300), (250, 250), ["net1"]),
        ]
        new_violations = [
            DRCViolation("v3", "spacing", "M3", (200, 200, 300, 300), (250, 250), ["net1"]),
            DRCViolation("v4", "spacing", "M3", (5000, 5000, 5100, 5100), (5050, 5050), ["net1"]),
        ]

        report = AttributionEngine.compute_delta(
            prev_metrics, new_metrics, prev_violations, new_violations
        )

        self.assertEqual(report.drc_delta, -2)
        self.assertEqual(report.wirelength_delta, 2.0)
        self.assertEqual(report.via_delta, 1)
        self.assertIn("v2", report.fixed_violations)
        self.assertIn("v4", report.new_violations)
        self.assertIn("v3", report.persisted_violations)
        self.assertIn("net1", report.per_net_drc_delta)

    def test_action_success_rate(self):
        prev_metrics = RoutingMetrics(drc_total=5, wirelength=100.0, via_count=10)
        new_metrics = RoutingMetrics(drc_total=5, wirelength=100.0, via_count=10)
        prev_violations = []
        new_violations = []
        execution_report = [
            {"action": "rip_up_segment", "success": True},
            {"action": "reassign_layer", "success": False, "rolled_back": True},
            {"action": "reassign_layer", "success": False},
        ]

        report = AttributionEngine.compute_delta(
            prev_metrics, new_metrics, prev_violations, new_violations, execution_report
        )

        self.assertAlmostEqual(report.action_success_rate, 1 / 3)
        self.assertEqual(report.failed_action_summary.get("reassign_layer"), 2)
        self.assertIn("action_success_rate", report.to_dict())
        self.assertIn("failed_action_summary", report.to_dict())


if __name__ == "__main__":
    unittest.main()
