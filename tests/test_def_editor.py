import unittest
import tempfile
from pathlib import Path

from src.def_editor import DEFEditor, DEFEditError


class TestDEFEditor(unittest.TestCase):
    def setUp(self):
        self.sample_def = """VERSION 5.8 ;
DIVIDERCHAR "/" ;
BUSBITCHARS "[]" ;
DESIGN test ;
UNITS DISTANCE MICRONS 2000 ;
DIEAREA ( 0 0 ) ( 10000 10000 ) ;
NETS 2 ;
- net1 ( comp1 A ) ( comp2 B )
  + ROUTED Metal2 ( 1000 2000 ) ( * 3000 )
  NEW Metal2 ( 1000 3000 ) ( 3000 * )
  NEW Metal3 ( 3000 3000 ) ( * 4000 ) VIA23_1C
  ;
- net2 ( comp3 Y ) ( comp4 A )
  + ROUTED Metal2 ( 5000 5000 ) ( 7000 * ) VIA12_1C_V
  ;
END NETS
END DESIGN
"""
        self.work_dir = Path(tempfile.mkdtemp(prefix="test_def_editor_"))
        self.def_path = self.work_dir / "test.def"
        self.def_path.write_text(self.sample_def)

    def tearDown(self):
        for p in self.work_dir.glob("*"):
            p.unlink()
        self.work_dir.rmdir()

    def test_parse(self):
        editor = DEFEditor(str(self.def_path))
        self.assertEqual(len(editor.nets), 2)
        self.assertIn("net1", editor.nets)

    def test_rip_up_segment(self):
        editor = DEFEditor(str(self.def_path))
        out = editor.rip_up_segment("net1", segment_index=0, layer="Metal2")
        self.assertTrue(Path(out).exists())

        new_content = Path(out).read_text()
        self.assertNotIn("+ ROUTED Metal2 ( 1000 2000 )", new_content)

    def test_reassign_layer(self):
        editor = DEFEditor(str(self.def_path))
        out = editor.reassign_layer("net1", segment_index=0, from_layer="Metal2", to_layer="Metal4")
        self.assertTrue(Path(out).exists())

        new_content = Path(out).read_text()
        self.assertIn("ROUTED Metal4", new_content)

    def test_change_via_type(self):
        editor = DEFEditor(str(self.def_path))
        out = editor.change_via_type("net1", via_index=0, new_type="VIA23_2C")
        self.assertTrue(Path(out).exists())

        new_content = Path(out).read_text()
        self.assertIn("VIA23_2C", new_content)
        self.assertNotIn("VIA23_1C", new_content)

    def test_replace_net_routing(self):
        editor = DEFEditor(str(self.def_path))
        new_route = "ROUTED Metal2 ( 1000 2000 ) ( 2000 * )"
        out = editor.replace_net_routing("net2", new_route)
        self.assertTrue(Path(out).exists())

        new_content = Path(out).read_text()
        self.assertIn(new_route, new_content)
        self.assertNotIn("5000 5000", new_content)


if __name__ == "__main__":
    unittest.main()
