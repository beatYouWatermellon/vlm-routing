"""
src/tcl_generator.py

Tcl script generators for OpenROAD commands used by the policy executor and
other EDA backend operations.  Centralizing Tcl generation makes it easier to
switch or extend EDA providers later.
"""

from typing import List, Optional, Tuple


class TclGenerator:
    """Generate OpenROAD Tcl snippets for common routing operations."""

    @staticmethod
    def generate_reroute_with_constraints(
        lef_file: str,
        def_file: str,
        output_def: str,
        net_name: str,
        preferred_layers: Optional[List[str]] = None,
        avoid_regions: Optional[List[Tuple[int, int, int, int]]] = None,
        output_drc: Optional[str] = None,
    ) -> str:
        """
        Generate a Tcl script that rips up a single net, injects temporary
        blockages, constrains routing layers, reroutes, and removes the
        temporary blockages.
        """
        lines = [
            f"read_lef {lef_file}",
            f"read_def {def_file}",
            "",
            f"# Rip-up target net: {net_name}",
            "set net [dbGetNetByName {}]".format(net_name),
            "if { $net != \"NULL\" } {",
            "  rip_up_net $net",
            "}",
            "",
        ]

        if avoid_regions:
            lines.append("# Temporary blockages")
            for idx, (x1, y1, x2, y2) in enumerate(avoid_regions):
                lines.append(
                    f"set bbox_{idx} [odb::dbBlockage_create $block {x1} {y1} {x2} {y2}]"
                )
            lines.append("")

        if preferred_layers:
            layer_range = "-".join(preferred_layers)
            lines.append(f"set_routing_layers -signal {layer_range}")
            lines.append("")

        drc_arg = f"-output_drc {output_drc}" if output_drc else ""
        lines.extend(
            [
                f"detailed_route {drc_arg}",
                "",
            ]
        )

        if avoid_regions:
            lines.append("# Remove temporary blockages")
            for idx in range(len(avoid_regions)):
                lines.append(f"odb::dbBlockage_destroy $bbox_{idx}")
            lines.append("")

        lines.extend(
            [
                f"write_def {output_def}",
            ]
        )

        return "\n".join(lines)

    @staticmethod
    def generate_local_drc_check(
        lef_file: str,
        def_file: str,
        output_drc: str,
    ) -> str:
        """
        Generate a Tcl script that runs only DRC checking on a DEF file.

        OpenROAD does not support net-filtered DRC in all versions, so this
        script falls back to a full DRC check.
        """
        return f"""read_lef {lef_file}
read_def {def_file}
detailed_route -output_drc {output_drc} -verbose 0
"""

    @staticmethod
    def generate_detailed_route(
        lef_file: str,
        def_file: str,
        output_def: str,
        output_drc: Optional[str] = None,
        output_guide: Optional[str] = None,
    ) -> str:
        """Generate a Tcl script for pure detailed routing."""
        drc_line = f"detailed_route -output_drc {output_drc}" if output_drc else "detailed_route"
        guide_line = ""
        if output_guide:
            guide_line = f"write_guides {output_guide}\n"

        return f"""read_lef {lef_file}
read_def {def_file}
{drc_line}
{guide_line}write_def {output_def}
"""

    @staticmethod
    def generate_baseline_flow(
        lef_file: str,
        def_file: str,
        output_def: str,
        output_dir: str,
        guide_file: Optional[str] = None,
        signal_layers: str = "Metal2-Metal5",
    ) -> str:
        """Generate the full baseline global + detailed routing flow."""
        guide_read = ""
        if guide_file:
            guide_read = f"""
if {{[info exists ::env(GUIDE_FILE)] && [file exists {guide_file}]}} {{
    read_guides {guide_file}
}}
"""

        return f"""read_lef {lef_file}
read_def {def_file}
{guide_read}
set_routing_layers -signal {signal_layers}

global_route \\
    -guide_file {output_dir}/route.guide \\
    -congestion_report_file {output_dir}/congestion.rpt \\
    -congestion_iterations 50 \\
    -verbose

detailed_route \\
    -output_drc {output_dir}/drc.rpt \\
    -output_maze {output_dir}/maze.log \\
    -verbose 1

report_wire_length -net * -detailed_route \\
    -file {output_dir}/wirelength.rpt

write_def {output_def}
write_db {output_dir}/routed.odb
"""
