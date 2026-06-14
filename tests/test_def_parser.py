import unittest
import tempfile
from pathlib import Path

from src.def_parser import DefParser, DefNet, DefSegment, DefVia


class TestDefParser(unittest.TestCase):
    def setUp(self):
        self.sample_def = """VERSION 5.8 ;
DIVIDERCHAR "/" ;
BUSBITCHARS "[]" ;
DESIGN test ;
UNITS DISTANCE MICRONS 2000 ;
DIEAREA ( 0 0 ) ( 10000 10000 ) ;
COMPONENTS 2 ;
- comp1 cell1 + PLACED ( 1000 2000 ) N ;
- comp2 cell1 + PLACED ( 3000 4000 ) N ;
END COMPONENTS
PINS 2 ;
- pin1 + NET net1 + DIRECTION INPUT + LAYER Metal1 ( 0 0 ) ( 100 100 )
  + PLACED ( 5000 5000 ) N ;
- pin2 + NET net2 + DIRECTION OUTPUT + LAYER Metal1 ( 0 0 ) ( 100 100 )
  + PLACED ( 7000 7000 ) N ;
END PINS
NETS 3 ;
- net1 ( comp1 A ) ( comp2 B )
  + ROUTED Metal2 ( 1000 2000 ) ( * 3000 )
  NEW Metal2 ( 1000 3000 ) ( 3000 * )
  NEW Metal3 ( 3000 3000 ) ( * 4000 ) VIA23_1C
  ;
- net2 ( pin1 ) ( pin2 )
  + ROUTED Metal2 ( 5000 5000 ) ( 7000 * )
  ;
- net3
  + ROUTED Metal1 ( 2000 2000 ) ( 4000 * )
  ;
END NETS
END DESIGN
"""
        self.work_dir = Path(tempfile.mkdtemp(prefix="test_def_parser_"))
        self.def_path = self.work_dir / "test.def"
        self.def_path.write_text(self.sample_def)

    def tearDown(self):
        if self.def_path.exists():
            self.def_path.unlink()
        self.work_dir.rmdir()

    def test_parse_die_area(self):
        die_area = DefParser.parse_die_area(str(self.def_path))
        self.assertEqual(die_area, (0, 0, 10000, 10000))

    def test_parse_nets_count(self):
        nets = DefParser.parse_nets(str(self.def_path))
        self.assertEqual(len(nets), 3)
        self.assertIn("net1", nets)
        self.assertIn("net2", nets)
        self.assertIn("net3", nets)

    def test_net_pins(self):
        nets = DefParser.parse_nets(str(self.def_path))
        net1 = nets["net1"]
        self.assertEqual(len(net1.pins), 2)
        self.assertEqual(net1.pins[0], ("comp1", "A"))
        self.assertEqual(net1.pins[1], ("comp2", "B"))

    def test_net_segments(self):
        nets = DefParser.parse_nets(str(self.def_path))
        net1 = nets["net1"]
        self.assertGreaterEqual(len(net1.segments), 3)
        layers = {seg.layer for seg in net1.segments}
        self.assertEqual(layers, {"Metal2", "Metal3"})

    def test_net_vias(self):
        nets = DefParser.parse_nets(str(self.def_path))
        net1 = nets["net1"]
        self.assertEqual(len(net1.vias), 1)
        via = net1.vias[0]
        self.assertEqual(via.via_name, "VIA23_1C")
        self.assertEqual(via.x, 3000)
        self.assertEqual(via.y, 4000)

    def test_segment_bbox(self):
        seg = DefSegment(
            net_name="n", segment_index=0, layer="M2",
            x1=1000, y1=2000, x2=3000, y2=4000
        )
        self.assertEqual(seg.bbox, (1000, 2000, 3000, 4000))


if __name__ == "__main__":
    unittest.main()
