"""Merchant AI (REQ-4.5): buy where the price is low, travel, sell where it is high.

Merchants only know prices of cities they have visited (REQ-4.1.6), so on a new
road they first go and look (scouting). Nothing about the price gap is hard-coded:
it closes only because merchants move goods (REQ-5.4).
"""

from __future__ import annotations

from popsim.components import Action, MerchantState, Orders, PriceMemory, Stamina
from popsim.systems.agent import known_price, price_ref, stock_target


def reserve(sim, eid: int, good: str) -> int:
    return stock_target(sim, eid, good) if good in sim.cfg["needs"]["daily_units"] else 0


def open_qty(sim, eid: int, side: str, good: str) -> int:
    orders = sim.world.get(eid, Orders)
    return sum(o.qty for o in orders.open if o.side == side and o.good == good)


def _set_trade(act: Action) -> None:
    act.kind, act.ticks_left = "trade", 0


def best_trade(sim, eid: int, city: int):
    m = sim.cfg["merchant"]
    unit = sim.unit
    budget = sim.gold(eid) * m["budget_share"]
    best = None
    for dest, _, road in sorted(sim.roads_from(city), key=lambda t: t[0]):
        trip_cost = road.stamina_cost * m["stamina_value_gold"] * unit
        for good in sim.goods:
            there = known_price(sim, eid, dest, good)
            if there is None:
                continue
            here = price_ref(sim, eid, city, good) * 1.05
            sell = there * (1 - m["sell_margin_safety"])
            margin = sell - here
            if margin <= 0:
                continue
            qty = int(min(m["carry_capacity"], budget // here))
            if qty <= 0:
                continue
            profit = margin * qty - trip_cost
            if profit < m["min_profit_gold"] * unit:
                continue
            cand = (profit, -dest, good, qty, int(sell - trip_cost / qty))
            if best is None or cand[:2] > best[:2]:
                best = cand
    if best is None:
        return None
    profit, ndest, good, qty, max_buy = best
    return {"dest": -ndest, "good": good, "qty": qty, "max_buy_price": max(1, max_buy)}


def merchant_decide(sim, eid: int, city: int) -> bool:
    """Returns True if the merchant committed to a trading action this tick."""
    m = sim.cfg["merchant"]
    ms = sim.world.get(eid, MerchantState)
    act = sim.world.get(eid, Action)
    st = sim.world.get(eid, Stamina)
    if ms is None:
        return False

    if ms.mode == "selling":
        cargo = sim.inv(eid, ms.good) - reserve(sim, eid, ms.good) + open_qty(sim, eid, "sell", ms.good)
        if cargo <= 0 or ms.wait >= sim.hours_to_ticks(m["max_sell_wait_hours"]):
            ms.mode, ms.good, ms.dest, ms.qty, ms.wait = "idle", None, None, 0, 0
        else:
            ms.wait += 1
            _set_trade(act)
            return True

    if ms.mode == "buying":
        have = sim.inv(eid, ms.good) - reserve(sim, eid, ms.good)
        waited_out = ms.wait >= sim.hours_to_ticks(m["max_buy_wait_hours"])
        if have >= ms.qty or (waited_out and have > 0):
            origin, good, dest = city, ms.good, ms.dest
            if st.current >= m["min_stamina_to_travel"] and sim.start_travel(eid, dest):
                ms.mode, ms.wait = "traveling", 0
                sim.metrics.record_flow(sim, origin, dest, good, have)
                return True
            if not waited_out:
                ms.wait += 1
                _set_trade(act)
                return True
            ms.mode = "idle"
        elif waited_out:
            ms.mode, ms.good, ms.dest, ms.qty, ms.wait = "idle", None, None, 0, 0
        else:
            ms.wait += 1
            _set_trade(act)
            return True

    if ms.mode in ("idle", "traveling"):
        ms.mode = "idle"
        if st.current < m["min_stamina_to_travel"]:
            return False
        plan = best_trade(sim, eid, city)
        if plan:
            ms.mode, ms.good, ms.dest, ms.qty = "buying", plan["good"], plan["dest"], plan["qty"]
            ms.max_buy_price, ms.wait = plan["max_buy_price"], 0
            _set_trade(act)
            return True
        if m.get("scout", True):
            mem = sim.world.get(eid, PriceMemory)
            for dest, _, _ in sorted(sim.roads_from(city), key=lambda t: t[0]):
                knows = any((dest, g) in mem.ema for g in sim.goods)
                if not knows and sim.start_travel(eid, dest):
                    ms.mode, ms.good, ms.dest, ms.qty = "traveling", None, dest, 0
                    return True
    return False


def on_arrival(sim, eid: int, city: int) -> None:
    """Called by the action system when a traveller reaches ``city``."""
    from popsim.systems.agent import observe_price

    # Arriving NPCs read the city's board: last traded prices (possibly stale).
    prices = sim.market.prices(sim.world_name, sim.city_name(city))
    mem = sim.world.get(eid, PriceMemory)
    for good, p in prices.items():
        if (city, good) not in mem.ema:
            mem.ema[(city, good)] = [float(p), sim.tick]
        else:
            observe_price(sim, eid, city, good, float(p))
    ms = sim.world.get(eid, MerchantState)
    if ms is None:
        return
    if ms.mode == "traveling" and ms.good:
        ms.mode, ms.wait = "selling", 0
    else:
        ms.mode, ms.good, ms.dest, ms.qty, ms.wait = "idle", None, None, 0, 0
