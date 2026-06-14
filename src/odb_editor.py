"""
src/odb_editor.py

Optional ODB-based DEF editor.  Uses OpenROAD's `odb` Python binding when
available; otherwise operations fall back to DEFEditor.

This module is intentionally a thin wrapper: the policy executor always
attempts ODBEditor first if requested, and falls back to DEFEditor on any
failure.
"""

from typing import Dict, List, Optional, Tuple

ODB_AVAILABLE = False
try:
    import odb  # type: ignore

    ODB_AVAILABLE = True
except ImportError:
    pass


class ODBEditError(Exception):
    """Raised when an ODB edit fails or odb is unavailable."""
    pass


class ODBEditor:
    """Optional ODB-based editor (stub when odb is unavailable)."""

    def __init__(self, def_file: str, lef_file: Optional[str] = None):
        if not ODB_AVAILABLE:
            raise ODBEditError(
                "odb Python binding is not available. "
                "Install OpenROAD with Python bindings or use DEFEditor."
            )
        self.def_file = def_file
        self.lef_file = lef_file

    def rip_up_segment(
        self,
        net_name: str,
        segment_index: int,
        layer: Optional[str] = None,
    ) -> str:
        raise ODBEditError("ODBEditor.rip_up_segment not yet implemented")

    def reassign_layer(
        self,
        net_name: str,
        segment_index: int,
        from_layer: str,
        to_layer: str,
    ) -> str:
        raise ODBEditError("ODBEditor.reassign_layer not yet implemented")

    def insert_jog(
        self,
        net_name: str,
        segment_index: int,
        layer: str,
        jog_point: Tuple[int, int],
        jog_direction: str = "horizontal",
    ) -> str:
        raise ODBEditError("ODBEditor.insert_jog not yet implemented")

    def move_via(
        self,
        net_name: str,
        via_index: int,
        new_position: Tuple[int, int],
        old_position: Optional[Tuple[int, int]] = None,
    ) -> str:
        raise ODBEditError("ODBEditor.move_via not yet implemented")

    def change_via_type(
        self,
        net_name: str,
        via_index: int,
        new_type: str,
        position: Optional[Tuple[int, int]] = None,
    ) -> str:
        raise ODBEditError("ODBEditor.change_via_type not yet implemented")
