"""
src/policy_executor.py

Action dispatcher for the redesigned routing agent.

Maps VLM JSON actions to concrete operations (DEF edits, Tcl scripts,
OpenROAD calls) and produces an execution report.  Falls back to safe
higher-level actions when fine-grained edits fail.
"""

import shutil
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple

from .eda_provider import EDAProvider
from .composite_operators import CompositeOperators
from .def_editor import DEFEditor, DEFEditError
from .fishbone_router import FishboneRouter
from .local_router import LocalRouter
from .odb_editor import ODB_AVAILABLE, ODBEditError, ODBEditor
from .routing_toolkit import DRCViolation, NetRoutingFeatures, RoutingMetrics


class PolicyExecutor:
    """Execute a VLM policy on a DEF design."""

    def __init__(
        self,
        provider: EDAProvider,
        enable_fine_actions: bool = True,
        local_edit_threshold_nets: int = 3,
        enable_local_eval: bool = False,
        local_eval_work_dir: Optional[str] = None,
        prefer_odb: bool = True,
    ):
        self.provider = provider
        self.enable_fine_actions = enable_fine_actions
        self.local_edit_threshold = local_edit_threshold_nets
        self.enable_local_eval = enable_local_eval
        self.prefer_odb = prefer_odb and ODB_AVAILABLE
        self.local_router: Optional[LocalRouter] = None
        if enable_local_eval:
            work_dir = local_eval_work_dir or "./outputs/local_eval"
            self.local_router = LocalRouter(provider, work_dir)

        editor_name = "ODBEditor" if self.prefer_odb else "DEFEditor"
        print(f"[PolicyExecutor] Fine-grained editor preference: {editor_name}")

    def _get_editor(self, def_file: str):
        """
        Return an editor instance for fine-grained DEF edits.

        If ``odb`` is available and ``prefer_odb`` is True, return an
        ``ODBEditor``.  Otherwise return a ``DEFEditor``.  Callers should
        catch :class:`ODBEditError` and retry with ``DEFEditor``.
        """
        if self.prefer_odb:
            try:
                return ODBEditor(def_file)
            except ODBEditError:
                pass
        return DEFEditor(def_file)

    def execute_policy(
        self,
        def_file: str,
        policy: Dict,
        current_violations: List[DRCViolation],
        current_net_features: Dict[str, NetRoutingFeatures],
        current_metrics: Optional[RoutingMetrics] = None,
        metrics_callback: Optional[Callable[[str], RoutingMetrics]] = None,
    ) -> Tuple[str, List[Dict]]:
        """
        Execute all actions in a policy and return the resulting DEF and
        per-action execution reports.

        A checkpoint is saved before each action; failed actions are rolled
        back so that later actions start from a consistent DEF.  If the
        caller supplies ``current_metrics`` and ``metrics_callback``, the
        aggregate result is compared with the starting metrics and the policy
        is rolled back when DRC degrades beyond the configured threshold.

        The caller is responsible for extracting metrics/violations/features
        from the returned DEF to avoid redundant DRC runs.
        """
        policy_data = policy.get("routing_policy", {})
        actions = policy_data.get("priority_actions", [])

        current_def = def_file
        execution_report: List[Dict] = []
        edited_nets: set = set()
        self._current_violations = current_violations

        if not actions:
            return current_def, execution_report

        # Save a global checkpoint so the whole policy can be rolled back.
        initial_checkpoint = self._save_checkpoint(def_file, "policy_start")

        for idx, action in enumerate(actions):
            action_type = action.get("action", "")
            params = action.get("parameters", {})
            reason = action.get("reason", "")

            entry = {
                "index": idx,
                "action": action_type,
                "parameters": params,
                "reason": reason,
                "success": False,
                "error": None,
                "rolled_back": False,
            }

            # Save per-action checkpoint.
            action_checkpoint = self._save_checkpoint(current_def, f"action_{idx}")

            try:
                current_def = self._execute_action(
                    current_def,
                    action_type,
                    params,
                    current_violations,
                    current_net_features,
                )
                entry["success"] = True
                if "net_name" in params:
                    edited_nets.add(params["net_name"])
                if "target_nets" in params:
                    edited_nets.update(params["target_nets"])
            except Exception as e:
                entry["success"] = False
                entry["error"] = str(e)
                print(f"[WARNING] Action {idx} ({action_type}) failed: {e}")
                # Roll back to the pre-action checkpoint.
                current_def = self._restore_checkpoint(action_checkpoint)
                entry["rolled_back"] = True

            execution_report.append(entry)

        # Run detailed route only if at least one mutating action succeeded.
        mutating_actions = {
            "rip_up_reroute",
            "incremental_route",
            "layer_assign",
            "set_blockage",
            "rip_up_segment",
            "reassign_layer",
            "insert_jog",
            "move_via",
            "change_via_type",
            "rip_up_net",
            "reroute_net_with_constraints",
            "fishbone_route_net",
            "set_routing_blockage",
            "set_soft_guidance",
            "relax_region",
            "route_repair",
            "route_in_box",
            "route_channel",
            "optimize_congestion",
            "cleanup_routing",
        }
        any_mutating = any(
            entry["success"]
            and entry["action"] in mutating_actions
            for entry in execution_report
        )
        if any_mutating:
            current_def = self.provider.run_detailed_route(current_def)

        # Optional aggregate rollback if the whole policy made things worse.
        if current_metrics is not None and metrics_callback is not None:
            new_metrics = metrics_callback(current_def)
            if self._should_rollback(current_metrics, new_metrics):
                print(
                    "[WARNING] Policy degraded routing quality; rolling back to "
                    "pre-policy checkpoint."
                )
                current_def = self._restore_checkpoint(initial_checkpoint)
                # Mark all actions as rolled back at the policy level.
                for entry in execution_report:
                    entry["policy_rolled_back"] = True

        return current_def, execution_report

    def _save_checkpoint(self, def_file: str, tag: str) -> str:
        """Copy ``def_file`` to a checkpoint path and return the path."""
        src = Path(def_file)
        if not src.exists():
            # In unit-test or mock scenarios the DEF may not exist; use the
            # original path as a no-op checkpoint.
            return str(src)
        checkpoint_path = src.parent / f"_checkpoint_{tag}_{int(time.time())}.def"
        shutil.copy(str(src), str(checkpoint_path))
        return str(checkpoint_path)

    @staticmethod
    def _restore_checkpoint(checkpoint_path: str) -> str:
        """Return the checkpoint path so the caller can use it as current_def."""
        return checkpoint_path

    def _should_rollback(
        self, prev: RoutingMetrics, new: RoutingMetrics
    ) -> bool:
        """Return True if the new metrics are significantly worse."""
        # DRC is the primary objective.  Roll back if DRC increased by more
        # than 20% or by at least 5 violations (whichever is larger).
        drc_threshold = max(int(prev.drc_total * 0.2), 5)
        if new.drc_total > prev.drc_total + drc_threshold:
            return True

        # Also roll back if wirelength degraded by more than 5% while DRC
        # did not improve.
        if prev.drc_total == 0 and new.drc_total == 0:
            if prev.wirelength > 0:
                wl_increase = (new.wirelength - prev.wirelength) / prev.wirelength
                if wl_increase > 0.05:
                    return True

        return False

    @staticmethod
    def _extract_target_nets(action_type: str, params: Dict) -> Set[str]:
        """Return the set of nets affected by an action."""
        nets: Set[str] = set()
        if "net_name" in params:
            nets.add(params["net_name"])
        if "target_nets" in params:
            nets.update(params["target_nets"])
        return nets

    @staticmethod
    def _is_local_eval_eligible(action_type: str) -> bool:
        """Return True if the action type is suitable for local evaluation."""
        return action_type in {
            "rip_up_segment",
            "reassign_layer",
            "insert_jog",
            "move_via",
            "change_via_type",
        }

    def _execute_action(
        self,
        def_file: str,
        action_type: str,
        params: Dict,
        current_violations: List[DRCViolation],
        current_net_features: Dict[str, NetRoutingFeatures],
    ) -> str:
        """Dispatch a single action to its handler."""
        if action_type in ("terminate", "noop"):
            return def_file

        if action_type == "rip_up_segment":
            return self._handle_rip_up_segment(def_file, params)

        if action_type == "reassign_layer":
            return self._handle_reassign_layer(def_file, params)

        if action_type == "insert_jog":
            return self._handle_insert_jog(def_file, params)

        if action_type == "move_via":
            return self._handle_move_via(def_file, params)

        if action_type == "change_via_type":
            return self._handle_change_via_type(def_file, params)

        if action_type == "rip_up_net":
            return self._handle_rip_up_net(def_file, params)

        if action_type == "reroute_net_with_constraints":
            return self._handle_reroute_net_with_constraints(def_file, params)

        if action_type == "fishbone_route_net":
            return self._handle_fishbone_route_net(def_file, params)

        if action_type == "set_routing_blockage":
            return self._handle_set_routing_blockage(def_file, params)

        if action_type == "set_soft_guidance":
            return self._handle_set_soft_guidance(def_file, params)

        if action_type == "relax_region":
            return self._handle_relax_region(def_file, params)

        # Composite / strategy operators
        if action_type == "route_in_box":
            return self._handle_route_in_box(def_file, params)

        if action_type == "route_channel":
            return self._handle_route_channel(def_file, params)

        if action_type == "route_repair":
            return self._handle_route_repair(
                def_file, params, current_violations, current_net_features
            )

        if action_type == "optimize_congestion":
            return self._handle_optimize_congestion(def_file, params)

        if action_type == "cleanup_routing":
            return self._handle_cleanup_routing(def_file, params)

        # Backwards-compatible coarse actions
        if action_type == "rip_up_reroute":
            return self._handle_rip_up_reroute(def_file, params)

        if action_type == "incremental_route":
            return self.provider.run_detailed_route(def_file)

        if action_type == "layer_assign":
            return self._handle_rip_up_reroute(def_file, params)

        if action_type == "set_blockage":
            return self._handle_set_blockage_compat(def_file, params)

        raise ValueError(f"Unknown action type: {action_type}")

    # ------------------------------------------------------------------
    # Fine-grained handlers
    # ------------------------------------------------------------------

    @staticmethod
    def _filter_violations_by_nets(
        violations: List[DRCViolation], target_nets: Set[str]
    ) -> List[DRCViolation]:
        """Return violations that involve any of the target nets."""
        return [v for v in violations if set(v.nets_involved) & target_nets]

    def _run_fine_edit(
        self,
        def_file: str,
        action_type: str,
        target_nets: Set[str],
        edit_fn,
    ) -> str:
        """
        Run a fine-grained edit with automatic ODB -> DEF fallback and optional
        local DRC evaluation.

        If ``odb`` is available, the edit is attempted via ``ODBEditor`` first.
        On :class:`ODBEditError` the same edit is retried with ``DEFEditor``.
        When ``odb`` is unavailable, ``DEFEditor`` is used directly.

        When ``enable_local_eval`` is True and the action type is eligible, the
        edit is evaluated with :class:`LocalRouter` before being committed.  If
        the local evaluation fails, a :class:`DEFEditError` is raised so the
        caller can roll back.
        """
        editor = self._get_editor(def_file)
        try:
            edited_def = edit_fn(editor)
        except ODBEditError as exc:
            print(f"[INFO] ODB edit failed ({exc}); falling back to DEFEditor")
            editor = DEFEditor(def_file)
            edited_def = edit_fn(editor)

        if self.enable_local_eval and self._is_local_eval_eligible(action_type):
            previous_region_violations = self._filter_violations_by_nets(
                self._current_violations, target_nets
            )
            result = self.local_router.evaluate_edit(
                edited_def,
                list(target_nets),
                previous_region_violations=previous_region_violations,
            )
            if not result.passed:
                raise DEFEditError(
                    f"Local eval rejected {action_type}: {result.detail}"
                )

        return edited_def

    def _handle_rip_up_segment(self, def_file: str, params: Dict) -> str:
        if not self.enable_fine_actions:
            raise DEFEditError("Fine actions are disabled")
        target_nets = {params["net_name"]}
        return self._run_fine_edit(
            def_file,
            "rip_up_segment",
            target_nets,
            lambda editor: editor.rip_up_segment(
                params["net_name"],
                segment_index=params.get("segment_index", 0),
                layer=params.get("layer"),
            ),
        )

    def _handle_reassign_layer(self, def_file: str, params: Dict) -> str:
        if not self.enable_fine_actions:
            raise DEFEditError("Fine actions are disabled")
        target_nets = {params["net_name"]}
        return self._run_fine_edit(
            def_file,
            "reassign_layer",
            target_nets,
            lambda editor: editor.reassign_layer(
                params["net_name"],
                segment_index=params.get("segment_index", 0),
                from_layer=params.get("from_layer", ""),
                to_layer=params.get("to_layer", ""),
            ),
        )

    def _handle_insert_jog(self, def_file: str, params: Dict) -> str:
        if not self.enable_fine_actions:
            raise DEFEditError("Fine actions are disabled")
        target_nets = {params["net_name"]}
        return self._run_fine_edit(
            def_file,
            "insert_jog",
            target_nets,
            lambda editor: editor.insert_jog(
                params["net_name"],
                segment_index=params.get("segment_index", 0),
                layer=params.get("layer"),
                jog_point=tuple(params["jog_point"]),
                jog_direction=params.get("jog_direction", "horizontal"),
            ),
        )

    def _handle_move_via(self, def_file: str, params: Dict) -> str:
        if not self.enable_fine_actions:
            raise DEFEditError("Fine actions are disabled")
        target_nets = {params["net_name"]}
        return self._run_fine_edit(
            def_file,
            "move_via",
            target_nets,
            lambda editor: editor.move_via(
                params["net_name"],
                via_index=params.get("via_index", 0),
                new_position=tuple(params["new_position"]),
                old_position=tuple(params["old_position"])
                if "old_position" in params
                else None,
            ),
        )

    def _handle_change_via_type(self, def_file: str, params: Dict) -> str:
        if not self.enable_fine_actions:
            raise DEFEditError("Fine actions are disabled")
        target_nets = {params["net_name"]}
        return self._run_fine_edit(
            def_file,
            "change_via_type",
            target_nets,
            lambda editor: editor.change_via_type(
                params["net_name"],
                via_index=params.get("via_index", 0),
                new_type=params.get("new_type", ""),
                position=tuple(params["position"]) if "position" in params else None,
            ),
        )

    def _handle_rip_up_net(self, def_file: str, params: Dict) -> str:
        net_name = params["net_name"]
        return self.provider.rip_up_nets(def_file, [net_name])

    def _handle_reroute_net_with_constraints(self, def_file: str, params: Dict) -> str:
        # Simplified: rip up the net and run detailed route with layer preference
        net_name = params["net_name"]
        ripped = self.provider.rip_up_nets(def_file, [net_name])
        # TODO: inject temporary blockages and set routing layers via Tcl
        return self.provider.run_detailed_route(ripped)

    def _handle_fishbone_route_net(self, def_file: str, params: Dict) -> str:
        lef_file = getattr(self.provider.toolkit, "lef_file", None)
        lef_path = str(lef_file) if lef_file else None
        router = FishboneRouter(
            def_file,
            lef_file=lef_path,
            obstacle_margin_nm=params.get("obstacle_margin_nm", 200),
        )
        route_text = router.fishbone_route_net(
            params["net_name"],
            preferred_layers=params.get("preferred_layers"),
            trunk_direction=params.get("trunk_direction", "auto"),
            max_branches_per_trunk=params.get("max_branches_per_trunk", 50),
        )
        if route_text is None:
            # Fallback to simple rip-up and reroute
            return self.provider.rip_up_nets(def_file, [params["net_name"]])

        editor = DEFEditor(def_file)
        return editor.replace_net_routing(params["net_name"], route_text)

    def _handle_set_routing_blockage(self, def_file: str, params: Dict) -> str:
        bbox = tuple(params["bbox"])
        layers = params.get("layers", ["M2", "M3", "M4"])
        hardness = params.get("hardness", "hard")
        # Note: DEF does not natively distinguish soft vs hard blockage.
        # The hardness parameter is recorded and can be used by downstream
        # tools; for now both map to the same BLOCKAGES section.
        return self.provider.set_routing_blockage(
            def_file, bbox, layers, hardness=hardness
        )

    def _handle_relax_region(self, def_file: str, params: Dict) -> str:
        editor = DEFEditor(def_file)
        return editor.relax_region(
            tuple(params["bbox"]), layers=params.get("layers")
        )

    def _handle_set_soft_guidance(self, def_file: str, params: Dict) -> str:
        """
        Apply soft layer guidance to a net by temporarily blocking
        non-preferred layers around the guide points, rerouting, then removing
        the temporary blockages.
        """
        net_name = params["net_name"]
        guide_points = params.get("guide_points", [])
        preferred_layer = params.get("layer", "M3")
        margin_nm = params.get("margin_nm", 1000)
        all_layers = params.get("all_layers", ["M2", "M3", "M4", "M5"])

        if not guide_points:
            return def_file

        xs = [int(p[0]) for p in guide_points]
        ys = [int(p[1]) for p in guide_points]
        bbox = (
            min(xs) - margin_nm,
            min(ys) - margin_nm,
            max(xs) + margin_nm,
            max(ys) + margin_nm,
        )
        blocked_layers = [l for l in all_layers if l.lower() != preferred_layer.lower()]

        # Rip up the target net so the router can follow the guidance.
        ripped = self.provider.rip_up_nets(def_file, [net_name])

        # Add temporary blockages on non-preferred layers.
        blocked = self.provider.set_routing_blockage(
            ripped, bbox, blocked_layers
        )

        # Reroute.
        routed = self.provider.run_detailed_route(blocked)

        # Remove the temporary blockages.
        editor = DEFEditor(routed)
        return editor.relax_region(bbox, layers=blocked_layers)

    # ------------------------------------------------------------------
    # Composite / strategy handlers
    # ------------------------------------------------------------------

    def _handle_route_in_box(self, def_file: str, params: Dict) -> str:
        ops = CompositeOperators(self.provider)
        result = ops.route_in_box(
            def_file,
            bbox=tuple(params["bbox"]),
            target_nets=params.get("target_nets", []),
            preferred_layers=params.get("preferred_layers"),
        )
        return result.output_def

    def _handle_route_channel(self, def_file: str, params: Dict) -> str:
        """Route inside a channel-shaped bbox (tall or wide)."""
        ops = CompositeOperators(self.provider)
        bbox = tuple(params["bbox"])
        target_nets = params.get("target_nets", [])
        result = ops.route_in_box(
            def_file,
            bbox=bbox,
            target_nets=target_nets,
            preferred_layers=params.get("preferred_layers"),
        )
        return result.output_def

    def _handle_route_repair(
        self,
        def_file: str,
        params: Dict,
        current_violations: List[DRCViolation],
        current_net_features: Dict[str, NetRoutingFeatures],
    ) -> str:
        ops = CompositeOperators(self.provider)
        result = ops.route_repair(
            def_file,
            violation_ids=params.get("violation_ids", []),
            current_violations=current_violations,
            current_net_features=current_net_features,
        )
        return result.output_def

    def _handle_optimize_congestion(self, def_file: str, params: Dict) -> str:
        ops = CompositeOperators(self.provider)
        result = ops.optimize_congestion(
            def_file,
            hotspot_bbox=tuple(params["bbox"]),
            affected_nets=params.get("affected_nets", []),
            layers=params.get("layers"),
        )
        return result.output_def

    def _handle_cleanup_routing(self, def_file: str, params: Dict) -> str:
        ops = CompositeOperators(self.provider)
        result = ops.cleanup_routing(def_file)
        return result.output_def

    # ------------------------------------------------------------------
    # Backwards-compatible coarse handlers
    # ------------------------------------------------------------------

    def _handle_rip_up_reroute(self, def_file: str, params: Dict) -> str:
        target_nets = params.get("target_nets", [])
        if not target_nets:
            return def_file
        ripped = self.provider.rip_up_nets(def_file, target_nets)
        return self.provider.run_detailed_route(ripped)

    def _handle_set_blockage_compat(self, def_file: str, action: Dict) -> str:
        avoid_regions = action.get("avoid_regions", [])
        if not avoid_regions:
            return def_file
        current = def_file
        for region in avoid_regions:
            bbox = region.get("bbox")
            if bbox and len(bbox) == 4:
                layers = action.get("layer_preference", ["M2", "M3", "M4"])
                current = self.provider.set_routing_blockage(
                    current, tuple(bbox), layers
                )
        return current
