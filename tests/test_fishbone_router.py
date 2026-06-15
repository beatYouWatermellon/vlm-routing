import unittest
import tempfile
from pathlib import Path

from src.fishbone_router import FishboneRouter


class TestFishboneRouter(unittest.TestCase):
    def setUp(self):
        self.sample_def = """VERSION 5.8 ;
DIVIDERCHAR "/" ;
BUSBITCHARS "[]" ;
DESIGN test ;
UNITS DISTANCE MICRONS 2000 ;
DIEAREA ( 0 0 ) ( 10000 10000 ) ;
LAYER Metal2
  TYPE ROUTING ;
  DIRECTION VERTICAL ;
  PITCH 0.2 0.2 ;
  WIDTH 0.07 ;
END Metal2
LAYER Metal3
  TYPE ROUTING ;
  DIRECTION HORIZONTAL ;
  PITCH 0.2 0.2 ;
  WIDTH 0.07 ;
END Metal3
LAYER Via2
  TYPE CUT ;
  SPACING 0.07 ;
  WIDTH 0.07 ;
END Via2
NETS 3 ;
- net1 ( 1000 1000 ) ( 2000 1000 ) ( 3000 1000 ) ( 4000 1000 )
  + ROUTED Metal2 ( 1000 1000 ) ( * 2000 )
  NEW Metal2 ( 2000 1000 ) ( * 2000 )
  ;
- other ( 5000 5000 ) ( 6000 5000 )
  + ROUTED Metal2 ( 5000 5000 ) ( 6000 * )
  ;
END NETS
END DESIGN
"""
        self.work_dir = Path(tempfile.mkdtemp(prefix="test_fishbone_"))
        self.def_path = self.work_dir / "test.def"
        self.lef_path = self.work_dir / "test.lef"
        self.def_path.write_text(self.sample_def)
        self.lef_path.write_text(self._lef_content())

    def tearDown(self):
        for p in self.work_dir.glob("*"):
            p.unlink()
        self.work_dir.rmdir()

    @staticmethod
    def _lef_content() -> str:
        return """VERSION 5.8 ;
LAYER Metal2
  TYPE ROUTING ;
  DIRECTION VERTICAL ;
  PITCH 0.2 0.2 ;
  WIDTH 0.07 ;
END Metal2
LAYER Metal3
  TYPE ROUTING ;
  DIRECTION HORIZONTAL ;
  PITCH 0.2 0.2 ;
  WIDTH 0.07 ;
END Metal3
LAYER Via2
  TYPE CUT ;
  SPACING 0.07 ;
  WIDTH 0.07 ;
END Via2
END LIBRARY
"""

    def test_fishbone_horizontal(self):
        router = FishboneRouter(str(self.def_path), str(self.lef_path))
        route = router.fishbone_route_net("net1")
        self.assertIsNotNone(route)
        self.assertIn("ROUTED", route)
        self.assertIn("Metal3", route)

    def test_fishbone_inserts_vias(self):
        router = FishboneRouter(str(self.def_path), str(self.lef_path))
        route = router.fishbone_route_net("net1")
        self.assertIsNotNone(route)
        # Trunk and branch are on different layers, so vias must be present.
        self.assertIn("VIA", route)
        # Each branch should terminate at the trunk with a via.
        via_count = route.count("VIA")
        self.assertGreaterEqual(via_count, 1)

    def test_fishbone_unknown_net(self):
        router = FishboneRouter(str(self.def_path), str(self.lef_path))
        route = router.fishbone_route_net("nonexistent")
        self.assertIsNone(route)


if __name__ == "__main__":
    unittest.main()
