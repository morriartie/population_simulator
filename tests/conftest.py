import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from popsim.config import load_config  # noqa: E402

SCENARIOS = ROOT / "scenarios"


@pytest.fixture
def scenario():
    def _load(name: str, **overrides):
        return load_config(SCENARIOS / f"{name}.yaml", overrides or None)

    return _load


@pytest.fixture
def small_cfg(scenario):
    """Two small isolated cities, quick to run."""
    cfg = scenario("baseline")
    for c in cfg["cities"]:
        c["population"] = 20
    return cfg
