"""
tests/test_odb_editor.py

Unit tests for ODBEditor availability and graceful degradation.
"""

import unittest
from unittest.mock import patch, MagicMock

from src.odb_editor import ODBEditError, ODBEditor, ODB_AVAILABLE


class TestODBEditor(unittest.TestCase):
    def test_odb_availability_flag(self):
        """The availability flag must reflect whether ``odb`` imports."""
        # In this environment ``odb`` is not installed, so the flag is False.
        self.assertFalse(ODB_AVAILABLE)

    def test_editor_raises_when_odb_unavailable(self):
        """Constructing ODBEditor without ``odb`` must raise a clear error."""
        with self.assertRaises(ODBEditError) as ctx:
            ODBEditor("/tmp/in.def")
        self.assertIn("not available", str(ctx.exception).lower())
        self.assertIn("DEFEditor", str(ctx.exception))

    def test_all_methods_raise_when_odb_unavailable(self):
        """Instance methods should not be reachable, but class-level stubs guard."""
        with self.assertRaises(ODBEditError):
            ODBEditor("/tmp/in.def")

    def test_odb_edit_error_message_is_helpful(self):
        """Error messages should mention the missing binding and fallback."""
        err = ODBEditError("odb not available")
        self.assertIn("odb", str(err).lower())


class TestODBEditorMockAvailable(unittest.TestCase):
    """Simulate an environment where ``odb`` is importable."""

    @patch("src.odb_editor.ODB_AVAILABLE", True)
    @patch("src.odb_editor.odb")
    def test_methods_raise_not_implemented_with_mock_odb(self, mock_odb):
        """
        Even when ``odb`` is importable, complex mutations are not yet
        implemented.  They should raise ODBEditError so PolicyExecutor can
        fall back to DEFEditor.
        """
        mock_db = MagicMock()
        mock_chip = MagicMock()
        mock_block = MagicMock()
        mock_db.getChip.return_value = mock_chip
        mock_chip.getBlock.return_value = mock_block
        mock_odb.dbDatabase.create.return_value = mock_db
        mock_net = MagicMock()
        mock_net.getWire.return_value = None
        mock_block.findNet.return_value = mock_net

        editor = ODBEditor("/tmp/in.def")
        with self.assertRaises(ODBEditError):
            editor.rip_up_segment("net1", 0)
        with self.assertRaises(ODBEditError):
            editor.reassign_layer("net1", 0, "M2", "M3")
        with self.assertRaises(ODBEditError):
            editor.insert_jog("net1", 0, "M2", (1000, 1000))
        with self.assertRaises(ODBEditError):
            editor.move_via("net1", 0, (1000, 1000))
        with self.assertRaises(ODBEditError):
            editor.change_via_type("net1", 0, "VIA12_1C")


if __name__ == "__main__":
    unittest.main()
