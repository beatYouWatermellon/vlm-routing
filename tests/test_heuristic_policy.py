import unittest

from src.heuristic_policy import HeuristicPolicyGenerator
from src.routing_toolkit import DRCViolation


class TestHeuristicPolicyGenerator(unittest.TestCase):
    def test_terminate_when_drc_clean(self):
        gen = HeuristicPolicyGenerator()
        policy = gen.generate_policy(
            image_paths=[],
            netlist_stats={},
            metrics={"drc_total": 0},
            drc_violations=[],
        )
        self.assertTrue(policy["routing_policy"]["termination_check"])

    def test_rips_up_worst_net(self):
        gen = HeuristicPolicyGenerator()
        violations = [
            DRCViolation("v1", "spacing", "M3", (0, 0, 100, 100), (50, 50), ["net1"]),
            DRCViolation("v2", "spacing", "M3", (0, 0, 100, 100), (50, 50), ["net2"]),
            DRCViolation("v3", "spacing", "M3", (0, 0, 100, 100), (50, 50), ["net2"]),
        ]
        policy = gen.generate_policy(
            image_paths=[],
            netlist_stats={},
            metrics={"drc_total": 3},
            drc_violations=violations,
        )
        actions = policy["routing_policy"]["priority_actions"]
        self.assertEqual(actions[0]["action"], "rip_up_net")
        self.assertEqual(actions[0]["parameters"]["net_name"], "net2")

    def test_route_repair_for_heavily_violated_net(self):
        gen = HeuristicPolicyGenerator()
        violations = [
            DRCViolation(f"v{i}", "spacing", "M3", (0, 0, 100, 100), (50, 50), ["net_bad"])
            for i in range(5)
        ]
        policy = gen.generate_policy(
            image_paths=[],
            netlist_stats={},
            metrics={"drc_total": 5},
            drc_violations=violations,
        )
        actions = policy["routing_policy"]["priority_actions"]
        self.assertEqual(actions[0]["action"], "route_repair")


if __name__ == "__main__":
    unittest.main()
