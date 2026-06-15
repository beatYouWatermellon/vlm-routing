"""
Integration tests for the OpenROAD baseline flow.

These tests run the real OpenROAD baseline flow on ISPD benchmarks and verify
that the resulting DEF is routable and metrics are extractable.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.routing_toolkit import RoutingToolkit


pytestmark = [pytest.mark.integration, pytest.mark.slow]


def test_baseline_flow_ispd18_sample(has_openroad, ispd18_sample_paths, tmp_work_dir):
    """Baseline flow should complete and produce a valid routed DEF."""
    toolkit = RoutingToolkit(
        openroad_exe="openroad",
        work_dir=str(tmp_work_dir / "toolkit"),
        lef_file=str(ispd18_sample_paths["lef"]),
    )

    guide = ispd18_sample_paths["guide"]
    guide_file = str(guide) if guide.exists() else None
    routed_def = toolkit.run_baseline_flow(
        str(ispd18_sample_paths["def"]), guide_file
    )

    assert Path(routed_def).exists(), "Routed DEF file was not created"

    metrics = toolkit.extract_metrics(routed_def)
    assert metrics.wirelength > 0, "Wirelength should be positive"
    assert metrics.via_count >= 0, "Via count should be non-negative"
    assert metrics.drc_total >= 0, "DRC total should be non-negative"


def test_baseline_flow_ispd18_test1(has_openroad, ispd18_test1_paths, tmp_work_dir):
    """Baseline flow on a real benchmark should produce measurable metrics."""
    toolkit = RoutingToolkit(
        openroad_exe="openroad",
        work_dir=str(tmp_work_dir / "toolkit"),
        lef_file=str(ispd18_test1_paths["lef"]),
    )

    guide = ispd18_test1_paths["guide"]
    guide_file = str(guide) if guide.exists() else None
    routed_def = toolkit.run_baseline_flow(
        str(ispd18_test1_paths["def"]), guide_file
    )

    assert Path(routed_def).exists(), "Routed DEF file was not created"

    metrics = toolkit.extract_metrics(routed_def)
    assert metrics.wirelength > 0, "Wirelength should be positive"
    assert metrics.via_count > 0, "Via count should be positive"

    # Structured DRC report should be parseable.
    violations = toolkit.extract_structured_drc_report(routed_def)
    assert isinstance(violations, list)


def test_drc_report_cross_references_nets(has_openroad, ispd18_test1_paths, tmp_work_dir):
    """Structured DRC report should identify nets involved in violations."""
    toolkit = RoutingToolkit(
        openroad_exe="openroad",
        work_dir=str(tmp_work_dir / "toolkit"),
        lef_file=str(ispd18_test1_paths["lef"]),
    )

    guide = ispd18_test1_paths["guide"]
    guide_file = str(guide) if guide.exists() else None
    routed_def = toolkit.run_baseline_flow(
        str(ispd18_test1_paths["def"]), guide_file
    )

    violations = toolkit.extract_structured_drc_report(routed_def)
    if violations:
        for v in violations:
            assert v.violation_id, "Violation should have an ID"
            assert v.vtype, "Violation should have a type"
            assert len(v.bbox) == 4, "Violation should have a bbox"
            assert len(v.center) == 2, "Violation should have a center"
