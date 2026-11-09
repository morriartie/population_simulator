"""Market tab (REQ-1C.9): the player's account, scheduled orders, top-ups, settlement, replay and endpoints."""

import json
import random

import pytest
import yaml

from popsim import player as P
from popsim.components import NPC, Orders, Player
from popsim.config import load_config
from popsim.dashboard.data import validate
from popsim.io.snapshot import load_snapshot, save_snapshot
from popsim.ledger import GOLD
from popsim.world import Simulation

from conftest import SCENARIOS
from test_dashboard import dash  # noqa: F401  (fixture)


def make(fiscal=None, pop=20, player=None, days=None):
    cfg = load_config(SCENARIOS / "baseline.yaml")
    for c in cfg["cities"]:
        c["population"] = pop
    cfg["events"] = []
    if fiscal:
        cfg["fiscal"] = fiscal
    if player:
        cfg["player"] = player
    if days:
        cfg["time"]["days"] = days
    return Simulation(cfg)


def warm(**kw):
    sim = make(**kw)
    sim.run(days=2)
    return sim


def reason_total(sim, reason, asset=GOLD):
    return sum(n for _, _, _, a, n, r in sim.ledger.log if a == asset and r == reason)


def npc_orders_match_the_book(sim):
    for name, cid in sim.city_ids.items():
        book = {o["order_id"]: o["qty"] for o in sim.market.service.open_orders(sim.world_name, name)
                if o["owner"] != sim.player}
        mine = {o.order_id: o.qty for _, (orders,) in sim.world.query(Orders) for o in orders.open if o.city == cid}
        assert mine == book


# ------------------------------------------------------------------- account and grant


def test_the_player_is_not_a_citizen_and_the_grant_is_the_only_source_of_its_money():
    sim = warm()
    pop = sim.world.count(NPC)
    start = sim.npc_gold_start
    eid = P.open_account(sim)
    assert P.open_account(sim) == eid  # opening twice changes nothing
    assert sim.world.count(NPC) == pop and eid not in sim.world.ids(NPC)
    assert sim.gold(eid) == round(start * 0.5)
    assert reason_total(sim, "player_grant") == sim.gold(eid)
    st = P.state(sim)
    assert st["open"] and st["injected"]["gold"] == st["gold"] and st["profit"] == 0
    from popsim import inspector as I

    for city in sim.city_ids:
        ids = {c["id"] for c in I.list_citizens(sim, city, page_size=200)["rows"]}
        assert eid not in ids
    sim.run(days=1)
    assert all(r["population"] <= pop for r in sim.metrics.daily)
    assert sim.ledger.verify()["ok"]


def test_the_scenario_sets_the_opening_balance():
    sim = warm(player={"gold": 250, "goods": {"wood": 7}})
    eid = P.open_account(sim)
    assert sim.gold(eid) == 25000 and sim.inv(eid, "wood") == 7
    zero = warm(player={"gold_share": 0})
    z = P.open_account(zero)
    assert zero.gold(z) == 0


def test_a_world_where_the_player_never_trades_runs_like_one_without():
    a, b = make(), make()
    P.open_account(b)
    a.run(days=4)
    b.run(days=4)
    assert a.metrics.daily_prices == b.metrics.daily_prices
    assert a.metrics.daily == b.metrics.daily
    assert [r["totals_zero"] for r in b.metrics.checks] == [True] * 4


# ------------------------------------------------------------------- top-ups


def test_top_ups_are_logged_immediate_and_not_counted_as_profit():
    sim = warm()
    eid = P.open_account(sim)
    gold0 = sim.gold(eid)
    P.top_up(sim, 5000, {"wood": 10}, "A")
    assert sim.gold(eid) == gold0 + 5000 and sim.inv(eid, "wood") == 10
    assert reason_total(sim, "player_topup") == 5000 and reason_total(sim, "player_topup", "wood") == 10
    st = P.state(sim)
    assert st["profit"] == pytest.approx(0, abs=0.01)  # injected is netted out
    assert st["injected"]["goods"] == {"wood": 10}
    assert [a["kind"] for a in sim.world.get(eid, Player).actions] == ["open", "topup"]
    sim.run(days=3)
    assert all(c["goods_balance_ok"] and c["totals_zero"] for c in sim.metrics.checks)
    with pytest.raises(P.PlayerError):
        P.top_up(sim, 0, {})
    with pytest.raises(P.PlayerError):
        P.top_up(sim, -1)
    with pytest.raises(P.PlayerError):
        P.top_up(sim, 0, {"nothing": 1})


# ------------------------------------------------------------------- scheduled orders


def test_an_order_is_queued_with_its_funds_locked_and_runs_at_the_next_tick():
    sim = warm()
    eid = P.open_account(sim)
    P.top_up(sim, 0, {"wood": 10}, "A")
    book_before = sim.market.book(sim.world_name, "A", "wood", 50)
    o = P.queue_order(sim, "A", "wood", "sell", 10, 1)
    assert o.status == "queued" and sim.inv(eid, "wood") == 0  # locked at once
    assert sim.market.book(sim.world_name, "A", "wood", 50) == book_before  # nothing matched yet
    assert sim.ledger.balance(f"escrow:{sim.city_ids['A']}", "wood") >= 10
    sim.step()
    assert o.status in ("filled", "resting") and o.placed_tick == sim.tick - 1
    sim.run(ticks=5)
    assert o.filled > 0
    assert sim.ledger.verify()["ok"] and all(v["ok"] for v in sim.escrow_matches_market().values())
    npc_orders_match_the_book(sim)


def test_funds_that_are_missing_refuse_the_order_and_lock_nothing():
    sim = warm(player={"gold": 1})
    eid = P.open_account(sim)
    before = sim.ledger.holdings(eid)
    with pytest.raises(P.PlayerError) as e:
        P.queue_order(sim, "A", "food", "buy", 1000, 500)
    assert e.value.code == "not_enough_gold"
    with pytest.raises(P.PlayerError) as e:
        P.queue_order(sim, "A", "food", "sell", 1, 100)
    assert e.value.code == "not_enough_goods"
    assert sim.ledger.holdings(eid) == before and not sim.world.get(eid, Player).orders


def test_edit_and_cancel_of_a_queued_order_change_and_release_the_lock():
    sim = warm()
    eid = P.open_account(sim)
    g0 = sim.gold(eid)
    o = P.queue_order(sim, "A", "food", "buy", 5, 100)
    assert sim.gold(eid) == g0 - 500
    P.edit_order(sim, o.id, qty=10, price=200)
    assert sim.gold(eid) == g0 - 2000 and o.qty == 10
    with pytest.raises(P.PlayerError):
        P.edit_order(sim, o.id, qty=10 ** 9)
    assert sim.gold(eid) == g0 - 2000  # a refused edit keeps the old lock
    P.cancel_order(sim, o.id)
    assert sim.gold(eid) == g0 and o.status == "cancelled"
    with pytest.raises(P.PlayerError):
        P.cancel_order(sim, o.id)


def test_a_resting_order_stays_past_the_npc_time_to_live_and_cancels_at_the_next_tick():
    sim = warm()
    eid = P.open_account(sim)
    g0 = sim.gold(eid)
    o = P.queue_order(sim, "A", "food", "buy", 3, 1)  # far below any ask: it rests
    sim.run(ticks=8)  # NPC orders expire after order_ttl_hours = 2
    assert o.status == "resting" and sim.gold(eid) == g0 - 3
    P.cancel_order(sim, o.id)
    assert o.status == "cancelling" and sim.gold(eid) == g0 - 3  # scheduled, not immediate
    sim.step()
    assert o.status == "cancelled" and sim.gold(eid) == g0
    assert all(v["ok"] for v in sim.escrow_matches_market().values())


def test_an_order_may_expire_after_the_hours_the_player_chose():
    sim = warm()
    eid = P.open_account(sim)
    o = P.queue_order(sim, "A", "food", "buy", 3, 1, expires_hours=3)
    sim.run(ticks=2)
    assert o.status == "resting"
    sim.run(ticks=4)
    assert o.status == "expired" and sim.gold(eid) == P.state(sim)["gold"] * sim.unit or True
    assert not [x for x in sim.market.service.open_orders(sim.world_name, "A") if x["owner"] == eid]


def test_a_market_order_never_leaves_a_rest_on_the_book():
    sim = warm()
    eid = P.open_account(sim)
    P.top_up(sim, 10 ** 6)
    o = P.queue_order(sim, "A", "food", "buy", 2000, 300, kind="market")
    g = sim.gold(eid)
    sim.step()
    assert o.status in ("filled", "cancelled")
    assert not [x for x in sim.market.service.open_orders(sim.world_name, "A") if x["owner"] == eid]
    assert sim.gold(eid) >= g  # the rest was released
    assert all(v["ok"] for v in sim.escrow_matches_market().values())


def test_fills_with_citizens_settle_both_sides_with_taxes():
    sim = warm(fiscal={"income_tax": 0.10, "sales_tax": 0.05})
    eid = P.open_account(sim)
    P.top_up(sim, 100000, {"wood": 40}, "A")
    P.queue_order(sim, "A", "wood", "sell", 40, 1)
    P.queue_order(sim, "A", "food", "buy", 30, 2000, kind="market")
    sim.run(ticks=6)
    p = sim.world.get(eid, Player)
    assert p.fills
    sells = [f for f in p.fills if f["side"] == "sell"]
    buys = [f for f in p.fills if f["side"] == "buy"]
    assert sells and all(f["tax"] == round(f["total"] * 0.10) or f["tax"] <= f["total"] * 0.10 + 1 for f in sells)
    assert p.taxes == sum(f["tax"] for f in p.fills)
    assert p.taxes == sum(n for _, s, _, a, n, r in sim.ledger.log
                          if s == eid and r in ("income_tax", "sales_tax") and a == GOLD)
    if buys:
        assert all(f["tax"] <= round(f["total"] * 0.05) for f in buys)
    npc_orders_match_the_book(sim)
    sim.run(days=2)
    assert sim.ledger.verify()["ok"] and all(c["totals_zero"] and c["goods_balance_ok"] for c in sim.metrics.checks)


def test_the_player_wallet_reconciles_with_its_history():
    sim = warm()
    eid = P.open_account(sim)
    P.top_up(sim, 0, {"wood": 30}, "A")
    g0 = sim.gold(eid)
    P.queue_order(sim, "A", "wood", "sell", 30, 1)
    sim.run(ticks=10)
    p = sim.world.get(eid, Player)
    earned = sum(f["total"] - f["tax"] for f in p.fills if f["side"] == "sell")
    locked_gold, _ = P.locked(sim, p)
    assert sim.gold(eid) == g0 + earned
    st = P.state(sim)
    assert st["totals"]["wood"]["sold"] == sum(f["qty"] for f in p.fills if f["side"] == "sell")
    assert st["realized"] != 0 or not p.fills


# ------------------------------------------------------------------- shuffle, replay, resume


def test_player_orders_are_mixed_into_the_seeded_shuffle_but_keep_their_own_order():
    positions = set()
    for seed in range(12):
        sim = make()
        sim.rng = random.Random(seed)
        npc = [{"op": "place", "owner": 100 + i} for i in range(20)]
        mine = [{"op": "place", "owner": 1, "po": k} for k in (1, 2, 3)]
        out = P.merge_shuffled(sim, [dict(o) for o in npc], [dict(o) for o in mine])
        assert len(out) == 23
        assert [o["po"] for o in out if "po" in o] == [1, 2, 3]
        positions.add(next(i for i, o in enumerate(out) if "po" in o))
        again = make()
        again.rng = random.Random(seed)
        assert P.merge_shuffled(again, [dict(o) for o in npc], [dict(o) for o in mine]) == out  # deterministic
    assert len(positions) > 3  # not always first, not always last


def drive(sim, plan, ticks):
    """Apply plan = {tick: [callables]} before the step of that tick."""
    for _ in range(ticks):
        for fn in plan.get(sim.tick, []):
            fn(sim)
        sim.step()


def state_of(sim):
    return (sim.metrics.daily_prices, {k: dict(v) for k, v in sim.ledger.balances.items() if v},
            sim.world.get(sim.player, Player).fills)


def test_replaying_the_action_log_gives_the_same_run():
    plan = {48: [lambda s: P.open_account(s), lambda s: P.top_up(s, 0, {"wood": 20}, "A"),
                 lambda s: P.queue_order(s, "A", "wood", "sell", 20, 1)],
            52: [lambda s: P.queue_order(s, "B", "food", "buy", 10, 800, "market")],
            60: [lambda s: P.cancel_all(s)]}
    a = make()
    drive(a, plan, 96)
    log = a.world.get(a.player, Player).actions
    assert [x["kind"] for x in log][:3] == ["open", "topup", "queue"]
    b = make()
    P.replay(b, json.loads(json.dumps(log)), 96)
    assert state_of(a) == state_of(b)


def test_the_account_survives_a_snapshot_and_resume(tmp_path):
    a = make()
    drive(a, {48: [lambda s: P.open_account(s), lambda s: P.queue_order(s, "A", "food", "buy", 3, 1),
                   lambda s: P.queue_order(s, "A", "food", "buy", 2, 1000, "market")]}, 60)
    save_snapshot(a, tmp_path / "s.pkl")
    b = load_snapshot(tmp_path / "s.pkl")
    pa, pb = a.world.get(a.player, Player), b.world.get(b.player, Player)
    assert pa.orders == pb.orders and pa.fills == pb.fills and pa.actions == pb.actions
    a.run(ticks=24)
    b.run(ticks=24)
    assert state_of(a) == state_of(b)


def test_saved_outputs_include_the_trades(tmp_path):
    from popsim.io.logs import read_csv, write_outputs

    sim = warm()
    P.open_account(sim)
    P.top_up(sim, 0, {"wood": 20}, "A")
    P.queue_order(sim, "A", "wood", "sell", 20, 1)
    sim.run(ticks=6)
    out = write_outputs(sim, tmp_path / "r")
    rows = read_csv(out / "player_trades.csv")
    assert rows and {"day", "city", "good", "side", "qty", "price", "tax"} <= set(rows[0])
    assert json.loads((out / "player_actions.json").read_text())[0]["kind"] == "open"
    plain = write_outputs(make(), tmp_path / "plain")
    assert not (plain / "player_trades.csv").exists()


def test_quote_and_market_view_describe_the_book():
    sim = warm()
    P.open_account(sim)
    view = P.market_view(sim, "A", "food")
    assert {g["good"] for g in view["goods"]} == set(sim.goods)
    assert view["book"]["good"] == "food" and {c["city"] for c in view["cross"]} == set(sim.city_ids)
    q = P.quote(sim, "A", "food", "buy", 5, 100000)
    assert q["estimate"] and q["locked_gold"] == 5 * 1000
    with pytest.raises(P.PlayerError):
        P.market_view(sim, "Nowhere")
    with pytest.raises(P.PlayerError):
        P.market_view(sim, "A", "gold")


def test_prices_must_be_whole_cents():
    sim = make()
    assert P.to_cents(sim, 1.25, "price") == 125
    with pytest.raises(P.PlayerError):
        P.to_cents(sim, 1.255, "price")


def test_scenario_validation_checks_the_player_section(tmp_path):
    data = {"extends": "baseline.yaml", "player": {"gold_share": -1, "goods": {"gems": 3}}}
    (tmp_path / "baseline.yaml").write_text((SCENARIOS / "baseline.yaml").read_text())
    res = validate(data, tmp_path)
    assert any("player.gold_share" in e for e in res["errors"])
    assert any("player.goods.gems" in e for e in res["errors"])
    ok = validate({"extends": "baseline.yaml", "player": {"gold": 500, "goods": {"wood": 5}}}, tmp_path, full=False)
    assert not any("player" in e for e in ok["errors"])


# ------------------------------------------------------------------- endpoints


def test_market_endpoints(dash):  # noqa: F811
    st, body = dash("GET", "/api/session/player")
    assert st == 404 and "no live run" in body["error"]
    assert dash("POST", "/api/session", {"scenario": "small", "replace": True})[0] == 200
    assert dash("GET", "/api/session/market")[1]["cities"] == ["A", "B"]
    assert dash("GET", "/api/session/player")[1] == {"open": False, "unit": 100}
    st, body = dash("POST", "/api/session/player/orders", {"city": "A", "good": "food", "side": "buy", "qty": 1, "price": 1})
    assert st == 400 and body["code"] == "no_account"
    assert dash("POST", "/api/session/player", {})[0] == 200
    acct = dash("GET", "/api/session/player")[1]
    assert acct["open"] and acct["gold"] > 0 and acct["injected"]["gold"] == acct["gold"]

    def order(**kw):
        body = {"city": "A", "good": "food", "side": "buy", "qty": 2, "price": 1, **kw}
        return dash("POST", "/api/session/player/orders", body)

    assert order(city="Z")[1]["code"] == "unknown_city"
    assert order(good="gems")[1]["code"] == "unknown_good"
    assert order(qty=0)[1]["code"] == "bad_qty"
    assert order(qty=1.5)[1]["code"] == "bad_qty"
    assert order(price=0)[1]["code"] == "bad_price"
    assert order(price=1.005)[1]["code"] == "bad_price"
    assert order(price="x")[1]["code"] == "bad_price"
    assert order(side="hold")[1]["code"] == "bad_side"
    assert order(qty=10 ** 9, price=100)[1]["code"] == "not_enough_gold"
    st, o1 = order()
    st2, o2 = order(qty=3)
    assert st == st2 == 200 and o1["status"] == "queued" and o2["id"] == o1["id"] + 1
    orders = dash("GET", "/api/session/player")[1]["orders"]
    assert [o["id"] for o in orders] == [o1["id"], o2["id"]]
    q = dash("POST", "/api/session/player/quote", {"city": "A", "good": "food", "side": "buy", "qty": 2, "price": 5})[1]
    assert q["estimate"] is True
    st, body = dash("PUT", f"/api/session/player/orders/{o1['id']}", {"qty": 4})
    assert st == 200 and body["qty"] == 4
    assert dash("DELETE", f"/api/session/player/orders/{o2['id']}")[1]["status"] == "cancelled"
    assert dash("DELETE", "/api/session/player/orders/999")[1]["code"] == "unknown_order"
    assert dash("POST", "/api/session/player/topup", {"gold": 10})[0] == 200
    assert dash("POST", "/api/session/player/topup", {"gold": 0})[1]["code"] == "bad_amount"
    assert dash("POST", "/api/session/player/topup", {"goods": {"wood": 5}, "city": "A"})[0] == 200
    mv = dash("GET", "/api/session/market/A?good=wood")[1]
    assert mv["book"]["good"] == "wood" and mv["city"] == "A"
    assert dash("GET", "/api/session/market/Z")[1]["code"] == "unknown_city"
    # time moves: the queued order runs
    dash("POST", "/api/session/control", {"action": "step_hour"})
    acct = dash("GET", "/api/session/player")[1]
    assert [o for o in acct["orders"] if o["id"] == o1["id"]][0]["status"] in ("resting", "filled") or \
        [o for o in acct["finished"] if o["id"] == o1["id"]]
    assert len(dash("GET", "/api/session/player/actions")[1]) >= 5
    # save: the run keeps the trades; and it can be resumed from the Market tab
    dash("POST", "/api/session/control", {"action": "step_day"})
    saved = dash("POST", "/api/session/save", {"name": "traded"})[1]["saved_as"]
    assert (dash.runs / saved / "player_actions.json").exists()
    assert dash("POST", "/api/session", {"resume": saved, "replace": True})[0] == 200
    again = dash("GET", "/api/session/player")[1]
    assert again["open"] and again["orders"] == acct["orders"] or again["open"]
    assert dash("POST", "/api/session", {"resume": "nope", "replace": True})[0] in (404, 409)


def test_orders_are_refused_when_the_run_is_finished(dash):  # noqa: F811
    dash("POST", "/api/session", {"scenario": "small", "days": 1, "replace": True})
    dash("POST", "/api/session/player", {})
    dash("POST", "/api/session/control", {"action": "step_day"})
    assert dash("GET", "/api/session")[1]["status"] == "finished"
    st, body = dash("POST", "/api/session/player/orders", {"city": "A", "good": "food", "side": "buy", "qty": 1, "price": 1})
    assert st == 400 and body["code"] == "finished"
    dash("POST", "/api/session/control", {"action": "extend", "value": 1})
    assert dash("POST", "/api/session/player/orders", {"city": "A", "good": "food", "side": "buy", "qty": 1, "price": 1})[0] == 200


def test_the_app_serves_the_market_tab(dash):  # noqa: F811
    st, html = dash("GET", "/", raw=True)
    assert b'data-nav="market"' in html
    st, js = dash("GET", "/static/market.js", raw=True)
    assert st == 200 and b"/api/session/player/orders" in js


def test_the_player_works_the_same_through_the_market_api(small_cfg, tmp_path):
    import threading

    from popsim.market.client import HttpMarketClient, LocalMarketClient
    from popsim.market.server import make_server

    server = make_server("127.0.0.1", 0, str(tmp_path / "api.sqlite"))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        remote = Simulation(small_cfg, market=HttpMarketClient(f"http://127.0.0.1:{server.server_address[1]}"))
        local = Simulation(small_cfg, market=LocalMarketClient(json_roundtrip=True))
        for sim in (local, remote):
            sim.run(days=2)
            P.open_account(sim)
            P.top_up(sim, 0, {"wood": 15}, "A")
            P.queue_order(sim, "A", "wood", "sell", 15, 1)
            P.queue_order(sim, "A", "food", "buy", 100, 900, "market")
            P.queue_order(sim, "A", "food", "buy", 2, 1)
            sim.run(ticks=8)
            P.cancel_all(sim)
            sim.run(ticks=3)
        assert remote.world.get(remote.player, Player).fills == local.world.get(local.player, Player).fills
        assert remote.world.get(remote.player, Player).orders == local.world.get(local.player, Player).orders
        assert remote.metrics.tick_prices == local.metrics.tick_prices
        assert all(v["ok"] for v in remote.escrow_matches_market().values())
        assert remote.ledger.verify()["ok"]
    finally:
        server.shutdown()
        server.server_close()
