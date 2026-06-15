"""
src/odb_editor.py

Optional ODB-based DEF editor.

Uses OpenROAD's ``odb`` Python binding when available.  When ``odb`` is not
installed, the class raises ``ODBEditError`` and ``PolicyExecutor`` falls back
 to ``DEFEditor``.

To enable ODB editing, OpenROAD must be built with Python bindings and the
``odb`` module must be importable in the active Python environment.
"""

from typing import Dict, List, Optional, Tuple

try:
    import odb  # type: ignore

    ODB_AVAILABLE = True
except ImportError:
    odb = None  # type: ignore
    ODB_AVAILABLE = False


class ODBEditError(Exception):
    """Raised when an ODB edit fails or odb is unavailable."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class ODBEditor:
    """
    ODB-based editor for complex routing edits.

    This editor requires the ``odb`` Python module.  If it is not available,
    all operations raise :class:`ODBEditError`, which signals
    ``PolicyExecutor`` to fall back to ``DEFEditor``.
    """

    def __init__(self, def_file: str, lef_file: Optional[str] = None):
        if not ODB_AVAILABLE:
            raise ODBEditError(
                "odb Python binding is not available. "
                "Install/build OpenROAD with Python bindings to use ODBEditor, "
                "or ensure the 'odb' module is on PYTHONPATH. "
                "Falling back to DEFEditor."
            )
        self.def_file = def_file
        self.lef_file = lef_file
        self._db = None
        self._block = None
        self._load_design()

    def _load_design(self):
        """Load LEF/DEF into an in-memory ODB database."""
        self._db = odb.dbDatabase.create()
        if self.lef_file:
            odb.odb_read_lef(self._db, self.lef_file)
        odb.odb_read_def(self._db, self.def_file)
        chip = self._db.getChip()
        if chip is None:
            raise ODBEditError(f"Could not load DEF into ODB: {self.def_file}")
        self._block = chip.getBlock()

    def _save_design(self, output_path: str) -> str:
        """Write the current ODB database back to a DEF file."""
        odb.odb_write_def(self._block, output_path)
        return output_path

    def _get_net(self, net_name: str):
        """Return the ODB net object or raise an error."""
        net = self._block.findNet(net_name)
        if net is None:
            raise ODBEditError(f"Net {net_name} not found in ODB")
        return net

    def rip_up_segment(
        self,
        net_name: str,
        segment_index: int,
        layer: Optional[str] = None,
    ) -> str:
        """Rip up the route entry containing the given segment index."""
        if not ODB_AVAILABLE:
            raise ODBEditError("odb not available")

        net = self._get_net(net_name)
        wire = net.getWire()
        if wire is None:
            raise ODBEditError(f"Net {net_name} has no routing")

        # ODB wire iteration is version-dependent.  The implementation below
        # is a best-effort skeleton; it should be validated against the exact
        # OpenROAD/odb build in use.
        try:
            itr = wire.iterator()
            idx = 0
            while itr.hasNext():
                _ = itr.next()
                if idx == segment_index:
                    # Real implementation would remove the path segment here.
                    # odb API for direct wire mutation is not stable across
                    # releases, so we raise a clear error rather than risk
                    # corrupt data.
                    raise ODBEditError(
                        "ODBEditor.rip_up_segment requires odb wire mutation "
                        "API validation for your OpenROAD build."
                    )
                idx += 1
        except AttributeError:
            raise ODBEditError(
                "ODB wire iterator API mismatch. "
                "Please validate against your OpenROAD/odb version."
            )

        output_path = self.def_file.replace(".def", "_odb_ripped.def")
        return self._save_design(output_path)

    def reassign_layer(
        self,
        net_name: str,
        segment_index: int,
        from_layer: str,
        to_layer: str,
    ) -> str:
        """Change the layer of a route segment."""
        if not ODB_AVAILABLE:
            raise ODBEditError("odb not available")
        raise ODBEditError(
            "ODBEditor.reassign_layer is not yet implemented. "
            "Use DEFEditor for this operation."
        )

    def insert_jog(
        self,
        net_name: str,
        segment_index: int,
        layer: str,
        jog_point: Tuple[int, int],
        jog_direction: str = "horizontal",
    ) -> str:
        """Insert a jog into a route segment."""
        if not ODB_AVAILABLE:
            raise ODBEditError("odb not available")
        raise ODBEditError(
            "ODBEditor.insert_jog is not yet implemented. "
            "Use DEFEditor for this operation."
        )

    def move_via(
        self,
        net_name: str,
        via_index: int,
        new_position: Tuple[int, int],
        old_position: Optional[Tuple[int, int]] = None,
    ) -> str:
        """Move a via to a new coordinate."""
        if not ODB_AVAILABLE:
            raise ODBEditError("odb not available")
        raise ODBEditError(
            "ODBEditor.move_via is not yet implemented. "
            "Use DEFEditor for this operation."
        )

    def change_via_type(
        self,
        net_name: str,
        via_index: int,
        new_type: str,
        position: Optional[Tuple[int, int]] = None,
    ) -> str:
        """Replace the via name for a specific via in a net."""
        if not ODB_AVAILABLE:
            raise ODBEditError("odb not available")
        raise ODBEditError(
            "ODBEditor.change_via_type is not yet implemented. "
            "Use DEFEditor for this operation."
        )


# Convenience alias used by unit tests and PolicyExecutor.
ODBEditorClass = ODBEditor
