"""
src/fishbone_router.py

Simplified fishbone (trunk + branch) net-level router.

Generates a structured routing topology for high-fanout nets and writes it
back to DEF.  The generated routes are then validated and repaired by
OpenROAD's detailed router.
"""

import re
from typing import Dict, List, Optional, Set, Tuple

from .def_parser import DefParser


class FishboneRouter:
    """
    Generate a trunk-and-branch routing topology for a single net.

    This is a heuristic router.  It does not guarantee DRC cleanliness; the
    output is intended as a high-quality initial solution for OpenROAD.
    """

    def __init__(
        self,
        def_file: str,
        lef_file: Optional[str] = None,
        obstacle_margin_nm: int = 200,
    ):
        self.def_file = def_file
        self.lef_file = lef_file
        self.obstacle_margin = obstacle_margin_nm

        self.nets = DefParser.parse_nets(def_file)
        self.die_area = DefParser.parse_die_area(def_file)
        self.lef_layers = (
            DefParser.parse_lef_layers(lef_file) if lef_file else {}
        )
        self.lef_vias = (
            DefParser.parse_lef_vias(lef_file) if lef_file else {}
        )

    def fishbone_route_net(
        self,
        net_name: str,
        preferred_layers: Optional[List[str]] = None,
        trunk_direction: str = "auto",
        max_branches_per_trunk: int = 50,
    ) -> Optional[str]:
        """
        Generate a fishbone route for the given net and return the DEF routing
        text (starting with 'ROUTED ...').  Returns None if the net cannot be
        routed.
        """
        net = self.nets.get(net_name)
        if net is None:
            return None

        pins = net.pin_coords
        if len(pins) < 2:
            return None

        trunk_layer, branch_layer = self._select_layer_pair(preferred_layers)
        if trunk_layer is None:
            return None

        direction = self._choose_trunk_direction(
            pins, trunk_layer, trunk_direction
        )

        # Obstacles from other nets
        obstacles = self._build_obstacles(net_name, [trunk_layer, branch_layer])

        # Compute trunk coordinate and range
        if direction == "horizontal":
            xs = [p[0] for p in pins]
            min_t, max_t = min(xs), max(xs)
            trunk_fixed = self._choose_trunk_fixed(pins, direction, obstacles)
            trunk_seg = {
                "layer": trunk_layer,
                "x1": min_t,
                "y1": trunk_fixed,
                "x2": max_t,
                "y2": trunk_fixed,
            }
        else:
            ys = [p[1] for p in pins]
            min_t, max_t = min(ys), max(ys)
            trunk_fixed = self._choose_trunk_fixed(pins, direction, obstacles)
            trunk_seg = {
                "layer": trunk_layer,
                "x1": trunk_fixed,
                "y1": min_t,
                "x2": trunk_fixed,
                "y2": max_t,
            }

        # Generate branches
        branches: List[Dict] = []
        for pin in pins[:max_branches_per_trunk]:
            branch = self._make_branch(
                pin, trunk_fixed, direction, branch_layer, obstacles
            )
            if branch:
                branches.extend(branch)

        return self._compose_def_routing(trunk_seg, branches, trunk_layer)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _select_layer_pair(
        self, preferred_layers: Optional[List[str]]
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Return (trunk_layer, branch_layer).  Prefer perpendicular layers so
        that branches connect to the trunk with a via.
        """
        candidates = preferred_layers or ["Metal3", "Metal4", "Metal2"]
        available = {k.lower(): k for k in self.lef_layers.keys()}

        resolved = []
        for c in candidates:
            key = c.lower()
            if key in available:
                resolved.append(available[key])
            elif key.startswith("metal") and key[5:].isdigit():
                # Allow "M3" style shorthand
                alt = f"Metal{key[1:]}" if key[0] == "m" else c
                if alt.lower() in available:
                    resolved.append(available[alt.lower()])
            elif key.startswith("m") and key[1:].isdigit():
                # "M3" -> "Metal3"
                alt = f"Metal{key[1:]}"
                if alt.lower() in available:
                    resolved.append(available[alt.lower()])

        # If LEF parsing yielded no layers, trust the candidate names directly.
        if not available:
            if len(resolved) >= 2:
                return resolved[0], resolved[1]
            if len(resolved) == 1:
                return resolved[0], resolved[0]
            return None, None

        if not resolved:
            return None, None

        # Try to find a perpendicular pair
        for trunk in resolved:
            trunk_dir = self._layer_direction(trunk)
            for branch in resolved:
                if branch == trunk:
                    continue
                branch_dir = self._layer_direction(branch)
                if branch_dir and trunk_dir and branch_dir != trunk_dir:
                    return trunk, branch

        # Fallback: use the same layer for both
        return resolved[0], resolved[0]

    def _layer_direction(self, layer: str) -> Optional[str]:
        props = self.lef_layers.get(layer, {})
        return props.get("direction", "").lower() or None

    def _choose_trunk_direction(
        self,
        pins: List[Tuple[int, int]],
        trunk_layer: str,
        trunk_direction: str,
    ) -> str:
        if trunk_direction != "auto":
            return trunk_direction

        layer_dir = self._layer_direction(trunk_layer)
        xs = [p[0] for p in pins]
        ys = [p[1] for p in pins]
        width = max(xs) - min(xs)
        height = max(ys) - min(ys)

        # If layer has a preferred direction, try to align trunk with it
        if layer_dir == "horizontal":
            return "horizontal"
        if layer_dir == "vertical":
            return "vertical"

        # Otherwise choose based on pin spread
        return "horizontal" if width >= height else "vertical"

    def _choose_trunk_fixed(
        self,
        pins: List[Tuple[int, int]],
        direction: str,
        obstacles: Set[Tuple[int, int, int, int, str]],
    ) -> int:
        """Choose the fixed coordinate of the trunk line (y for horizontal)."""
        if direction == "horizontal":
            coords = [p[1] for p in pins]
        else:
            coords = [p[0] for p in pins]

        if not coords:
            return 0

        median = sorted(coords)[len(coords) // 2]

        # Simple obstacle avoidance: scan a few positions near median and pick
        # the one with least obstacle density
        best = median
        best_cost = float("inf")
        for delta in range(-1000, 1001, 200):
            coord = median + delta
            cost = self._obstacle_density_at_trunk(
                pins, direction, coord, obstacles
            )
            if cost < best_cost:
                best_cost = cost
                best = coord

        return best

    def _obstacle_density_at_trunk(
        self,
        pins: List[Tuple[int, int]],
        direction: str,
        coord: int,
        obstacles: Set[Tuple[int, int, int, int, str]],
    ) -> float:
        if direction == "horizontal":
            xs = [p[0] for p in pins]
            bbox = (min(xs), coord, max(xs), coord)
        else:
            ys = [p[1] for p in pins]
            bbox = (coord, min(ys), coord, max(ys))

        count = 0
        for ob in obstacles:
            if self._bbox_intersects(bbox, (ob[0], ob[1], ob[2], ob[3])):
                count += 1
        return count

    @staticmethod
    def _bbox_intersects(
        a: Tuple[int, int, int, int], b: Tuple[int, int, int, int]
    ) -> bool:
        return not (
            a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3]
        )

    def _make_branch(
        self,
        pin: Tuple[int, int],
        trunk_fixed: int,
        direction: str,
        branch_layer: str,
        obstacles: Set[Tuple[int, int, int, int, str]],
    ) -> List[Dict]:
        """
        Generate branch segments from pin to trunk.

        For horizontal trunk at y=trunk_fixed:
            branch: (pin_x, pin_y) -> (pin_x, trunk_fixed) on branch_layer
        For vertical trunk at x=trunk_fixed:
            branch: (pin_x, pin_y) -> (trunk_fixed, pin_y) on branch_layer
        """
        px, py = pin
        if direction == "horizontal":
            if py == trunk_fixed:
                return []
            seg = {
                "layer": branch_layer,
                "x1": px,
                "y1": py,
                "x2": px,
                "y2": trunk_fixed,
            }
        else:
            if px == trunk_fixed:
                return []
            seg = {
                "layer": branch_layer,
                "x1": px,
                "y1": py,
                "x2": trunk_fixed,
                "y2": py,
            }

        # Simple obstacle check: if the straight branch collides with an
        # obstacle, try a small jog offset
        if self._segment_collides(seg, obstacles):
            for offset in (-400, 400, -800, 800, -1200, 1200):
                jogged = self._jog_branch(seg, offset, direction)
                if not self._segment_collides(jogged[0], obstacles) and (
                    len(jogged) < 2
                    or not self._segment_collides(jogged[1], obstacles)
                ):
                    return jogged
            # Could not avoid; emit straight branch anyway and let OpenROAD fix

        return [seg]

    def _jog_branch(
        self, seg: Dict, offset: int, direction: str
    ) -> List[Dict]:
        """Convert a straight branch into an L or Z shape with a jog."""
        x1, y1, x2, y2 = seg["x1"], seg["y1"], seg["x2"], seg["y2"]
        layer = seg["layer"]

        if direction == "horizontal":
            # Vertical branch: jog in x
            mid_y = (y1 + y2) // 2
            return [
                {"layer": layer, "x1": x1, "y1": y1, "x2": x1 + offset, "y2": y1},
                {"layer": layer, "x1": x1 + offset, "y1": y1, "x2": x1 + offset, "y2": y2},
                {"layer": layer, "x1": x1 + offset, "y1": y2, "x2": x2, "y2": y2},
            ]
        else:
            # Horizontal branch: jog in y
            mid_x = (x1 + x2) // 2
            return [
                {"layer": layer, "x1": x1, "y1": y1, "x2": x2, "y2": y1},
                {"layer": layer, "x1": x2, "y1": y1, "x2": x2, "y2": y1 + offset},
                {"layer": layer, "x1": x2, "y1": y1 + offset, "x2": x2, "y2": y2},
            ]

    def _segment_collides(
        self, seg: Dict, obstacles: Set[Tuple[int, int, int, int, str]]
    ) -> bool:
        bbox = (
            min(seg["x1"], seg["x2"]),
            min(seg["y1"], seg["y2"]),
            max(seg["x1"], seg["x2"]),
            max(seg["y1"], seg["y2"]),
        )
        for ob in obstacles:
            if seg["layer"].lower() != ob[4].lower():
                continue
            if self._bbox_intersects(bbox, (ob[0], ob[1], ob[2], ob[3])):
                return True
        return False

    def _build_obstacles(
        self,
        target_net: str,
        layers: List[str],
    ) -> Set[Tuple[int, int, int, int, str]]:
        """Build a set of obstacle bboxes from other nets' segments."""
        obstacles: Set[Tuple[int, int, int, int, str]] = set()
        layer_set = {l.lower() for l in layers}
        margin = self.obstacle_margin

        for net_name, net in self.nets.items():
            if net_name == target_net:
                continue
            for seg in net.segments:
                if seg.layer.lower() not in layer_set:
                    continue
                obstacles.add(
                    (
                        seg.bbox[0] - margin,
                        seg.bbox[1] - margin,
                        seg.bbox[2] + margin,
                        seg.bbox[3] + margin,
                        seg.layer,
                    )
                )
        return obstacles

    def _select_via_name(self, layer_a: str, layer_b: str) -> str:
        """Select a via connecting two routing layers."""
        layer_set = {layer_a.lower(), layer_b.lower()}

        # Try to match a via from the LEF via definitions.
        for via_name, via_info in self.lef_vias.items():
            via_layers = {l.lower() for l in via_info.get("layers", [])}
            if layer_set <= via_layers:
                return via_name

        # Fallback: derive via name from layer numbers, e.g. Metal2+Metal3 -> VIA23_1C
        def layer_num(name: str) -> int:
            digits = re.findall(r"\d+", name)
            return int(digits[0]) if digits else 0

        nums = sorted([layer_num(layer_a), layer_num(layer_b)])
        if len(nums) == 2 and nums[0] > 0 and nums[1] > 0:
            return f"VIA{nums[0]}{nums[1]}_1C"
        return "VIA"

    def _compose_def_routing(
        self,
        trunk_seg: Dict,
        branches: List[Dict],
        trunk_layer: str,
    ) -> str:
        """Compose DEF route text from segments, inserting vias where needed."""
        lines = []

        # Trunk
        lines.append(
            f"ROUTED {trunk_seg['layer']} ( {trunk_seg['x1']} {trunk_seg['y1']} ) "
            f"( {trunk_seg['x2']} {trunk_seg['y2']} )"
        )

        # Branches: insert a via at the trunk intersection when layers differ.
        for seg in branches:
            need_via = seg["layer"].lower() != trunk_layer.lower()
            if need_via:
                via_name = self._select_via_name(seg["layer"], trunk_layer)
                lines.append(
                    f"NEW {seg['layer']} ( {seg['x1']} {seg['y1']} ) "
                    f"( {seg['x2']} {seg['y2']} ) {via_name}"
                )
            else:
                lines.append(
                    f"NEW {seg['layer']} ( {seg['x1']} {seg['y1']} ) "
                    f"( {seg['x2']} {seg['y2']} )"
                )

        return "\n      ".join(lines)


# Keep a LocalRouter alias for the policy executor
LocalRouter = FishboneRouter
