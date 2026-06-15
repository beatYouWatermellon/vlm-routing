"""Shared fixtures for integration tests."""

import shutil
import subprocess
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _openroad_exe():
    """Return the OpenROAD executable path, or None if not found."""
    exe = shutil.which("openroad")
    if exe:
        return exe
    return None


@pytest.fixture(scope="session")
def openroad_exe():
    return _openroad_exe()


@pytest.fixture(scope="session")
def has_openroad(openroad_exe):
    if openroad_exe is None:
        pytest.skip("OpenROAD not found on PATH")
    return True


@pytest.fixture(scope="session")
def ispd18_test1_paths():
    data_dir = PROJECT_ROOT / "data" / "ispd2018" / "ispd18_test1"
    if not data_dir.exists():
        pytest.skip("ispd18_test1 benchmark data not found")
    lef = data_dir / "ispd18_test1.lef"
    def_ = data_dir / "ispd18_test1.def"
    guide = data_dir / "ispd18_test1.guide"
    if not def_.exists():
        pytest.skip("ispd18_test1.def not found")
    return {"dir": data_dir, "lef": lef, "def": def_, "guide": guide}


@pytest.fixture(scope="session")
def ispd18_sample_paths():
    data_dir = PROJECT_ROOT / "data" / "ispd2018" / "ispd18_sample"
    if not data_dir.exists():
        pytest.skip("ispd18_sample benchmark data not found")
    lef = data_dir / "ispd18_sample.lef"
    def_ = data_dir / "ispd18_sample.def"
    guide = data_dir / "ispd18_sample.guide"
    if not def_.exists():
        pytest.skip("ispd18_sample.def not found")
    return {"dir": data_dir, "lef": lef, "def": def_, "guide": guide}


@pytest.fixture
def tmp_work_dir(tmp_path):
    work = tmp_path / "work"
    work.mkdir(parents=True, exist_ok=True)
    return work
