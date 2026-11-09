"""Positions and movement (REQ-3A): roads from distance, walking, jobs at positions,
commute in job choice, houses and the home rest bonus, and compatibility with scenarios
that have no coordinates."""

import copy
import hashlib
import json
import math

import pytest

from popsim import inspector as I
from popsim.components import NPC, Action, Home, House, Position, Road, Stamina, Health, Needs, Traits
from popsim.config import load_config
from popsim.dashboard.data import validate
from popsim.io.snapshot import load_snapshot, save_snapshot
from popsim.systems.agent import job_options
from popsim.systems.basic import PhysiologySystem
from popsim.systems.decision import DecisionSystem
from popsim.systems.market import MarketSystem
from popsim.systems.movement import cfg as mcfg, distance, pos, start_walk, walk_stamina, walk_ticks
from popsim.world import Simulation

from conftest import SCENARIOS

NODE = {"initial": 300, "max_amount": 400, "regen_per_hour": 5.0, "base_rate": 0.7, "stamina_cost": 5.0}


def spatial_cfg(**over):
    cfg = load_config(SCENARIOS / "baseline.yaml")
    cfg["cities"] = [
        {"name": "A", "x": 0, "y": 0, "radius": 3, "population": 12,
         "nodes": [dict(NODE, good="food", at=[2.5, 0]), dict(NODE, good="wood", at=[-2.5, 0])]},
        {"name": "B", "x": 30, "y": 40, "radius": 3, "population": 12,
         "nodes": [dict(NODE, good="food", at=[1, 1]), dict(NODE, good="wood", at=[-1, -1])]},
    ]
    cfg["roads"], cfg["events"] = [], []
    cfg.update(over)
    return cfg


def legacy_cfg(pop=15):
    cfg = load_config(SCENARIOS / "baseline.yaml")
    for c in cfg["cities"]:
        c["population"] = pop
    cfg["events"] = [{"day": 1, "type": "add_road", "a": "A", "b": "B", "hours": 4, "stamina_cost": 10}]
    return cfg


# ---------------------------------------------------------- compatibility (REQ-3A.9)


def test_scenario_without_coordinates_gives_the_results_of_the_version_before_positions():
    sim = Simulation(legacy_cfg())
    sim.run(days=5)
    assert not sim.spatial
    blob = json.dumps([sim.metrics.tick_prices, sim.metrics.daily_prices, sim.metrics.flows, sim.event_log,
                       sorted((str(k), sorted(v.items())) for k, v in sim.ledger.balances.items())],
                      sort_keys=True, default=str)
    # fingerprint taken from the version before positions existed (its arbitrage run was
    # byte-identical to this code's); a change here means old scenarios no longer reproduce
    assert hashlib.sha256(blob.encode()).hexdigest() == "9f64571f7603bd855b0e7fe074c490fcc757a5e46bdcff265622f6ec5443fc38"
    assert all(r["walk_hours"] == 0 for r in sim.metrics.daily)


def test_generated_positions_do_not_touch_the_simulation_rng():
    with_xy = spatial_cfg()
    for c in with_xy["cities"]:
        for n in c["nodes"]:
            n.pop("at")  # generated from the positions stream
    without = copy.deepcopy(with_xy)
    for c in without["cities"]:
        del c["x"], c["y"], c["radius"]
    a, b = Simulation(with_xy), Simulation(without)
    assert a.spatial and not b.spatial
    assert a.rng.getstate() == b.rng.getstate()
    assert Simulation(with_xy).world.stores[Position].keys() == a.world.stores[Position].keys()
    pa = {k: (v.x, v.y) for k, v in a.world.stores[Position].items()}
    pb = {k: (v.x, v.y) for k, v in Simulation(with_xy).world.stores[Position].items()}
    assert pa == pb  # generated positions are reproducible
    for nid, (_, ) in a.world.query(I.Yield):  # and inside the city
        city = a.world.get(nid, I.Location).city
        c = a.world.get(city, Position)
        assert distance((c.x, c.y), pos(a, nid)) <= a.world.get(city, I.Area).radius + 1e-9


# ------------------------------------------------------------------ roads (REQ-3A.2)


def road(sim):
    return next(r for _, (r,) in sim.world.query(Road))


def test_road_values_follow_distance_and_explicit_values_override():
    cfg = spatial_cfg(roads=[{"a": "A", "b": "B"}])
    sim = Simulation(cfg)
    m = mcfg(sim)["road"]
    r = road(sim)
    assert r.length == pytest.approx(50.0)  # 3-4-5 triangle, times 10
    assert r.hours == pytest.approx(50.0 / m["speed"]) and r.stamina_cost == pytest.approx(50.0 * m["stamina_per_unit"])
    r2 = road(Simulation(spatial_cfg(roads=[{"a": "A", "b": "B", "length": 80, "hours": 7}])))
    assert (r2.length, r2.hours) == (80.0, 7.0) and r2.stamina_cost == pytest.approx(80.0 * m["stamina_per_unit"])
    with pytest.raises(ValueError, match="below the straight-line"):
        Simulation(spatial_cfg(roads=[{"a": "A", "b": "B", "length": 40}]))
    # events derive the same way, and log the derived values
    sim = Simulation(spatial_cfg(events=[{"day": 0, "type": "add_road", "a": "A", "b": "B"}]))
    sim.run(days=1)
    assert sim.event_log[0]["length"] == pytest.approx(50.0) and sim.event_log[0]["hours"] == pytest.approx(10.0)


def test_roads_without_coordinates_still_need_hours_and_stamina():
    cfg = legacy_cfg()
    cfg["roads"] = [{"a": "A", "b": "B"}]
    with pytest.raises(ValueError, match="hours and stamina_cost"):
        Simulation(cfg)


def test_validation_of_coordinates_and_roads(tmp_path):
    base = spatial_cfg()
    ok = validate(base, tmp_path)
    assert ok["ok"], ok["errors"]
    mixed = copy.deepcopy(base)
    del mixed["cities"][1]["x"], mixed["cities"][1]["y"]
    assert any("every city has x and y or none" in e for e in validate(mixed, tmp_path)["errors"])
    outside = copy.deepcopy(base)
    outside["cities"][0]["nodes"][0]["at"] = [9, 9]
    assert any("outside the city radius" in e for e in validate(outside, tmp_path)["errors"])
    shortroad = copy.deepcopy(base)
    shortroad["roads"] = [{"a": "A", "b": "B", "length": 10}]
    assert any("below the straight-line distance" in e for e in validate(shortroad, tmp_path)["errors"])
    derived = copy.deepcopy(base)
    derived["roads"] = [{"a": "A", "b": "B"}]
    derived["events"] = [{"day": 1, "type": "add_road", "a": "A", "b": "B"}]
    assert validate(derived, tmp_path)["ok"]
    legacy = legacy_cfg()
    legacy["roads"] = [{"a": "A", "b": "B"}]
    assert any("roads[0].hours" in e for e in validate(legacy, tmp_path)["errors"])
    at_no_xy = copy.deepcopy(legacy)
    at_no_xy["roads"] = []
    at_no_xy["cities"][0]["nodes"][0]["at"] = [1, 1]
    assert any("give the city x and y first" in e for e in validate(at_no_xy, tmp_path)["errors"])
    same = copy.deepcopy(base)
    same["cities"][1].update(x=0, y=0)
    assert any("same position" in e for e in validate(same, tmp_path)["errors"])


# ----------------------------------------------------------- walking (REQ-3A.5, 3A.6)


def pick(sim, n=0):
    return sim.world.ids(NPC)[n]


def test_walking_time_and_stamina_follow_distance():
    sim = Simulation(spatial_cfg())
    eid = pick(sim)
    node = sim.nodes_by_city[sim.city_ids["A"]][0]
    sim.world.get(eid, Position).x, sim.world.get(eid, Position).y = -2.0, 0.0  # 4.5 units from the node
    st, act = sim.world.get(eid, Stamina), sim.world.get(eid, Action)
    st.current = 90.0
    dist = distance(pos(sim, eid), pos(sim, node))
    assert dist == pytest.approx(4.5)
    start_walk(sim, eid, act, node, ("gather", node, 4))
    m = mcfg(sim)["walk"]
    assert act.ticks_left == math.ceil(dist / m["speed"] / sim.h) == walk_ticks(sim, dist)
    start = st.current
    from popsim.systems.action import walk_tick

    for _ in range(act.walk_total):
        assert act.kind == "walk"
        walk_tick(sim, eid, act, st)
    assert start - st.current == pytest.approx(dist * m["stamina_per_unit"])
    assert pos(sim, eid) == pos(sim, node) and act.kind == "gather" and act.ticks_left == 4  # arrived: the job starts


def test_a_job_starts_only_after_arrival():
    sim = Simulation(spatial_cfg(events=[]))
    for _ in range(4 * 24):
        sim.step()
        for eid, (act, p) in sim.world.query(Action, Position):
            if act.kind == "gather":
                assert distance((p.x, p.y), pos(sim, act.target)) <= mcfg(sim)["arrival_radius"] + 1e-9, \
                    "an NPC gathers away from its node"
    # the scenario really exercises it: histories show walks that lead straight into work
    walks_into_work = 0
    for _, (rec,) in sim.world.query(I.Record):
        acts = list(rec.actions)
        walks_into_work += sum(1 for a, b in zip(acts, acts[1:]) if a["kind"] == "walk" and b["kind"] == "gather")
    assert walks_into_work > 0


def test_a_walk_can_be_interrupted_and_leaves_the_npc_where_it_is():
    sim = Simulation(spatial_cfg())
    eid = pick(sim)
    node = sim.nodes_by_city[sim.city_ids["A"]][0]
    st, act = sim.world.get(eid, Stamina), sim.world.get(eid, Action)
    sim.world.get(eid, Position).x = -2.0
    st.current = 90.0
    start_walk(sim, eid, act, node, ("gather", node, 4))
    st.current = 0.0  # too tired to go on
    from popsim.systems.action import walk_tick

    before = pos(sim, eid)
    walk_tick(sim, eid, act, st)
    assert act.kind == "idle" and act.then is None and pos(sim, eid) == before


def test_orders_can_be_placed_while_walking_and_from_anywhere_in_the_city():
    sim = Simulation(spatial_cfg())
    eid = pick(sim)
    sim.ledger.transfer(eid, "nature", "food", sim.inv(eid, "food"), reason="test")  # now short of food
    node = sim.nodes_by_city[sim.city_ids["A"]][1]
    act = sim.world.get(eid, Action)
    sim.world.get(eid, Position).x = -2.9
    sim.world.get(eid, Stamina).current = 90.0
    start_walk(sim, eid, act, node, ("gather", node, 4))
    assert act.kind == "walk"
    MarketSystem().run(sim)
    orders = sim.world.get(eid, I.Orders).open
    assert any(o.side == "buy" and o.good == "food" for o in orders)


def test_walking_is_the_same_for_everyone():
    sim = Simulation(spatial_cfg())
    a, b, c = (pick(sim, i) for i in range(3))
    node = sim.nodes_by_city[sim.city_ids["A"]][0]
    sim.world.get(b, Health).sick = True
    sim.world.get(c, I.Demographics).age_days = 70 * 365
    res = []
    for eid in (a, b, c):
        sim.world.get(eid, Position).x, sim.world.get(eid, Position).y = -2.0, 0.0
        act = sim.world.get(eid, Action)
        start_walk(sim, eid, act, node, None)
        res.append((act.walk_total, round(act.walk_stamina, 9)))
    assert res[0] == res[1] == res[2]


# --------------------------------------------------- commute in job choice (REQ-3A.7)


def one_node_pair():
    """One NPC and two otherwise identical food nodes: one underfoot, one far away."""
    cfg = spatial_cfg()
    cfg["cities"][0]["nodes"] = [dict(NODE, good="food", at=[0, 0]), dict(NODE, good="food", at=[2.8, 0])]
    cfg["decision"]["job_weights"] = {"income": 1.0, "preference": 0.0, "skill": 0.0}
    sim = Simulation(cfg)
    eid = next(e for e in sim.world.ids(NPC) if sim.world.get(e, I.Location).city == sim.city_ids["A"])
    sim.world.get(eid, Traits).job_preference = {g: 0.0 for g in sim.goods}
    sim.world.get(eid, Stamina).current = 95.0
    near, far = sim.nodes_by_city[sim.city_ids["A"]]
    p = sim.world.get(eid, Position)
    p.x, p.y = pos(sim, near)
    return sim, eid, near, far


def test_walk_counts_both_ways_for_income_per_stamina_but_not_for_income_per_hour():
    sim, eid, near, far = one_node_pair()
    opts = {nid: (score, per_hour) for score, nid, _, per_hour in job_options(sim, eid, sim.city_ids["A"])}
    # same pay per hour: the commute is never added to the working time
    assert opts[near][1] == pytest.approx(opts[far][1])
    # but the farther one is worth less per stamina, by exactly shift / (shift + 2 * walk)
    work = I.job_options  # noqa: F841  (kept for readability: formula below mirrors REQ-3A.7)
    from popsim.systems.agent import work_stamina_per_tick

    y = sim.world.get(far, I.Yield)
    shift = work_stamina_per_tick(sim, eid, y) * sim.hours_to_ticks(sim.cfg["decision"]["shift_hours"])
    walk = walk_stamina(sim, distance(pos(sim, eid), pos(sim, far)))
    assert opts[near][0] == pytest.approx(1.0)
    assert opts[far][0] == pytest.approx(shift / (shift + 2 * walk))
    assert opts[far][0] < opts[near][0]


def test_a_farther_node_of_equal_pay_is_ranked_below_a_nearer_one_wherever_the_npc_stands():
    sim, eid, near, far = one_node_pair()
    city = sim.city_ids["A"]
    assert job_options(sim, eid, city)[0][1] == near
    p = sim.world.get(eid, Position)
    p.x, p.y = pos(sim, far)  # now the other node is underfoot
    assert job_options(sim, eid, city)[0][1] == far


def test_a_job_that_cannot_be_reached_with_the_stamina_left_is_not_offered():
    sim, eid, near, far = one_node_pair()
    city = sim.city_ids["A"]
    st = sim.world.get(eid, Stamina)
    from popsim.systems.agent import work_stamina_per_tick

    y = sim.world.get(far, I.Yield)
    walk = walk_stamina(sim, distance(pos(sim, eid), pos(sim, far)))
    one_tick = work_stamina_per_tick(sim, eid, y)
    st.current = one_tick + walk - 0.01  # the walk plus one tick of work is just out of reach
    assert [o[1] for o in job_options(sim, eid, city)] == [near]
    st.current = one_tick + walk + 0.01
    assert {o[1] for o in job_options(sim, eid, city)} == {near, far}


# -------------------------------------------------------- houses and rest (REQ-3A.8)


def housed_cfg(houses=4):
    cfg = spatial_cfg()
    cfg["cities"][0]["houses"] = [{"at": [0.5 * i, 0.0]} for i in range(houses)]
    return cfg


def test_houses_are_assigned_and_limited():
    sim = Simulation(housed_cfg(4))
    city = sim.city_ids["A"]
    homes = [sim.world.get(e, Home) for e in sim.world.ids(NPC) if sim.world.get(e, I.Location).city == city]
    assert sum(1 for h in homes if h.house is not None) == 4  # only four houses for twelve people
    for hid, (house,) in sim.world.query(House):
        assert house.occupant is not None and sim.world.get(house.occupant, Home).house == hid
        assert pos(sim, hid) == pos(sim, house.occupant)  # they start at home


def test_resting_at_home_recovers_faster_than_sleeping_outside():
    sim = Simulation(housed_cfg(4))
    city = sim.city_ids["A"]
    members = [e for e in sim.world.ids(NPC) if sim.world.get(e, I.Location).city == city]
    homed, outdoor = members[0], members[-1]
    assert sim.world.get(homed, Home).house is not None and sim.world.get(outdoor, Home).house is None
    for eid in (homed, outdoor):
        st, act, hl = sim.world.get(eid, Stamina), sim.world.get(eid, Action), sim.world.get(eid, Health)
        st.current, st.daily_max, hl.sick = 10.0, 100.0, False
        act.kind, act.ticks_left = "sleep", 5
        sim.world.get(eid, Needs).levels["shelter"] = 0.9
    sim.world.get(outdoor, Position).x = 0.0
    sim.world.get(outdoor, Position).y = 1.0
    PhysiologySystem().run(sim)
    gain = {e: sim.world.get(e, Stamina).current - 10.0 + sim.cfg["physiology"]["stamina"]["awake_drain_per_hour"] * 0
            for e in (homed, outdoor)}
    base = sim.cfg["physiology"]["stamina"]["sleep_restore_per_hour"] * sim.h
    assert gain[outdoor] == pytest.approx(base)
    assert gain[homed] == pytest.approx(base * mcfg(sim)["home_rest_multiplier"])
    # moving the homed NPC away from its house takes the bonus away
    for eid in (homed,):
        sim.world.get(eid, Stamina).current = 10.0
        sim.world.get(eid, Position).x = 2.9
    PhysiologySystem().run(sim)
    assert sim.world.get(homed, Stamina).current - 10.0 == pytest.approx(base)


def test_an_npc_walks_home_only_when_the_bonus_outweighs_the_walk():
    sim = Simulation(housed_cfg(4))
    eid = next(e for e in sim.world.ids(NPC) if sim.world.get(e, Home).house is not None)
    st = sim.world.get(eid, Stamina)
    house = sim.world.get(eid, Home).house
    p = sim.world.get(eid, Position)
    st.daily_max = 100.0
    # tired and close: worth it
    p.x, p.y = pos(sim, house)[0] + 1.0, pos(sim, house)[1]
    st.current = 20.0
    assert DecisionSystem.home_worth_walking(sim, eid, st) == house
    # nearly rested: the extra recovery is smaller than the walk, so it sleeps where it is
    st.current = 98.0
    assert DecisionSystem.home_worth_walking(sim, eid, st) is None
    # tired but very far away
    st.current = 20.0
    p.x = 400.0
    assert DecisionSystem.home_worth_walking(sim, eid, st) is None
    # already home: nothing to walk
    p.x, p.y = pos(sim, house)
    assert DecisionSystem.home_worth_walking(sim, eid, st) is None


def test_a_scenario_without_houses_sleeps_where_it_stands():
    sim = Simulation(spatial_cfg())
    assert not sim.world.count(House)
    assert all(sim.world.get(e, Home).house is None for e in sim.world.ids(NPC))
    sim.run(days=2)
    assert not any(a.kind == "walk" and sim.world.get(a.target, House) for _, (a,) in sim.world.query(Action))


# ------------------------------------------------- snapshots, output, inspector (REQ-3A.10)


def test_positions_survive_snapshot_and_resume(tmp_path):
    sim = Simulation(housed_cfg(4))
    sim.run(days=2)
    save_snapshot(sim, tmp_path / "s.pkl")
    back = load_snapshot(tmp_path / "s.pkl")
    assert {e: pos(sim, e) for e in sim.world.ids(NPC)} == {e: pos(back, e) for e in back.world.ids(NPC)}
    sim.run(days=1)
    back.run(days=1)
    assert {e: pos(sim, e) for e in sim.world.ids(NPC)} == {e: pos(back, e) for e in back.world.ids(NPC)}
    assert sim.ledger.balances == back.ledger.balances


def test_world_layout_output_and_walk_columns(tmp_path):
    from popsim.io.logs import read_csv, write_outputs

    cfg = spatial_cfg(roads=[{"a": "A", "b": "B"}])
    cfg["cities"][0]["houses"] = {"count": 3}
    cfg["cities"][0]["workplaces"] = [{"task": "guard", "wage": 1.5, "at": [1, 1]}]
    sim = Simulation(cfg)
    sim.run(days=3)
    write_outputs(sim, tmp_path)
    world = json.loads((tmp_path / "world.json").read_text())
    assert world["spatial"] and {c["name"] for c in world["cities"]} == {"A", "B"}
    assert world["roads"][0]["length"] == pytest.approx(50.0) and len(world["houses"]) == 3
    assert world["workplaces"][0]["task"] == "guard" and (world["workplaces"][0]["x"], world["workplaces"][0]["y"]) == (1.0, 1.0)
    assert read_csv(tmp_path / "roads.csv")[0]["length"] == 50.0
    daily = read_csv(tmp_path / "cities_daily.csv")
    assert sum(float(r["walk_hours"]) for r in daily) > 0 and sum(float(r["walk_stamina"]) for r in daily) > 0
    assert sim.ledger.verify()["ok"]  # walking moves no gold or goods


def test_inspector_shows_position_destination_and_commute():
    cfg = housed_cfg(4)
    cfg["cities"][0]["radius"] = 12  # a big city: walks last several hours and can be seen mid-way
    cfg["cities"][0]["nodes"] = [dict(NODE, good="food", at=[10, 0]), dict(NODE, good="wood", at=[-10, 0])]
    sim = Simulation(cfg)
    sim.run(days=1)
    walker = next((e for e in sim.world.ids(NPC) if sim.world.get(e, Action).kind == "walk"), None)
    for _ in range(48):
        if walker is not None:
            break
        sim.step()
        walker = next((e for e in sim.world.ids(NPC) if sim.world.get(e, Action).kind == "walk"), None)
    assert walker is not None
    d = I.citizen_detail(sim, walker)
    json.dumps(d)
    assert d["status"]["position"] is not None and d["status"]["walk"]["destination"] is not None
    assert d["status"]["walk"]["ticks_left"] >= 1 and d["action"]["label"].startswith("walk")
    for o in d["jobs"]["options"]:
        assert o["distance"] >= 0 and o["round_trip_stamina"] == pytest.approx(2 * o["walk_stamina"], abs=0.02)
    m = I.city_map(sim, "A")
    assert m["spatial"] and m["radius"] == 12.0 and len(m["nodes"]) == 2 and len(m["houses"]) == 4
    assert all(c["to"] for c in m["citizens"] if c["walking"])
    with pytest.raises(I.NotFound):
        I.city_map(sim, "Nowhere")
    legacy = Simulation(legacy_cfg(6))
    assert I.citizen_detail(legacy, legacy.world.ids(NPC)[0])["status"]["position"] is None
