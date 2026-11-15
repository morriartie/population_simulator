import json

import pytest

from golden_util import GOLDEN, run_hash, scenario_names


@pytest.mark.parametrize("name", scenario_names())
def test_scenario_output_is_unchanged(name):
    want = json.loads(GOLDEN.read_text())
    assert run_hash(name) == want[name], f"{name}: outputs differ from the recorded golden hash"
