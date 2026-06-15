import unittest

import numpy as np

from src.visual_renderer import VisualRenderer


class TestVisualRenderer(unittest.TestCase):
    def test_physical_to_grid(self):
        px, py = VisualRenderer._physical_to_grid(
            [(0, 0), (500, 500), (1000, 1000)], 10, 10, die_area=(0, 0, 1000, 1000)
        )
        self.assertEqual(px, [0.0, 5.0, 9.0])
        self.assertEqual(py, [0.0, 5.0, 9.0])

    def test_physical_to_grid_clamps_out_of_bounds(self):
        px, py = VisualRenderer._physical_to_grid(
            [(-100, -100), (2000, 2000)], 10, 10, die_area=(0, 0, 1000, 1000)
        )
        self.assertEqual(px, [0.0, 9.0])
        self.assertEqual(py, [0.0, 9.0])

    def test_physical_bbox_to_grid(self):
        gx1, gy1, gx2, gy2 = VisualRenderer._physical_bbox_to_grid(
            (250, 250, 750, 750), 10, 10, die_area=(0, 0, 1000, 1000)
        )
        self.assertEqual(gx1, 2)
        self.assertEqual(gy1, 2)
        self.assertEqual(gx2, 8)
        self.assertEqual(gy2, 8)


if __name__ == "__main__":
    unittest.main()
