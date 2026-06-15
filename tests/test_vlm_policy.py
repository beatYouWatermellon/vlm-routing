import os
import unittest

from src.vlm_policy import VLMPolicyGenerator


class TestVLMPolicyGenerator(unittest.TestCase):
    def test_format_previous_attribution_with_blacklist(self):
        attribution = {
            "drc_delta": -2,
            "wirelength_delta": 2.0,
            "via_delta": 1,
            "fixed_violations": ["v2"],
            "new_violations": ["v4"],
            "moved_violations": [],
            "persisted_violations": ["v3"],
            "per_net_drc_delta": {"net1": 1},
            "action_results": [
                {"action": "rip_up_segment", "success": True},
                {"action": "reassign_layer", "success": False, "rolled_back": True},
            ],
            "action_success_rate": 0.5,
            "failed_action_summary": {"reassign_layer": 1},
        }
        blacklist = [
            {"action": "reassign_layer", "parameters": {}, "reason": "DRC increased"}
        ]

        text = VLMPolicyGenerator._format_previous_attribution(
            last_action="reassign_layer",
            last_metrics=None,
            metrics={"drc_total": 3},
            attribution=attribution,
            blacklisted_actions=blacklist,
        )

        self.assertIn("Action success rate: 50%", text)
        self.assertIn("reassign_layer: 1", text)
        self.assertIn("Avoid repeating these recent failed actions", text)

    def test_fallback_policy_avoids_blacklist(self):
        # Instantiate with a fake API key to bypass client initialization failure.
        os.environ["ANTHROPIC_API_KEY"] = "fake_key_for_test"
        vlm = VLMPolicyGenerator(model="claude-test", client_type="anthropic")
        vlm.blacklisted_actions = [{"action": "rip_up_net"}]

        policy = vlm._fallback_policy({"drc_total": 5}, iteration=0)
        action = policy["routing_policy"]["priority_actions"][0]["action"]
        self.assertNotEqual(action, "rip_up_net")


if __name__ == "__main__":
    unittest.main()
