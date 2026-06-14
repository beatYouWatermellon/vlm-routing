"""
src/def_parser.py

DEF/LEF parsing utilities used by the routing agent.

Provides a lightweight, pure-Python parser for extracting net geometry,
pin locations, vias, and layer information from DEF files.  These
structures are used for structured DRC cross-referencing, per-net feature
extraction, and fine-grained DEF editing.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple


@dataclass
class DefSegment:
    """A single wire segment belonging to a net."""

    net_name: str
    segment_index: int
    layer: str
    x1: int
    y1: int
    x2: int
    y2: int

    @property
    def bbox(self) -> Tuple[int, int, int, int]:
        return (
            min(self.x1, self.x2),
            min(self.y1, self.y2),
            max(self.x1, self.x2),
            max(self.y1, self.y2),
        )

    @property
    def length(self) -> int:
        return abs(self.x2 - self.x1) + abs(self.y2 - self.y1)


@dataclass
class DefVia:
    """A via instance belonging to a net."""

    net_name: str
    via_index: int
    layer: str
    x: int
    y: int
    via_name: str


@dataclass
class DefNet:
    """Parsed routing information for a single net."""

    name: str
    pins: List[Tuple[str, str]] = field(default_factory=list)
    pin_coords: List[Tuple[int, int]] = field(default_factory=list)
    route_entries: List[Dict] = field(default_factory=list)
    segments: List[DefSegment] = field(default_factory=list)
    vias: List[DefVia] = field(default_factory=list)
    layer_usage: Dict[str, int] = field(default_factory=dict)

    @property
    def fanout(self) -> int:
        return len(self.pins)

    @property
    def hpwl(self) -> int:
        if self.pin_coords:
            xs = [c[0] for c in self.pin_coords]
            ys = [c[1] for c in self.pin_coords]
            return (max(xs) - min(xs)) + (max(ys) - min(ys))

        # Fallback: approximate from routed segment bounding box
        if not self.segments:
            return 0
        xs = []
        ys = []
        for seg in self.segments:
            xs.extend([seg.x1, seg.x2])
            ys.extend([seg.y1, seg.y2])
        return (max(xs) - min(xs)) + (max(ys) - min(ys))

    @property
    def hpwl_um(self) -> float:
        """HPWL in micrometers (assuming DBU = nanometers)."""
        return self.hpwl / 1000.0

    @property
    def bbox(self) -> Optional[Tuple[int, int, int, int]]:
        if not self.pin_coords:
            return None
        xs = [c[0] for c in self.pin_coords]
        ys = [c[1] for c in self.pin_coords]
        return (min(xs), min(ys), max(xs), max(ys))


class DefParser:
    """Pure-Python DEF parser focused on routing geometry."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @staticmethod
    def parse_nets(def_file: str) -> Dict[str, DefNet]:
        """Parse the NETS section and return a map of net_name -> DefNet."""
        content = Path(def_file).read_text(encoding="utf-8")
        nets_section = DefParser._extract_nets_section(content)
        if nets_section is None:
            return {}

        nets: Dict[str, DefNet] = {}
        net_blocks = DefParser._split_net_blocks(nets_section)

        for block in net_blocks:
            net = DefParser._parse_net_block(block)
            if net:
                nets[net.name] = net

        return nets

    @staticmethod
    def parse_die_area(def_file: str) -> Optional[Tuple[int, int, int, int]]:
        """Return die area as (x1, y1, x2, y2) in DBU."""
        content = Path(def_file).read_text(encoding="utf-8")
        die_pattern = re.compile(
            r"DIEAREA\s*\(\s*([\d.]+)\s+([\d.]+)\s*\)\s*\(\s*([\d.]+)\s+([\d.]+)\s*\)",
            re.IGNORECASE,
        )
        match = die_pattern.search(content)
        if match:
            return (
                int(float(match.group(1))),
                int(float(match.group(2))),
                int(float(match.group(3))),
                int(float(match.group(4))),
            )
        return None

    @staticmethod
    def parse_lef_layers(lef_file: str) -> Dict[str, Dict]:
        """
        Parse LAYER definitions from a LEF file.

        Returns a dict mapping layer name (case-preserved) to properties such as
        direction, pitch, width, offset, etc.  Only ROUTING and CUT layers are
        included.
        """
        content = Path(lef_file).read_text(encoding="utf-8")
        layers: Dict[str, Dict] = {}

        layer_pattern = re.compile(
            r"LAYER\s+(\S+)\s+(.*?)END\s+\1",
            re.DOTALL | re.IGNORECASE,
        )

        for match in layer_pattern.finditer(content):
            name = match.group(1)
            body = match.group(2)

            ltype_match = re.search(r"TYPE\s+(\S+)", body, re.IGNORECASE)
            if not ltype_match:
                continue
            ltype = ltype_match.group(1).upper()
            if ltype not in {"ROUTING", "CUT"}:
                continue

            props: Dict = {"type": ltype}

            direction_match = re.search(
                r"DIRECTION\s+(HORIZONTAL|VERTICAL)", body, re.IGNORECASE
            )
            if direction_match:
                props["direction"] = direction_match.group(1).upper()

            pitch_match = re.search(
                r"PITCH\s+([\d.]+)(?:\s+([\d.]+))?", body, re.IGNORECASE
            )
            if pitch_match:
                props["pitch_x"] = float(pitch_match.group(1))
                props["pitch_y"] = float(
                    pitch_match.group(2) if pitch_match.group(2) else pitch_match.group(1)
                )

            width_match = re.search(r"WIDTH\s+([\d.]+)", body, re.IGNORECASE)
            if width_match:
                props["width"] = float(width_match.group(1))

            offset_match = re.search(
                r"OFFSET\s+([\d.]+)(?:\s+([\d.]+))?", body, re.IGNORECASE
            )
            if offset_match:
                props["offset_x"] = float(offset_match.group(1))
                props["offset_y"] = float(
                    offset_match.group(2) if offset_match.group(2) else offset_match.group(1)
                )

            spacing_match = re.search(r"SPACING\s+([\d.]+)", body, re.IGNORECASE)
            if spacing_match:
                props["spacing"] = float(spacing_match.group(1))

            area_match = re.search(r"AREA\s+([\d.]+)", body, re.IGNORECASE)
            if area_match:
                props["area"] = float(area_match.group(1))

            layers[name] = props

        return layers

    @staticmethod
    def parse_lef_vias(lef_file: str) -> Dict[str, Dict]:
        """
        Parse VIA definitions from a LEF file.

        Returns a dict mapping via name to properties including layer stack and
        geometry (simplified).
        """
        content = Path(lef_file).read_text(encoding="utf-8")
        vias: Dict[str, Dict] = {}

        via_pattern = re.compile(
            r"VIA\s+(\S+)\s+(.*?)END\s+\1",
            re.DOTALL | re.IGNORECASE,
        )

        for match in via_pattern.finditer(content):
            name = match.group(1)
            body = match.group(2)
            layers: List[str] = re.findall(
                r"LAYER\s+(\S+)\s*;", body, re.IGNORECASE
            )
            vias[name] = {"layers": layers}

        return vias

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_nets_section(content: str) -> Optional[str]:
        nets_match = re.search(
            r"^\s*NETS\s+\d+\s*;(.*?)^\s*END\s+NETS",
            content,
            re.DOTALL | re.IGNORECASE | re.MULTILINE,
        )
        if nets_match:
            return nets_match.group(1)
        return None

    @staticmethod
    def _split_net_blocks(nets_section: str) -> List[str]:
        """
        Split the NETS section into individual net blocks.

        Each block starts with '- net_name' and ends with the matching ';'.
        """
        blocks: List[str] = []
        current: List[str] = []
        depth = 0

        for line in nets_section.splitlines(keepends=True):
            stripped = line.strip()

            # Start of a new net block
            if stripped.startswith("-"):
                if current:
                    blocks.append("".join(current))
                    current = []
                depth = 1
                current.append(line)
                continue

            if not current:
                continue

            current.append(line)

            # A bare ';' terminates the current net
            if stripped == ";":
                blocks.append("".join(current))
                current = []
                depth = 0

        if current:
            blocks.append("".join(current))

        return blocks

    @staticmethod
    def _parse_net_block(block: str) -> Optional[DefNet]:
        lines = block.splitlines()
        if not lines:
            return None

        # First line: '- net_name ( comp pin ) ( comp pin ) ...'
        first_line = lines[0].strip()
        net_match = re.match(r"-\s+(\S+)(?:\s+(.*))?", first_line)
        if not net_match:
            return None

        net_name = net_match.group(1)
        net = DefNet(name=net_name)

        # Split the block into header (pins) and body (routing).
        # Routing starts at the first '+ ROUTED' or 'NEW ' line.
        header_lines: List[str] = [net_match.group(2) or ""]
        route_start_idx = len(lines)

        for idx, line in enumerate(lines[1:], start=1):
            stripped = line.strip()
            if "+ ROUTED" in stripped or stripped.upper().startswith("NEW "):
                route_start_idx = idx
                break
            header_lines.append(line)

        header_text = "\n".join(header_lines)

        # Parse pins and their coordinates from the header
        pin_matches = re.findall(r"\(\s*(\S+)\s+(\S+)\s*\)", header_text)
        for comp, pin in pin_matches:
            net.pins.append((comp, pin))

        pin_coords = re.findall(r"\(\s*([\d.]+)\s+([\d.]+)\s*\)", header_text)
        for x, y in pin_coords:
            net.pin_coords.append((int(float(x)), int(float(y))))

        # Remaining lines describe routing
        route_text = "\n".join(lines[route_start_idx:])
        route_entries = DefParser._split_route_entries(route_text)

        seg_index = 0
        via_index = 0
        for entry in route_entries:
            parsed = DefParser._parse_route_entry(entry, net_name)
            if not parsed:
                continue

            net.route_entries.append(parsed)
            layer = parsed["layer"]

            # Count layer usage by segment length
            for seg in parsed.get("segments", []):
                seg_obj = DefSegment(
                    net_name=net_name,
                    segment_index=seg_index,
                    layer=seg["layer"],
                    x1=seg["x1"],
                    y1=seg["y1"],
                    x2=seg["x2"],
                    y2=seg["y2"],
                )
                net.segments.append(seg_obj)
                net.layer_usage[seg["layer"]] = (
                    net.layer_usage.get(seg["layer"], 0) + seg_obj.length
                )
                seg_index += 1

            for via in parsed.get("vias", []):
                via_obj = DefVia(
                    net_name=net_name,
                    via_index=via_index,
                    layer=via["layer"],
                    x=via["x"],
                    y=via["y"],
                    via_name=via["via_name"],
                )
                net.vias.append(via_obj)
                via_index += 1

        return net

    @staticmethod
    def _split_route_entries(route_text: str) -> List[str]:
        """
        Split route text into entries starting with '+ ROUTED' or 'NEW'.
        """
        entries: List[str] = []
        current: List[str] = []

        for line in route_text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue

            if stripped.startswith("+") or stripped.upper().startswith("NEW "):
                if current:
                    entries.append("\n".join(current))
                    current = []
                # Remove leading '+' if present
                if stripped.startswith("+"):
                    stripped = stripped[1:].strip()
                current.append(stripped)
            else:
                current.append(stripped)

        if current:
            entries.append("\n".join(current))

        return entries

    @staticmethod
    def _parse_route_entry(entry_text: str, net_name: str) -> Optional[Dict]:
        """
        Parse a single route entry (e.g. 'ROUTED Metal2 ( x y ) ( * y2 ) ...')
        into layer, segments, and vias.
        """
        tokens = entry_text.split()
        if not tokens:
            return None

        # First token should be ROUTED or NEW followed by the layer name
        idx = 0
        if tokens[0].upper() in {"ROUTED", "NEW"}:
            idx = 1

        if idx >= len(tokens):
            return None

        layer = tokens[idx]
        idx += 1

        result: Dict = {"layer": layer, "segments": [], "vias": [], "items": []}

        cur_x: Optional[int] = None
        cur_y: Optional[int] = None

        while idx < len(tokens):
            tok = tokens[idx]

            # Start of a point
            if tok == "(":
                if idx + 3 >= len(tokens):
                    break
                x_tok = tokens[idx + 1]
                y_tok = tokens[idx + 2]
                close_tok = tokens[idx + 3]
                if close_tok != ")":
                    idx += 1
                    continue

                new_x = cur_x if x_tok == "*" else int(float(x_tok))
                new_y = cur_y if y_tok == "*" else int(float(y_tok))

                if cur_x is not None and cur_y is not None:
                    if new_x != cur_x or new_y != cur_y:
                        result["segments"].append(
                            {
                                "layer": layer,
                                "x1": cur_x,
                                "y1": cur_y,
                                "x2": new_x,
                                "y2": new_y,
                            }
                        )

                cur_x, cur_y = new_x, new_y
                result["items"].append(("point", cur_x, cur_y))
                idx += 4

            # Via name follows a point
            elif tok.upper().startswith("VIA") or tok.upper().startswith("M"):
                # Layer change token (e.g. 'Metal3') appearing mid-entry means
                # a via or a layer transition.  We treat any token that looks
                # like a layer name as a layer change for subsequent segments.
                if cur_x is None or cur_y is None:
                    idx += 1
                    continue

                # If the token matches a known via naming pattern, record it
                if re.match(r"^(VIA|Via)[A-Za-z0-9_]+|^[A-Za-z]+\d+_[A-Za-z0-9_]+VIA", tok):
                    result["vias"].append(
                        {
                            "layer": layer,
                            "x": cur_x,
                            "y": cur_y,
                            "via_name": tok,
                        }
                    )
                    result["items"].append(("via", tok, cur_x, cur_y))
                    idx += 1
                else:
                    # Treat as layer change for subsequent geometry
                    layer = tok
                    result["items"].append(("layer_change", layer))
                    idx += 1
            else:
                idx += 1

        return result


# Backwards-compatible helper used by routing_toolkit
parse_def_nets = DefParser.parse_nets
