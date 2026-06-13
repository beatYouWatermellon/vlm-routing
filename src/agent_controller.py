"""
src/agent_controller.py

Agent controller. Orchestrates the optimization loop:
Extract -> Render -> VLM Policy -> Execute -> Evaluate -> Iterate.
"""

import json
import shutil
import time
from typing import Dict, List, Optional, Tuple
from pathlib import Path
from dataclasses import asdict

from .routing_toolkit import RoutingToolkit, RoutingMetrics, RoutingState
from .visual_renderer import VisualRenderer
from .vlm_policy import VLMPolicyGenerator


class RoutingAgent:
    """
    Routing optimization agent.

    Runs a ReAct-style loop where the VLM reasons about routing state and the
    agent executes tool calls. Maintains best solution, history, and checkpoints.
    """

    def __init__(
        self,
        toolkit: RoutingToolkit,
        renderer: VisualRenderer,
        vlm: VLMPolicyGenerator,
        work_dir: str,
        max_iterations: int = 20,
        patience: int = 5,
    ):
        self.toolkit = toolkit
        self.renderer = renderer
        self.vlm = vlm
        self.work_dir = Path(work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.max_iterations = max_iterations
        self.patience = patience

        self.history: List[Tuple[str, RoutingMetrics, str]] = []
        self.current_def: Optional[str] = None
        self.current_metrics: Optional[RoutingMetrics] = None
        self.best_def: Optional[str] = None
        self.best_metrics: Optional[RoutingMetrics] = None
        self.no_improve_count = 0

    def optimize(
        self, initial_def: str, guide_file: Optional[str] = None
    ) -> Tuple[str, RoutingMetrics]:
        """Run the full optimization loop."""
        print("=" * 70)
        print("VLM Routing Agent - Optimization Starting")
        print("=" * 70)

        # Phase 1: baseline routing
        print("\n[Phase 1] Running baseline routing...")
        start_time = time.time()

        self.current_def = self.toolkit.run_baseline_flow(initial_def, guide_file)
        self.current_metrics = self.toolkit.extract_metrics(self.current_def)

        baseline_time = time.time() - start_time
        print(f"[OK] Baseline complete in {baseline_time:.1f}s")
        print(
            f"    DRC: {self.current_metrics.drc_total} | "
            f"WL: {self.current_metrics.wirelength:.2f} um | "
            f"Vias: {self.current_metrics.via_count}"
        )

        self.best_def = self.current_def
        self.best_metrics = self.current_metrics
        self._save_checkpoint(-1, self.current_def, self.current_metrics)

        if self.current_metrics.drc_total == 0:
            print("\n[COMPLETE] Design is already DRC-clean!")
            return self.current_def, self.current_metrics

        # Iterative optimization
        iteration = 0
        for iteration in range(self.max_iterations):
            print(f"\n{'='*70}")
            print(f"[Iteration {iteration + 1}/{self.max_iterations}]")
            print(
                f"Current Best: DRC={self.best_metrics.drc_total}, "
                f"WL={self.best_metrics.wirelength:.2f}"
            )
            print(f"{'='*70}")

            iter_start = time.time()

            print("  [Step 1/5] Extracting state...")
            state = self._extract_state(self.current_def)

            print("  [Step 2/5] Rendering visual state...")
            image_paths = self._render_visual_state(state, iteration)

            print("  [Step 3/5] Querying VLM for policy...")
            last_action = self.history[-1][2] if self.history else "baseline"
            last_metrics = self.history[-1][1] if self.history else None

            policy = self.vlm.generate_policy(
                image_paths=image_paths,
                netlist_stats=state.netlist_stats,
                metrics=asdict(self.current_metrics),
                last_action=last_action,
                last_metrics=asdict(last_metrics) if last_metrics else None,
                iteration=iteration,
            )

            policy_data = policy.get("routing_policy", {})
            strategy_type = policy_data.get("strategy_type", "unknown")
            analysis = policy_data.get("analysis", "")
            print(f"    VLM Analysis: {analysis}")
            print(f"    Strategy: {strategy_type}")

            if policy_data.get("termination_check", False):
                print("  [VLM] Termination suggested.")
                break

            print("  [Step 4/5] Executing policy...")
            new_def, new_metrics = self._execute_policy(
                self.current_def, policy, iteration
            )

            print("  [Step 5/5] Evaluating result...")
            drc_change = new_metrics.drc_total - self.current_metrics.drc_total
            wl_change = new_metrics.wirelength - self.current_metrics.wirelength

            print(
                f"    Result: DRC={new_metrics.drc_total} ({drc_change:+d}), "
                f"WL={new_metrics.wirelength:.2f} ({wl_change:+.2f})"
            )

            is_better = self._is_better_metrics(new_metrics, self.best_metrics)
            if is_better:
                self.best_def = new_def
                self.best_metrics = new_metrics
                self.no_improve_count = 0
                print("    >>> New best solution found! <<<")
            else:
                self.no_improve_count += 1

            action_str = strategy_type
            self.history.append((image_paths[0], new_metrics, action_str))

            self.current_def = new_def
            self.current_metrics = new_metrics

            self._save_checkpoint(iteration, new_def, new_metrics)

            if new_metrics.drc_total == 0:
                print("\n[COMPLETE] DRC is clean! Optimization complete.")
                break

            if self.no_improve_count >= self.patience:
                print(
                    f"\n[STOP] No improvement for {self.patience} iterations. "
                    "Early stopping."
                )
                break

            iter_time = time.time() - iter_start
            print(f"  Iteration time: {iter_time:.1f}s")

        print(f"\n{'='*70}")
        print("Optimization Complete!")
        print(f"{'='*70}")
        print(
            f"Best result: DRC={self.best_metrics.drc_total}, "
            f"WL={self.best_metrics.wirelength:.2f}, "
            f"Via={self.best_metrics.via_count}"
        )
        print(f"Total iterations: {iteration + 1}")
        print(f"Best DEF: {self.best_def}")

        return self.best_def, self.best_metrics

    def _extract_state(self, def_file: str) -> RoutingState:
        congestion = self.toolkit.extract_congestion_map(def_file)
        routing_layers = self.toolkit.extract_routing_layers(
            def_file, layers=["M2", "M3", "M4", "M5"]
        )
        netlist_stats = self.toolkit.extract_netlist_stats(def_file)
        drc_report = self.toolkit.extract_drc_report(def_file)

        drc_markers = []
        for v in drc_report.get("violations", []):
            if isinstance(v, dict) and "x" in v and "y" in v:
                vtype = v.get("type", "unknown")
                x = v["x"]
                y = v["y"]
                drc_markers.append((x, y, vtype))

        return RoutingState(
            def_file=def_file,
            congestion_map=congestion,
            routing_layers=routing_layers,
            metrics=self.current_metrics,
            netlist_stats=netlist_stats,
            drc_report=drc_report,
            drc_markers=drc_markers,
            iteration=len(self.history),
        )

    def _render_visual_state(
        self, state: RoutingState, iteration: int
    ) -> List[str]:
        metrics_dict = asdict(state.metrics) if state.metrics else None

        main_image = self.renderer.render_state(
            congestion_map=state.congestion_map,
            routing_layers=state.routing_layers,
            drc_markers=state.drc_markers,
            netlist_stats=state.netlist_stats,
            metrics=metrics_dict,
            iteration=iteration,
        )

        images = [main_image]

        if self.history:
            prev_image = self.history[-1][0]
            if Path(prev_image).exists() and Path(main_image).exists():
                diff_image = self.renderer.render_diff(
                    prev_image, main_image, iteration
                )
                images.append(diff_image)

        return images

    def _execute_policy(
        self, def_file: str, policy: Dict, iteration: int
    ) -> Tuple[str, RoutingMetrics]:
        policy_data = policy.get("routing_policy", {})
        actions = policy_data.get("priority_actions", [])

        current_def = def_file

        if not actions:
            print("    No actions specified, returning current state")
            metrics = self.toolkit.extract_metrics(current_def)
            return current_def, metrics

        for action in actions:
            action_type = action.get("action", "")

            if action_type == "rip_up_reroute":
                target_nets = action.get("target_nets", [])
                if target_nets:
                    print(f"    Action: Rip-up & reroute {len(target_nets)} nets")
                    current_def, _ = self.toolkit.rip_up_and_reroute(
                        current_def, target_nets, action
                    )
                else:
                    print("    Action: rip_up_reroute with no target nets, skipping")
                    metrics = self.toolkit.extract_metrics(current_def)
                    return current_def, metrics

            elif action_type == "incremental_route":
                print("    Action: Incremental route")
                current_def = self.toolkit.run_incremental_route(current_def)

            elif action_type == "layer_assign":
                target_nets = action.get("target_nets", [])
                if target_nets:
                    print(f"    Action: Layer assign for {len(target_nets)} nets")
                    current_def, _ = self.toolkit.rip_up_and_reroute(
                        current_def, target_nets, action
                    )

            elif action_type == "set_blockage":
                avoid_regions = action.get("avoid_regions", [])
                if avoid_regions:
                    print(f"    Action: Set {len(avoid_regions)} routing blockages")
                    for region in avoid_regions:
                        bbox = region.get("bbox")
                        if bbox and len(bbox) == 4:
                            layers = action.get("layer_preference", ["M2", "M3", "M4"])
                            current_def = self.toolkit.set_routing_blockage(
                                current_def, tuple(bbox), layers
                            )

            elif action_type == "terminate":
                print("    Action: Terminate")
                break

            else:
                print(f"    Unknown action type: {action_type}, skipping")

        final_metrics = self.toolkit.extract_metrics(current_def)
        return current_def, final_metrics

    @staticmethod
    def _is_better_metrics(new: RoutingMetrics, best: RoutingMetrics) -> bool:
        if new.drc_total < best.drc_total:
            return True
        if new.drc_total > best.drc_total:
            return False
        if new.wirelength < best.wirelength:
            return True
        if new.wirelength > best.wirelength:
            return False
        return new.via_count < best.via_count

    def _save_checkpoint(
        self, iteration: int, def_file: str, metrics: RoutingMetrics
    ):
        checkpoint_dir = self.work_dir / "checkpoints"
        checkpoint_dir.mkdir(exist_ok=True)

        shutil.copy(def_file, checkpoint_dir / f"iter_{iteration:03d}.def")

        with open(checkpoint_dir / f"iter_{iteration:03d}.json", "w") as f:
            json.dump(asdict(metrics), f, indent=2)


if __name__ == "__main__":
    print(
        "AgentController module loaded. Use with RoutingToolkit, "
        "VisualRenderer, VLMPolicyGenerator."
    )
