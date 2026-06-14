import unittest
import tempfile
from pathlib import Path
from src.routing_toolkit import RoutingToolkit, RoutingMetrics, NetRoutingFeatures


class TestRoutingToolkit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work_dir = tempfile.mkdtemp(prefix="test_toolkit_")
        cls.toolkit = RoutingToolkit(
            openroad_exe="openroad",
            work_dir=cls.work_dir,
            lef_file="data/ispd2018/ispd18_test1/ispd18_test1.lef",
        )

    def test_routing_metrics_defaults(self):
        metrics = RoutingMetrics()
        self.assertEqual(metrics.drc_total, 0)
        self.assertEqual(metrics.wirelength, 0.0)
        self.assertEqual(metrics.via_count, 0)

    def test_drc_breakdown(self):
        metrics = RoutingMetrics(
            drc_spacing=2, drc_short=1, drc_min_width=3
        )
        breakdown = metrics.drc_breakdown
        self.assertEqual(breakdown["spacing"], 2)
        self.assertEqual(breakdown["short"], 1)
        self.assertEqual(breakdown["min_width"], 3)

    def test_parse_die_area(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".def", delete=False
        ) as f:
            f.write("DIEAREA ( 0 0 ) ( 100000 200000 ) ;\n")
            path = f.name

        die_area = self.toolkit._parse_die_area(path)
        self.assertEqual(die_area, (0, 0, 100000, 200000))
        Path(path).unlink()


class TestRoutingToolkitWithData(unittest.TestCase):
    def test_extract_netlist_stats(self):
        def_file = "data/ispd2018/ispd18_test1/ispd18_test1.def"
        if not Path(def_file).exists():
            self.skipTest(f"Test data not found: {def_file}")

        toolkit = RoutingToolkit(
            openroad_exe="openroad",
            work_dir=tempfile.mkdtemp(prefix="test_toolkit_data_"),
            lef_file="data/ispd2018/ispd18_test1/ispd18_test1.lef",
        )

        stats = toolkit.extract_netlist_stats(def_file)
        self.assertGreater(stats["total_nets"], 0)
        self.assertIsInstance(stats["high_fanout_nets"], list)

    def test_congestion_map_shape(self):
        def_file = "data/ispd2018/ispd18_test1/ispd18_test1.def"
        if not Path(def_file).exists():
            self.skipTest(f"Test data not found: {def_file}")

        toolkit = RoutingToolkit(
            openroad_exe="openroad",
            work_dir=tempfile.mkdtemp(prefix="test_toolkit_data_"),
            lef_file="data/ispd2018/ispd18_test1/ispd18_test1.lef",
        )

        cmap = toolkit.extract_congestion_map(def_file, resolution=256)
        self.assertEqual(cmap.shape, (256, 256))

    def test_extract_net_routing_features(self):
        def_file = "outputs/baseline_test/ispd18_test1/baseline_ispd18_test1.def"
        if not Path(def_file).exists():
            self.skipTest(f"Routed DEF not found: {def_file}")

        toolkit = RoutingToolkit(
            openroad_exe="openroad",
            work_dir=tempfile.mkdtemp(prefix="test_toolkit_features_"),
            lef_file="data/ispd2018/ispd18_test1/ispd18_test1.lef",
        )

        features = toolkit.extract_net_routing_features(def_file)
        self.assertGreater(len(features), 0)

        net1 = features.get("net1")
        self.assertIsNotNone(net1)
        self.assertIsInstance(net1, NetRoutingFeatures)
        self.assertGreater(net1.routed_segments, 0)
        self.assertGreaterEqual(net1.via_count, 0)


if __name__ == "__main__":
    unittest.main()
