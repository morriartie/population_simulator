import os
import subprocess
import sys
from pathlib import Path

from popsim.io.snapshot import load_snapshot, save_snapshot
from popsim.ledger import GOLD
from popsim.world import Simulation

ROOT = Path(__file__).resolve().parents[1]


def test_conservation_and_escrow(small_cfg):
    sim = Simulation(small_cfg)
    issued = -sim.ledger.balance("authority", GOLD)
    sim.run(days=6)
    rep = sim.ledger.verify()
    assert rep["ok"], rep
    assert sim.ledger.supply(GOLD) == issued  # fixed money supply
    assert all(c["totals_zero"] and c["goods_balance_ok"] for c in sim.metrics.checks)
    esc = sim.escrow_matches_market()
    assert all(v["ok"] for v in esc.values()), esc
    assert sum(r["volume"] for r in sim.metrics.daily_prices) > 0  # markets actually trade


def test_stimulus_moves_gold_without_creating_it(scenario):
    cfg = scenario("inflation", events=[{"day": 1, "type": "stimulus", "city": "all", "per_npc": 10.0}])
    for c in cfg["cities"]:
        c["population"] = 15
    sim = Simulation(cfg)
    supply = sim.ledger.supply(GOLD)
    treasury = sum(sim.gold(cid) for cid in sim.city_ids.values())
    sim.run(days=1.05)
    assert sim.ledger.supply(GOLD) == supply
    assert sim.event_log and sim.event_log[0]["type"] == "stimulus"
    paid = int(round(sim.event_log[0]["total_gold"] * sim.unit))
    assert paid == 15 * 2 * 1000
    assert sum(sim.gold(cid) for cid in sim.city_ids.values()) < treasury


def test_same_seed_same_run_different_seed_different_run(small_cfg):
    a, b = Simulation(small_cfg), Simulation(small_cfg)
    a.run(days=3)
    b.run(days=3)
    assert a.metrics.tick_prices == b.metrics.tick_prices
    assert a.ledger.balances == b.ledger.balances
    cfg2 = dict(small_cfg, seed=small_cfg["seed"] + 1)
    c = Simulation(cfg2)
    c.run(days=3)
    assert c.metrics.tick_prices != a.metrics.tick_prices


def test_deterministic_across_processes_and_hash_seeds():
    code = (
        "import sys, hashlib, json; sys.path.insert(0, 'src');"
        "from popsim.config import load_config; from popsim.world import Simulation;"
        "cfg = load_config('scenarios/arbitrage.yaml');"
        "[c.update(population=12) for c in cfg['cities']];"
        "cfg['events'] = [{'day': 1, 'type': 'add_road', 'a': 'A', 'b': 'B', 'hours': 4, 'stamina_cost': 10}];"
        "s = Simulation(cfg); s.run(days=3);"
        "print(hashlib.sha256(json.dumps(s.metrics.tick_prices).encode()).hexdigest())"
    )
    outs = set()
    for seed in ("0", "123"):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        outs.add(subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                                capture_output=True, text=True, check=True).stdout.strip())
    assert len(outs) == 1


def test_snapshot_resume_is_identical(small_cfg, tmp_path):
    sim = Simulation(small_cfg)
    sim.run(days=2)
    save_snapshot(sim, tmp_path / "s.pkl")
    sim.run(days=2)
    expected = sim.metrics.tick_prices[-48 * len(sim.city_ids) * len(sim.goods):]

    resumed = load_snapshot(tmp_path / "s.pkl")
    assert resumed.tick == 2 * 24
    resumed.run(days=2)
    got = resumed.metrics.tick_prices[-48 * len(resumed.city_ids) * len(resumed.goods):]
    assert got == expected
    assert resumed.ledger.verify()["ok"]


def test_road_lets_merchants_travel_and_trade(scenario):
    cfg = scenario("arbitrage")
    for c in cfg["cities"]:
        c["population"] = 30
    cfg["events"] = [{"day": 1, "type": "add_road", "a": "A", "b": "B", "hours": 6, "stamina_cost": 15}]
    sim = Simulation(cfg)
    sim.run(days=8)
    assert sim.metrics.flows, "merchants should ship goods once a road exists"
    assert any(f["from"] == "A" and f["to"] == "B" and f["good"] == "wood" for f in sim.metrics.flows)
    assert sim.ledger.verify()["ok"]
    assert all(v["ok"] for v in sim.escrow_matches_market().values())
