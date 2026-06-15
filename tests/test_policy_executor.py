import os
import tempfile
import unittest
from typing import Dict, List, Optional
from unittest.mock import MagicMock, patch
import numpy as np

from src.def_editor import DEFEditor
from src.odb_editor import ODBEditError, ODBEditor, ODB_AVAILABLE
from src.policy_executor import PolicyExecutor
from src.eda_provider import EDAProvider
from src.routing_toolkit import RoutingMetrics, DRCViolation


_MINIMAL_ROUTED_DEF = """\
VERSION 5.8 ;
DIVIDERCHAR "/" ;
BUSBITCHARS "[]" ;
DESIGN test_design ;
UNITS DISTANCE MICRONS 1000 ;
DIEAREA ( 0 0 ) ( 10000 10000 ) ;
TRACKS X 100 DO 50 STEP 200 LAYER M2 ;
TRACKS Y 100 DO 50 STEP 200 LAYER M3 ;
COMPONENTS 2 ;
- inst1 BUFX2 + PLACED ( 1000 1000 ) N ;
- inst2 BUFX2 + PLACED ( 5000 5000 ) N ;
END COMPONENTS
PINS 0 ;
END PINS
NETS 1 ;
- net1
  ( inst1 Y ) ( inst2 A )
  + ROUTED M2 ( 1000 1000 ) ( 5000 1000 ) 0 M3 ( 5000 1000 ) ( 5000 5000 ) 0
 ;
END NETS
END DESIGN
"""


class MockEDAProvider(EDAProvider):
    """Minimal EDAProvider mock for unit tests."""

    def run_baseline_flow(self, def_file: str, guide_file: Optional[str] = None) -> str:
        return def_file

    def run_incremental_route(
        self, def_file: str, net_list=None, output_name=None, timeout=None
    ) -> str:
        return def_file

    def run_detailed_route(self, def_file: str, output_name=None) -> str:
        return def_file

    def extract_drc_report(self, def_file: str):
        return []

    def extract_metrics(self, def_file: str, drc_violations=None) -> RoutingMetrics:
        return RoutingMetrics()

    def extract_congestion_map(self, def_file: str, resolution: int = 256):
        return np.zeros((resolution, resolution))

    def extract_routing_layers(
        self, def_file: str, layers=None, resolution: int = 512
    ) -> Dict[str, np.ndarray]:
        return {layer: np.zeros((resolution, resolution)) for layer in (layers or [])}

    def rip_up_nets(self, def_file: str, net_list: List[str]) -> str:
        return def_file

    def set_routing_blockage(
        self, def_file: str, bbox, layers, output_name=None, hardness="hard"
    ) -> str:
        return def_file


class TestPolicyExecutor(unittest.TestCase):
    def test_terminate(self):
        provider = MockEDAProvider()
        executor = PolicyExecutor(provider)
        policy = {
            "routing_policy": {
                "iteration": 0,
                "strategy_type": "terminate",
                "priority_actions": [{"action": "terminate", "parameters": {}}],
                "termination_check": True,
            }
        }
        new_def, report = executor.execute_policy(
            "/tmp/in.def", policy, [], {}
        )
        self.assertEqual(new_def, "/tmp/in.def")
        self.assertEqual(len(report), 1)
        self.assertTrue(report[0]["success"])

    def test_no_actions(self):
        provider = MockEDAProvider()
        executor = PolicyExecutor(provider)
        policy = {"routing_policy": {"priority_actions": []}}
        new_def, report = executor.execute_policy(
            "/tmp/in.def", policy, [], {}
        )
        self.assertEqual(new_def, "/tmp/in.def")
        self.assertEqual(len(report), 0)

    def test_terminate_skips_detailed_route(self):
        """A terminate-only policy should not invoke the EDA provider router."""

        class SpyProvider(MockEDAProvider):
            def __init__(self):
                super().__init__()
                self.route_calls = 0

            def run_detailed_route(self, def_file, output_name=None):
                self.route_calls += 1
                return def_file

        provider = SpyProvider()
        executor = PolicyExecutor(provider)
        policy = {
            "routing_policy": {
                "iteration": 0,
                "strategy_type": "terminate",
                "priority_actions": [{"action": "terminate", "parameters": {}}],
                "termination_check": True,
            }
        }
        new_def, report = executor.execute_policy(
            "/tmp/in.def", policy, [], {}
        )
        self.assertEqual(new_def, "/tmp/in.def")
        self.assertEqual(len(report), 1)
        self.assertTrue(report[0]["success"])
        self.assertEqual(provider.route_calls, 0)

    def test_failed_action_is_rolled_back(self):
        provider = MockEDAProvider()
        executor = PolicyExecutor(provider)
        policy = {
            "routing_policy": {
                "priority_actions": [
                    {"action": "noop", "parameters": {}},
                    {
                        "action": "rip_up_segment",
                        "parameters": {"net_name": "missing_net"},
                    },
                    {"action": "noop", "parameters": {}},
                ]
            }
        }
        new_def, report = executor.execute_policy(
            "/tmp/in.def", policy, [], {}
        )
        self.assertEqual(len(report), 3)
        self.assertTrue(report[0]["success"])
        self.assertFalse(report[1]["success"])
        self.assertTrue(report[1]["rolled_back"])
        self.assertTrue(report[2]["success"])


class TestPolicyExecutorViolationFilter(unittest.TestCase):
    """Verify violation filtering helpers."""

    def test_filter_violations_by_nets_handles_lists(self):
        """_filter_violations_by_nets must treat nets_involved as a list."""
        violations = [
            DRCViolation(
                violation_id="v1",
                vtype="spacing",
                layer="M2",
                bbox=(0, 0, 100, 100),
                center=(50, 50),
                nets_involved=["net1", "net2"],
            ),
            DRCViolation(
                violation_id="v2",
                vtype="short",
                layer="M3",
                bbox=(0, 0, 100, 100),
                center=(50, 50),
                nets_involved=["net3"],
            ),
        ]
        result = PolicyExecutor._filter_violations_by_nets(
            violations, {"net1"}
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].violation_id, "v1")


class TestPolicyExecutorODBFallback(unittest.TestCase):
    """Verify PolicyExecutor falls back to DEFEditor when ODB is unavailable."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.def_path = os.path.join(self.tmpdir.name, "test_routed.def")
        with open(self.def_path, "w", encoding="utf-8") as f:
            f.write(_MINIMAL_ROUTED_DEF)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_get_editor_uses_def_when_odb_unavailable(self):
        """When ``odb`` is absent, _get_editor must return a DEFEditor."""
        provider = MockEDAProvider()
        executor = PolicyExecutor(provider)
        self.assertFalse(executor.prefer_odb)
        editor = executor._get_editor(self.def_path)
        self.assertIsInstance(editor, DEFEditor)

    def test_rip_up_segment_without_odb(self):
        """A rip_up_segment action should succeed via DEFEditor fallback."""
        provider = MockEDAProvider()
        executor = PolicyExecutor(provider)
        policy = {
            "routing_policy": {
                "priority_actions": [
                    {
                        "action": "rip_up_segment",
                        "parameters": {"net_name": "net1", "segment_index": 0},
                    }
                ]
            }
        }
        new_def, report = executor.execute_policy(
            self.def_path, policy, [], {}
        )
        self.assertEqual(len(report), 1)
        self.assertTrue(report[0]["success"])
        self.assertTrue(os.path.exists(new_def))

    @patch("src.policy_executor.ODB_AVAILABLE", True)
    @patch("src.policy_executor.ODBEditor")
    def test_rip_up_segment_falls_back_from_odb_error(self, mock_odb_editor_cls):
        """
        If ODBEditor is available but raises ODBEditError, the same edit must
        be retried with DEFEditor.
        """
        mock_odb_editor_cls.side_effect = ODBEditError("mock odb failure")

        provider = MockEDAProvider()
        executor = PolicyExecutor(provider)
        self.assertTrue(executor.prefer_odb)

        policy = {
            "routing_policy": {
                "priority_actions": [
                    {
                        "action": "rip_up_segment",
                        "parameters": {"net_name": "net1", "segment_index": 0},
                    }
                ]
            }
        }
        new_def, report = executor.execute_policy(
            self.def_path, policy, [], {}
        )
        self.assertEqual(len(report), 1)
        self.assertTrue(report[0]["success"])
        self.assertTrue(os.path.exists(new_def))


class TestPolicyExecutorLocalEval(unittest.TestCase):
    """Verify local evaluation gate is wired into fine-grained edits."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.def_path = os.path.join(self.tmpdir.name, "test_routed.def")
        with open(self.def_path, "w", encoding="utf-8") as f:
            f.write(_MINIMAL_ROUTED_DEF)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_local_eval_rejects_bad_edit(self):
        """
        When local eval is enabled and the local router reports a failure,
        the action should be rolled back.
        """
        provider = MockEDAProvider()
        executor = PolicyExecutor(provider, enable_local_eval=True)

        # Force local router to reject every edit.
        executor.local_router.evaluate_edit = lambda *args, **kwargs: MagicMock(
            passed=False,
            local_drc_total=5,
            local_drc_delta=5,
            region_bbox=(0, 0, 1, 1),
            runtime_seconds=0.0,
            detail="mock rejection",
        )

        policy = {
            "routing_policy": {
                "priority_actions": [
                    {
                        "action": "rip_up_segment",
                        "parameters": {"net_name": "net1", "segment_index": 0},
                    }
                ]
            }
        }
        new_def, report = executor.execute_policy(
            self.def_path, policy, [], {}
        )
        self.assertEqual(len(report), 1)
        self.assertFalse(report[0]["success"])
        self.assertTrue(report[0]["rolled_back"])

    def test_local_eval_accepts_good_edit(self):
        """
        When local eval is enabled and the local router passes, the action
        should succeed.
        """
        provider = MockEDAProvider()
        executor = PolicyExecutor(provider, enable_local_eval=True)

        executor.local_router.evaluate_edit = lambda *args, **kwargs: MagicMock(
            passed=True,
            local_drc_total=0,
            local_drc_delta=0,
            region_bbox=(0, 0, 1, 1),
            runtime_seconds=0.0,
            detail="mock acceptance",
        )

        policy = {
            "routing_policy": {
                "priority_actions": [
                    {
                        "action": "rip_up_segment",
                        "parameters": {"net_name": "net1", "segment_index": 0},
                    }
                ]
            }
        }
        new_def, report = executor.execute_policy(
            self.def_path, policy, [], {}
        )
        self.assertEqual(len(report), 1)
        self.assertTrue(report[0]["success"])


if __name__ == "__main__":
    unittest.main()
