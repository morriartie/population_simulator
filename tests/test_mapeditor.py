"""Server side of the scenario map editor (REQ-1C.8): the preview of automatic positions, the helpers
(arrange, connect, fill in, scatter, remove positions), error paths, caps and warnings."""

import copy
import math

import pytest

from popsim.dashboard import layout as L
from popsim.dashboard.data import validate, preview
from popsim.io.logs import world_layout
from popsim.world import Simulation

from conftest import SCENARIOS


@pytest.fixture
def scen(tmp_path):
    (tmp_path / "baseline.yaml").write_text((SCENARIOS / "baseline.yaml").read_text())
    return tmp_path


def base():
    return {"extends": "baseline.yaml", "world_name": "t", "time": {"days": 3}}


def ten_cities(n=10):
    return [{"name": f"C{i}", "population": 5, "radius": 1.0 + (i % 4),
             "nodes": [{"good": "food", "initial": 50, "max_amount": 100, "regen_per_hour": 1,
                        "base_rate": 0.7, "stamina_cost": 5}]} for i in range(n)]


def apply(data, scen, action, **params):
    return L.run_helper(data, action, params, scen)


def ok(data, scen):
    rep = validate(data, scen)
    assert rep["ok"], rep["errors"]
    return rep


# ----------------------------------------------------------------- preview


def test_preview_positions_are_the_ones_the_simulation_builds(scen):
    data = {**base(), "cities": [
        {"name": "A", "x": 0, "y": 0, "radius": 4, "population": 8, "houses": {"count": 5},
         "workplaces": [{"task": "guard", "wage": 2}, {"task": "guard", "wage": 2, "at": [1, 1]}],
         "nodes": [{"good": "food", "initial": 100, "max_amount": 200, "regen_per_hour": 2, "base_rate": 0.7, "stamina_cost": 5},
                   {"good": "wood", "initial": 100, "max_amount": 200, "regen_per_hour": 2, "base_rate": 0.7, "stamina_cost": 5,
                    "at": [-2, 0]}]},
        {"name": "B", "x": 30, "y": 40, "population": 8,
         "nodes": [{"good": "food", "initial": 100, "max_amount": 200, "regen_per_hour": 2, "base_rate": 0.7, "stamina_cost": 5}]}]}
    rep = preview(data, scen)
    assert rep["ok"], rep["errors"]
    real = Simulation(rep["resolved"])  # the full world, with citizens
    want = world_layout(real)
    for key in ("cities", "nodes", "houses", "workplaces", "roads"):
        skip = ("ci", "i", "occupant")  # the preview has no citizens, so no occupants
        got = [{k: v for k, v in item.items() if k not in skip} for item in rep["world"][key]]
        assert got == [{k: v for k, v in item.items() if k not in skip} for item in want[key]], key
    # every automatic position is inside its city, explicit ones are where they were put
    wp = [w for w in rep["world"]["workplaces"] if w["ci"] == 0]
    assert (wp[1]["x"], wp[1]["y"]) == (1.0, 1.0) and math.hypot(wp[0]["x"], wp[0]["y"]) <= 4
    # entries are numbered in the order the scenario lists them
    assert [(n["ci"], n["i"]) for n in rep["world"]["nodes"]] == [(0, 0), (0, 1), (1, 0)]
    assert [h["i"] for h in rep["world"]["houses"] if h["ci"] == 0] == [0, 1, 2, 3, 4]
    # asking again gives the same answer
    assert preview(data, scen)["world"] == rep["world"]


def test_preview_without_citizens_is_fast_to_build_and_reports_errors(scen):
    data = {**base(), "cities": ten_cities(3)}
    for c in data["cities"]:
        c["population"] = 5000
    assert preview(data, scen)["ok"]  # no 15000 citizens built for a preview
    bad = {**base(), "cities": [{"name": "A", "x": 0, "y": 0, "radius": 2, "population": 5,
                                 "nodes": [{"good": "food", "initial": 1, "max_amount": 2, "regen_per_hour": 1, "base_rate": 1,
                                            "stamina_cost": 1, "at": [9, 9]}]}]}
    rep = preview(bad, scen)
    assert not rep["ok"] and rep["world"] is None


# ------------------------------------------------------------- error paths


def test_errors_and_warnings_carry_the_path_of_the_item(scen):
    data = {**base(), "cities": [
        {"name": "A", "x": 0, "y": 0, "radius": 2, "population": 5,
         "nodes": [{"good": "food", "initial": 1, "max_amount": 2, "regen_per_hour": 1, "base_rate": 1, "stamina_cost": 1,
                    "at": [9, 9]}]},
        {"name": "B", "x": 1, "y": 0, "radius": 2, "population": 5, "nodes": []}],
        "roads": [{"a": "A", "b": "B", "length": 0.1}]}
    rep = validate(data, scen)
    paths = {i["path"] for i in rep["error_items"]}
    assert "cities[0].nodes[0].at" in paths and "roads[0].length" in paths
    item = next(i for i in rep["error_items"] if i["path"] == "cities[0].nodes[0].at")
    assert "outside the city radius" in item["message"] and not item["message"].startswith("cities")
    assert any(i["path"] == "cities[1]" and "overlaps" in i["message"] for i in rep["warning_items"])
    assert [i["message"] for i in rep["error_items"] if i["path"] is None] == []
    unresolved = validate({"extends": "nope.yaml"}, scen)
    assert unresolved["error_items"] == [{"path": None, "message": unresolved["errors"][0]}]
    same = validate({**base(), "cities": [{"name": "A", "x": 0, "y": 0, "population": 1},
                                           {"name": "B", "x": 0, "y": 0, "population": 1}]}, scen)
    assert any(i["path"] == "cities[1]" and "same position" in i["message"] for i in same["error_items"])


def test_warnings_for_cut_off_cities_and_unreachable_nodes(scen):
    cities = [{"name": n, "x": x, "y": 0, "radius": 2, "population": 5,
               "nodes": [{"good": "food", "initial": 50, "max_amount": 100, "regen_per_hour": 1, "base_rate": 0.7, "stamina_cost": 5}]}
              for n, x in (("A", 0), ("B", 30), ("C", 60))]
    data = {**base(), "cities": cities, "roads": [{"a": "A", "b": "B"}]}
    rep = validate(data, scen)
    assert any(i["path"] == "cities[2]" and "cut off" in i["message"] for i in rep["warning_items"])
    assert not any("cut off" in w for w in validate({**base(), "cities": cities}, scen)["warnings"])  # no roads at all: on purpose
    far = copy.deepcopy(data)
    far["cities"][0]["radius"] = 60
    far["cities"][0]["nodes"][0]["at"] = [55, 0]
    assert any(i["path"] == "cities[0].nodes[0]" and "very far" in i["message"] for i in validate(far, scen)["warning_items"])
    empty = copy.deepcopy(data)
    empty["cities"][1]["nodes"][0].update(initial=0, regen_per_hour=0)
    assert any(i["path"] == "cities[1].nodes[0]" and "no one can gather" in i["message"] for i in validate(empty, scen)["warning_items"])


def test_caps_keep_a_scenario_buildable(scen):
    assert any("at most 50 cities" in e for e in validate({**base(), "cities": ten_cities(51)}, scen)["errors"])
    many = {**base(), "cities": [{"name": "A", "population": 5, "nodes": [
        {"good": "food", "initial": 1, "max_amount": 2, "regen_per_hour": 1, "base_rate": 1, "stamina_cost": 1}] * 201}]}
    assert any("at most 200 resource nodes" in e for e in validate(many, scen)["errors"])


# ----------------------------------------------------------------- arrange


def test_arrange_cities_never_overlaps_and_is_deterministic(scen):
    for n in (1, 2, 3, 7, 10):
        data = {**base(), "cities": ten_cities(n)}
        out = apply(data, scen, "arrange_cities", gap=10)
        again = apply(data, scen, "arrange_cities", gap=10)
        assert out == again
        cs = out["data"]["cities"]
        assert all("x" in c and "y" in c for c in cs) and len(out["added"]) == n
        for i, a in enumerate(cs):
            for b in cs[i + 1:]:
                gap = math.hypot(a["x"] - b["x"], a["y"] - b["y"]) - a["radius"] - b["radius"]
                assert gap >= 10 - 0.05, (n, a["name"], b["name"], gap)  # allowing for rounding to 0.01
        ok(out["data"], scen)
    assert "x" not in data["cities"][0]  # the input is never modified


def test_arrange_only_places_cities_that_have_no_position(scen):
    data = {**base(), "cities": ten_cities(3)}
    data["cities"][0].update(x=100, y=50)
    out = apply(data, scen, "arrange_cities", gap=5)["data"]["cities"]
    assert (out[0]["x"], out[0]["y"]) == (100, 50)
    assert out[1]["x"] > 100 + out[0]["radius"] and out[2]["x"] > out[1]["x"]
    assert apply({**base(), "cities": out}, scen, "arrange_cities")["added"] == []


# ----------------------------------------------------------------- connect


def components(cities, roads):
    parent = {c["name"]: c["name"] for c in cities}

    def find(n):
        while parent[n] != n:
            n = parent[n]
        return n

    for r in roads:
        parent[find(r["a"])] = find(r["b"])
    return len({find(n) for n in parent})


def test_connect_cities_joins_everything_with_a_minimum_spanning_tree(scen):
    data = apply({**base(), "cities": ten_cities(8)}, scen, "arrange_cities")["data"]
    out = apply(data, scen, "connect_cities")
    roads = out["data"]["roads"]
    assert len(roads) == 7 and components(out["data"]["cities"], roads) == 1
    assert len({tuple(sorted((r["a"], r["b"]))) for r in roads}) == 7
    assert all(set(r) == {"a", "b"} for r in roads)  # hours, stamina and length are derived
    ok(out["data"], scen)
    assert apply(out["data"], scen, "connect_cities")["added"] == []  # nothing left to do
    extra = apply(out["data"], scen, "connect_cities", nearest=2)
    assert extra["added"] and all("nearest neighbour" in a for a in extra["added"])
    pairs = [tuple(sorted((r["a"], r["b"]))) for r in extra["data"]["roads"]]
    assert len(pairs) == len(set(pairs))
    assert apply(data, scen, "connect_cities") == apply(data, scen, "connect_cities")


def test_connect_counts_existing_roads_and_road_events(scen):
    data = apply({**base(), "cities": ten_cities(4)}, scen, "arrange_cities")["data"]
    data["roads"] = [{"a": "C0", "b": "C1"}]
    data["events"] = [{"day": 2, "type": "add_road", "a": "C2", "b": "C3"}]
    out = apply(data, scen, "connect_cities")
    assert len(out["added"]) == 1  # two components left, so one road
    assert components(out["data"]["cities"], out["data"]["roads"] + [{"a": "C2", "b": "C3"}]) == 1


def test_connect_needs_positions(scen):
    with pytest.raises(L.HelperError, match="position first"):
        apply({**base(), "cities": ten_cities(3)}, scen, "connect_cities")
    with pytest.raises(L.HelperError, match="at least two"):
        apply({**base(), "cities": ten_cities(1)}, scen, "connect_cities")


# ----------------------------------------------------------------- fill in


def test_fill_resources_adds_only_missing_goods_once(scen):
    data = {**base(), "cities": ten_cities(2)}  # each city has food only; baseline goods are food and wood
    out = apply(data, scen, "fill_resources")
    assert out["added"] == ["C0: a wood node", "C1: a wood node"]
    assert all({n["good"] for n in c["nodes"]} == {"food", "wood"} for c in out["data"]["cities"])
    assert all("at" not in n for c in out["data"]["cities"] for n in c["nodes"])  # automatic positions
    ok(out["data"], scen)
    assert apply(out["data"], scen, "fill_resources")["added"] == []


def test_fill_workplaces_and_houses(scen):
    data = {**base(), "cities": ten_cities(2)}
    out = apply(data, scen, "fill_workplaces", city="C1", count=3, task="guard", wage=2.5)
    assert out["data"]["cities"][1]["workplaces"] == [{"task": "guard", "wage": 2.5}] * 3
    assert "workplaces" not in out["data"]["cities"][0]
    out = apply(out["data"], scen, "fill_houses", count=4)  # every city
    assert [c["houses"] for c in out["data"]["cities"]] == [{"count": 4}, {"count": 4}]
    out = apply(out["data"], scen, "fill_houses", city="C0", count=2)
    assert out["data"]["cities"][0]["houses"] == {"count": 6}
    ok(out["data"], scen)
    with pytest.raises(L.HelperError, match="at most"):
        apply(out["data"], scen, "fill_houses", count=6000)
    with pytest.raises(L.HelperError, match="unknown city"):
        apply(out["data"], scen, "fill_workplaces", city="Z", count=1)


# ----------------------------------------------------------------- scatter


def placed(scen, n=2):
    return apply({**base(), "cities": ten_cities(n)}, scen, "arrange_cities")["data"]


def test_scatter_creates_ordinary_items_inside_the_area_and_the_city(scen):
    data = placed(scen)
    params = dict(city="C1", kind="resource", what="wood", count=12, center=[0.3, 0.0], radius=0.8, richness=2)
    out = apply(data, scen, "scatter", **params)
    city = out["data"]["cities"][1]
    new = [n for n in city["nodes"] if n["good"] == "wood"]
    assert len(new) == 12 and out["added"] == ["C1: 12 nodes scattered in a circle of 0.8 km"]
    assert all(math.hypot(n["at"][0] - 0.3, n["at"][1]) <= 0.8 + 0.01 for n in new)
    assert all(math.hypot(*n["at"]) <= city["radius"] + 0.01 for n in new)
    assert new[0]["initial"] == 500 and new[0]["regen_per_hour"] == 8.0  # richness 2 doubles the defaults
    assert len({tuple(n["at"]) for n in new}) == 12  # spread out, no two on the same spot
    assert apply(data, scen, "scatter", **params) == out  # same gesture, same result
    other = apply(data, scen, "scatter", **{**params, "center": [0.31, 0.0]})
    assert [n.get("at") for n in other["data"]["cities"][1]["nodes"]] != [n.get("at") for n in city["nodes"]]
    ok(out["data"], scen)


def test_scatter_is_clipped_to_the_city_and_checks_its_input(scen):
    data = placed(scen)
    city_r = data["cities"][0]["radius"]
    out = apply(data, scen, "scatter", city="C0", kind="workplace", what="guard", count=8, center=[city_r, 0], radius=city_r, wage=3)
    assert all(math.hypot(*w["at"]) <= city_r + 0.01 for w in out["data"]["cities"][0]["workplaces"])
    assert out["data"]["cities"][0]["workplaces"][0]["wage"] == 3
    with pytest.raises(L.HelperError, match="does not overlap"):
        apply(data, scen, "scatter", city="C0", kind="house", count=3, center=[50, 50], radius=1)
    with pytest.raises(L.HelperError, match="kind"):
        apply(data, scen, "scatter", city="C0", kind="tree", count=3, center=[0, 0], radius=1)
    with pytest.raises(L.HelperError, match="what must be"):
        apply(data, scen, "scatter", city="C0", kind="resource", what="gold", count=3, center=[0, 0], radius=1)
    with pytest.raises(L.HelperError, match="count must be at most"):
        apply(data, scen, "scatter", city="C0", kind="resource", what="food", count=500, center=[0, 0], radius=1)
    with pytest.raises(L.HelperError, match="position"):
        apply({**base(), "cities": ten_cities(2)}, scen, "scatter", city="C0", kind="house", count=1, center=[0, 0], radius=1)


def test_scatter_houses_turns_a_count_into_a_list_and_keeps_the_automatic_ones(scen):
    data = placed(scen)
    data["cities"][0]["houses"] = {"count": 3}
    out = apply(data, scen, "scatter", city="C0", kind="house", count=4, center=[0, 0], radius=0.5)
    houses = out["data"]["cities"][0]["houses"]
    assert len(houses) == 7 and houses[:3] == [{}, {}, {}] and all("at" in h for h in houses[3:])
    sim = Simulation(validate(out["data"], scen)["resolved"], npcs=False)
    assert sum(1 for _ in sim.world.query(__import__("popsim.components", fromlist=["House"]).House)) == 7


# ------------------------------------------------------------ remove positions


def test_remove_positions_keeps_travel_and_validates(scen):
    data = placed(scen)
    data["roads"] = [{"a": "C0", "b": "C1"}]
    data["events"] = [{"day": 1, "type": "add_road", "a": "C0", "b": "C1", "length": 500}]
    data["cities"][0]["nodes"][0]["at"] = [0.2, 0.1]
    spatial = Simulation(validate(data, scen)["resolved"], npcs=False)
    before = world_layout(spatial)["roads"][0]
    out = apply(data, scen, "remove_positions")
    new = out["data"]
    assert all(k not in c for c in new["cities"] for k in ("x", "y", "radius"))
    assert all("at" not in n for c in new["cities"] for n in c["nodes"])
    assert new["roads"][0]["hours"] == pytest.approx(before["hours"], abs=0.01)
    assert new["roads"][0]["stamina_cost"] == pytest.approx(before["stamina_cost"], abs=0.01) and "length" not in new["roads"][0]
    ev = new["events"][0]
    assert ev["hours"] == pytest.approx(500 / 5.0) and ev["stamina_cost"] == pytest.approx(500 * 0.5) and "length" not in ev
    assert out["notes"] and not Simulation(validate(new, scen)["resolved"], npcs=False).spatial
    assert apply(new, scen, "remove_positions")["added"] == []


# --------------------------------------------------------------- robustness


def test_helpers_keep_everything_they_do_not_edit(scen):
    data = {**base(), "cities": ten_cities(3), "my_notes": {"author": "me"}, "npc": {"merchant_share": 0.2}}
    data["cities"][0]["custom"] = [1, 2, 3]
    data["cities"][1]["nodes"][0]["comment"] = "keep me"
    for action, params in (("arrange_cities", {}), ("fill_resources", {}), ("fill_workplaces", {"count": 1}),
                           ("fill_houses", {"count": 2})):
        out = apply(data, scen, action, **params)["data"]
        assert out["my_notes"] == {"author": "me"} and out["npc"] == {"merchant_share": 0.2}
        assert out["cities"][0]["custom"] == [1, 2, 3] and out["cities"][1]["nodes"][0]["comment"] == "keep me"
        data = out


def test_inherited_lists_are_copied_before_they_are_edited(scen):
    out = apply(base(), scen, "arrange_cities")["data"]  # the cities come from baseline.yaml
    assert [c["name"] for c in out["cities"]] == ["A", "B"] and all("x" in c for c in out["cities"])
    assert "cities" not in base()  # the scenario itself had no list of its own
    ok(out, scen)


def test_unknown_helper_and_bad_params(scen):
    with pytest.raises(L.HelperError, match="unknown helper"):
        apply(base(), scen, "explode")
    with pytest.raises(L.HelperError, match="gap must be"):
        apply(base(), scen, "arrange_cities", gap=-1)
    with pytest.raises(L.HelperError, match="params"):
        L.run_helper(base(), "arrange_cities", [], scen)


# ------------------------------------------------- places first, citizens after (REQ-3.8)


def test_the_world_is_built_in_two_steps_and_the_result_is_the_same_as_building_it_at_once(scen):
    from popsim.components import NPC, Position
    from popsim.config import load_config

    for name, cfg in (("legacy", load_config(SCENARIOS / "arbitrage.yaml")), ("spatial", load_config(SCENARIOS / "spatial.yaml"))):
        for c in cfg["cities"]:
            c["population"] = 15
        at_once = Simulation(cfg)
        in_steps = Simulation(cfg, npcs=False)
        assert in_steps.world.count(NPC) == 0                       # the places exist, nobody lives there yet
        assert in_steps.world.count(Position) > 0 and in_steps.city_ids
        in_steps.populate()
        assert in_steps.world.count(NPC) == at_once.world.count(NPC) == 30, name
        assert in_steps.ledger.balances == at_once.ledger.balances
        assert {e: (p.x, p.y) for e, (p,) in in_steps.world.query(Position)} == {e: (p.x, p.y) for e, (p,) in at_once.world.query(Position)}
        assert in_steps.rng.getstate() == at_once.rng.getstate()
        at_once.run(days=3)
        in_steps.run(days=3)
        assert in_steps.metrics.tick_prices == at_once.metrics.tick_prices and in_steps.ledger.balances == at_once.ledger.balances


def test_citizens_are_put_into_the_finished_world_once(scen):
    from popsim.components import NPC

    sim = Simulation(validate({**base(), "cities": placed(scen, 2)["cities"]}, scen)["resolved"], npcs=False)
    assert sim.world.count(NPC) == 0
    sim.populate()
    assert sim.world.count(NPC) == 10
    with pytest.raises(ValueError, match="already has citizens"):
        sim.populate()


def test_a_sample_of_citizens_exercises_the_citizen_code_without_the_money(scen):
    from popsim.components import NPC, Role

    data = {**base(), "cities": [{**c, "population": 5000} for c in ten_cities(3)]}
    sim = Simulation(validate(data, scen)["resolved"], npcs=False)
    sim.populate(sample=2)
    assert sim.world.count(NPC) == 6                                 # two per city, not 15000
    assert sum(1 for _, (r,) in sim.world.query(Role) if r.kind == "merchant") >= 3   # the merchant code runs too
    assert all(sim.gold(c) == 0 for c in sim.city_ids.values())      # treasuries are minted only for the real population


def test_quick_checks_catch_mistakes_in_the_citizen_settings(scen):
    data = {**base(), "cities": ten_cities(2), "npc": {"gold": {"mean": "lots", "sd": 1, "min": 1}}}
    for kwargs in ({"full": False}, {"full": True}):
        rep = validate(data, scen, **kwargs)
        assert not rep["ok"] and any("world failed to build" in e for e in rep["errors"]), kwargs
    assert validate({**base(), "cities": ten_cities(2)}, scen, full=False)["ok"]
