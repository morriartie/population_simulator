"""Resource nodes on the overworld and houses from a percentage (REQ-3.7, REQ-3A.11)."""

import copy
import math

import pytest

from popsim.components import Branch, House, Location, NPC, Trip
from popsim.config import load_config
from popsim.dashboard import layout as L
from popsim.dashboard.data import validate
from popsim.io.logs import world_layout
from popsim.world import Simulation

from conftest import SCENARIOS

NODE = dict(initial=300, max_amount=400, regen_per_hour=5.0, base_rate=0.7, stamina_cost=5.0)


@pytest.fixture
def scen(tmp_path):
    (tmp_path / "baseline.yaml").write_text((SCENARIOS / "baseline.yaml").read_text())
    return tmp_path


def spatial_cfg(pct=None, days=4, nodes=None):
    cfg = load_config(str(SCENARIOS / "baseline.yaml"))
    cfg["time"]["days"] = days
    cfg["cities"] = [
        {"name": "A", "x": 0, "y": 0, "radius": 3, "population": 20},
        {"name": "B", "x": 30, "y": 0, "radius": 3, "population": 20},
    ]
    cfg["roads"] = [{"a": "A", "b": "B"}]
    cfg["events"] = []
    cfg["nodes"] = nodes if nodes is not None else [
        dict(NODE, good="food", at=[6, 4], branch={"road": ["A", "B"], "t": 0.2}),
        dict(NODE, good="wood", at=[24, -4], branch={"road": ["A", "B"], "t": 0.8}),
    ]
    if pct is not None:
        cfg["housing_pct"] = pct
    return cfg


def data_for(scen, **kw):
    cfg = spatial_cfg(**kw)
    return {"extends": "baseline.yaml", "world_name": "t", "time": cfg["time"], "cities": cfg["cities"],
            "roads": cfg["roads"], "nodes": cfg["nodes"], **({"housing_pct": cfg["housing_pct"]} if "housing_pct" in cfg else {})}


# ----------------------------------------------------------------- housing


@pytest.mark.parametrize("pct,expected", [(80, 16), (100, 20), (120, 24), (0, 0)])
def test_generated_houses_follow_the_percentage(pct, expected):
    sim = Simulation(spatial_cfg(pct=pct), npcs=False)
    assert sim.world.count(House) == 2 * expected


def test_a_city_overrides_the_global_percentage_and_manual_houses_come_on_top():
    cfg = spatial_cfg(pct=100)
    cfg["cities"][1]["housing_pct"] = 50
    cfg["cities"][0]["houses"] = [{"at": [0.5, 0.5]}, {}]
    sim = Simulation(cfg, npcs=False)
    per = {}
    for _, (h,) in sim.world.query(House):
        per[h.city] = per.get(h.city, 0) + 1
    assert per[sim.city_ids["A"]] == 22 and per[sim.city_ids["B"]] == 10


def test_a_scenario_without_housing_pct_has_no_generated_houses():
    cfg = spatial_cfg()
    assert Simulation(cfg, npcs=False).world.count(House) == 0


def test_generated_houses_are_reproducible_and_a_moved_one_keeps_its_place():
    cfg = spatial_cfg(pct=100)
    layout = lambda c: [(h["x"], h["y"]) for h in world_layout(Simulation(c, npcs=False))["houses"]]
    assert layout(cfg) == layout(copy.deepcopy(cfg))
    moved = copy.deepcopy(cfg)
    moved["cities"][0]["house_positions"] = [{"index": 3, "at": [1.0, -1.0]}]
    a, b = layout(cfg), layout(moved)
    assert b[3] == (1.0, -1.0) and [p for i, p in enumerate(b) if i != 3] == [p for i, p in enumerate(a) if i != 3]


def test_more_houses_than_citizens_leave_some_empty_and_fewer_leave_citizens_homeless():
    for pct, homeless in ((80, 4), (120, 0)):
        sim = Simulation(spatial_cfg(pct=pct))
        homes = [sim.world.get(e, __import__("popsim.components", fromlist=["Home"]).Home) for e, _ in sim.world.query(NPC)]
        assert sum(1 for h in homes if h.house is None) == 2 * homeless


def test_validation_of_housing(scen):
    d = data_for(scen, pct=100)
    assert validate(d, scen)["ok"]
    bad = copy.deepcopy(d)
    bad["housing_pct"] = -5
    assert any(i["path"] == "housing_pct" for i in validate(bad, scen)["error_items"])
    bad = copy.deepcopy(d)
    bad["cities"][0]["house_positions"] = [{"index": 20, "at": [0, 0]}]   # only 0-19 are generated
    assert any(i["path"] == "cities[0].house_positions[0].index" for i in validate(bad, scen)["error_items"])


# --------------------------------------------------------- overworld nodes


def test_nodes_lie_outside_cities_with_a_branch_no_shorter_than_the_straight_line():
    sim = Simulation(spatial_cfg(), npcs=False)
    assert len(sim.overworld_nodes) == 2 and not sim.nodes_by_city
    for nid in sim.overworld_nodes:
        br = sim.world.get(nid, Branch)
        assert sim.world.get(nid, Location).city is None
        straight = math.hypot(br.jx - sim.world.get(nid, __import__("popsim.components", fromlist=["Position"]).Position).x,
                              br.jy - sim.world.get(nid, __import__("popsim.components", fromlist=["Position"]).Position).y)
        assert br.length == pytest.approx(straight) and br.hours == pytest.approx(br.length / 5.0)
    br = sim.world.get(sim.overworld_nodes[0], Branch)
    assert (br.jx, br.jy) == pytest.approx((6.0, 0.0))     # t = 0.2 of the 30 km road


def test_a_node_given_only_by_position_branches_from_the_nearest_road():
    cfg = spatial_cfg(nodes=[dict(NODE, good="food", at=[20, 9])])
    sim = Simulation(cfg, npcs=False)
    br = sim.world.get(sim.overworld_nodes[0], Branch)
    assert br.t == pytest.approx(20 / 30)
    cfg = spatial_cfg(nodes=[dict(NODE, good="food", branch={"road": ["A", "B"], "t": 0.5})])  # no position
    sim = Simulation(cfg, npcs=False)
    nid = sim.overworld_nodes[0]
    p = sim.world.get(nid, __import__("popsim.components", fromlist=["Position"]).Position)
    assert math.hypot(p.x - 15, p.y) >= 2.0


def test_validation_rejects_nodes_in_cities_unknown_roads_and_short_branches(scen):
    d = data_for(scen)
    assert validate(d, scen)["ok"]
    for mutate, path in (
        (lambda n: n.update(at=[1, 1]), "nodes[0].at"),                       # inside city A
        (lambda n: n["branch"].update(road=["A", "Z"]), "nodes[0].branch.road"),
        (lambda n: n["branch"].update(t=1.5), "nodes[0].branch.t"),
        (lambda n: n.update(branch_length=1.0), "nodes[0].branch_length"),
    ):
        bad = copy.deepcopy(d)
        mutate(bad["nodes"][0])
        assert any(i["path"] == path for i in validate(bad, scen)["error_items"]), path
    flat = copy.deepcopy(d)
    for c in flat["cities"]:
        del c["x"], c["y"]
    assert any(i["path"] == "nodes" for i in validate(flat, scen)["error_items"])
    noroad = copy.deepcopy(d)
    noroad["roads"] = []
    noroad["nodes"][0].pop("branch")
    assert any("no road to branch from" in i["message"] for i in validate(noroad, scen)["error_items"])


def test_citizens_gather_on_a_round_trip_and_sell_back_in_a_city():
    sim = Simulation(spatial_cfg(days=5))
    seen_away, away_orders = 0, 0
    for _ in range(5 * sim.ticks_per_day):
        sim.step()
        for eid, (trip, loc) in sim.world.query(Trip, Location):
            assert loc.city is None                       # on the roads or at the node: in no city
            seen_away += 1
            orders = sim.world.get(eid, __import__("popsim.components", fromlist=["Orders"]).Orders)
            away_orders += len(orders.open)
    assert seen_away > 0 and away_orders == 0             # nobody trades while away
    assert all(c["totals_zero"] and c["goods_balance_ok"] for c in sim.metrics.checks)


def test_the_trip_costs_the_main_road_part_plus_the_branch_both_ways():
    from popsim.systems.movement import node_access

    sim = Simulation(spatial_cfg(), npcs=False)
    a, b = sim.city_ids["A"], sim.city_ids["B"]
    nid = sim.overworld_nodes[0]            # t = 0.2 on A-B (30 km, 6 h, 15 stamina)
    br = sim.world.get(nid, Branch)
    hours, stamina = node_access(sim, a, nid)
    assert hours == pytest.approx(6.0 * 0.2 + br.hours) and stamina == pytest.approx(15.0 * 0.2 + br.stamina_cost)
    hours_b, stamina_b = node_access(sim, b, nid)
    assert hours_b == pytest.approx(6.0 * 0.8 + br.hours) and stamina_b > stamina


def test_removing_the_main_road_makes_the_node_unreachable_but_keeps_it():
    sim = Simulation(spatial_cfg(), npcs=False)
    a = sim.city_ids["A"]
    assert len(sim.nodes_for(a)) == 2
    for rid, (r,) in list(sim.world.query(__import__("popsim.components", fromlist=["Road"]).Road)):
        sim.world.destroy(rid)
    assert sim.nodes_for(a) == [] and len(sim.overworld_nodes) == 2


def test_overworld_runs_are_deterministic_and_conserve_goods_and_gold():
    def run():
        sim = Simulation(spatial_cfg(pct=100, days=4))
        sim.run()
        return sim

    one, two = run(), run()
    assert one.metrics.daily == two.metrics.daily
    assert one.metrics.checks and all(c["totals_zero"] and c["goods_balance_ok"] for c in one.metrics.checks)
    assert one.escrow_matches_market() == two.escrow_matches_market()


# ----------------------------------------------------------------- helpers


def test_fill_resources_in_a_spatial_scenario_puts_nodes_on_roads(scen):
    d = data_for(scen)
    d["nodes"] = [d["nodes"][0]]                       # food only: wood is missing everywhere
    out = L.run_helper(d, "fill_resources", {}, scen)
    assert [n["good"] for n in out["data"]["nodes"]] == ["food", "wood"]
    assert out["data"]["nodes"][1]["branch"]["road"] == ["A", "B"]
    assert validate(out["data"], scen)["ok"]
    again = L.run_helper(out["data"], "fill_resources", {}, scen)
    assert again["added"] == []


def test_fill_resources_reports_a_city_without_a_road(scen):
    d = data_for(scen)
    d["nodes"] = []
    d["roads"] = []
    out = L.run_helper(d, "fill_resources", {}, scen)
    assert out["added"] == [] and any("no road" in n for n in out["notes"])


def test_scatter_nodes_on_the_overworld(scen):
    d = data_for(scen)
    d["nodes"] = []
    params = dict(kind="node", what="food", count=6, center=[15, 10], radius=3, richness=2)
    out = L.run_helper(d, "scatter", params, scen)
    nodes = out["data"]["nodes"]
    assert len(nodes) == 6 and all(n["branch"]["road"] == ["A", "B"] for n in nodes)
    assert all(math.hypot(n["at"][0] - 15, n["at"][1] - 10) <= 3.01 for n in nodes)
    assert nodes[0]["initial"] == 500
    assert L.run_helper(d, "scatter", params, scen) == out          # reproducible
    assert validate(out["data"], scen)["ok"]
    with pytest.raises(L.HelperError, match="inside cities"):
        L.run_helper(d, "scatter", {**params, "center": [0, 0], "radius": 0.5}, scen)
    d["roads"] = []
    with pytest.raises(L.HelperError, match="no road"):
        L.run_helper(d, "scatter", params, scen)


def test_the_layout_marks_overworld_nodes_and_their_branch(scen):
    from popsim.dashboard.data import preview

    rep = preview(data_for(scen, pct=100), scen)
    assert rep["ok"], rep["errors"]
    nodes = rep["world"]["nodes"]
    assert [n["oi"] for n in nodes] == [0, 1] and all(n["overworld"] and n["city"] is None for n in nodes)
    assert nodes[0]["branch"]["a"] == "A" and nodes[0]["branch"]["t"] == pytest.approx(0.2)
