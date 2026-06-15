"""
src/heuristic_policy.py

A deterministic, VLM-free policy generator for benchmarking the execution
pipeline without requiring API keys.  It picks the net involved in the most
DRC violations and rips it up, or applies a composite congestion strategy
when many violations cluster in one area.
"""

from collections import Counter
from typing import Dict, List, Optional

from .routing_toolkit import DRCViolation


class HeuristicPolicyGenerator:
    """Generate routing policies using simple heuristics instead of a VLM."""

    def generate_policy(
        self,
        image_paths: List[str],
        netlist_stats: Dict,
        metrics: Dict,
        last_action: str = "None",
        last_metrics: Optional[Dict] = None,
        iteration: int = 0,
        drc_violations: Optional[List[DRCViolation]] = None,
        net_features: Optional[Dict] = None,
        previous_attribution: Optional[Dict] = None,
        violation_crop_paths: Optional[List[str]] = None,
    ) -> Dict:
        """Return a policy dict in the same schema as VLMPolicyGenerator."""
        drc_total = metrics.get("drc_total", 0)

        if drc_total == 0:
            return self._terminate_policy(iteration)

        if not drc_violations:
            return self._terminate_policy(iteration)

        # Count DRCs per net.
        net_counter: Counter = Counter()
        for v in drc_violations:
            for net in v.nets_involved:
                net_counter[net] += 1

        if not net_counter:
            return self._terminate_policy(iteration)

        worst_net, _ = net_counter.most_common(1)[0]

        # If the worst net has many violations, try a composite repair.
        if net_counter[worst_net] >= 3:
            violation_ids = [
                v.violation_id
                for v in drc_violations
                if worst_net in v.nets_involved
            ][:5]
            return {
                "routing_policy": {
                    "iteration": iteration,
                    "strategy_type": "heuristic_route_repair",
                    "analysis": f"Net {worst_net} has {net_counter[worst_net]} violations; repairing cluster.",
                    "diagnosis": [],
                    "priority_actions": [
                        {
                            "action": "route_repair",
                            "parameters": {"violation_ids": violation_ids},
                            "reason": f"Heuristic: repair violations involving {worst_net}",
                            "expected_impact": "drc_fix",
                        }
                    ],
                    "termination_check": False,
                    "next_state_focus": "Check DRC delta",
                }
            }

        # Simple fallback: rip up the worst net.
        return {
            "routing_policy": {
                "iteration": iteration,
                "strategy_type": "heuristic_rip_up_net",
                "analysis": f"Net {worst_net} has the most violations; ripping it up.",
                "diagnosis": [],
                "priority_actions": [
                    {
                        "action": "rip_up_net",
                        "parameters": {"net_name": worst_net},
                        "reason": f"Heuristic: {worst_net} involved in {net_counter[worst_net]} violations",
                        "expected_impact": "drc_fix",
                    }
                ],
                "termination_check": False,
                "next_state_focus": "Check DRC delta",
            }
        }

    @staticmethod
    def _terminate_policy(iteration: int) -> Dict:
        return {
            "routing_policy": {
                "iteration": iteration,
                "strategy_type": "heuristic_terminate",
                "analysis": "DRC is clean or no violations known; terminating.",
                "diagnosis": [],
                "priority_actions": [],
                "termination_check": True,
                "next_state_focus": "Optimization complete",
            }
        }
