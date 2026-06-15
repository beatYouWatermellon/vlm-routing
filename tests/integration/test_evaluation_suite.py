"""Tests for scripts/run_evaluation_suite.py helper functions."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.run_evaluation_suite import format_table, pct


pytestmark = pytest.mark.integration


def test_pct_with_zero_base():
    assert pct(1.0, 0.0) == "N/A"


def test_pct_positive_delta():
    assert pct(5.0, 100.0) == "+5.00"


def test_pct_negative_delta():
    assert pct(-5.0, 100.0) == "-5.00"


def test_format_table_with_reports():
    base_report = {
        "optimized": {
            "drc_total": 10,
            "wirelength_um": 1000.0,
            "via_count": 50,
            "score": 5.0,
        }
    }
    agent_report = {
        "optimized": {
            "drc_total": 5,
            "wirelength_um": 950.0,
            "via_count": 48,
            "score": 6.0,
        }
    }
    table = format_table([("ispd18_test1", base_report, agent_report)])
    assert "ispd18_test1" in table
    assert "10" in table
    assert "5" in table
    assert "950.00" in table
    assert "+20.00" in table  # score improvement percentage


def test_format_table_with_missing_report():
    table = format_table([("ispd18_test1", None, None)])
    assert "ispd18_test1" in table
    assert "N/A" in table
