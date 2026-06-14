"""
src/policy_executor.py

Action dispatcher for the redesigned routing agent.

Maps VLM JSON actions to concrete operations (DEF edits, Tcl scripts,
OpenROAD calls) and produces an execution report.  Falls back to safe
higher-level actions when fine-grained edits fail.
"""

import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .eda_provider import EDAProvider
from .def_editor import DEFEditor, DEFEditError
from .fishbone_router import FishboneRouter
from .routing_toolkit import DRCViolation, NetRoutingFeatures


class PolicyExecutor:
    """Execute a VLM policy on a DEF design."""

    def __init__(
        self,
        provider: EDAProvider,
        enable_fine_actions: bool = True,
        local_edit_threshold_nets: int = 3,
    ):
        self.provider = provider
        self.enable_fine_actions = enable_fine_actions
        self.local_edit_threshold = local_edit_threshold_nets

    def execute_policy(
        self,
        def_file: str,
        policy: Dict,
        current_violations: List[DRCViolation],
        current_net_features: Dict[str, NetRoutingFeatures],
    ) -> Tuple[str, List[Dict]]:
        """
        Execute all actions in a policy and return the resulting DEF and
        per-action execution reports.

        The caller is responsible for extracting metrics/violations/features
        from the returned DEF to avoid redundant DRC runs.
        """
        policy_data = policy.get("routing_policy", {})
        actions = policy_data.get("priority_actions", [])

        current_def = def_file
        execution_report: List[Dict] = []
        edited_nets: set = set()

        if not actions:
            return current_def, execution_report

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
            }

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

            execution_report.append(entry)

        # Always run detailed route after edits to ensure a valid routed DEF
        current_def = self.provider.run_detailed_route(current_def)
        return current_def, execution_report

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
            # Not implemented in this simplified version
            return def_file

        if action_type == "relax_region":
            return self._handle_relax_region(def_file, params)

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

    def _handle_rip_up_segment(self, def_file: str, params: Dict) -> str:
        if not self.enable_fine_actions:
            raise DEFEditError("Fine actions are disabled")
        editor = DEFEditor(def_file)
        return editor.rip_up_segment(
            params["net_name"],
            segment_index=params.get("segment_index", 0),
            layer=params.get("layer"),
        )

    def _handle_reassign_layer(self, def_file: str, params: Dict) -> str:
        if not self.enable_fine_actions:
            raise DEFEditError("Fine actions are disabled")
        editor = DEFEditor(def_file)
        return editor.reassign_layer(
            params["net_name"],
            segment_index=params.get("segment_index", 0),
            from_layer=params.get("from_layer", ""),
            to_layer=params.get("to_layer", ""),
        )

    def _handle_insert_jog(self, def_file: str, params: Dict) -> str:
        if not self.enable_fine_actions:
            raise DEFEditError("Fine actions are disabled")
        editor = DEFEditor(def_file)
        return editor.insert_jog(
            params["net_name"],
            segment_index=params.get("segment_index", 0),
            layer=params.get("layer"),
            jog_point=tuple(params["jog_point"]),
            jog_direction=params.get("jog_direction", "horizontal"),
        )

    def _handle_move_via(self, def_file: str, params: Dict) -> str:
        if not self.enable_fine_actions:
            raise DEFEditError("Fine actions are disabled")
        editor = DEFEditor(def_file)
        return editor.move_via(
            params["net_name"],
            via_index=params.get("via_index", 0),
            new_position=tuple(params["new_position"]),
            old_position=tuple(params["old_position"])
            if "old_position" in params
            else None,
        )

    def _handle_change_via_type(self, def_file: str, params: Dict) -> str:
        if not self.enable_fine_actions:
            raise DEFEditError("Fine actions are disabled")
        editor = DEFEditor(def_file)
        return editor.change_via_type(
            params["net_name"],
            via_index=params.get("via_index", 0),
            new_type=params.get("new_type", ""),
            position=tuple(params["position"]) if "position" in params else None,
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
        return self.provider.set_routing_blockage(def_file, bbox, layers)

    def _handle_relax_region(self, def_file: str, params: Dict) -> str:
        editor = DEFEditor(def_file)
        return editor.relax_region(
            tuple(params["bbox"]), layers=params.get("layers")
        )

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
