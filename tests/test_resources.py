"""The resource contract (REQ-5B)."""

import copy

import pytest

from popsim import resources
from popsim.config import load_config

from golden_util import ROOT


@pytest.fixture
def cfg():
    return load_config(None)


def test_default_contract_reproduces_the_old_constants(cfg):
    r = resources.Resources(cfg)
    assert r.good_need == {"food": "food", "wood": "shelter"}
    assert r.need_good == {"food": "food", "shelter": "wood"}
    assert r.per_unit("food") == 0.5 and r.per_unit("wood") == 0.5
    assert r.daily_units == {"food": 1.4, "wood": 0.6}
    assert resources.validate(cfg) == ([], [])


def test_shipped_scenarios_have_no_errors():
    for p in sorted((ROOT / "scenarios").glob("*.yaml")):
        errs, _ = resources.validate(load_config(p))
        assert errs == [], (p.name, errs)


def _errors(cfg, mutate):
    c = copy.deepcopy(cfg)
    mutate(c)
    return resources.validate(c)


@pytest.mark.parametrize("mutate,needle", [
    (lambda c: c["resources"]["food"]["satisfies"].update(need="nope"), "is not a need"),
    (lambda c: c["resources"]["wood"]["satisfies"].update(need="food"), "one good per need"),
    (lambda c: c["resources"]["food"]["satisfies"].update(per_unit=0), "positive number"),
    (lambda c: c["resources"]["food"].update(daily_units=-1), "daily_units"),
    (lambda c: c["resources"]["food"]["supply"].update(process="nope"), "is not in processes"),
    (lambda c: c["resources"]["food"]["supply"].update(process="grove"), "never makes a 'food' node"),
    (lambda c: c["resources"]["food"]["trigger"].update(signal="need:ghost"), "is not a signal"),
    (lambda c: c["resources"]["food"]["trigger"].update(signal="need:shelter"), "close the loop"),
    (lambda c: c["resources"]["food"]["trigger"].update(signal="weather"), "need:<name> or service"),
    (lambda c: c["resources"]["seed"].update(byproduct_of="ghost"), "is not a good"),
    (lambda c: c["resources"]["seed"].update(consumed_by=["ghost"]), "is not in processes"),
    (lambda c: c["generators"]["plant_food"].update(from_resource="ghost"), "has no entry in resources"),
    (lambda c: c["generators"]["plant_food"]["create"].update(process="grove"), "reacts to need:food"),
])
def test_contract_errors_name_the_entry(cfg, mutate, needle):
    errs, _ = _errors(cfg, mutate)
    assert any(needle in e for e in errs), errs


@pytest.mark.parametrize("mutate,needle", [
    (lambda c: c["resources"]["seed"].pop("byproduct_of"), "nothing produces seed"),
    (lambda c: c["resources"]["seed"].update(consumed_by=[]), "no process consumes it"),
    (lambda c: c["resources"]["food"]["supply"].update(policy="ghost"), "is not in policies"),
    (lambda c: c["resources"]["food"].pop("supply"), "nothing is created when it fires"),
    (lambda c: (c.update(goods=["food", "wood"]), c["policies"]["farming"].update(enabled=True)), "not in goods"),
])
def test_contract_warnings(cfg, mutate, needle):
    errs, warns = _errors(cfg, mutate)
    assert any(needle in w for w in warns), warns


def test_old_keys_are_rejected_with_the_new_place(cfg):
    c = copy.deepcopy(cfg)
    c["shelter"]["per_wood"] = 0.4
    c["physiology"]["metabolism"]["satiety_per_food"] = 0.4
    c["needs"]["daily_units"] = {"food": 1.0}
    errs, _ = resources.validate(c)
    joined = " ".join(errs)
    assert "resources.wood.satisfies.per_unit" in joined
    assert "resources.food.satisfies.per_unit" in joined
    assert "resources.<good>.daily_units" in joined


def test_from_resource_rules_resolve_to_the_old_literals(cfg):
    g = cfg["generators"]
    assert g["plant_food"] == {
        "policy": "farming", "when": {"signal": "need:food", "share_gt": 0.10, "for_days": 1},
        "create": {"process": "field", "at": "fertile_site", "reserve": 40},
        "limits": {"max_active": 2, "cooldown_days": 2}}
    assert g["plant_trees"] == {
        "policy": "forestry", "when": {"signal": "need:shelter", "share_gt": 0.10, "for_days": 1},
        "create": {"process": "grove", "at": "fertile_site", "reserve": 20},
        "limits": {"max_active": 2, "cooldown_days": 2}}
    assert "from_resource" not in str(g)


def test_a_key_written_in_the_rule_wins_and_expanding_twice_changes_nothing():
    cfg = load_config(None, {"generators": {"plant_food": {"limits": {"max_active": 5},
                                                           "when": {"share_gt": 0.3}}}})
    rule = cfg["generators"]["plant_food"]
    assert rule["limits"] == {"max_active": 5, "cooldown_days": 2}      # merged key by key
    assert rule["when"] == {"signal": "need:food", "share_gt": 0.3, "for_days": 1}
    assert resources.expand(copy.deepcopy(cfg)) == cfg


def test_an_invalid_contract_stops_the_load():
    with pytest.raises(resources.ConfigError, match="is not a need"):
        load_config(None, {"resources": {"food": {"satisfies": {"need": "nope"}}}})


def test_the_good_behind_a_need_is_config_only():
    """REQ-5B.7: rename the shelter good from wood to timber with config alone; citizens burn timber for shelter
    and the forestry rule plants timber."""
    from popsim.world import Simulation

    node = {"initial": 300, "max_amount": 400, "regen_per_hour": 5.0, "base_rate": 0.7, "stamina_cost": 5.0}
    over = {
        "goods": ["food", "timber"], "time": {"days": 4},
        "initial_inventory": {"food": 3, "timber": 2},
        "pricing": {"initial_prices": {"food": 2.0, "timber": 2.0}},
        "cities": [{"name": "A", "population": 30, "nodes": [{"good": "food", **node}, {"good": "timber", **node}]}],
        "roads": [], "events": [],
        "resources": {
            "wood": None,   # null drops the inherited entry
            "timber": {"satisfies": {"need": "shelter", "per_unit": 0.5}, "daily_units": 0.6,
                       "supply": {"process": "grove", "policy": "forestry", "at": "fertile_site", "reserve": 20,
                                  "limits": {"max_active": 2, "cooldown_days": 2}},
                       "trigger": {"signal": "need:shelter", "share_gt": 0.10, "for_days": 1}}},
        "processes": {"grove": {"wood_spot": {"on_enter": [{"spawn_node": {
            "good": "timber", "initial_frac": 0.15, "max_amount": 300, "regen_per_hour": 3.5, "base_rate": 0.7,
            "stamina_cost": 5.0}}]}}},
        "generators": {"plant_trees": {"from_resource": "timber"}},
    }
    cfg = load_config(ROOT / "scenarios" / "baseline.yaml", over)
    assert resources.validate(cfg)[0] == []
    assert cfg["generators"]["plant_trees"]["create"]["process"] == "grove"
    sim = Simulation(cfg)
    r = resources.of(sim)
    assert r.good_need == {"food": "food", "timber": "shelter"} and r.daily_units == {"food": 1.4, "timber": 0.6}
    sim.run()
    assert sim.metrics.counters[("consume", "timber")] > 0
    assert ("consume", "wood") not in sim.metrics.counters


def test_the_scenario_editor_lists_contract_errors_and_warnings():
    from popsim.dashboard.data import validate

    base = {"extends": "default", "cities": [{"name": "A", "population": 5, "nodes": [
        {"good": "food", "initial": 50, "max_amount": 80, "regen_per_hour": 1.0, "base_rate": 0.7,
         "stamina_cost": 5.0}]}], "roads": [], "events": []}
    bad = validate({**base, "resources": {"food": {"supply": {"process": "nope"}}}}, ROOT / "scenarios", full=False)
    assert not bad["ok"] and any("resources.food.supply.process" in e for e in bad["errors"])
    warn = validate({**base, "resources": {"seed": {"consumed_by": []}}}, ROOT / "scenarios", full=False)
    assert any("no process consumes it" in w for w in warn["warnings"])
