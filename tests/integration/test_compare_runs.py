"""Integration tests for scripts/compare_runs.py."""

import json
import subprocess
import sys
from pathlib import Path

import pytest


pytestmark = pytest.mark.integration


PROJECT_ROOT = Path(__file__).resolve().parents[2]
COMPARE_SCRIPT = PROJECT_ROOT / "scripts" / "compare_runs.py"


def _make_report(work_dir: Path, benchmark: str, drc, wl, via, score):
    report = {
        "benchmark": benchmark,
        "scoring_mode": "simplified",
        "baseline": {
            "drc_total": drc,
            "drc_breakdown": {},
            "wirelength_um": wl,
            "via_count": via,
            "score": score,
        },
        "optimized": {
            "drc_total": drc,
            "drc_breakdown": {},
            "wirelength_um": wl,
            "via_count": via,
            "score": score,
        },
        "improvement": {
            "score_pct": 0.0,
            "drc_delta": 0,
            "wl_delta_um": 0.0,
            "via_delta": 0,
        },
        "conclusion": "test",
    }
    path = work_dir / benchmark / "evaluation" / f"{benchmark}_report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report), encoding="utf-8")
    return path


def test_compare_runs_table(tmp_path):
    """compare_runs.py should produce a table with expected columns."""
    base_dir = tmp_path / "baseline"
    agent_dir = tmp_path / "agent"

    _make_report(base_dir, "ispd18_test1", drc=10, wl=1000.0, via=50, score=5.0)
    _make_report(agent_dir, "ispd18_test1", drc=5, wl=950.0, via=48, score=6.0)

    result = subprocess.run(
        [
            sys.executable,
            str(COMPARE_SCRIPT),
            str(base_dir),
            str(agent_dir),
            "--benchmarks",
            "ispd18_test1",
            "--labels",
            "base",
            "agent",
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    output = result.stdout
    assert "ispd18_test1" in output
    assert "base_DRC" in output
    assert "agent_DRC" in output
    assert "Δagent_DRC" in output


def test_compare_runs_json_output(tmp_path):
    """compare_runs.py --output should write a valid JSON table."""
    base_dir = tmp_path / "baseline"
    agent_dir = tmp_path / "agent"
    output_json = tmp_path / "comparison.json"

    _make_report(base_dir, "ispd18_test1", drc=10, wl=1000.0, via=50, score=5.0)
    _make_report(agent_dir, "ispd18_test1", drc=5, wl=950.0, via=48, score=6.0)

    subprocess.run(
        [
            sys.executable,
            str(COMPARE_SCRIPT),
            str(base_dir),
            str(agent_dir),
            "--benchmarks",
            "ispd18_test1",
            "--output",
            str(output_json),
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    assert output_json.exists()
    data = json.loads(output_json.read_text())
    assert data["benchmarks"] == ["ispd18_test1"]
    assert len(data["rows"]) == 1
