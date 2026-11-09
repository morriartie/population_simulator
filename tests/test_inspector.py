"""Citizen inspector and records (REQ-1C.5, REQ-1C.6): listing, detail, ledger agreement,
determinism with recording on/off, snapshots, retention."""

import json

import pytest

from popsim import inspector as I
from popsim.components import NPC, Record
from popsim.io.snapshot import load_snapshot, read_world, save_snapshot
from popsim.ledger import GOLD, escrow_account
from popsim.world import Simulation


def _cfg(small_cfg, **inspector):
    cfg = dict(small_cfg)
    cfg["events"] = [{"day": 1, "type": "add_road", "a": "A", "b": "B", "hours": 4, "stamina_cost": 10}]
    if inspector:
        cfg["inspector"] = {"record": True, "buffer": 500, "dead_keep_days": 30, **inspector}
    return cfg


@pytest.fixture(scope="module")
def run6(request):
    from conftest import SCENARIOS
    from popsim.config import load_config

    cfg = load_config(SCENARIOS / "baseline.yaml")
    for c in cfg["cities"]:
        c["population"] = 20
    cfg["events"] = [{"day": 1, "type": "add_road", "a": "A", "b": "B", "hours": 4, "stamina_cost": 10}]
    sim = Simulation(cfg)
    sim.run(days=6)
    return sim


def test_list_returns_exactly_the_citizens_there_and_sorts_filters_pages(run6):
    sim = run6
    for name, cid in sim.city_ids.items():
        want = {eid for eid, (_, loc) in sim.world.query(NPC, I.Location) if loc.city == cid}
        got = I.list_citizens(sim, name, page_size=200)
        assert {r["id"] for r in got["rows"]} == want and got["total"] == len(want)
    assert sum(c["citizens"] for c in I.city_overview(sim)) == sim.world.count(NPC)

    asc = I.list_citizens(sim, "A", sort="gold", order="asc", page_size=200)["rows"]
    assert [r["gold"] for r in asc] == sorted(r["gold"] for r in asc)
    desc = I.list_citizens(sim, "A", sort="gold", order="desc", page_size=200)["rows"]
    assert [r["gold"] for r in desc] == sorted((r["gold"] for r in desc), reverse=True)

    pages = [I.list_citizens(sim, "A", sort="id", page=p, page_size=7) for p in (1, 2, 3, 4)]
    assert pages[0]["pages"] == 3 and pages[0]["total"] == 20
    ids = [r["id"] for p in pages[:3] for r in p["rows"]]
    assert ids == sorted(ids) and len(ids) == 20
    assert pages[3]["page"] == 3  # past the end -> clamped to the last page

    merch = I.list_citizens(sim, "A", role="merchant", page_size=200)["rows"]
    assert merch and all(r["role"] == "merchant" for r in merch)
    mid = sorted(r["gold"] for r in asc)[len(asc) // 2]
    rich = I.list_citizens(sim, "A", gold_min=mid, page_size=200)["rows"]
    assert rich and all(r["gold"] >= mid for r in rich) and len(rich) < len(asc)
    healthy = I.list_citizens(sim, "A", sick=False, page_size=200)["rows"]
    assert all(not r["sick"] for r in healthy)
    one = asc[3]
    assert [r["id"] for r in I.list_citizens(sim, "A", q=one["name"])["rows"]] == [one["id"]]
    # a number matches the exact id, and also any name containing it (A-018)
    hits = [r["id"] for r in I.list_citizens(sim, "A", q=str(one["id"]))["rows"]]
    assert one["id"] in hits and all(str(one["id"]) in r["name"] or r["id"] == one["id"]
                                     for r in I.list_citizens(sim, "A", q=str(one["id"]))["rows"])


def test_list_errors(run6):
    with pytest.raises(I.NotFound):
        I.list_citizens(run6, "Nowhere")
    for bad in ({"sort": "nope"}, {"order": "up"}, {"role": "king"}, {"page": 0}, {"page_size": 9999}):
        with pytest.raises(ValueError):
            I.list_citizens(run6, "A", **bad)
    with pytest.raises(I.NotFound):
        I.citizen_detail(run6, 10**6)
    with pytest.raises(I.NotFound):
        I.citizen_detail(run6, next(iter(run6.city_ids.values())))  # a city is not a citizen


def test_detail_matches_ledger_market_and_needs(run6):
    sim = run6
    locked_total = 0
    for eid, (npc,) in sim.world.query(NPC):
        d = I.citizen_detail(sim, eid)
        json.dumps(d)  # fully serialisable
        assert d["alive"] and d["name"] == npc.name
        assert d["wallet"]["gold"] == sim.gold(eid) / sim.unit
        assert d["wallet"]["inventory"] == {g: sim.inv(eid, g) for g in sim.goods}
        needs = sim.world.get(eid, I.Needs)
        assert {n["need"]: n["level"] for n in d["needs"]} == {k: round(v, 3) for k, v in needs.levels.items()}
        orders = sim.world.get(eid, I.Orders)
        assert len(d["orders"]["open"]) == len(orders.open)
        locked_total += round(d["wallet"]["locked_gold"] * sim.unit)
        assert {m["good"] for m in d["memory"]} >= set(sim.goods)
    escrow = sum(sim.ledger.balance(escrow_account(cid), GOLD) for cid in sim.city_ids.values())
    assert locked_total == escrow


def test_purchase_history_matches_ledger_transfers(run6):
    sim = run6
    spent: dict[int, int] = {}
    earned: dict[int, int] = {}
    bought: dict[tuple[int, str], int] = {}
    for _, src, dst, asset, amount, reason in sim.ledger.log:
        if reason != "trade":
            continue
        if asset == GOLD:
            earned[dst] = earned.get(dst, 0) + amount
        else:
            bought[(dst, asset)] = bought.get((dst, asset), 0) + amount
    checked = 0
    for eid, (_,) in sim.world.query(NPC):
        rec = sim.world.get(eid, Record)
        assert len(rec.fills) < rec.size  # nothing was dropped, so totals can be compared
        h = I.citizen_detail(sim, eid)["history"]
        assert h["recorded"] and h["fills_total"] == len(rec.fills)
        got_qty = {g: t["bought_qty"] for g, t in h["totals"].items() if t["bought_qty"]}
        want_qty = {g: q for (e, g), q in bought.items() if e == eid}
        assert got_qty == want_qty
        got_gold = sum(round(t["sold_value"] * sim.unit) for t in h["totals"].values() if t["sold_value"])
        assert got_gold == earned.get(eid, 0)
        checked += len(got_qty)
    assert checked > 0


def test_recording_and_queries_do_not_change_results(small_cfg):
    cfg_on = _cfg(small_cfg, record=True)
    cfg_off = _cfg(small_cfg, record=False)
    a, b, c = Simulation(cfg_on), Simulation(cfg_off), Simulation(cfg_on)
    assert not b.world.count(Record) and a.world.count(Record) == a.world.count(NPC)
    a.run(days=4)
    b.run(days=4)
    for _ in range(4 * 24):  # same run, with the inspector asked about everyone every tick
        c.step()
        for city in c.city_ids:
            I.list_citizens(c, city, page_size=200)
        I.list_citizens(c, I.TRAVELLING)
        for eid in c.world.ids(NPC):
            I.citizen_detail(c, eid)
    for other in (b, c):
        assert a.ledger.balances == other.ledger.balances
        assert a.rng.getstate() == other.rng.getstate()
        assert a.metrics.tick_prices == other.metrics.tick_prices
        assert a.metrics.daily == other.metrics.daily
        assert a.ledger.log == other.ledger.log


def test_unrecorded_run_shows_current_state_and_marks_history(small_cfg):
    sim = Simulation(_cfg(small_cfg, record=False))
    sim.run(days=1)
    eid = sim.world.ids(NPC)[0]
    d = I.citizen_detail(sim, eid)
    assert d["history"]["recorded"] is False and d["history"]["fills"] == []
    assert d["wallet"]["gold"] == sim.gold(eid) / sim.unit and d["plan"]["goods"]


def test_buffers_stay_within_limits(small_cfg):
    sim = Simulation(_cfg(small_cfg, buffer=5))
    sim.run(days=5)
    for _, (rec,) in sim.world.query(Record):
        assert rec.size == 5
        assert max(len(rec.fills), len(rec.actions), len(rec.tasks), len(rec.daily)) <= 5
    assert any(len(r.fills) == 5 for _, (r,) in sim.world.query(Record))


def test_history_survives_snapshot_and_resume(small_cfg, tmp_path):
    sim = Simulation(_cfg(small_cfg))
    sim.run(days=3)
    save_snapshot(sim, tmp_path / "s.pkl")
    ro = read_world(tmp_path / "s.pkl")  # read-only load, no market
    eid = max(sim.world.ids(NPC), key=lambda e: len(sim.world.get(e, Record).fills))
    before = I.citizen_detail(sim, eid)["history"]
    assert before["fills"] and I.citizen_detail(ro, eid)["history"] == before
    resumed = load_snapshot(tmp_path / "s.pkl")
    resumed.run(days=1)
    sim.run(days=1)
    after = I.citizen_detail(resumed, eid)["history"]
    assert after["fills_total"] >= before["fills_total"]
    assert after == I.citizen_detail(sim, eid)["history"]  # resuming == never stopping


def test_dead_citizen_stays_inspectable_then_is_removed(small_cfg):
    sim = Simulation(_cfg(small_cfg, dead_keep_days=1))
    sim.run(days=2)
    eid = sim.world.ids(NPC)[0]
    sim.kill(eid, "test_cause")
    d = I.citizen_detail(sim, eid)
    assert d["alive"] is False and d["died"]["cause"] == "test_cause" and d["wallet"]["gold"] >= 0
    assert eid not in {r["id"] for c in sim.city_ids for r in I.list_citizens(sim, c, page_size=200)["rows"]}
    sim.run(days=3)
    with pytest.raises(I.NotFound):
        I.citizen_detail(sim, eid)


def test_plan_and_jobs_use_the_decision_functions(run6):
    sim = run6
    rng_state = sim.rng.getstate()
    for eid, (_,) in list(sim.world.query(NPC))[:10]:
        d = I.citizen_detail(sim, eid)
        plan = d["plan"]
        assert plan["savings_target"] > 0 and plan["reservation_wage_per_hour"] > 0
        assert {g["good"] for g in plan["goods"]} == set(sim.goods)
        for g in plan["goods"]:
            assert g["reservation_bid"] > 0 and g["reservation_ask"] > 0
            assert g["intent"]["action"] in ("buy", "sell", "hold")
        assert plan["pending_payments"] == [] and d["jobs"]["contracts"] == []
    assert sim.rng.getstate() == rng_state  # reading never draws from the RNG
