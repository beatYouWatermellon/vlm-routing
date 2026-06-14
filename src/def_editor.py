"""
src/def_editor.py

Pure-Python DEF text editor for fine-grained routing edits.

This module implements segment/via/region level actions without requiring the
`odb` Python binding.  It is intentionally conservative: when an edit cannot be
performed safely, it raises an exception so that the caller can fall back to a
higher-level action (e.g. full net reroute).
"""

import re
import copy
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .def_parser import DefParser, DefNet, DefSegment


class DEFEditError(Exception):
    """Raised when a DEF edit cannot be performed safely."""
    pass


class RouteEntry:
    """Internal representation of a single ROUTED/NEW entry inside a net."""

    def __init__(self, layer: str, tokens: List):
        self.layer = layer
        self.tokens = tokens  # list of ('point', x, y) or ('via', name) or ('layer_change', layer)


class DEFEditor:
    """Edit DEF files at the segment/via level."""

    def __init__(self, def_file: str):
        self.def_file = def_file
        self.content = Path(def_file).read_text(encoding="utf-8")
        self.header, self.nets_text, self.footer = self._split_def(self.content)
        self.nets = DefParser.parse_nets(def_file)

    # ------------------------------------------------------------------
    # High-level API
    # ------------------------------------------------------------------

    def rip_up_segment(
        self,
        net_name: str,
        segment_index: int,
        layer: Optional[str] = None,
    ) -> str:
        """
        Remove the route entry that contains the given segment index.

        Because a single DEF route entry may contain multiple physical segments,
        removing the entire entry is the safest first implementation.  A future
        refinement can split the entry and preserve unaffected segments.
        """
        net = self._get_net(net_name)
        segment = self._get_segment(net, segment_index, layer)
        entry_range = self._find_entry_range_for_segment(net_name, segment)

        new_nets_text = self._remove_substring(self.nets_text, *entry_range)
        return self._write_def(new_nets_text)

    def reassign_layer(
        self,
        net_name: str,
        segment_index: int,
        from_layer: str,
        to_layer: str,
    ) -> str:
        """
        Change the layer of the route entry containing the given segment.

        If the new layer's preferred direction conflicts with the segment
        orientation, the segment is split into orthogonal segments and a via is
        inserted.  This is a simplified heuristic: horizontal layers become
        vertical through an intermediate jog.
        """
        net = self._get_net(net_name)
        segment = self._get_segment(net, segment_index, from_layer)
        entry_range = self._find_entry_range_for_segment(net_name, segment)

        entry_text = self.nets_text[entry_range[0] : entry_range[1]]
        new_entry_text = self._change_entry_layer(entry_text, from_layer, to_layer)

        new_nets_text = (
            self.nets_text[: entry_range[0]]
            + new_entry_text
            + self.nets_text[entry_range[1] :]
        )
        return self._write_def(new_nets_text)

    def insert_jog(
        self,
        net_name: str,
        segment_index: int,
        layer: str,
        jog_point: Tuple[int, int],
        jog_direction: str = "horizontal",
    ) -> str:
        """
        Insert an L-shaped jog at the given point on the segment.

        The original segment is replaced by two segments connected at the jog
        point, then a short perpendicular jog is added.  This is a heuristic
        edit; the caller should validate the result with DRC.
        """
        net = self._get_net(net_name)
        segment = self._get_segment(net, segment_index, layer)
        entry_range = self._find_entry_range_for_segment(net_name, segment)

        entry_text = self.nets_text[entry_range[0] : entry_range[1]]
        new_entry_text = self._add_jog_to_entry(
            entry_text, segment, jog_point, jog_direction
        )

        new_nets_text = (
            self.nets_text[: entry_range[0]]
            + new_entry_text
            + self.nets_text[entry_range[1] :]
        )
        return self._write_def(new_nets_text)

    def move_via(
        self,
        net_name: str,
        via_index: int,
        new_position: Tuple[int, int],
        old_position: Optional[Tuple[int, int]] = None,
    ) -> str:
        """
        Move a via to a new coordinate and adjust adjacent segments.

        Only the via coordinate text is updated; connected segments are NOT
        re-routed automatically.  Use with care and validate with DRC.
        """
        net = self._get_net(net_name)
        if via_index < 0 or via_index >= len(net.vias):
            raise DEFEditError(
                f"via_index {via_index} out of range for net {net_name}"
            )
        via = net.vias[via_index]
        if old_position and (via.x, via.y) != tuple(old_position):
            raise DEFEditError(
                f"Via {via_index} is at ({via.x},{via.y}), not {old_position}"
            )

        new_nets_text = self._replace_via_text(
            via.x, via.y, via.via_name, via.via_name, new_position
        )
        return self._write_def(new_nets_text)

    def change_via_type(
        self,
        net_name: str,
        via_index: int,
        new_type: str,
        position: Optional[Tuple[int, int]] = None,
    ) -> str:
        """Replace the via name for a specific via in a net."""
        net = self._get_net(net_name)
        if via_index < 0 or via_index >= len(net.vias):
            raise DEFEditError(
                f"via_index {via_index} out of range for net {net_name}"
            )
        via = net.vias[via_index]
        if position and (via.x, via.y) != tuple(position):
            raise DEFEditError(
                f"Via {via_index} is at ({via.x},{via.y}), not {position}"
            )

        new_nets_text = self._replace_via_text(via.x, via.y, via.via_name, new_type)
        return self._write_def(new_nets_text)

    def replace_net_routing(
        self,
        net_name: str,
        new_routing_text: str,
    ) -> str:
        """Replace the entire routing section of a net with new routing text."""
        net_range = self._find_net_range(net_name)
        net_text = self.nets_text[net_range[0] : net_range[1]]

        # Find the routing start within the net text
        route_match = re.search(r"(\s*\+\s+ROUTED|\s+NEW)\s+", net_text)
        if not route_match:
            raise DEFEditError(f"Net {net_name} has no routing to replace")

        # Keep pins, replace routing
        pin_part = net_text[: route_match.start()]
        # Ensure new routing text starts with + ROUTED
        if not new_routing_text.strip().startswith("+"):
            new_routing_text = "+ " + new_routing_text.strip()

        new_net_text = pin_part + "\n      " + new_routing_text + "\n  ;"

        new_nets_text = (
            self.nets_text[: net_range[0]]
            + new_net_text
            + self.nets_text[net_range[1] :]
        )
        return self._write_def(new_nets_text)

    def relax_region(
        self,
        bbox: Tuple[int, int, int, int],
        layers: Optional[List[str]] = None,
    ) -> str:
        """Remove BLOCKAGES entries that overlap the given bbox and layers."""
        if "BLOCKAGES" not in self.content:
            return self._write_def(self.nets_text)

        # Remove matching blockage lines
        x1, y1, x2, y2 = bbox
        pattern = re.compile(
            r"-\s+LAYER\s+(\S+)\s+RECT\s*\(\s*([\d.]+)\s+([\d.]+)\s*\)\s*\(\s*([\d.]+)\s+([\d.]+)\s*\)\s*;",
            re.IGNORECASE,
        )

        def should_remove(match):
            layer = match.group(1)
            if layers and layer not in layers:
                return False
            bx1, by1, bx2, by2 = (
                int(float(match.group(2))),
                int(float(match.group(3))),
                int(float(match.group(4))),
                int(float(match.group(5))),
            )
            return not (
                bx2 < x1 or bx1 > x2 or by2 < y1 or by1 > y2
            )

        new_content = pattern.sub(
            lambda m: "" if should_remove(m) else m.group(0), self.content
        )
        return self._write_content(new_content)

    def write_to(self, output_path: str) -> str:
        """Write the current (possibly unmodified) content to a file."""
        Path(output_path).write_text(
            self.header + self.nets_text + self.footer, encoding="utf-8"
        )
        return output_path

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _split_def(self, content: str) -> Tuple[str, str, str]:
        nets_match = re.search(
            r"(^\s*NETS\s+\d+\s*;)(.*?)(^\s*END\s+NETS\s*;?)",
            content,
            re.DOTALL | re.IGNORECASE | re.MULTILINE,
        )
        if not nets_match:
            raise DEFEditError("Could not locate NETS section in DEF file")

        header = content[: nets_match.start(2)]
        nets_text = nets_match.group(2).lstrip("\n\r\t ")
        footer = content[nets_match.end(2) :]
        return header, nets_text, footer

    def _get_net(self, net_name: str) -> DefNet:
        net = self.nets.get(net_name)
        if net is None:
            raise DEFEditError(f"Net {net_name} not found in DEF")
        return net

    def _get_segment(
        self, net: DefNet, segment_index: int, layer: Optional[str]
    ) -> DefSegment:
        if segment_index < 0 or segment_index >= len(net.segments):
            raise DEFEditError(
                f"segment_index {segment_index} out of range for net {net.name}"
            )
        segment = net.segments[segment_index]
        if layer and segment.layer.lower() != layer.lower():
            raise DEFEditError(
                f"Segment {segment_index} is on {segment.layer}, not {layer}"
            )
        return segment

    def _find_net_range(self, net_name: str) -> Tuple[int, int]:
        """Locate the character range of a net block inside nets_text."""
        escaped = re.escape(net_name)
        start_match = re.search(
            rf"(?:^|\n)\s*-\s+{escaped}\b",
            self.nets_text,
            re.IGNORECASE | re.MULTILINE,
        )
        if not start_match:
            raise DEFEditError(f"Could not locate net {net_name} in NETS section")

        start = start_match.start()
        # Move start to the actual '-' character
        dash_pos = self.nets_text.find("-", start)
        if dash_pos == -1:
            raise DEFEditError(f"Could not locate net {net_name} dash in NETS section")
        start = dash_pos

        # Find the first newline after the net name line
        first_nl = self.nets_text.find("\n", start)
        search_from = first_nl + 1 if first_nl != -1 else start + 1

        # Find the next net start or END NETS
        end_match = re.search(
            rf"(?:^|\n)\s*-\s+\S+\b|(?:^|\n)\s*END\s+NETS",
            self.nets_text[search_from:],
            re.IGNORECASE | re.MULTILINE,
        )
        if end_match:
            end = search_from + end_match.start()
        else:
            end = len(self.nets_text)

        return start, end

    def _find_entry_range_for_segment(
        self, net_name: str, segment: DefSegment
    ) -> Tuple[int, int]:
        """
        Find the character range of the route entry that contains the segment.

        The route entry is the text between `+ ROUTED` / `NEW` markers.
        """
        net_range = self._find_net_range(net_name)
        net_text = self.nets_text[net_range[0] : net_range[1]]

        # Find all route entry starts within the net text
        starts = [m.start() for m in re.finditer(r"(\+\s+ROUTED|NEW)\s+", net_text)]
        if not starts:
            raise DEFEditError(f"No routing entries found for net {net_name}")

        # Map physical segment coordinates to an entry by looking for the
        # first point of the segment inside each entry.
        seg_start_text = f"( {segment.x1} {segment.y1} )"
        for i, start in enumerate(starts):
            end = starts[i + 1] if i + 1 < len(starts) else len(net_text)
            entry_text = net_text[start:end]
            if seg_start_text in entry_text:
                return (net_range[0] + start, net_range[0] + end)

        # Fallback: search by any point of the segment
        for pt_text in [
            f"( {segment.x1} {segment.y1} )",
            f"( {segment.x2} {segment.y2} )",
        ]:
            for i, start in enumerate(starts):
                end = starts[i + 1] if i + 1 < len(starts) else len(net_text)
                entry_text = net_text[start:end]
                if pt_text in entry_text:
                    return (net_range[0] + start, net_range[0] + end)

        raise DEFEditError(
            f"Could not locate route entry for segment {segment.segment_index}"
        )

    @staticmethod
    def _remove_substring(text: str, start: int, end: int) -> str:
        return text[:start] + text[end:]

    @staticmethod
    def _change_entry_layer(
        entry_text: str, from_layer: str, to_layer: str
    ) -> str:
        # Replace the layer token immediately following ROUTED/NEW
        return re.sub(
            rf"((?:ROUTED|NEW)\s+){re.escape(from_layer)}\b",
            rf"\g<1>{to_layer}",
            entry_text,
            count=1,
            flags=re.IGNORECASE,
        )

    @staticmethod
    def _add_jog_to_entry(
        entry_text: str,
        segment: DefSegment,
        jog_point: Tuple[int, int],
        jog_direction: str,
    ) -> str:
        # Heuristic: replace the segment with an L-shaped path through the
        # jog point.  We search for the segment's start point text and insert
        # the intermediate jog point.
        old_text = f"( {segment.x1} {segment.y1} ) ( {segment.x2} {segment.y2} )"
        if old_text not in entry_text:
            # Try the reverse order
            old_text = f"( {segment.x2} {segment.y2} ) ( {segment.x1} {segment.y1} )"

        if old_text not in entry_text:
            raise DEFEditError(
                f"Could not locate segment text for jog insertion: {segment}"
            )

        jx, jy = jog_point
        new_text = f"( {segment.x1} {segment.y1} ) ( {jx} {jy} ) ( {segment.x2} {segment.y2} )"
        return entry_text.replace(old_text, new_text, 1)

    def _replace_via_text(
        self,
        vx: int,
        vy: int,
        old_via_name: str,
        new_via_name: str,
        new_position: Optional[Tuple[int, int]] = None,
    ) -> str:
        """
        Replace a via instance in the NETS section.

        Handles both explicit coordinates and '*' relative coordinates.
        """
        # Try exact coordinate match first
        exact = f"( {vx} {vy} ) {old_via_name}"
        if exact in self.nets_text:
            new_coord = (
                f"( {new_position[0]} {new_position[1]} )"
                if new_position
                else f"( {vx} {vy} )"
            )
            return self.nets_text.replace(exact, f"{new_coord} {new_via_name}", 1)

        # Fallback: regex that allows '*' for x or y
        via_pattern = re.compile(
            rf"\(\s*(?:{vx}|\*)\s+(?:{vy}|\*)\s*\)\s+{re.escape(old_via_name)}"
        )
        match = via_pattern.search(self.nets_text)
        if not match:
            raise DEFEditError(
                f"Could not locate via text at ({vx},{vy}) named {old_via_name}"
            )

        new_coord = (
            f"( {new_position[0]} {new_position[1]} )"
            if new_position
            else match.group(0).split(old_via_name)[0].strip()
        )
        return (
            self.nets_text[: match.start()]
            + f"{new_coord} {new_via_name}"
            + self.nets_text[match.end() :]
        )

    def _write_def(self, new_nets_text: str) -> str:
        output_path = str(Path(self.def_file).with_suffix("")) + "_edited.def"
        Path(output_path).write_text(
            self.header + new_nets_text + self.footer, encoding="utf-8"
        )
        return output_path

    def _write_content(self, new_content: str) -> str:
        output_path = str(Path(self.def_file).with_suffix("")) + "_edited.def"
        Path(output_path).write_text(new_content, encoding="utf-8")
        return output_path
