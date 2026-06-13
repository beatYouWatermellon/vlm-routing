"""
src/routing_toolkit.py

OpenROAD tool wrapper.
Provides a unified interface for state extraction, routing execution, DRC checks,
and metric evaluation. The core design bridges to OpenROAD via dynamically
generated Tcl scripts invoked through ``openroad -exit script.tcl``.
"""

import os
import re
import json
import subprocess
import tempfile
import shutil
from pathlib import Path
from typing import List, Dict, Tuple, Optional, Union
from dataclasses import dataclass, field, asdict
import numpy as np


@dataclass
class RoutingMetrics:
    """Container for routing quality metrics."""

    drc_total: int = 0
    drc_spacing: int = 0
    drc_min_width: int = 0
    drc_short: int = 0
    drc_end_of_line: int = 0
    drc_via_spacing: int = 0
    drc_other: int = 0
    wirelength: float = 0.0
    via_count: int = 0
    wirelength_per_layer: Dict[str, float] = field(default_factory=dict)
    iteration: int = 0

    def to_dict(self) -> Dict:
        return asdict(self)

    @property
    def drc_breakdown(self) -> Dict[str, int]:
        return {
            "spacing": self.drc_spacing,
            "min_width": self.drc_min_width,
            "short": self.drc_short,
            "end_of_line": self.drc_end_of_line,
            "via_spacing": self.drc_via_spacing,
            "other": self.drc_other,
        }


@dataclass
class RoutingState:
    """Current routing state used as input to the VLM."""

    def_file: str = ""
    congestion_map: Optional[np.ndarray] = None
    routing_layers: Dict[str, np.ndarray] = field(default_factory=dict)
    metrics: Optional[RoutingMetrics] = None
    netlist_stats: Optional[Dict] = None
    drc_report: Optional[Dict] = None
    drc_markers: List[Tuple[float, float, str]] = field(default_factory=list)
    iteration: int = 0


class RoutingToolkit:
    """
    OpenROAD routing tool wrapper.

    Interacts with OpenROAD by generating Tcl scripts and running them in
    separate ``openroad -exit`` subprocess calls. Each call is state-isolated,
    with inputs/outputs communicated through environment variables and files.
    """

    def __init__(self, openroad_exe: str, work_dir: str, lef_file: str):
        self.openroad = openroad_exe
        self.work_dir = Path(work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.lef_file = Path(lef_file)
        self.current_def: Optional[str] = None
        self.iteration = 0
        self._env = os.environ.copy()
        self._env["OPENROAD_EXE"] = openroad_exe
        self._tcl_counter = 0

        self._validate_openroad()

    def _validate_openroad(self):
        try:
            result = subprocess.run(
                [self.openroad, "-version"],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode != 0:
                raise RuntimeError(f"OpenROAD validation failed: {result.stderr}")
            print(f"[OK] OpenROAD: {result.stdout.strip()}")
        except FileNotFoundError:
            raise RuntimeError(f"OpenROAD not found: {self.openroad}")

    def _next_tcl_name(self) -> str:
        self._tcl_counter += 1
        return f"_temp_{self.iteration}_{self._tcl_counter}.tcl"

    def _run_tcl_script(
        self,
        tcl_content: str,
        env_vars: Optional[Dict] = None,
        timeout: int = 600,
        keep_tcl: bool = False,
    ) -> Tuple[int, str, str]:
        """Execute a Tcl script in OpenROAD and return (returncode, stdout, stderr)."""
        tcl_path = self.work_dir / self._next_tcl_name()
        tcl_path.write_text(tcl_content, encoding="utf-8")

        env = self._env.copy()
        if env_vars:
            env.update(env_vars)

        try:
            result = subprocess.run(
                [self.openroad, "-exit", "-no_init", str(tcl_path)],
                capture_output=True, text=True, env=env, timeout=timeout
            )
            return result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            return -1, "", "Timeout"
        finally:
            if not keep_tcl and tcl_path.exists():
                tcl_path.unlink()

    # ------------------------------------------------------------------
    # State extraction
    # ------------------------------------------------------------------

    def extract_congestion_map(
        self, def_file: str, resolution: int = 256
    ) -> np.ndarray:
        """
        Extract a global congestion heatmap by running FastRoute with a
        congestion report and parsing the report into a fixed-size grid.
        """
        congestion_rpt = self.work_dir / f"congestion_{self.iteration}.rpt"

        tcl_script = f"""
read_lef {self.lef_file}
read_def {def_file}
global_route -congestion_report_file {congestion_rpt} -congestion_iterations 1 -allow_congestion
"""
        retcode, stdout, stderr = self._run_tcl_script(tcl_script, timeout=300)

        if retcode != 0:
            print(f"[WARNING] Congestion extraction failed: {stderr[:200]}")
            return np.zeros((resolution, resolution), dtype=np.float32)

        return self._parse_congestion_report(congestion_rpt, resolution)

    def _parse_congestion_report(
        self, rpt_file: Path, resolution: int
    ) -> np.ndarray:
        grid = np.zeros((resolution, resolution), dtype=np.float32)

        if not rpt_file.exists():
            return grid

        all_coords = []
        overflow_data = []

        with open(rpt_file, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                if len(parts) >= 6:
                    try:
                        x, y = int(parts[0]), int(parts[1])
                        overflow = float(parts[5])
                        all_coords.append((x, y))
                        overflow_data.append((x, y, overflow))
                    except (ValueError, IndexError):
                        continue

        if not all_coords:
            return grid

        max_gx = max(c[0] for c in all_coords) + 1
        max_gy = max(c[1] for c in all_coords) + 1

        scale_x = resolution / max(max_gx, 1)
        scale_y = resolution / max(max_gy, 1)

        for gx, gy, overflow in overflow_data:
            px = min(int(gx * scale_x), resolution - 1)
            py = min(int(gy * scale_y), resolution - 1)
            grid[py, px] = max(grid[py, px], overflow)

        return grid

    def extract_routing_layers(
        self,
        def_file: str,
        layers: Optional[List[str]] = None,
        resolution: int = 512,
    ) -> Dict[str, np.ndarray]:
        """
        Render requested routing layers as binary images by parsing the DEF
        NETS section and drawing ROUTED/NEW segments.
        """
        if layers is None:
            layers = ["Metal2", "Metal3", "Metal4", "Metal5"]

        die_area = self._parse_die_area(def_file)
        if die_area is None:
            return {layer: np.zeros((resolution, resolution), dtype=np.uint8) for layer in layers}

        die_x1, die_y1, die_x2, die_y2 = die_area
        die_width = max(die_x2 - die_x1, 1)
        die_height = max(die_y2 - die_y1, 1)
        scale_x = resolution / die_width
        scale_y = resolution / die_height

        result = {}
        for layer in layers:
            routing_map = np.zeros((resolution, resolution), dtype=np.uint8)
            segments = self._parse_def_routing_for_layer(def_file, layer)

            for (x1, y1, x2, y2) in segments:
                ix1 = max(0, min(int((x1 - die_x1) * scale_x), resolution - 1))
                iy1 = max(0, min(int((y1 - die_y1) * scale_y), resolution - 1))
                ix2 = max(0, min(int((x2 - die_x1) * scale_x), resolution - 1))
                iy2 = max(0, min(int((y2 - die_y1) * scale_y), resolution - 1))
                self._draw_line(routing_map, ix1, iy1, ix2, iy2)

            result[layer] = routing_map

        return result

    def _parse_die_area(self, def_file: str) -> Optional[Tuple[int, int, int, int]]:
        die_pattern = re.compile(
            r"DIEAREA\s*\(\s*([\d.]+)\s+([\d.]+)\s*\)\s*\(\s*([\d.]+)\s+([\d.]+)\s*\)",
            re.IGNORECASE,
        )
        with open(def_file, "r") as f:
            content = f.read()
        match = die_pattern.search(content)
        if match:
            return (
                int(float(match.group(1))),
                int(float(match.group(2))),
                int(float(match.group(3))),
                int(float(match.group(4))),
            )
        return None

    def _parse_def_routing_for_layer(
        self, def_file: str, target_layer: str
    ) -> List[Tuple[int, int, int, int]]:
        segments = []

        with open(def_file, "r") as f:
            content = f.read()

        nets_match = re.search(
            r"NETS\s+\d+\s*;(.*?)END\s+NETS", content, re.DOTALL | re.IGNORECASE
        )
        if not nets_match:
            return segments

        nets_text = nets_match.group(1)
        layer_pattern = re.compile(
            rf"(?:ROUTED|NEW)\s+{re.escape(target_layer)}\s+(.*?)(?=(?:NEW|\-|;|END))",
            re.DOTALL | re.IGNORECASE,
        )

        for match in layer_pattern.finditer(nets_text):
            segment_text = match.group(1)
            coords = re.findall(r"\(\s*([\d.]+)\s+([\d.]+)\s*\)", segment_text)
            points = [(int(float(x)), int(float(y))) for x, y in coords]

            for i in range(len(points) - 1):
                segments.append(
                    (points[i][0], points[i][1], points[i + 1][0], points[i + 1][1])
                )

        return segments

    @staticmethod
    def _draw_line(img: np.ndarray, x1: int, y1: int, x2: int, y2: int):
        """Bresenham line drawing on a 2D array."""
        dx = abs(x2 - x1)
        dy = abs(y2 - y1)
        sx = 1 if x1 < x2 else -1
        sy = 1 if y1 < y2 else -1
        err = dx - dy

        h, w = img.shape
        x, y = x1, y1

        while True:
            if 0 <= x < w and 0 <= y < h:
                img[y, x] = 1
            if x == x2 and y == y2:
                break
            e2 = 2 * err
            if e2 > -dy:
                err -= dy
                x += sx
            if e2 < dx:
                err += dx
                y += sy

    def extract_netlist_stats(self, def_file: str) -> Dict:
        """Parse the DEF file and return structured netlist statistics."""
        stats = {
            "total_nets": 0,
            "total_pins": 0,
            "high_fanout_nets": [],
            "long_distance_nets": [],
            "nets_in_congestion": [],
            "macro_count": 0,
            "std_cell_count": 0,
            "component_count": 0,
            "io_pin_count": 0,
        }

        with open(def_file, "r") as f:
            content = f.read()

        pins_match = re.search(r"PINS\s+(\d+)", content, re.IGNORECASE)
        if pins_match:
            stats["io_pin_count"] = int(pins_match.group(1))

        comps_match = re.search(
            r"COMPONENTS\s+(\d+)\s*;(.*?)END\s+COMPONENTS",
            content,
            re.DOTALL | re.IGNORECASE,
        )
        if comps_match:
            stats["component_count"] = int(comps_match.group(1))
            stats["std_cell_count"] = stats["component_count"]

        nets_match = re.search(
            r"^\s*NETS\s+(\d+)\s*;(.*?)^\s*END\s+NETS",
            content,
            re.DOTALL | re.IGNORECASE | re.MULTILINE,
        )
        if not nets_match:
            return stats

        stats["total_nets"] = int(nets_match.group(1))
        nets_text = nets_match.group(2)

        net_pattern = re.compile(
            r"-\s+(\S+)\s+\(\s*([^)]*)\s*\)(.*?)(?=\-\s+\S+\s+\(|END)",
            re.DOTALL,
        )

        for net_match in net_pattern.finditer(nets_text):
            net_name = net_match.group(1)
            pins_text = net_match.group(2)

            pin_coords = re.findall(r"\(\s*([\d.]+)\s+([\d.]+)\s*\)", pins_text)
            coords = [
                (float(x), float(y))
                for x, y in pin_coords
                if x.replace(".", "").isdigit()
            ]

            fanout = len(coords)
            stats["total_pins"] += fanout

            if coords:
                xs = [c[0] for c in coords]
                ys = [c[1] for c in coords]
                hpwl = (max(xs) - min(xs)) + (max(ys) - min(ys))
                hpwl_um = hpwl / 1000.0

                if fanout > 50:
                    stats["high_fanout_nets"].append(
                        {"name": net_name, "fanout": fanout, "hpwl": round(hpwl_um, 2)}
                    )

                if hpwl_um > 500:
                    stats["long_distance_nets"].append(
                        {"name": net_name, "hpwl": round(hpwl_um, 2), "fanout": fanout}
                    )

        stats["high_fanout_nets"] = sorted(
            stats["high_fanout_nets"], key=lambda x: x["fanout"], reverse=True
        )[:10]
        stats["long_distance_nets"] = sorted(
            stats["long_distance_nets"], key=lambda x: x["hpwl"], reverse=True
        )[:10]

        return stats

    def extract_drc_report(self, def_file: str) -> Dict:
        """Run a DRC check and parse the resulting report."""
        drc_rpt = self.work_dir / f"drc_{self.iteration}.rpt"

        tcl_script = f"""
read_lef {self.lef_file}
read_def {def_file}
detailed_route -output_drc {drc_rpt} -verbose 0
"""
        retcode, stdout, stderr = self._run_tcl_script(tcl_script, timeout=1200)

        drc_data = {
            "total_violations": 0,
            "spacing": 0,
            "min_width": 0,
            "short": 0,
            "end_of_line": 0,
            "via_spacing": 0,
            "corner_spacing": 0,
            "adjacent_cut_spacing": 0,
            "min_area": 0,
            "other": 0,
            "violations": [],
        }

        if not drc_rpt.exists():
            return drc_data

        violation_pattern = re.compile(
            r"(\w+)\s+([\d.]+)\s+([\d.]+)\s+(\S+)\s*(.*)"
        )

        with open(drc_rpt, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue

                match = violation_pattern.match(line)
                if match:
                    vtype = match.group(1)
                    x = float(match.group(2))
                    y = float(match.group(3))
                    layer = match.group(4)
                    desc = match.group(5)

                    drc_data["violations"].append(
                        {"type": vtype, "x": x, "y": y, "layer": layer, "description": desc}
                    )

                    vtype_lower = vtype.lower()
                    if "spacing" in vtype_lower and "via" not in vtype_lower:
                        drc_data["spacing"] += 1
                    elif "minwidth" in vtype_lower or "min_width" in vtype_lower:
                        drc_data["min_width"] += 1
                    elif "short" in vtype_lower:
                        drc_data["short"] += 1
                    elif "endofline" in vtype_lower or "eol" in vtype_lower:
                        drc_data["end_of_line"] += 1
                    elif "via_spacing" in vtype_lower:
                        drc_data["via_spacing"] += 1
                    elif "corner" in vtype_lower:
                        drc_data["corner_spacing"] += 1
                    elif "adjacent_cut" in vtype_lower:
                        drc_data["adjacent_cut_spacing"] += 1
                    elif "minarea" in vtype_lower or "min_area" in vtype_lower:
                        drc_data["min_area"] += 1
                    else:
                        drc_data["other"] += 1
                else:
                    vtype_lower = line.lower()
                    if "spacing" in vtype_lower:
                        drc_data["spacing"] += 1
                        drc_data["violations"].append({"type": "spacing", "raw": line})
                    elif "short" in vtype_lower:
                        drc_data["short"] += 1
                        drc_data["violations"].append({"type": "short", "raw": line})

        drc_data["total_violations"] = len(drc_data["violations"])
        return drc_data

    def extract_metrics(self, def_file: str) -> RoutingMetrics:
        """Combine DRC, wirelength, and via metrics into a RoutingMetrics object."""
        drc_data = self.extract_drc_report(def_file)
        wirelength_rpt = self.work_dir / f"wirelength_{self.iteration}.rpt"

        tcl_script = f"""
read_lef {self.lef_file}
read_def {def_file}
report_wire_length -net * -detailed_route -file {wirelength_rpt}
"""
        retcode, stdout, stderr = self._run_tcl_script(tcl_script, timeout=300)

        wirelength = 0.0
        via_count = 0
        wirelength_per_layer: Dict[str, float] = {}

        # Parse net-level wirelength report produced by OpenROAD v2.0:
        #   "drt: <net_name> <total_wl> <#pins>"
        if wirelength_rpt.exists():
            with open(wirelength_rpt, "r") as f:
                content = f.read()

            net_wl_pattern = re.compile(
                r"^\s*drt:\s+\S+\s+([\d.]+)\s+\d+", re.MULTILINE
            )
            wirelength = sum(
                float(m.group(1)) for m in net_wl_pattern.finditer(content)
            )

        # Count vias directly from the routed DEF. OpenROAD inserts via names
        # after a routing coordinate, e.g.:
        #   "NEW Metal2 ( x y ) VIA23_1C ..."
        via_count = self._count_vias_in_def(def_file)

        # Fallback: some OpenROAD builds emit a summary line to stdout/stderr.
        if wirelength == 0.0 and retcode == 0:
            combined = stdout + stderr
            total_wl_match = re.search(
                r"Total wire length =\s*([\d.]+)\s*um", combined, re.IGNORECASE
            )
            if total_wl_match:
                wirelength = float(total_wl_match.group(1))

        return RoutingMetrics(
            drc_total=drc_data["total_violations"],
            drc_spacing=drc_data["spacing"],
            drc_min_width=drc_data["min_width"],
            drc_short=drc_data["short"],
            drc_end_of_line=drc_data["end_of_line"],
            drc_via_spacing=drc_data["via_spacing"],
            drc_other=drc_data["other"],
            wirelength=wirelength,
            via_count=via_count,
            wirelength_per_layer=wirelength_per_layer,
            iteration=self.iteration,
        )

    @staticmethod
    def _count_vias_in_def(def_file: str) -> int:
        """Count via instances in the NETS section of a routed DEF file."""
        try:
            with open(def_file, "r") as f:
                content = f.read()
        except Exception:
            return 0

        # Extract the NETS section so we don't count via definitions in the
        # VIAS block.
        nets_match = re.search(
            r"NETS\s+\d+\s*;(.*?)END\s+NETS", content, re.DOTALL | re.IGNORECASE
        )
        if not nets_match:
            return 0

        nets_text = nets_match.group(1)
        vias = re.findall(
            r"NEW\s+\S+\s+\(\s*[\d.]+\s+[\d.]+\s*\)\s+(VIA[A-Za-z0-9_]+|Via[A-Za-z0-9_]+)",
            nets_text,
        )
        return len(vias)

    # ------------------------------------------------------------------
    # Routing actions
    # ------------------------------------------------------------------

    def run_baseline_flow(
        self, def_file: str, guide_file: Optional[str] = None
    ) -> str:
        """Run the baseline FastRoute -> TritonRoute flow."""
        output_def = self.work_dir / f"baseline_{Path(def_file).stem}.def"
        output_dir = self.work_dir / "baseline_output"
        output_dir.mkdir(exist_ok=True)

        env_vars = {
            "LEF_FILE": str(self.lef_file),
            "DEF_FILE": str(def_file),
            "OUTPUT_DEF": str(output_def),
            "OUTPUT_DIR": str(output_dir),
        }
        if guide_file:
            env_vars["GUIDE_FILE"] = str(guide_file)

        tcl_script = """
read_lef $::env(LEF_FILE)
read_def $::env(DEF_FILE)

if {[info exists ::env(GUIDE_FILE)] && [file exists $::env(GUIDE_FILE)]} {
    read_guides $::env(GUIDE_FILE)
}

set_routing_layers -signal Metal2-Metal5

global_route \
    -guide_file $::env(OUTPUT_DIR)/route.guide \
    -congestion_report_file $::env(OUTPUT_DIR)/congestion.rpt \
    -congestion_iterations 50 \
    -verbose

detailed_route \
    -output_drc $::env(OUTPUT_DIR)/drc.rpt \
    -output_maze $::env(OUTPUT_DIR)/maze.log \
    -verbose 1

report_wire_length -net * -detailed_route \
    -file $::env(OUTPUT_DIR)/wirelength.rpt

write_def $::env(OUTPUT_DEF)
write_db $::env(OUTPUT_DIR)/routed.odb
"""
        retcode, stdout, stderr = self._run_tcl_script(
            tcl_script, env_vars, timeout=3600
        )

        if retcode != 0:
            print(f"[ERROR] Baseline routing failed: {stderr[:500]}")
            return def_file

        print(f"[OK] Baseline routing complete: {output_def}")
        self.current_def = str(output_def)
        return str(output_def)

    def rip_up_nets(self, def_file: str, net_list: List[str]) -> str:
        """
        Remove routed segments for the specified nets while preserving net
        declarations and pin connections.
        """
        output_def = self.work_dir / f"ripped_iter{self.iteration}.def"
        target_nets = set(net_list)

        with open(def_file, "r") as f:
            lines = f.readlines()

        new_lines = []
        in_target_net = False
        skip_route_section = False

        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()

            if stripped.startswith("-"):
                net_match = re.match(r"-\s+(\S+)", stripped)
                if net_match:
                    net_name = net_match.group(1)
                    in_target_net = net_name in target_nets
                    skip_route_section = False
                    new_lines.append(line)
                    i += 1
                    continue

            if in_target_net and (
                stripped.startswith("+ ROUTED")
                or stripped.startswith("+ FIXED")
                or stripped.startswith("NEW")
            ):
                skip_route_section = True
                i += 1
                continue

            if skip_route_section:
                if stripped.startswith("-") or stripped == ";":
                    skip_route_section = False
                    if stripped == ";":
                        new_lines.append(line)
                    continue
                i += 1
                continue

            new_lines.append(line)
            i += 1

        with open(output_def, "w") as f:
            f.writelines(new_lines)

        print(f"[OK] Rip-up complete: {len(net_list)} nets -> {output_def}")
        return str(output_def)

    def run_incremental_route(
        self,
        def_file: str,
        output_name: Optional[str] = None,
        timeout: int = 1800,
    ) -> str:
        """Run detailed routing on a DEF containing unrouted nets."""
        if output_name is None:
            output_name = f"routed_iter{self.iteration}.def"

        output_def = self.work_dir / output_name
        output_dir = self.work_dir / f"route_output_{self.iteration}"
        output_dir.mkdir(exist_ok=True)

        env_vars = {
            "LEF_FILE": str(self.lef_file),
            "DEF_FILE": str(def_file),
            "OUTPUT_DEF": str(output_def),
            "OUTPUT_DIR": str(output_dir),
        }

        tcl_script = """
read_lef $::env(LEF_FILE)
read_def $::env(DEF_FILE)

detailed_route \
    -output_drc $::env(OUTPUT_DIR)/drc.rpt \
    -verbose 1

report_wire_length -net * -detailed_route \
    -file $::env(OUTPUT_DIR)/wirelength.rpt

write_def $::env(OUTPUT_DEF)
"""
        retcode, stdout, stderr = self._run_tcl_script(
            tcl_script, env_vars, timeout=timeout
        )

        if retcode != 0:
            print(f"[WARNING] Incremental routing failed: {stderr[:300]}")
            return def_file

        self.iteration += 1
        print(f"[OK] Incremental routing complete: {output_def}")
        return str(output_def)

    def rip_up_and_reroute(
        self, def_file: str, net_list: List[str], strategy: Dict
    ) -> Tuple[str, RoutingMetrics]:
        """Rip up the given nets and run incremental detailed routing."""
        print(f"[ACTION] Rip-up & Reroute: {len(net_list)} nets")

        ripped_def = self.rip_up_nets(def_file, net_list)
        new_def = self.run_incremental_route(ripped_def)
        metrics = self.extract_metrics(new_def)

        return new_def, metrics

    def set_routing_blockage(
        self,
        def_file: str,
        bbox: Tuple[int, int, int, int],
        layers: List[str],
        output_name: Optional[str] = None,
    ) -> str:
        """Insert a BLOCKAGES section into the DEF before END DESIGN."""
        if output_name is None:
            output_name = f"blocked_iter{self.iteration}.def"

        output_def = self.work_dir / output_name

        with open(def_file, "r") as f:
            content = f.read()

        x1, y1, x2, y2 = bbox
        blockage_entries = []
        for layer in layers:
            blockage_entries.append(
                f"    - LAYER {layer}"
                f"      RECT ( {x1} {y1} ) ( {x2} {y2} ) ;"
            )

        blockages_section = (
            f"BLOCKAGES {len(blockage_entries)} ;\n"
            + "\n".join(blockage_entries)
            + "\nEND BLOCKAGES\n"
        )

        content = content.replace("END DESIGN", blockages_section + "END DESIGN")

        with open(output_def, "w") as f:
            f.write(content)

        return str(output_def)


if __name__ == "__main__":
    toolkit = RoutingToolkit(
        openroad_exe="openroad",
        work_dir="/tmp/routing_test",
        lef_file="data/ispd2018/ispd18_test1/ispd18_test1.lef",
    )
    print("RoutingToolkit initialized successfully")
