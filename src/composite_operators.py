"""
src/composite_operators.py

Composite / strategy-level routing operators.

Each operator encapsulates a higher-level repair strategy (e.g. reroute
everything inside a box, repair a specific violation cluster, or optimize a
congestion hotspot).  Operators are responsible for their own checkpointing
and rollback using the EDAProvider and AttributionEngine.
"""

import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

from .eda_provider import EDAProvider
from .attribution_engine import AttributionEngine
from .routing_toolkit import DRCViolation, NetRoutingFeatures, RoutingMetrics


@dataclass
class CompositeResult:
    """Result of executing a composite operator."""

    success: bool
    output_def: str
    metrics: Optional[RoutingMetrics]
    detail: str
    rolled_back: bool = False


class CompositeOperators:
    """Higher-level routing strategies built from fine-grained actions."""

    def __init__(self, provider: EDAProvider):
        self.provider = provider
        self.attribution = AttributionEngine()

    # ------------------------------------------------------------------
    # Public operators
    # ------------------------------------------------------------------

    def route_in_box(
        self,
        def_file: str,
        bbox: Tuple[int, int, int, int],
        target_nets: List[str],
        preferred_layers: Optional[List[str]] = None,
        previous_metrics: Optional[RoutingMetrics] = None,
    ) -> CompositeResult:
        """
        Rip up ``target_nets`` inside ``bbox`` and reroute them with optional
        layer preference.  Roll back if the result is worse.
        """
        start_def = self._save_checkpoint(def_file, "route_in_box_start")

        try:
            # Rip up target nets globally (simplest correct implementation).
            # Future improvement: only strip segments inside the bbox.
            ripped = self.provider.rip_up_nets(def_file, target_nets)

            # Optionally inject a temporary soft blockage around the bbox
            # to discourage the router from using the same congested paths.
            if preferred_layers:
                # Layer constraint is applied by ripping up and setting layer
                # preference via Tcl in a future iteration.  For now we rely on
                # the detailed router's default cost.
                pass

            routed = self.provider.run_detailed_route(ripped)
            metrics = self.provider.extract_metrics(routed)

            if self._should_rollback(previous_metrics, metrics):
                return CompositeResult(
                    success=False,
                    output_def=start_def,
                    metrics=previous_metrics,
                    detail="Route-in-box degraded metrics; rolled back",
                    rolled_back=True,
                )

            return CompositeResult(
                success=True,
                output_def=routed,
                metrics=metrics,
                detail=f"Rerouted {len(target_nets)} nets in box {bbox}",
            )
        except Exception as e:
            return CompositeResult(
                success=False,
                output_def=start_def,
                metrics=previous_metrics,
                detail=f"Route-in-box failed: {e}",
                rolled_back=True,
            )

    def route_repair(
        self,
        def_file: str,
        violation_ids: List[str],
        current_violations: List[DRCViolation],
        current_net_features: Dict[str, NetRoutingFeatures],
        previous_metrics: Optional[RoutingMetrics] = None,
    ) -> CompositeResult:
        """
        Automatically choose the smallest repair action for each violation.

        Current heuristic:
          - For violations involving a single net with <=3 segment indices,
            rip up the net.
          - Otherwise, rip up all involved nets.
        """
        start_def = self._save_checkpoint(def_file, "route_repair_start")

        involved_nets: Set[str] = set()
        for v in current_violations:
            if v.violation_id in violation_ids:
                involved_nets.update(v.nets_involved)

        if not involved_nets:
            return CompositeResult(
                success=True,
                output_def=def_file,
                metrics=previous_metrics,
                detail="No nets involved in requested violations",
            )

        try:
            ripped = self.provider.rip_up_nets(def_file, list(involved_nets))
            routed = self.provider.run_detailed_route(ripped)
            metrics = self.provider.extract_metrics(routed)

            if self._should_rollback(previous_metrics, metrics):
                return CompositeResult(
                    success=False,
                    output_def=start_def,
                    metrics=previous_metrics,
                    detail="Route repair degraded metrics; rolled back",
                    rolled_back=True,
                )

            return CompositeResult(
                success=True,
                output_def=routed,
                metrics=metrics,
                detail=f"Repaired {len(involved_nets)} nets for violations {violation_ids}",
            )
        except Exception as e:
            return CompositeResult(
                success=False,
                output_def=start_def,
                metrics=previous_metrics,
                detail=f"Route repair failed: {e}",
                rolled_back=True,
            )

    def optimize_congestion(
        self,
        def_file: str,
        hotspot_bbox: Tuple[int, int, int, int],
        affected_nets: List[str],
        layers: Optional[List[str]] = None,
        previous_metrics: Optional[RoutingMetrics] = None,
    ) -> CompositeResult:
        """
        Reduce congestion in ``hotspot_bbox`` by inserting a soft routing
        blockage and rerouting the affected nets.
        """
        start_def = self._save_checkpoint(def_file, "optimize_congestion_start")

        try:
            blocked = self.provider.set_routing_blockage(
                def_file,
                bbox=hotspot_bbox,
                layers=layers or ["M2", "M3", "M4"],
            )
            ripped = self.provider.rip_up_nets(blocked, affected_nets)
            routed = self.provider.run_detailed_route(ripped)
            metrics = self.provider.extract_metrics(routed)

            if self._should_rollback(previous_metrics, metrics):
                return CompositeResult(
                    success=False,
                    output_def=start_def,
                    metrics=previous_metrics,
                    detail="Congestion optimization degraded metrics; rolled back",
                    rolled_back=True,
                )

            return CompositeResult(
                success=True,
                output_def=routed,
                metrics=metrics,
                detail=f"Optimized congestion in {hotspot_bbox} for {len(affected_nets)} nets",
            )
        except Exception as e:
            return CompositeResult(
                success=False,
                output_def=start_def,
                metrics=previous_metrics,
                detail=f"Congestion optimization failed: {e}",
                rolled_back=True,
            )

    def cleanup_routing(
        self,
        def_file: str,
        previous_metrics: Optional[RoutingMetrics] = None,
    ) -> CompositeResult:
        """
        DRC=0 后的轻量清理：跑一次 detailed_route 看是否能优化 wirelength/via。
        """
        start_def = self._save_checkpoint(def_file, "cleanup_start")

        try:
            routed = self.provider.run_detailed_route(def_file)
            metrics = self.provider.extract_metrics(routed)

            if self._should_rollback(previous_metrics, metrics, allow_wl_regression=False):
                return CompositeResult(
                    success=False,
                    output_def=start_def,
                    metrics=previous_metrics,
                    detail="Cleanup degraded quality; rolled back",
                    rolled_back=True,
                )

            return CompositeResult(
                success=True,
                output_def=routed,
                metrics=metrics,
                detail="Cleanup completed",
            )
        except Exception as e:
            return CompositeResult(
                success=False,
                output_def=start_def,
                metrics=previous_metrics,
                detail=f"Cleanup failed: {e}",
                rolled_back=True,
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _save_checkpoint(self, def_file: str, tag: str) -> str:
        """Copy the DEF to a checkpoint path and return the path."""
        from pathlib import Path
        import shutil

        src = Path(def_file)
        if not src.exists():
            return str(src)
        checkpoint_path = src.parent / f"_checkpoint_{tag}_{int(time.time())}.def"
        shutil.copy(str(src), str(checkpoint_path))
        return str(checkpoint_path)

    @staticmethod
    def _should_rollback(
        prev: Optional[RoutingMetrics],
        new: Optional[RoutingMetrics],
        allow_wl_regression: bool = True,
    ) -> bool:
        """Return True if ``new`` is significantly worse than ``prev``."""
        if prev is None or new is None:
            return False

        drc_threshold = max(int(prev.drc_total * 0.2), 5)
        if new.drc_total > prev.drc_total + drc_threshold:
            return True

        if not allow_wl_regression and prev.drc_total == 0 and new.drc_total == 0:
            if prev.wirelength > 0:
                wl_increase = (new.wirelength - prev.wirelength) / prev.wirelength
                if wl_increase > 0.05:
                    return True

        return False
