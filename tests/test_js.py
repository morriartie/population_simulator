"""JavaScript tests of the dashboard (run with Node's built-in test runner; skipped without Node)."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_dashboard_javascript():
    run = subprocess.run(["node", "--test", "--test-reporter=spec", "tests/js/"], cwd=ROOT,
                         capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stdout[-4000:] + run.stderr[-2000:]
