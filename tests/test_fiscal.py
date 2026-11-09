"""Taxes and public spending (REQ-5.5): income and sales tax, public jobs paid by the treasury,
support payments, per-city policy, conservation, and no effect when nothing is configured."""

import copy

import pytest

from popsim import inspector as I
from popsim.components import NPC, Action, Needs, Position, Record, Workplace
from popsim.config import load_config
from popsim.dashboard.data import validate
from popsim.ledger import GOLD
from popsim.systems.agent import job_options
from popsim.systems.fiscal import FiscalSystem, pay_wage, policy
from popsim.systems.movement import distance, pos
from popsim.world import Simulation

from conftest import SCENARIOS


def cfg_with(fiscal=None, workplaces=None, pop=20, b=None):
    cfg = load_config(SCENARIOS / "baseline.yaml")
    for c in cfg["cities"]:
        c["population"] = pop
    if fiscal is not None:
        cfg["fiscal"] = fiscal
    if workplaces is not None:
        cfg["cities"][0]["workplaces"] = workplaces
    cfg["cities"][1].update(b or {})
    cfg["events"] = []
    return cfg


def reason_total(sim, reason, asset=GOLD):
    return sum(n for _, _, _, a, n, r in sim.ledger.log if a == asset and r == reason)


def test_nothing_configured_means_no_taxes_no_support_no_public_jobs():
    sim = Simulation(cfg_with())
    sim.run(days=3)
    for reason in ("income_tax", "sales_tax", "wage", "support"):
        assert reason_total(sim, reason) == 0
    assert all(r["tax_collected"] == r["wages_paid"] == r["support_paid"] == 0 for r in sim.metrics.daily)


# ----------------------------------------------------------------------- taxes


def test_income_tax_is_a_share_of_sales_proceeds_paid_to_the_treasury():
    sim = Simulation(cfg_with({"income_tax": 0.10}))
    treasury = {c: sim.gold(c) for c in sim.city_ids.values()}
    sim.run(days=4)
    sales = reason_total(sim, "trade")  # gold that changed hands, i.e. sellers' gross proceeds
    tax = reason_total(sim, "income_tax")
    fills = sum(1 for *_, a, n, r in sim.ledger.log if a == GOLD and r == "trade")
    assert sales > 0 and tax > 0
    assert abs(tax - 0.10 * sales) <= 0.5 * fills + 1  # each fill rounds to a whole cent
    assert reason_total(sim, "sales_tax") == 0
    # it ended up in the treasuries, and only there
    gained = sum(sim.gold(c) - treasury[c] for c in sim.city_ids.values())
    assert gained == tax
    assert sim.ledger.verify()["ok"]


def test_sales_tax_is_paid_by_the_buyer_on_each_transaction():
    sim = Simulation(cfg_with({"sales_tax": 0.05}))
    sim.run(days=4)
    value = reason_total(sim, "trade")
    tax = reason_total(sim, "sales_tax")
    assert tax > 0 and reason_total(sim, "income_tax") == 0
    fills = sum(1 for *_, a, n, r in sim.ledger.log if a == GOLD and r == "trade")
    assert abs(tax - 0.05 * value) <= 0.5 * fills + 1
    # the payer is the buyer (an NPC), the receiver a treasury (a city)
    cities = set(sim.city_ids.values())
    assert all(dst in cities and src not in cities for _, src, dst, a, n, r in sim.ledger.log if r == "sales_tax")


def test_each_city_has_its_own_rates_and_collects_locally():
    cfg = cfg_with({"income_tax": 0.05}, b={"fiscal": {"income_tax": 0.30}})
    sim = Simulation(cfg)
    a, b = sim.city_ids["A"], sim.city_ids["B"]
    assert policy(sim, a)["income_tax"] == 0.05 and policy(sim, b)["income_tax"] == 0.30
    sim.run(days=6)
    # tax collected in each city is its own rate times the value traded there
    by_city: dict[str, float] = {}
    for r in sim.metrics.daily_prices:
        if r["vwap"] is not None:
            by_city[r["city"]] = by_city.get(r["city"], 0.0) + r["vwap"] * r["volume"]
    tax = {r["city"]: 0.0 for r in sim.metrics.daily}
    for r in sim.metrics.daily:
        tax[r["city"]] += r["tax_collected"]
    assert tax["A"] == pytest.approx(0.05 * by_city["A"], rel=0.05)
    assert tax["B"] == pytest.approx(0.30 * by_city["B"], rel=0.05)


def test_policy_is_fixed_for_the_whole_run():
    sim = Simulation(cfg_with({"income_tax": 0.1, "sales_tax": 0.02, "support": {"amount": 2.0}}))
    before = {c: copy.deepcopy(policy(sim, c)) for c in sim.city_ids.values()}
    sim.run(days=10)
    assert before == {c: policy(sim, c) for c in sim.city_ids.values()}


# ------------------------------------------------------------------ public jobs

JOB = {"task": "guard", "wage": 8.0, "slots": 3}


def worker_ids(sim):
    return [e for e, (a,) in sim.world.query(Action) if a.kind == "work"]


def test_public_jobs_are_worked_and_paid_from_the_treasury_with_tax_withheld():
    sim = Simulation(cfg_with({"income_tax": 0.10}, workplaces=[JOB]))
    a = sim.city_ids["A"]
    start = sim.gold(a)
    sim.run(days=4)
    wages = reason_total(sim, "wage")
    assert wages > 0, "nobody took the public job"
    unit_wage = round(8.0 * sim.unit * sim.h)
    assert wages % unit_wage == 0  # wage per hour worked, in whole ticks
    # the wage is paid from the city's own treasury to a citizen, and 10% comes straight back as income tax
    cities = set(sim.city_ids.values())
    assert all(src == a and dst not in cities for _, src, dst, asset, n, r in sim.ledger.log if r == "wage")
    wage_tax = sum(n for _, s, d, asset, n, r in sim.ledger.log if r == "income_tax" and d == a)
    assert wage_tax >= round(0.10 * wages) - len(sim.ledger.log)  # includes sales tax on A's market too
    assert sum(r["wages_paid"] for r in sim.metrics.daily if r["city"] == "A") * sim.unit == pytest.approx(wages)
    assert sim.gold(a) < start + wage_tax  # the treasury paid out
    assert sim.ledger.verify()["ok"]


def test_public_job_slots_are_limited():
    cfg = cfg_with({"income_tax": 0.0}, workplaces=[dict(JOB, slots=2)], pop=40)
    sim = Simulation(cfg)
    wid = sim.workplaces_by_city[sim.city_ids["A"]][0]
    peak = 0
    for _ in range(3 * 24):
        sim.step()
        busy = sum(1 for _, (a,) in sim.world.query(Action)
                   if (a.kind == "work" and a.target == wid) or (a.kind == "walk" and a.then and a.then[:2] == ("work", wid)))
        peak = max(peak, busy)
    assert peak == 2


def test_an_empty_treasury_closes_the_public_job():
    sim = Simulation(cfg_with({"income_tax": 0.0}, workplaces=[JOB]))
    a = sim.city_ids["A"]
    wid = sim.workplaces_by_city[a][0]
    eid = next(e for e in sim.world.ids(NPC) if sim.sim_city(e) == a) if hasattr(sim, "sim_city") else sim.npcs_in(a)[0]
    # drain the treasury
    sim.ledger.transfer(a, "nature", GOLD, sim.gold(a), reason="test")
    assert pay_wage(sim, eid, wid) == -1
    assert all(o[1] != wid for o in job_options(sim, eid, a))  # not even offered
    sim.ledger.transfer("authority", a, GOLD, 10**6, reason="test")
    assert any(o[1] == wid for o in job_options(sim, eid, a))


def test_public_jobs_are_taken_only_at_their_position_and_count_the_commute():
    cfg = cfg_with({"income_tax": 0.0}, workplaces=[dict(JOB, at=[3, 0])])
    for c in cfg["cities"]:
        c.update(x=0 if c["name"] == "A" else 30, y=0 if c["name"] == "A" else 40, radius=4)
        c["nodes"] = [dict(n, at=[-2, 0]) for n in c["nodes"]]
    sim = Simulation(cfg)
    wid = sim.workplaces_by_city[sim.city_ids["A"]][0]
    for _ in range(3 * 24):
        sim.step()
        for _, (act, p) in sim.world.query(Action, Position):
            if act.kind == "work":
                assert distance((p.x, p.y), pos(sim, act.target)) <= sim.cfg["movement"]["arrival_radius"] + 1e-9
    assert reason_total(sim, "wage") > 0
    # farther away means a lower score, wherever the NPC stands (REQ-3A.7); a fresh world, so no slot is taken
    sim = Simulation(cfg)
    wid = sim.workplaces_by_city[sim.city_ids["A"]][0]
    eid = sim.npcs_in(sim.city_ids["A"])[0]
    sim.world.get(eid, I.Stamina).current = 95.0
    near_pos = pos(sim, wid)
    sim.world.get(eid, Position).x, sim.world.get(eid, Position).y = near_pos
    score_near = next(o[0] for o in job_options(sim, eid, sim.city_ids["A"]) if o[1] == wid)
    sim.world.get(eid, Position).x = -3.5
    score_far = next(o[0] for o in job_options(sim, eid, sim.city_ids["A"]) if o[1] == wid)
    assert score_far <= score_near


def test_income_tax_lowers_the_value_of_a_public_job():
    cfg = cfg_with({"income_tax": 0.0}, workplaces=[JOB])
    taxed = copy.deepcopy(cfg)
    taxed["fiscal"]["income_tax"] = 0.5
    scores = []
    for c in (cfg, taxed):
        sim = Simulation(c)
        a = sim.city_ids["A"]
        wid = sim.workplaces_by_city[a][0]
        eid = sim.npcs_in(a)[0]
        sim.world.get(eid, I.Stamina).current = 95.0
        # same NPC, same gathering alternatives: compare the wage per hour the option reports
        scores.append(next(o[3] for o in job_options(sim, eid, a) if o[1] == wid))
    assert scores[1] == pytest.approx(scores[0] * 0.5)


# ---------------------------------------------------------------------- support


def needy_sim(amount=5.0, pop=10):
    sim = Simulation(cfg_with({"support": {"amount": amount, "hour": 8}}, pop=pop))
    sim.tick = 8  # hour 8 of day 0
    a = sim.city_ids["A"]
    return sim, a, sim.npcs_in(a)


def test_support_goes_to_residents_with_a_survival_need_at_or_below_urgent():
    sim, a, npcs = needy_sim()
    needy, fine = npcs[:3], npcs[3:]
    for e in npcs:
        n = sim.world.get(e, Needs)
        n.levels.update(food=0.9, rest=0.9, shelter=0.9)
    sim.world.get(needy[0], Needs).levels["food"] = sim.world.get(needy[0], Needs).urgent["food"]  # at the threshold
    sim.world.get(needy[1], Needs).levels["rest"] = 0.0
    sim.world.get(needy[2], Needs).levels["shelter"] = sim.world.get(needy[2], Needs).urgent["shelter"] - 0.01
    sim.world.get(fine[0], Needs).levels["wealth"] = 0.0  # a poor purse alone is not a survival need
    gold0 = {e: sim.gold(e) for e in npcs}
    treasury = sim.gold(a)
    FiscalSystem().run(sim)
    assert all(sim.gold(e) - gold0[e] == 500 for e in needy)
    assert all(sim.gold(e) == gold0[e] for e in fine)
    assert sim.gold(a) == treasury - 3 * 500
    assert reason_total(sim, "support") == 1500
    assert sim.metrics._treasury["A"]["support"] == 1500


def test_support_is_shared_when_the_treasury_cannot_pay_everyone_and_only_at_its_hour():
    sim, a, npcs = needy_sim(amount=5.0)
    for e in npcs:
        sim.world.get(e, Needs).levels["food"] = 0.0
    sim.ledger.transfer(a, "nature", GOLD, sim.gold(a) - 1001, reason="test")  # 1001 cents for 10 people
    FiscalSystem().run(sim)
    assert reason_total(sim, "support") == 10 * 100 and sim.gold(a) == 1  # 100 each, the remainder stays
    sim.tick = 9  # not the payment hour
    before = reason_total(sim, "support")
    FiscalSystem().run(sim)
    assert reason_total(sim, "support") == before


def test_support_is_paid_by_the_residents_own_city_only():
    sim, a, _ = needy_sim()
    b = sim.city_ids["B"]
    for e in sim.world.ids(NPC):
        sim.world.get(e, Needs).levels["food"] = 0.0
    tb = sim.gold(b)
    sim.cfg["fiscal"]["support"]["amount"] = 5.0
    sim._policy = {}
    sim.ledger.transfer(a, "nature", GOLD, sim.gold(a), reason="test")  # A has nothing to give
    FiscalSystem().run(sim)
    assert sim.gold(b) < tb  # B paid its own
    assert all(src != a for _, src, dst, asset, n, r in sim.ledger.log if r == "support")


# ---------------------------------------------------- whole run, output, inspector


def full_cfg():
    cfg = cfg_with({"income_tax": 0.1, "sales_tax": 0.02, "support": {"amount": 3.0, "hour": 8}},
                   workplaces=[JOB, {"task": "road_building", "wage": 5.0, "slots": 4}])
    return cfg


def test_money_is_conserved_with_taxes_wages_and_support():
    sim = Simulation(full_cfg())
    issued = -sim.ledger.balance("authority", GOLD)
    sim.run(days=8)
    assert sim.ledger.verify()["ok"]
    assert sim.ledger.supply(GOLD) == issued  # nothing created or destroyed
    assert all(c["totals_zero"] and c["goods_balance_ok"] for c in sim.metrics.checks)
    assert all(v["ok"] for v in sim.escrow_matches_market().values())
    for reason in ("income_tax", "sales_tax", "wage"):
        assert reason_total(sim, reason) > 0, reason
    cols = {"tax_collected", "wages_paid", "support_paid"}
    assert cols <= set(sim.metrics.daily[0])
    tax_total = sum(r["tax_collected"] for r in sim.metrics.daily) * sim.unit
    assert tax_total == pytest.approx(reason_total(sim, "income_tax") + reason_total(sim, "sales_tax"))


def test_runs_with_public_finance_are_deterministic():
    a, b = Simulation(full_cfg()), Simulation(full_cfg())
    a.run(days=5)
    b.run(days=5)
    assert a.ledger.balances == b.ledger.balances and a.rng.getstate() == b.rng.getstate()
    assert a.metrics.daily == b.metrics.daily


def test_inspector_shows_work_wages_taxes_and_the_city_policy():
    sim = Simulation(full_cfg())
    sim.run(days=4)
    rec_worker = next(e for e in sim.world.ids(NPC)
                      if any(t["good"] in ("guard", "road_building") for t in sim.world.get(e, Record).tasks))
    d = I.citizen_detail(sim, rec_worker)
    tasks = [t for t in d["history"]["tasks"] if t["good"] in ("guard", "road_building")]
    assert tasks and all(t["est_value"] > 0 and t["units"] == 0 for t in tasks)  # value = wages actually received
    assert d["plan"]["fiscal"]["income_tax"] == 0.1 and d["plan"]["fiscal"]["sales_tax"] == 0.02
    assert d["history"]["fiscal"]["taxes_paid"] > 0
    assert any(o["public"] for o in d["jobs"]["options"]) or any(a["label"] for a in d["history"]["actions"])
    working = next((e for e in sim.world.ids(NPC) if sim.world.get(e, Action).kind == "work"), None)
    for _ in range(48):
        if working is not None:
            break
        sim.step()
        working = next((e for e in sim.world.ids(NPC) if sim.world.get(e, Action).kind == "work"), None)
    assert working is not None
    dw = I.citizen_detail(sim, working)
    assert dw["action"]["label"].startswith("work:") and dw["jobs"]["current"]["public"]
    assert dw["jobs"]["current"]["net_wage_per_hour"] == pytest.approx(dw["jobs"]["current"]["wage_per_hour"] * 0.9)


def test_validation_of_fiscal_settings(tmp_path):
    base = full_cfg()
    assert validate(base, tmp_path)["ok"], validate(base, tmp_path)["errors"]
    for path, value, text in [
        (("fiscal", "income_tax"), 1.5, "fiscal.income_tax"),
        (("fiscal", "sales_tax"), -0.1, "fiscal.sales_tax"),
        (("fiscal", "support", "amount"), -1, "fiscal.support.amount"),
        (("fiscal", "support", "hour"), 30, "fiscal.support.hour"),
        (("fiscal", "public_jobs", "slots"), 0, "fiscal.public_jobs.slots"),
    ]:
        bad = copy.deepcopy(base)
        bad["fiscal"].setdefault("public_jobs", {})
        d = bad
        for k in path[:-1]:
            d = d.setdefault(k, {})
        d[path[-1]] = value
        assert any(text in e for e in validate(bad, tmp_path)["errors"]), path
    bad = copy.deepcopy(base)
    bad["cities"][1]["fiscal"] = {"income_tax": 2}
    assert any("cities[1].fiscal.income_tax" in e for e in validate(bad, tmp_path)["errors"])
    bad = copy.deepcopy(base)
    bad["cities"][0]["workplaces"][0]["slots"] = 0
    assert any("slots" in e for e in validate(bad, tmp_path)["errors"])
    poor = copy.deepcopy(base)
    poor["money"] = {"treasury_share": 0}
    assert any("no gold to pay this wage" in w for w in validate(poor, tmp_path)["warnings"])
