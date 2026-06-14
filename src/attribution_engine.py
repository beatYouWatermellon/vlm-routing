"""
src/attribution_engine.py

Attributable feedback engine.  Computes deltas between two routing states and
identifies which violations were fixed, newly introduced, moved, or persisted,
as well as per-net DRC deltas.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .routing_toolkit import RoutingMetrics, DRCViolation


@dataclass
class AttributionReport:
    """Result of comparing two routing states."""

    drc_delta: int = 0
    wirelength_delta: float = 0.0
    via_delta: int = 0
    fixed_violations: List[str] = field(default_factory=list)
    new_violations: List[str] = field(default_factory=list)
    moved_violations: List[str] = field(default_factory=list)
    persisted_violations: List[str] = field(default_factory=list)
    per_net_drc_delta: Dict[str, int] = field(default_factory=dict)
    action_results: List[Dict] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "drc_delta": self.drc_delta,
            "wirelength_delta": self.wirelength_delta,
            "via_delta": self.via_delta,
            "fixed_violations": self.fixed_violations,
            "new_violations": self.new_violations,
            "moved_violations": self.moved_violations,
            "persisted_violations": self.persisted_violations,
            "per_net_drc_delta": self.per_net_drc_delta,
            "action_results": self.action_results,
        }


class AttributionEngine:
    """Compute attributable deltas between routing iterations."""

    @staticmethod
    def compute_delta(
        prev_metrics: RoutingMetrics,
        new_metrics: RoutingMetrics,
        prev_violations: List[DRCViolation],
        new_violations: List[DRCViolation],
        execution_report: Optional[List[Dict]] = None,
    ) -> AttributionReport:
        """
        Compare two routing states and return an AttributionReport.

        Violations are matched by their unique violation_id.  A violation is
        considered "moved" if it disappears and a new violation of the same
        type appears within a small spatial window.
        """
        prev_ids = {v.violation_id for v in prev_violations}
        new_ids = {v.violation_id for v in new_violations}

        fixed = sorted(prev_ids - new_ids)
        new = sorted(new_ids - prev_ids)
        persisted = sorted(prev_ids & new_ids)

        # Detect moved violations: a fixed violation whose center is close to a
        # new violation of the same type and layer.
        prev_map = {v.violation_id: v for v in prev_violations}
        new_map = {v.violation_id: v for v in new_violations}

        moved: List[str] = []
        used_new: set = set()
        for fixed_id in fixed:
            fv = prev_map[fixed_id]
            for new_id in new:
                if new_id in used_new:
                    continue
                nv = new_map[new_id]
                if (
                    fv.vtype.lower() == nv.vtype.lower()
                    and fv.layer.lower() == nv.layer.lower()
                    and AttributionEngine._distance(fv.center, nv.center) < 2000
                ):
                    moved.append(f"{fixed_id}->{new_id}")
                    used_new.add(new_id)
                    break

        fixed = [v for v in fixed if not any(v == m.split("->")[0] for m in moved)]
        new = [v for v in new if v not in used_new]

        # Per-net DRC delta
        prev_net_drc: Dict[str, int] = {}
        for v in prev_violations:
            for net in v.nets_involved:
                prev_net_drc[net] = prev_net_drc.get(net, 0) + 1

        new_net_drc: Dict[str, int] = {}
        for v in new_violations:
            for net in v.nets_involved:
                new_net_drc[net] = new_net_drc.get(net, 0) + 1

        all_nets = set(prev_net_drc.keys()) | set(new_net_drc.keys())
        per_net_delta = {
            net: new_net_drc.get(net, 0) - prev_net_drc.get(net, 0)
            for net in all_nets
        }

        return AttributionReport(
            drc_delta=new_metrics.drc_total - prev_metrics.drc_total,
            wirelength_delta=new_metrics.wirelength - prev_metrics.wirelength,
            via_delta=new_metrics.via_count - prev_metrics.via_count,
            fixed_violations=sorted(fixed),
            new_violations=sorted(new),
            moved_violations=sorted(moved),
            persisted_violations=sorted(persisted),
            per_net_drc_delta=per_net_delta,
            action_results=execution_report or [],
        )

    @staticmethod
    def _distance(a: Tuple[int, int], b: Tuple[int, int]) -> float:
        return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5
