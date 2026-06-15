"""
src/local_router.py

Local evaluation router for fine-grained edits.

For edits that affect only a small number of nets, LocalRouter rips up those
nets, runs a (hopefully fast) incremental detailed route, and reports the DRC
violations that fall inside the affected region.  The goal is to reject bad
edits early instead of discovering them after a full global detailed route.
"""

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from .eda_provider import EDAProvider
from .def_parser import DefParser
from .routing_toolkit import DRCViolation


@dataclass
class LocalCheckResult:
    """Result of a local edit evaluation."""

    passed: bool
    local_drc_total: int
    local_drc_delta: int
    region_bbox: Tuple[int, int, int, int]
    runtime_seconds: float
    detail: str


class LocalRouter:
    """
    Evaluate fine-grained edits locally before committing to them globally.

    The implementation currently runs a full detailed route on a copy of the
    edited DEF with the affected nets ripped up.  OpenROAD's detailed router
    only reroutes the unrouted nets, so this is typically much faster than a
    from-scratch global route.  The DRC report is then filtered to the
    bounding box of the affected nets (plus a margin) to judge whether the
    edit helped or hurt the local area.
    """

    def __init__(self, provider: EDAProvider, work_dir: str):
        self.provider = provider
        self.work_dir = Path(work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self._counter = 0

    def evaluate_edit(
        self,
        edited_def: str,
        target_nets: List[str],
        previous_region_violations: Optional[List[DRCViolation]] = None,
        margin_nm: int = 2000,
    ) -> LocalCheckResult:
        """
        Evaluate ``edited_def`` after a fine-grained edit on ``target_nets``.

        Returns a LocalCheckResult indicating whether the edit improved or at
        least did not worsen the local DRC picture.
        """
        start = time.time()
        self._counter += 1

        nets = DefParser.parse_nets(edited_def)
        region_bbox = self._compute_region_bbox(nets, target_nets, margin_nm)

        # Rip up the affected nets and let the detailed router repair them.
        ripped_def = self.provider.rip_up_nets(edited_def, target_nets)
        routed_def = self.provider.run_detailed_route(ripped_def)

        # Extract DRC and keep only violations inside the affected region.
        drc_violations = self.provider.extract_drc_report(routed_def)
        region_violations = [
            v for v in drc_violations if self._point_in_bbox(v.center, region_bbox)
        ]

        prev_count = len(previous_region_violations) if previous_region_violations else 0
        local_drc_total = len(region_violations)
        local_drc_delta = local_drc_total - prev_count

        # An edit passes if it did not increase the local DRC count, or if the
        # absolute number of local violations is very small (<=1).
        passed = local_drc_delta <= 0 or local_drc_total <= 1

        runtime = time.time() - start
        detail = (
            f"region={region_bbox}, local_drc={local_drc_total}, "
            f"delta={local_drc_delta:+d}, passed={passed}"
        )

        return LocalCheckResult(
            passed=passed,
            local_drc_total=local_drc_total,
            local_drc_delta=local_drc_delta,
            region_bbox=region_bbox,
            runtime_seconds=runtime,
            detail=detail,
        )

    @staticmethod
    def _compute_region_bbox(
        nets: Dict[str, "DefNet"],
        target_nets: List[str],
        margin_nm: int,
    ) -> Tuple[int, int, int, int]:
        """Compute a bbox around the target nets expanded by ``margin_nm``."""
        xs: List[int] = []
        ys: List[int] = []
        for net_name in target_nets:
            net = nets.get(net_name)
            if net is None or net.bbox is None:
                continue
            x1, y1, x2, y2 = net.bbox
            xs.extend([x1, x2])
            ys.extend([y1, y2])

        if not xs or not ys:
            # Degenerate case: return a small default box around the origin.
            return (-margin_nm, -margin_nm, margin_nm, margin_nm)

        x1 = min(xs) - margin_nm
        y1 = min(ys) - margin_nm
        x2 = max(xs) + margin_nm
        y2 = max(ys) + margin_nm
        return (x1, y1, x2, y2)

    @staticmethod
    def _point_in_bbox(
        point: Tuple[int, int], bbox: Tuple[int, int, int, int]
    ) -> bool:
        x, y = point
        x1, y1, x2, y2 = bbox
        return x1 <= x <= x2 and y1 <= y <= y2
