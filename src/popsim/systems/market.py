"""Market clearing system (REQ-4.4, REQ-1A.6, REQ-1A.7).

Per city, per tick, two batched calls to the market API:
  1. cancel orders older than the TTL (refund escrow, adapt prices),
  2. place new bids/asks (escrow locked first), matched immediately by the CDA.

Fills are settled from the city's escrow account. Each tick's VWAP becomes the
observed market price that NPCs in that city fold into their EMA memory.
"""

from __future__ import annotations

from popsim.components import Action, Location, MerchantState, Orders, OpenOrder
from popsim.ledger import GOLD, escrow_account
from popsim import player as pl
from popsim.records import on_fill
from popsim.systems.fiscal import pay_income_tax, pay_sales_tax
from popsim.systems.agent import (
    GOOD_NEED, ask_price, bid_price, observe_price, price_ref, stock_target,
)
from popsim.world.rand import clamp


def _adapt(sim, eid: int, side: str, good: str, filled: bool, urgency: float = 0.0) -> None:
    orders = sim.world.get(eid, Orders)
    if orders is None:
        return
    p = sim.cfg["pricing"]
    table = orders.bid_premium if side == "buy" else orders.ask_discount
    cur = table.get(good, 0.0)
    if filled:
        table[good] = cur * p["adapt_decay"]
    else:
        table[good] = clamp(cur + p["adapt_step"] * (1 + min(urgency, 2.0)), 0.0, p["adapt_cap"])


def settle_cancel(sim, eid: int, city: int, res: dict, refund_to=None, adapt: bool = True) -> None:
    esc = escrow_account(city)
    dst = eid if refund_to is None else refund_to
    remaining, price, side, good = res["remaining"], res["price"], res["side"], res["good"]
    if side == "buy":
        sim.ledger.transfer(esc, dst, GOLD, remaining * price, reason="escrow_release")
    else:
        sim.ledger.transfer(esc, dst, good, remaining, reason="escrow_release")
    orders = sim.world.get(eid, Orders)
    if orders is not None:
        orders.open = [o for o in orders.open if o.order_id != res["order_id"]]
    if adapt and remaining > 0:
        from popsim.systems.agent import need_urgency_for_good
        _adapt(sim, eid, side, good, filled=False, urgency=need_urgency_for_good(sim, eid, good))


def settle_fill(sim, city: int, f: dict) -> None:
    esc = escrow_account(city)
    good, qty, price = f["good"], f["qty"], f["price"]
    buyer, seller = f["buyer"], f["seller"]
    sim.ledger.transfer(esc, buyer, good, qty, reason="trade")
    sim.ledger.transfer(esc, seller, GOLD, qty * price, reason="trade")
    improvement = qty * (f["buy_limit"] - price)
    if improvement:
        sim.ledger.transfer(esc, buyer, GOLD, improvement, reason="escrow_release")
    maker = f["maker_order"]
    maker_owner = buyer if maker == f["buy_order"] else seller
    orders = sim.world.get(maker_owner, Orders)
    if orders is not None:
        for o in orders.open:
            if o.order_id == maker:
                o.qty -= qty
                break
        orders.open = [o for o in orders.open if o.qty > 0]
    _adapt(sim, buyer, "buy", good, filled=True)
    _adapt(sim, seller, "sell", good, filled=True)
    on_fill(sim, city, f)
    # Taxes (REQ-5.5): the seller pays income tax on the proceeds, the buyer the sales tax on the
    # transaction, both to the treasury of the city where the trade happened.
    tax_seller = pay_income_tax(sim, seller, city, qty * price, "income_tax")
    tax_buyer = pay_sales_tax(sim, buyer, city, qty * price)
    if sim.player is not None and (buyer == sim.player or seller == sim.player):
        pl.on_fill(sim, city, f, tax_buyer, tax_seller)


def desired_orders(sim, eid: int, city: int) -> list[tuple[str, str, int, int]]:
    """(side, good, qty, price) the NPC wants on the book right now."""
    orders = sim.world.get(eid, Orders)
    has = {(o.side, o.good) for o in orders.open}
    ms = sim.world.get(eid, MerchantState)
    gold = sim.gold(eid)
    out = []
    for good in sim.goods:
        if ("buy", good) in has or ("sell", good) in has:
            continue
        inv = sim.inv(eid, good)
        target = stock_target(sim, eid, good) if good in GOOD_NEED else 0
        trade_buy = ms is not None and ms.mode == "buying" and ms.good == good
        trade_sell = ms is not None and ms.mode == "selling" and ms.good == good
        if trade_buy:
            qty = target + ms.qty - inv
            if qty > 0:
                ref = price_ref(sim, eid, city, good)
                noise = sim.rng.gauss(0.0, 0.02)
                prem = orders.bid_premium.get(good, 0.0)
                price = min(int(round(ref * (1 + noise) * (1.02 + prem))), ms.max_buy_price)
                price = max(1, price)
                qty = min(qty, gold // price)
                if qty > 0:
                    out.append(("buy", good, qty, price))
                    gold -= qty * price
            continue
        shortfall = target - inv
        if shortfall > 0 and not trade_sell:
            price = bid_price(sim, eid, city, good)
            qty = min(shortfall, gold // price)
            if qty > 0:
                out.append(("buy", good, qty, price))
                gold -= qty * price
        elif inv - target > 0:
            out.append(("sell", good, inv - target, ask_price(sim, eid, city, good)))
    return out


class MarketSystem:
    name = "market"

    def run(self, sim) -> None:
        ttl = sim.hours_to_ticks(sim.cfg["pricing"]["order_ttl_hours"])
        world = sim.world_name
        cities = sorted(sim.city_ids.items(), key=lambda kv: kv[1])

        # 1) cancellations of stale orders, one batch per city
        cancels: dict[int, list[dict]] = {}
        for eid, (orders,) in sim.world.query(Orders):
            for o in orders.open:
                if sim.tick - o.placed_tick >= ttl:
                    cancels.setdefault(o.city, []).append(
                        {"op": "cancel", "order_id": o.order_id, "owner": eid, "ref": eid})
        for cid, pops in pl.cancel_ops(sim).items():  # the player's cancels, then expired orders (REQ-1C.9)
            cancels.setdefault(cid, []).extend(pops)
        for name, cid in cities:
            ops = cancels.get(cid)
            if not ops:
                continue
            resp = sim.market.submit_batch(world, name, sim.tick, ops)
            sim.board[cid] = resp["top"]
            for op, res in zip(ops, resp["results"]):
                if "po" in op:
                    pl.on_cancel_result(sim, cid, op, res)
                elif res["ok"]:
                    settle_cancel(sim, op["owner"], cid, res)
                else:  # already filled or gone: drop our local record
                    orders = sim.world.get(op["owner"], Orders)
                    orders.open = [o for o in orders.open if o.order_id != op["order_id"]]

        # 2) new orders, escrow locked before submission
        places: dict[int, list[dict]] = {}
        for eid, (orders, loc, act) in sim.world.query(Orders, Location, Action):
            if loc.city is None or act.kind == "sleep":
                continue
            for side, good, qty, price in desired_orders(sim, eid, loc.city):
                esc = escrow_account(loc.city)
                if side == "buy":
                    ok = sim.ledger.try_transfer(eid, esc, GOLD, qty * price, reason="escrow_lock")
                else:
                    ok = sim.ledger.try_transfer(eid, esc, good, qty, reason="escrow_lock")
                if ok:
                    places.setdefault(loc.city, []).append(
                        {"op": "place", "owner": eid, "side": side, "good": good,
                         "qty": qty, "price": price, "ref": eid})

        player_places = pl.place_ops(sim)
        for name, cid in cities:
            # fair arrival order, still seeded; the player's orders are mixed in (REQ-1C.9)
            ops = pl.merge_shuffled(sim, places.get(cid, []), player_places.get(cid, []))
            rests: list[dict] = []
            volume: dict[str, dict] = {}
            prices: dict[str, int] = {}
            if ops:
                resp = sim.market.submit_batch(world, name, sim.tick, ops)
                volume, prices = resp["volume"], resp["prices"]
                sim.board[cid] = resp["top"]
                for op, res in zip(ops, resp["results"]):
                    eid = op["owner"]
                    if "po" in op:  # the player's order
                        if pl.on_place_result(sim, cid, op, res):
                            for f in res["fills"]:
                                settle_fill(sim, cid, f)
                            if pl.after_place(sim, cid, op, res):
                                rests.append(op)
                        continue
                    if not res["ok"]:
                        esc = escrow_account(cid)
                        if op["side"] == "buy":
                            sim.ledger.transfer(esc, eid, GOLD, op["qty"] * op["price"], reason="escrow_release")
                        else:
                            sim.ledger.transfer(esc, eid, op["good"], op["qty"], reason="escrow_release")
                        continue
                    for f in res["fills"]:
                        settle_fill(sim, cid, f)
                    if res["remaining"] > 0:
                        sim.world.get(eid, Orders).open.append(OpenOrder(
                            order_id=res["order_id"], city=cid, side=op["side"], good=op["good"],
                            qty=res["remaining"], price=op["price"], placed_tick=sim.tick))
            if rests:  # a market order never stays on the book
                po = pl.account(sim)[1]
                cops = [{"op": "cancel", "order_id": pl._order(po, r["po"]).order_id, "owner": r["owner"],
                         "ref": r["owner"], "po": r["po"]} for r in rests]
                resp = sim.market.submit_batch(world, name, sim.tick, cops)
                sim.board[cid] = resp["top"]
                for op, res in zip(cops, resp["results"]):
                    pl.on_cancel_result(sim, cid, op, res, market_rest=True)
            sim.metrics.record_market_tick(sim, cid, volume, prices)
            # 3) NPCs in the city observe the local board (local information only):
            # this tick's VWAP if anything traded; otherwise unmet bids above, or
            # unsold asks below, their expectation pull it toward the book.
            residents = sim.npcs_in(cid)
            top = sim.board.get(cid, {})
            for good in sim.goods:
                v = volume.get(good)
                if v and v["qty"] > 0:
                    signal = ("trade", v["value"] / v["qty"])
                else:
                    t = top.get(good, {})
                    signal = ("book", t.get("bid"), t.get("ask"))
                for eid in residents:
                    if signal[0] == "trade":
                        observe_price(sim, eid, cid, good, signal[1])
                    else:
                        ref = price_ref(sim, eid, cid, good)
                        bid, ask = signal[1], signal[2]
                        if bid is not None and bid > ref:
                            observe_price(sim, eid, cid, good, bid)
                        elif ask is not None and ask < ref:
                            observe_price(sim, eid, cid, good, ask)
