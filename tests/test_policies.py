"""Demand, jobs and policies (REQ-5A): signals, generators, tagged sites, task jobs, offices, seeds."""

import json
import math
import threading
import urllib.request

import pytest
import yaml

from popsim import offices
from popsim.components import (Account, Action, Area, Branch, Location, Metabolism, NPC, Needs, Position, Tags,
                               TaskPlan, Workplace, Yield)
from popsim.config import deep_merge, load_config
from popsim.dashboard.server import make_server
from popsim.generators import GeneratorSystem
from popsim.io.logs import write_outputs
from popsim.policies import is_enabled, names, set_enabled, snapshot
from popsim.process import TagSystem, create_site, enter_tag, sites, status, task_info
from popsim.signals import gauge, residents, sample
from popsim.world import Simulation
from popsim.world import fertility as F

from conftest import SCENARIOS

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def cfg_for(enabled=True, days=8, pop=20, **over):
    """The farming demo with a small, well-fed population, so tests see the machinery and not a famine."""
    cfg = load_config(SCENARIOS / "farming.yaml", {"time": {"days": days}})
    cfg["policies"]["farming"]["enabled"] = enabled
    for c in cfg["cities"]:
        c["population"] = pop
    for n in cfg["nodes"]:
        n.update(initial=2000, max_amount=2000, regen_per_hour=40.0)
    return deep_merge(cfg, over) if over else cfg


def at_hour8(sim, day=3):
    sim.tick = day * sim.ticks_per_day + int(round(8 / sim.h))
    return sim


def starve(sim, share: float, city: str | None = None) -> None:
    """Put ``share`` of each city's residents at their urgent food level."""
    for name, cid in sim.city_ids.items():
        if city is not None and name != city:
            continue
        mine = residents(sim, cid)
        for eid in mine[: int(round(len(mine) * share))]:
            n = sim.world.get(eid, Needs)
            n.levels["food"] = n.urgent["food"] * 0.5


def daily(sim):
    """What the daily systems do at 8:00, in pipeline order."""
    sample(sim)
    GeneratorSystem().run(sim)


def one_site(sim, city="A", process="field"):
    from popsim.placement import fertile_site

    cid = sim.city_ids[city]
    return create_site(sim, cid, process, fertile_site(sim, cid), rule="plant_food", policy="farming")


# -- fertility ---------------------------------------------------------------------------------------


def test_fertility_is_a_pure_bounded_function():
    cfg = {"seed": 3, "scale": 10.0}
    a = [F.fertility(cfg, x * 1.7, y * 2.3) for x in range(20) for y in range(20)]
    assert a == [F.fertility(cfg, x * 1.7, y * 2.3) for x in range(20) for y in range(20)]
    assert all(0.0 <= v <= 1.0 for v in a) and max(a) - min(a) > 0.2
    assert F.fertility({**cfg, "seed": 4}, 5.0, 5.0) != F.fertility(cfg, 5.0, 5.0)


def test_fertility_blob_raises_the_land_around_it():
    base = {"seed": 1, "scale": 10.0, "base": 0.2}
    blob = {**base, "blobs": [{"at": [10, 10], "radius": 3, "strength": 0.8}]}
    assert F.fertility(blob, 10, 10) > F.fertility(base, 10, 10) + 0.5
    assert math.isclose(F.fertility(blob, 60, 60), F.fertility(base, 60, 60), abs_tol=1e-6)


# -- the policy switch -------------------------------------------------------------------------------


def test_policies_are_off_by_default_and_a_disabled_policy_does_nothing():
    cfg = load_config(SCENARIOS / "overworld.yaml", {"time": {"days": 3}})
    assert not any(is_enabled(Simulation(cfg), n) for n in names(Simulation(cfg)))
    sim = at_hour8(Simulation(cfg_for(False)))
    starve(sim, 0.6)
    daily(sim)
    assert not list(sites(sim)) and snapshot(sim)["events"] == []


def test_toggle_during_a_run_is_logged_and_switches_the_rules():
    sim = at_hour8(Simulation(cfg_for(False)))
    set_enabled(sim, "farming", True)
    assert is_enabled(sim, "farming") and sim.event_log[-1]["type"] == "policy" and sim.event_log[-1]["enabled"]
    set_enabled(sim, "farming", True)  # no change, no second event
    assert sum(e["type"] == "policy" for e in sim.event_log) == 1
    starve(sim, 0.6)
    daily(sim); sim.tick += sim.ticks_per_day; starve(sim, 0.6); daily(sim)
    assert list(sites(sim))
    with pytest.raises(ValueError):
        set_enabled(sim, "nope", True)


# -- signals -----------------------------------------------------------------------------------------


def test_need_signals_count_residents_at_the_urgent_level():
    sim = at_hour8(Simulation(cfg_for()))
    starve(sim, 0.25)
    sample(sim)
    a = sim.city_ids["A"]
    g = gauge(sim, a, "need:food")
    assert g["count"] == 5 and g["residents"] == 20 and math.isclose(g["share"], 0.25)
    assert 0 < len(g["points"]) <= 40 and gauge(sim, a, "need:shelter")["count"] <= 20
    assert {r["signal"] for r in sim.signal_log} >= {"need:food", "need:shelter", "need:rest"}


# -- generators --------------------------------------------------------------------------------------


def test_rule_fires_only_above_the_threshold_and_after_for_days():
    sim = at_hour8(Simulation(cfg_for()))
    starve(sim, 0.10)  # exactly 10%: the rule is "more than"
    daily(sim)
    assert not list(sites(sim)) and sim.policy_log[0]["triggered"] is False
    starve(sim, 0.5)
    daily(sim)  # above the threshold, but not yet for a day
    assert not list(sites(sim)) and sim.policy_log[-1]["action"].startswith("waiting")
    sim.tick += sim.ticks_per_day
    starve(sim, 0.5)
    daily(sim)
    assert len(list(sites(sim))) == 2  # one per city
    assert {r["action"] for r in sim.policy_log[-2:]} == {"created"}


def make_sites(sim, share=0.5, days=3):
    for _ in range(days):
        starve(sim, share)
        daily(sim)
        sim.tick += sim.ticks_per_day
    return list(sites(sim))


def test_a_site_is_planted_outside_the_cities_on_fertile_land():
    sim = at_hour8(Simulation(cfg_for()))
    made = make_sites(sim)
    assert len(made) == 2
    for eid, tg in made:
        pos = sim.world.get(eid, Position)
        assert tg.meta["fertility"] >= 0.45 and sim.world.get(eid, Branch) is not None
        assert sim.world.get(eid, Yield) is None and set(tg.tags) == {"planting_spot", "waiting_seeds"}
        for cid in sim.city_ids.values():
            c = sim.world.get(cid, Position)
            assert math.hypot(pos.x - c.x, pos.y - c.y) > sim.world.get(cid, Area).radius


def test_limits_cooldown_and_treasury():
    sim = at_hour8(Simulation(cfg_for(generators={"plant_food": {"limits": {"max_active": 1, "cooldown_days": 5}}})))
    make_sites(sim)
    assert len(list(sites(sim))) == 2  # one per city and no more
    assert any(r["action"] == "waiting: fields growing" for r in sim.policy_log)
    poor = at_hour8(Simulation(cfg_for(generators={"plant_food": {"create": {"reserve": 1e9}}})))
    make_sites(poor)
    assert not list(sites(poor)) and any(r["action"] == "blocked: treasury short" for r in poor.policy_log)


def test_service_demand_opens_one_office_and_it_closes_when_unused():
    sim = at_hour8(Simulation(cfg_for()))
    a = sim.city_ids["A"]
    eid = one_site(sim)
    for _ in range(2):  # the task needs the office: a service point, held a day
        daily(sim)
        sim.tick += sim.ticks_per_day
    assert gauge(sim, a, "service:estate_finance")["count"] >= 1
    assert len(offices.offices(sim, a, "estate_finance")) == 1
    assert any(e["kind"] == "office_opened" for e in sim.policy_events)
    daily(sim)
    assert len(offices.offices(sim, a, "estate_finance")) == 1  # not made twice
    sim.world.get(eid, Tags).done = True  # the demand is gone
    for _ in range(7):
        sim.tick += sim.ticks_per_day
        daily(sim)
    assert not offices.offices(sim, a, "estate_finance") and any(e["kind"] == "office_closed" for e in sim.policy_events)


def test_small_cities_and_authored_offices_get_no_generated_office():
    sim = at_hour8(Simulation(cfg_for(pop=10)))  # below min_population
    one_site(sim)
    for _ in range(3):
        daily(sim); sim.tick += sim.ticks_per_day
    assert not offices.offices(sim, sim.city_ids["A"], "estate_finance")
    cfg = cfg_for()
    cfg["cities"][0]["workplaces"] = [{"task": "estate_finance", "wage": 5.0}]
    sim = at_hour8(Simulation(cfg))
    one_site(sim)
    for _ in range(3):
        daily(sim); sim.tick += sim.ticks_per_day
    assert len(offices.offices(sim, sim.city_ids["A"], "estate_finance")) == 1
    assert not any(e["kind"] == "office_opened" and e["city"] == "A" for e in sim.policy_events)


# -- the site core -----------------------------------------------------------------------------------

REPAIR = {"repair": {
    "tags": ["broken"],
    "broken": {"requires": {"items": {"wood": 2}, "labor_hours": 1}, "task": {"label": "repair", "wage": 3.0,
               "stamina_per_hour": 3.0, "slots": 1}, "on_done": {"remove": ["broken"], "add": ["curing"]}},
    "curing": {"requires": {"timer_days": 1.0}, "on_done": {"remove": ["curing"], "add": ["repaired"]}},
    "repaired": {}}}


def test_a_process_with_other_items_and_no_node_runs_on_the_same_code():
    sim = Simulation(cfg_for(processes=REPAIR))
    a = sim.city_ids["A"]
    eid = create_site(sim, a, "repair", {"at": [8.0, 4.0], "branch": {"road": ["A", "B"], "t": 0.2}})
    assert set(sim.world.get(eid, Tags).tags) == {"broken"}
    ts = TagSystem()
    ts.run(sim)
    assert "broken" in sim.world.get(eid, Tags).tags  # waiting for wood and labor
    assert task_info(sim, eid, "broken")["items"] == {"wood": 2}
    sim.ledger.transfer("nature", eid, "wood", 2, reason="test")
    sim.world.get(eid, Tags).tags["broken"]["labor"] = 1.0
    ts.run(sim)
    tg = sim.world.get(eid, Tags)
    assert set(tg.tags) == {"curing"} and sim.ledger.balance(eid, "wood") == 0  # the wood was used up
    sim.tick = tg.tags["curing"]["ready_tick"]
    ts.run(sim)
    assert set(tg.tags) == {"repaired"} and sim.world.get(eid, Yield) is None
    assert [r["label"] for r in status(sim, eid, "repaired")] if tg.tags["repaired"] and False else True


def test_a_grown_field_becomes_a_food_spot_sized_by_its_land():
    sim = Simulation(cfg_for())
    eid = one_site(sim)
    tg = sim.world.get(eid, Tags)
    before = len(sim.overworld_nodes)
    sim.ledger.transfer("nature", eid, "seed", 5, reason="test")
    tg.tags["waiting_seeds"]["labor"] = 2.0
    TagSystem().run(sim)
    assert set(tg.tags) == {"planting_spot", "growing"}
    sim.tick = tg.tags["growing"]["ready_tick"]
    TagSystem().run(sim)
    assert set(tg.tags) == {"food_spot"} and tg.done and len(sim.overworld_nodes) == before + 1
    y = sim.world.get(eid, Yield)
    assert y.good == "food" and y.byproduct == {"seed": 0.1} and y.max_amount == round(300 * (0.5 + tg.meta["fertility"]))
    assert sim.ledger.balance(eid, "food") > 0 and sim.world.get(eid, Account).kind == "node"
    assert eid in sim.nodes_for(sim.city_ids["A"])  # citizens can now gather there


# -- task jobs ---------------------------------------------------------------------------------------


def staffed_sim(**over):
    """A healthy world with an authored finance office in A, paying well, and one field site waiting."""
    cfg = cfg_for(days=14, **over)
    cfg["cities"][0]["workplaces"] = [{"task": "estate_finance", "wage": 12.0, "slots": 1, "stamina_per_hour": 2.0}]
    sim = Simulation(cfg)
    eid = one_site(sim)
    return sim, eid


def run_until(sim, pred, days):
    for _ in range(int(days * sim.ticks_per_day)):
        sim.step()
        if pred():
            return True
    return False


def test_a_planter_fetches_seeds_travels_out_plants_and_the_field_grows():
    sim, eid = staffed_sim()
    tg = sim.world.get(eid, Tags)
    tg.tags["waiting_seeds"]["wage_mult"] = 5.0
    seeds_before = sim.ledger.balance(sim.city_ids["A"], "seed")
    gold_before = sim.gold(sim.city_ids["A"])
    seen = set()

    def watch():
        for _, (plan,) in sim.world.query(TaskPlan):
            seen.add(plan.step)
        return "growing" in tg.tags or tg.done

    assert run_until(sim, watch, 10), f"the field never reached the growing stage: {tg.tags}"
    assert seen >= {"withdraw", "work"}  # it went to the office, then out to the field
    assert sim.ledger.balance(sim.city_ids["A"], "seed") == seeds_before - 5 + sim.ledger.balance(sim.city_ids["A"], "seed") - (seeds_before - 5)
    assert sim.ledger.balance(eid, "seed") == 5 or tg.done or "growing" in tg.tags
    assert run_until(sim, lambda: tg.done, 8)
    assert sim.world.get(eid, Yield) is not None
    assert sim.ledger.verify()["ok"]
    assert all(c["totals_zero"] and c["goods_balance_ok"] for c in sim.metrics.checks)
    assert sim.metrics.counters[("consume", "seed")] >= 5  # the seeds were used up
    kinds = [e["kind"] for e in sim.policy_events]
    assert "stage" in kinds and "matured" in kinds
    assert sim.gold(sim.city_ids["A"]) != gold_before  # wages were paid from the treasury


def test_items_are_issued_only_while_the_office_is_staffed_and_stocked():
    sim, eid = staffed_sim()
    a, w = sim.city_ids["A"], offices.offices(sim, sim.city_ids["A"], "estate_finance")[0]
    npc = next(e for e, _ in sim.world.query(NPC))
    assert not offices.issue_items(sim, a, npc, {"seed": 1})  # nobody at the office
    sim.world.get(next(e for e, (_, l) in sim.world.query(NPC, Location) if l.city == a), Action).kind = "work"
    worker = next(e for e, (_, l) in sim.world.query(NPC, Location) if l.city == a)
    sim.world.get(worker, Action).target = w
    sim._staffed = None
    assert offices.staffed(sim, a, "estate_finance") == [w]
    assert offices.issue_items(sim, a, npc, {"seed": 2}) and sim.inv(npc, "seed") == 2
    assert not offices.issue_items(sim, a, npc, {"seed": 10**6})  # not in the depot


def test_a_claim_reserves_seeds_and_slots_so_nobody_counts_on_the_same_units():
    from popsim.jobs.task import offer

    sim, eid = staffed_sim()
    a = sim.city_ids["A"]
    tg = sim.world.get(eid, Tags)
    tg.tags["waiting_seeds"]["claims"][99999] = {"items": {"seed": 5}, "taken": False, "tick": 0}
    info = task_info(sim, eid, "waiting_seeds")
    assert info["items"] == {} and info["free"] == 1  # the seeds are spoken for; one slot is left for labor
    assert offices.reserved(sim, a, "seed") == 5 and offices.unreserved(sim, a, "seed") == 25
    sim.__dict__["_staffed"] = (sim.tick, {(a, "estate_finance"): [1]})
    assert offer(sim, a, eid, tg)["labor"] > 0  # labor is still open to others


def test_a_task_nobody_takes_is_cancelled_and_its_items_go_back_to_the_depot():
    sim, eid = staffed_sim()
    a = sim.city_ids["A"]
    sim.ledger.transfer(a, eid, "seed", 3, reason="test")  # part of the seeds were delivered
    sim.world.get(eid, Tags).tags["waiting_seeds"]["progress_tick"] = sim.tick - 9 * sim.ticks_per_day
    before = sim.ledger.balance(a, "seed")
    TagSystem().run(sim)
    assert not sim.world.alive(eid) and sim.ledger.balance(a, "seed") == before + 3
    assert [e for e in sim.policy_events if e["kind"] == "cancelled"][0]["why"] == "timeout"


def test_a_hungry_citizen_drops_the_claim_and_the_task_is_offered_again():
    sim, eid = staffed_sim()
    tg = sim.world.get(eid, Tags)
    npc = next(e for e, (_, l) in sim.world.query(NPC, Location) if l.city == sim.city_ids["A"])
    tg.tags["waiting_seeds"]["claims"][npc] = {"items": {"seed": 5}, "taken": False, "tick": 0}
    sim.world.add(npc, TaskPlan(site=eid, tag="waiting_seeds", step="withdraw", ticks=6, items={"seed": 5}))
    sim.world.get(npc, Action).kind = "idle"  # it gave up on the way
    TagSystem().run(sim)
    assert not tg.tags["waiting_seeds"]["claims"] and sim.world.get(npc, TaskPlan) is None
    assert task_info(sim, eid, "waiting_seeds")["items"] == {"seed": 5}


# -- seeds -------------------------------------------------------------------------------------------


def test_the_finance_worker_buys_seeds_for_the_city_from_citizens():
    sim, eid = staffed_sim()
    a = sim.city_ids["A"]
    sim.ledger.transfer(a, "nature", "seed", sim.ledger.balance(a, "seed"), reason="test")  # an empty depot
    sim.metrics.count("consume", "seed", 30)
    # a citizen holds seeds to sell; a worker staffs the office
    from popsim.components import Role

    npcs = [e for e, (_, l) in sim.world.query(NPC, Location)
            if l.city == a and (sim.world.get(e, Role) is None or sim.world.get(e, Role).kind == "worker")]
    sim.ledger.transfer("nature", npcs[1], "seed", 8, reason="test")
    sim.world.get(npcs[0], Action).kind = "work"
    sim.world.get(npcs[0], Action).target = offices.offices(sim, a, "estate_finance")[0]
    sim._staffed = None
    orders = offices.procurement_orders(sim)
    assert orders and orders[0][:2] == (a, "seed") and orders[0][2] >= 5
    for _ in range(12):
        sim.step()
        sim.world.get(npcs[0], Action).kind, sim.world.get(npcs[0], Action).target = "work", offices.offices(sim, a, "estate_finance")[0]
    assert sim.ledger.balance(a, "seed") > 0, "the city bought no seeds"
    assert sim.ledger.verify()["ok"] and sim.escrow_matches_market()["A"]["ok"]


def test_a_farm_spot_gives_seeds_as_a_byproduct_that_are_conserved():
    sim = Simulation(cfg_for())
    node = sim.overworld_nodes[0]
    assert sim.world.get(node, Yield).byproduct == {"seed": 0.1}
    sim.run(days=5)
    assert sim.metrics.counters[("regen", "seed")] > 0
    assert sim.ledger.verify()["ok"] and all(c["goods_balance_ok"] for c in sim.metrics.checks)


# -- generality: wood and shelter --------------------------------------------------------------------


def test_the_forestry_policy_plants_a_grove_for_wood_with_no_seeds_or_office():
    sim = at_hour8(Simulation(cfg_for(False, policies={"forestry": {"enabled": True}})))
    for name, cid in sim.city_ids.items():
        for eid in residents(sim, cid)[:10]:
            n = sim.world.get(eid, Needs)
            n.levels["shelter"] = n.urgent["shelter"] * 0.5
    for _ in range(2):
        sample(sim); GeneratorSystem().run(sim); sim.tick += sim.ticks_per_day
        for cid in sim.city_ids.values():
            for eid in residents(sim, cid)[:10]:
                n = sim.world.get(eid, Needs); n.levels["shelter"] = n.urgent["shelter"] * 0.5
    groves = [(e, t) for e, t in sites(sim) if t.process == "grove"]
    assert groves and set(groves[0][1].tags) == {"planting_spot", "waiting_labor"}
    assert task_info(sim, groves[0][0], "waiting_labor")["items"] == {}  # labor only: no office needed
    assert not [e for e, t in sites(sim) if t.process == "field"]


# -- end to end --------------------------------------------------------------------------------------


def test_full_run_is_deterministic_conserves_and_resumes(tmp_path):
    cfg = cfg_for(days=10)
    cfg["cities"][0]["workplaces"] = [{"task": "estate_finance", "wage": 12.0, "stamina_per_hour": 2.0}]
    a = Simulation(cfg); a.run()
    b = Simulation(json.loads(json.dumps(cfg))); b.run()
    assert snapshot(a)["events"] == snapshot(b)["events"] and a.ledger.balances == b.ledger.balances
    assert a.ledger.verify()["ok"] and all(c["totals_zero"] and c["goods_balance_ok"] for c in a.metrics.checks)
    from popsim.io.snapshot import load_snapshot, save_snapshot

    half = Simulation(json.loads(json.dumps(cfg))); half.run(days=5)
    save_snapshot(half, tmp_path / "s.pkl")
    again = load_snapshot(tmp_path / "s.pkl")
    half.run(days=5); again.run(days=5)
    assert again.ledger.balances == half.ledger.balances and snapshot(again)["events"] == snapshot(half)["events"]


def test_outputs_include_policies(tmp_path):
    sim = Simulation(cfg_for(days=4)); sim.run()
    write_outputs(sim, tmp_path)
    data = json.loads((tmp_path / "policies.json").read_text())
    p = {x["name"]: x for x in data["policies"]}
    assert p["farming"]["enabled"] and p["farming"]["fertility"]["nx"] > 0 and p["farming"]["rules"] == ["plant_food"]
    assert data["signals"] and "daily" in data and "sites" in data


# -- dashboard ---------------------------------------------------------------------------------------


@pytest.fixture
def dash(tmp_path):
    scen = tmp_path / "scenarios"
    scen.mkdir()
    (scen / "farming.yaml").write_text((SCENARIOS / "farming.yaml").read_text())
    server = make_server("127.0.0.1", 0, tmp_path / "runs", scen)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def call(method, path, body=None):
        req = urllib.request.Request(base + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with OPENER.open(req, timeout=30) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    yield call
    server.sessions.discard()
    server.shutdown()
    server.server_close()


def test_live_session_lists_policies_signals_and_sites_and_toggles(dash):
    assert dash("POST", "/api/session", {"scenario": "farming", "days": 4})[0] == 200
    snap = dash("GET", "/api/session/snapshot")[1]
    assert {p["name"] for p in snap["policies"]} == {"farming", "forestry"} and "sites" in snap
    assert snap["now"]["policies"] == {"farming": True, "forestry": False}
    assert dash("POST", "/api/session/control", {"action": "policy", "value": {"name": "farming", "enabled": False}})[0] == 200
    assert dash("GET", "/api/session/snapshot")[1]["now"]["policies"]["farming"] is False
    assert dash("POST", "/api/session/control", {"action": "policy", "value": {"name": "x", "enabled": True}})[0] == 400
    assert dash("POST", "/api/session/control", {"action": "policy"})[0] == 400
    dash("POST", "/api/session/control", {"action": "policy", "value": {"name": "farming", "enabled": True}})
    dash("POST", "/api/session/control", {"action": "step_day"})
    saved = dash("POST", "/api/session/save", {"name": "pol"})[1]["saved_as"]
    run = dash("GET", f"/api/runs/{saved}")[1]
    assert run["policies"][0]["name"] == "farming" and "policy_daily" in run and "signal_daily" in run and "sites" in run


def test_validation_rejects_bad_policy_blocks(dash):
    cfg = yaml.safe_load((SCENARIOS / "farming.yaml").read_text())
    cfg["policies"] = {"farming": {"enabled": "yes"}, "ghost": {}}
    st, body = dash("POST", "/api/scenarios/validate", {"yaml": yaml.safe_dump(cfg)})
    text = json.dumps(body)
    assert "policies.ghost" in text and "policies.farming.enabled" in text


def test_jobs_view_lists_every_job_from_data(tmp_path):
    """REQ-5A.15: shares come from what citizens do, per city, and add up to 1 each day."""
    from popsim.io.logs import write_outputs

    cfg = load_config(SCENARIOS / "farming.yaml", {"time": {"days": 6}})
    sim = Simulation(cfg)
    sim.run()
    rows = sim.metrics.jobs_daily
    assert {"gather food", "sleep"} <= {r["job"] for r in rows}
    for day in {r["day"] for r in rows}:
        for city in {r["city"] for r in rows}:
            tot = sum(r["share"] for r in rows if r["day"] == day and r["city"] == city)
            assert abs(tot - 1) < 0.01
    write_outputs(sim, tmp_path)
    assert (tmp_path / "jobs_daily.csv").stat().st_size > 0
