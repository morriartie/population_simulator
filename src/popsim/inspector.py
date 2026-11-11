"""Read side of the inspector (REQ-1C.5): list the citizens of a city and describe
one of them in full.

Everything here is read-only. It never draws from ``sim.rng`` (reservation prices
are computed without the random forecast error) and never needs ``sim.market``,
so it works on a live run and on a snapshot loaded without a market.
"""

from __future__ import annotations

from popsim.components import (
    NPC, Action, Area, City, Demographics, Health, Home, House, Location, MerchantState, Metabolism,
    Needs, Orders, Position, PriceMemory, Record, Role, Skills, Stamina, Traits, Travel, Trip, Workplace, Yield,
)
from popsim.systems.fiscal import net_wage_per_hour, policy
from popsim.systems.movement import at_home, distance_to, walk_distance, walk_stamina
from popsim.systems.agent import (
    GOOD_NEED, ask_price, bid_price, expected_daily_spend, job_options, price_ref,
    reservation_wage, stock_target, wealth_target,
)

TRAVELLING = "travelling"
SORT_KEYS = ("id", "name", "role", "age", "gender", "gold", "satiety", "stamina", "sick", "action")
MAX_PAGE_SIZE = 200
HISTORY_LIMIT = 200  # newest entries returned per history list


class NotFound(KeyError):
    pass


def _pos(sim, eid: int) -> dict | None:
    """x, y of an entity, or None when the scenario has no coordinates (REQ-3A.9)."""
    p = sim.world.get(eid, Position)
    if p is None or not getattr(sim, "spatial", False):
        return None
    return {"x": round(p.x, 3), "y": round(p.y, 3)}


def _g(sim, cents) -> float:
    return round(cents / sim.unit, 4)


def _r(v, n=3):
    return None if v is None else round(float(v), n)


# ---------------------------------------------------------------- lists


def city_overview(sim) -> list[dict]:
    """Every city with its citizen count, plus the people currently on the road."""
    counts = {cid: 0 for cid in sim.city_ids.values()}
    road = 0
    for _, (_, loc) in sim.world.query(NPC, Location):
        if loc.city is None:
            road += 1
        else:
            counts[loc.city] += 1
    out = [{"name": name, "citizens": counts[cid]} for name, cid in sorted(sim.city_ids.items())]
    out.append({"name": TRAVELLING, "citizens": road})
    return out


def _action_label(sim, act: Action) -> str:
    if act.kind == "gather" and act.target is not None:
        y = sim.world.get(act.target, Yield)
        return f"gather {y.good}" if y else "gather"
    if act.kind == "work" and act.target is not None:
        wp = sim.world.get(act.target, Workplace)
        return f"work: {wp.task}" if wp else "work"
    if act.kind == "travel" and act.target is not None:
        return f"travel to {sim.city_name(act.target)}"
    if act.kind == "walk" and act.target is not None:
        y = sim.world.get(act.target, Yield)
        if y is not None:
            return f"walk to {y.good} node"
        wp = sim.world.get(act.target, Workplace)
        if wp is not None:
            return f"walk to {wp.task}"
        return "walk home" if sim.world.get(act.target, House) is not None else "walk"
    return act.kind


def _row(sim, eid: int, npc: NPC, loc: Location) -> dict:
    demo = sim.world.get(eid, Demographics)
    st = sim.world.get(eid, Stamina)
    met = sim.world.get(eid, Metabolism)
    hl = sim.world.get(eid, Health)
    act = sim.world.get(eid, Action)
    role = sim.world.get(eid, Role)
    return {
        "id": eid, "name": npc.name, "role": role.kind if role else None,
        "age": round(demo.age_days / 365, 1), "gender": demo.gender,
        "gold": _g(sim, sim.gold(eid)), "satiety": _r(met.satiety), "stamina": _r(st.current, 1),
        "sick": bool(hl.sick), "action": _action_label(sim, act),
        "city": sim.city_name(loc.city) if loc.city is not None else TRAVELLING,
    }


def list_citizens(sim, city: str, *, sort: str = "name", order: str = "asc", role: str | None = None,
                  sick: bool | None = None, gold_min: float | None = None, gold_max: float | None = None,
                  satiety_min: float | None = None, satiety_max: float | None = None,
                  q: str | None = None, page: int = 1, page_size: int = 50) -> dict:
    if city == TRAVELLING:
        cid = None
    elif city in sim.city_ids:
        cid = sim.city_ids[city]
    else:
        raise NotFound(f"no city {city!r}")
    if sort not in SORT_KEYS:
        raise ValueError(f"sort must be one of {list(SORT_KEYS)}")
    if order not in ("asc", "desc"):
        raise ValueError("order must be asc or desc")
    if role not in (None, "worker", "merchant"):
        raise ValueError("role must be worker or merchant")
    if page < 1 or not 1 <= page_size <= MAX_PAGE_SIZE:
        raise ValueError(f"page must be >= 1 and page_size between 1 and {MAX_PAGE_SIZE}")

    needle = (q or "").strip().lower()
    rows = []
    for eid, (npc, loc) in sim.world.query(NPC, Location):
        if loc.city != cid:
            continue
        row = _row(sim, eid, npc, loc)
        if role and row["role"] != role:
            continue
        if sick is not None and row["sick"] != sick:
            continue
        if gold_min is not None and row["gold"] < gold_min:
            continue
        if gold_max is not None and row["gold"] > gold_max:
            continue
        if satiety_min is not None and row["satiety"] < satiety_min:
            continue
        if satiety_max is not None and row["satiety"] > satiety_max:
            continue
        if needle and needle not in row["name"].lower() and needle != str(eid):
            continue
        rows.append(row)
    rows.sort(key=lambda r: (r[sort], r["id"]), reverse=(order == "desc"))
    total = len(rows)
    pages = max(1, -(-total // page_size))
    page = min(page, pages)
    start = (page - 1) * page_size
    return {"city": city, "total": total, "page": page, "pages": pages, "page_size": page_size,
            "rows": rows[start:start + page_size]}


# -------------------------------------------------------------- detail


def _city_averages(sim) -> dict[tuple[str, str], float]:
    """Volume-weighted average price per (city name, good) over the whole run, in gold."""
    acc: dict[tuple[str, str], list[float]] = {}
    for r in sim.metrics.daily_prices:
        if r.get("vwap") is not None and r.get("volume"):
            a = acc.setdefault((r["city"], r["good"]), [0.0, 0.0])
            a[0] += r["vwap"] * r["volume"]
            a[1] += r["volume"]
    return {k: v[0] / v[1] for k, v in acc.items() if v[1]}


def _history(sim, eid: int, rec: Record | None) -> dict:
    if rec is None:
        return {"recorded": False, "fills": [], "actions": [], "tasks": [], "daily": [], "totals": {},
                "fiscal": None}
    avgs = _city_averages(sim)
    fills = []
    totals: dict[str, dict] = {}
    for tick, city, good, side, qty, price, other in rec.fills:
        cname = sim.city_name(city) if sim.world.alive(city) else str(city)
        other_npc = sim.world.get(other, NPC)
        fills.append({"tick": tick, "day": sim.day(tick), "city": cname, "good": good, "side": side,
                      "qty": qty, "price": _g(sim, price), "total": _g(sim, qty * price),
                      "counterparty": other, "counterparty_name": other_npc.name if other_npc else None})
        t = totals.setdefault(good, {"bought_qty": 0, "bought_value": 0.0, "sold_qty": 0, "sold_value": 0.0,
                                     "market_value_bought": 0.0, "market_value_sold": 0.0})
        key = "bought" if side == "buy" else "sold"
        t[f"{key}_qty"] += qty
        t[f"{key}_value"] += qty * price / sim.unit
        t[f"market_value_{key}"] += qty * avgs.get((cname, good), price / sim.unit)
    for t in totals.values():
        t["avg_paid"] = _r(t["bought_value"] / t["bought_qty"], 4) if t["bought_qty"] else None
        t["avg_received"] = _r(t["sold_value"] / t["sold_qty"], 4) if t["sold_qty"] else None
        t["market_avg_when_bought"] = _r(t["market_value_bought"] / t["bought_qty"], 4) if t["bought_qty"] else None
        t["market_avg_when_sold"] = _r(t["market_value_sold"] / t["sold_qty"], 4) if t["sold_qty"] else None
        for k in ("bought_value", "sold_value"):
            t[k] = _r(t[k], 4)
        del t["market_value_bought"], t["market_value_sold"]
    actions = [{"kind": a["kind"], "label": a["label"], "city": a["city"], "start_tick": a["start"],
                "end_tick": a["end"], "start_day": a["start"] * sim.h / 24, "ticks": a["end"] - a["start"] + 1,
                "reason": a["reason"]} for a in rec.actions]
    tasks = [{"good": t["good"], "city": t["city"], "start_tick": t["start"], "end_tick": t["end"],
              "start_day": t["start"] * sim.h / 24, "hours": (t["end"] - t["start"] + 1) * sim.h, "units": t["units"],
              "est_value": _g(sim, t["value"])} for t in rec.tasks]
    daily = [{"day": d["day"], "spent": _g(sim, d["spent"]), "earned": _g(sim, d["earned"]),
              "gold": _g(sim, d["gold"]), "taxes": _g(sim, d.get("taxes", 0)),
              "support": _g(sim, d.get("support", 0))} for d in rec.daily]
    fiscal = {"taxes_paid": _g(sim, sum(d.get("taxes", 0) for d in rec.daily) + rec.taxes_today),
              "support_received": _g(sim, sum(d.get("support", 0) for d in rec.daily) + rec.support_today)}
    return {"recorded": True, "fills": fills[::-1][:HISTORY_LIMIT], "fills_total": len(fills),
            "actions": actions[::-1][:HISTORY_LIMIT], "tasks": tasks[::-1][:HISTORY_LIMIT],
            "daily": daily[-HISTORY_LIMIT:], "totals": totals, "fiscal": fiscal}


def _city_fiscal(sim, city: int) -> dict:
    """The tax and support policy of a city and what its treasury holds (REQ-5.5)."""
    pol = policy(sim, city)
    return {"city": sim.city_name(city), "income_tax": pol["income_tax"], "sales_tax": pol["sales_tax"],
            "support_per_day": float(pol["support"].get("amount", 0.0)), "treasury": _g(sim, sim.gold(city))}


def _plan(sim, eid: int, city: int) -> dict:
    """The decision system's current numbers for this NPC (noise-free)."""
    needs = sim.world.get(eid, Needs)
    ms = sim.world.get(eid, MerchantState)
    gold = sim.gold(eid)
    goods = []
    for good in sim.goods:
        target = stock_target(sim, eid, good) if good in GOOD_NEED else 0
        inv = sim.inv(eid, good)
        bid = bid_price(sim, eid, city, good, noise=0.0)
        ask = ask_price(sim, eid, city, good, noise=0.0)
        shortfall = target - inv
        if shortfall > 0:
            qty = min(shortfall, gold // bid) if bid else 0
            intent = {"action": "buy", "qty": qty, "limit": _g(sim, bid)}
        elif inv - target > 0:
            intent = {"action": "sell", "qty": inv - target, "limit": _g(sim, ask)}
        else:
            intent = {"action": "hold", "qty": 0, "limit": None}
        trade = None
        if ms is not None and ms.good == good and ms.mode in ("buying", "selling", "traveling"):
            trade = {"mode": ms.mode, "qty": ms.qty,
                     "dest": sim.city_name(ms.dest) if ms.dest is not None else None,
                     "max_buy_price": _g(sim, ms.max_buy_price)}
        goods.append({"good": good, "inventory": inv, "stock_target": target, "shortfall": max(0, shortfall),
                      "expected_price": _g(sim, price_ref(sim, eid, city, good)),
                      "reservation_bid": _g(sim, bid), "reservation_ask": _g(sim, ask),
                      "intent": intent, "merchant_trade": trade})
    return {
        "city": sim.city_name(city),
        "gold": _g(sim, gold),
        "savings_target": _g(sim, wealth_target(sim, eid, city)),
        "wealth_level": _r(needs.levels.get("wealth")),
        "expected_daily_spend": _g(sim, expected_daily_spend(sim, eid, city)),
        "reservation_wage_per_hour": _g(sim, reservation_wage(sim, eid, city)),
        "goods": goods,
        "fiscal": _city_fiscal(sim, city),
        "pending_payments": [],  # loans and installments do not exist yet (REQ-1A.8, REQ-3.7)
        "note": "Noise-free values from the same functions the decision system uses; the random "
                "forecast error added at order time is left out.",
    }


def _jobs(sim, eid: int, loc: Location, act: Action, rec: Record | None) -> dict:
    role = sim.world.get(eid, Role)
    st = sim.world.get(eid, Stamina)
    hl = sim.world.get(eid, Health)
    skills = sim.world.get(eid, Skills).levels
    eff = sim.cfg["physiology"]["health"]["sick_work_efficiency"] if hl and hl.sick else 1.0
    current = None
    if act.kind == "gather" and act.target is not None:
        y = sim.world.get(act.target, Yield)
        nloc = sim.world.get(act.target, Location)
        trip = sim.world.get(eid, Trip)
        price_city = nloc.city if nloc is not None and nloc.city is not None else (trip.origin if trip else None)
        if y is not None and price_city is not None:
            avail = min(1.0, sim.ledger.balance(act.target, y.good) / 10.0)
            per_stamina = price_ref(sim, eid, price_city, y.good) * skills.get(y.good, 1.0) * eff \
                / y.stamina_cost * avail
            current = {"node": act.target, "good": y.good, "city": sim.city_name(nloc.city) if nloc.city is not None else "overworld",
                       "ticks_left": act.ticks_left,
                       "expected_income_per_stamina": _g(sim, per_stamina),
                       "node_stock": sim.ledger.balance(act.target, y.good),
                       "position": _pos(sim, act.target),
                       "distance": _r(distance_to(sim, eid, act.target), 3)}
    if act.kind == "work" and act.target is not None:
        wp = sim.world.get(act.target, Workplace)
        if wp is not None:
            current = {"node": act.target, "good": wp.task, "city": sim.city_name(wp.city), "public": True,
                       "ticks_left": act.ticks_left, "position": _pos(sim, act.target),
                       "distance": _r(distance_to(sim, eid, act.target), 3),
                       "wage_per_hour": _g(sim, wp.wage), "net_wage_per_hour": _g(sim, net_wage_per_hour(sim, wp)),
                       "expected_income_per_stamina": _g(sim, net_wage_per_hour(sim, wp) / max(wp.stamina_per_hour, 1e-9)),
                       "node_stock": None}
    options = []
    if loc.city is not None:
        for score, nid, good, per_hour in job_options(sim, eid, loc.city):
            dist = walk_distance(sim, eid, nid)
            wp = sim.world.get(nid, Workplace)
            options.append({"node": nid, "good": good, "city": sim.city_name(loc.city), "public": wp is not None,
                            "score": _r(score), "expected_income_per_hour": _g(sim, per_hour),
                            "skill": _r(skills.get(good, 1.0)), "distance": _r(dist, 3),
                            "walk_stamina": _r(walk_stamina(sim, dist), 2),
                            "round_trip_stamina": _r(2 * walk_stamina(sim, dist), 2)})
    return {"role": role.kind if role else None, "current": current, "options": options,
            "stamina": _r(st.current, 1),
            "contracts": [],  # job contracts are not built yet (REQ-1A.8)
            "note": "Gathering tasks at resource nodes (income at the NPC's own expected price) and public jobs paid "
                    "by the city treasury (after income tax)."}


def citizen_detail(sim, eid: int) -> dict:
    """Everything about one living citizen. Dead ones come from ``sim.graveyard``."""
    npc = sim.world.get(eid, NPC)
    if npc is None:
        grave = sim.graveyard.get(eid)
        if grave is not None:
            return grave["detail"]
        raise NotFound(f"no citizen {eid}")
    w = sim.world
    loc, act, needs = w.get(eid, Location), w.get(eid, Action), w.get(eid, Needs)
    st, met, hl = w.get(eid, Stamina), w.get(eid, Metabolism), w.get(eid, Health)
    demo, tr, orders = w.get(eid, Demographics), w.get(eid, Traits), w.get(eid, Orders)
    home, mem, role = w.get(eid, Home), w.get(eid, PriceMemory), w.get(eid, Role)
    ms, rec, travel = w.get(eid, MerchantState), w.get(eid, Record), w.get(eid, Travel)
    city = loc.city if loc.city is not None else home.city

    locked_gold, locked_goods = 0, {}
    open_orders = []
    for o in orders.open:
        if o.side == "buy":
            locked_gold += o.qty * o.price
        else:
            locked_goods[o.good] = locked_goods.get(o.good, 0) + o.qty
        open_orders.append({"side": o.side, "good": o.good, "qty": o.qty, "price": _g(sim, o.price),
                            "city": sim.city_name(o.city), "age_ticks": sim.tick - o.placed_tick})

    reason = None
    if rec is not None and rec.reason is not None:
        reason = {"tick": rec.reason["tick"], "choice": rec.reason["choice"],
                  "scores": rec.reason["scores"], "note": rec.reason["note"]}
    merchant = None
    if ms is not None:
        merchant = {"mode": ms.mode, "good": ms.good, "qty": ms.qty,
                    "dest": sim.city_name(ms.dest) if ms.dest is not None else None,
                    "max_buy_price": _g(sim, ms.max_buy_price), "wait": ms.wait,
                    "visited": [sim.city_name(c) for c in ms.visited]}
    memory = []
    for (c, good), (price, seen) in sorted(mem.ema.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        memory.append({"city": sim.city_name(c), "good": good, "price": _g(sim, price),
                       "last_seen_day": sim.day(seen) if seen >= 0 else None,
                       "source": "observed" if seen >= 0 else "prior"})

    return {
        "alive": True,
        "id": eid, "name": npc.name, "role": role.kind if role else None,
        "status": {
            "age_years": round(demo.age_days / 365, 2),
            "life_expectancy_years": round(demo.life_expectancy_days / 365, 2),
            "gender": demo.gender,
            "home": sim.city_name(home.city),
            "location": sim.city_name(loc.city) if loc.city is not None else TRAVELLING,
            "position": _pos(sim, eid),
            "walk": None if act.kind != "walk" else {
                "to": _action_label(sim, act), "destination": (
                    {"x": round(act.walk_to[0], 3), "y": round(act.walk_to[1], 3)} if act.walk_to else None),
                "ticks_left": act.ticks_left, "then": act.then[0] if act.then else None},
            "house": None if home.house is None or not sim.world.alive(home.house) else {
                "id": home.house, "position": _pos(sim, home.house),
                "distance": _r(distance_to(sim, eid, home.house), 3), "at_home": at_home(sim, eid)},
            "travel": None if travel is None else {
                "origin": sim.city_name(travel.origin), "dest": sim.city_name(travel.dest),
                "ticks_left": travel.ticks_left},
            "stamina": {"current": _r(st.current, 1), "daily_max": _r(st.daily_max, 1),
                        "absolute_max": _r(st.absolute_max, 1), "sleep_hours_today": _r(st.sleep_hours_today, 1),
                        "good_rest_streak": st.good_rest_streak},
            "satiety": _r(met.satiety), "burn_per_hour": _r(met.base_burn * met.burn_multiplier, 4),
            "sickness": {"sick": bool(hl.sick), "probability": _r(hl.sickness_probability, 5),
                         "immune_strength": _r(hl.immune_strength)},
        },
        "needs": [{"need": n, "level": _r(needs.levels[n]), "comfort": _r(needs.comfort[n]),
                   "urgent": _r(needs.urgent[n]), "critical": _r(needs.critical[n]),
                   "urgency": _r(needs.urgency.get(n, 0.0)), "zero_hours": _r(needs.zero_hours.get(n, 0.0), 1)}
                  for n in needs.levels],
        "wallet": {"gold": _g(sim, sim.gold(eid)),
                   "inventory": {g: sim.inv(eid, g) for g in sim.goods},
                   "locked_gold": _g(sim, locked_gold), "locked_goods": locked_goods},
        "traits": {k: _r(getattr(tr, k)) for k in ("risk", "urgency_tolerance", "thrift", "luxury",
                                                    "forecast_noise", "reactivity", "stock_days")}
                  | {"need_weights": {k: _r(v) for k, v in tr.need_weights.items()},
                     "job_preference": {k: _r(v) for k, v in tr.job_preference.items()}},
        "skills": {k: _r(v) for k, v in w.get(eid, Skills).levels.items()},
        "action": {"kind": act.kind, "label": _action_label(sim, act), "ticks_left": act.ticks_left,
                   "reason": reason, "merchant": merchant},
        "orders": {"open": open_orders,
                   "bid_premium": {k: _r(v) for k, v in orders.bid_premium.items()},
                   "ask_discount": {k: _r(v) for k, v in orders.ask_discount.items()}},
        "plan": _plan(sim, eid, city),
        "jobs": _jobs(sim, eid, loc, act, rec),
        "history": _history(sim, eid, rec),
        "memory": memory,
        "tick": sim.tick, "day": sim.day(),
    }


def city_map(sim, city: str) -> dict:
    """The local map of one city (REQ-1C.2): its area, nodes, houses, workplaces and the citizens
    standing in it with their positions and destinations."""
    if city not in sim.city_ids:
        raise NotFound(f"no city {city!r}")
    cid = sim.city_ids[city]
    pos = sim.world.get(cid, Position)
    area = sim.world.get(cid, Area)
    nodes = [{"id": nid, "good": sim.world.get(nid, Yield).good, "stock": sim.ledger.balance(nid, sim.world.get(nid, Yield).good),
              **(_pos(sim, nid) or {"x": 0.0, "y": 0.0})} for nid in sim.nodes_by_city.get(cid, [])]
    houses = [{"id": eid, "occupant": h.occupant, **(_pos(sim, eid) or {"x": 0.0, "y": 0.0})}
              for eid, (h,) in sim.world.query(House) if h.city == cid]
    works = [{"id": eid, "task": w.task, "wage": w.wage / sim.unit, **(_pos(sim, eid) or {"x": 0.0, "y": 0.0})}
             for eid, (w,) in sim.world.query(Workplace) if w.city == cid]
    people = []
    for eid, (npc, loc, act) in sim.world.query(NPC, Location, Action):
        if loc.city != cid:
            continue
        p = sim.world.get(eid, Position)
        people.append({"id": eid, "name": npc.name, "x": round(p.x, 3), "y": round(p.y, 3), "action": _action_label(sim, act),
                       "walking": act.kind == "walk",
                       "to": None if act.kind != "walk" or not act.walk_to else
                       {"x": round(act.walk_to[0], 3), "y": round(act.walk_to[1], 3)}})
    return {"city": city, "spatial": bool(getattr(sim, "spatial", False)), "center": {"x": pos.x, "y": pos.y},
            "radius": area.radius if area else 0.0, "nodes": nodes, "houses": houses, "workplaces": works,
            "citizens": people, "tick": sim.tick, "day": sim.day()}
