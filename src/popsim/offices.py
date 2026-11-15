"""Offices and services (REQ-5A.9): a workplace of some task in a city, staffed while a citizen is at work there.

``staffed`` and ``office_for`` tell whether a service can be given now; ``issue_items`` hands items from the
city's inventory to a citizen (the estate finance worker's service, used by task jobs); ``procurement_orders`` is
what the finance worker does on shift: buy, with the treasury's gold, the items open tasks need beyond the depot.
A job that needs an office asks here and posts a ``service:<task>`` signal (REQ-5A.5), so offices are made by
demand (REQ-5A.6).
"""

from __future__ import annotations

from popsim.components import Action, Orders, Workplace
from popsim.ledger import GOLD

SERVICE = "estate_finance"


def _by_task(sim) -> dict[tuple[int, str], list[int]]:
    """(city, task) -> workplaces that have a citizen at work this tick (cached for the tick)."""
    cache = sim.__dict__.get("_staffed")
    if cache is not None and cache[0] == sim.tick:
        return cache[1]
    out: dict[tuple[int, str], list[int]] = {}
    seen = set()
    for _, (act,) in sim.world.query(Action):
        if act.kind == "work" and act.target is not None and act.target not in seen:
            wp = sim.world.get(act.target, Workplace)
            if wp is not None:
                seen.add(act.target)
                out.setdefault((wp.city, wp.task), []).append(act.target)
    for v in out.values():
        v.sort()
    sim._staffed = (sim.tick, out)
    return out


def staffed(sim, city: int, task: str) -> list[int]:
    return _by_task(sim).get((city, task), [])


def offices(sim, city: int, task: str) -> list[int]:
    return [w for w in sim.workplaces_by_city.get(city, []) if (wp := sim.world.get(w, Workplace)) and wp.task == task]


def office_exists(sim, city: int, task: str) -> bool:
    return bool(offices(sim, city, task))


def office_for(sim, city: int, task: str) -> int | None:
    on = staffed(sim, city, task)
    return on[0] if on else None


def reserved(sim, city: int, good: str) -> int:
    """Units of ``good`` in the depot promised to citizens who have not yet collected them."""
    from popsim.process import sites

    n = 0
    for _, tg in sites(sim):
        if tg.city != city:
            continue
        for data in tg.tags.values():
            for c in data["claims"].values():
                if not c.get("taken"):
                    n += (c.get("items") or {}).get(good, 0)
    return n


def unreserved(sim, city: int, good: str) -> int:
    return max(0, sim.ledger.balance(city, good) - reserved(sim, city, good))


def issue_items(sim, city: int, eid: int, items: dict) -> bool:
    """Hand ``items`` from the depot of ``city`` to citizen ``eid``, only while the office is staffed and the
    units are there. Returns whether it was done."""
    if not staffed(sim, city, SERVICE):
        return False
    if any(sim.ledger.balance(city, g) < n for g, n in items.items()):
        return False
    for g, n in sorted(items.items()):
        sim.ledger.transfer(city, eid, g, n, reason="issue")
    return True


def procurement_orders(sim) -> list[tuple[int, str, int, int]]:
    """(city, good, qty, price) buy orders for the cities whose finance worker is on shift: the units open tasks
    still need beyond what the depot holds and the orders already out, within half the treasury (REQ-5A.9)."""
    from popsim.process import sites, task_info

    out = []
    for cid in sorted(sim.city_ids.values()):
        if not staffed(sim, cid, SERVICE):
            continue
        want: dict[str, int] = {}
        for eid, tg in sites(sim):
            if tg.city != cid:
                continue
            for tag in sorted(tg.tags):
                info = task_info(sim, eid, tag)
                if info and info["service"] == SERVICE:
                    for g, n in info["items"].items():
                        want[g] = want.get(g, 0) + n
        if not want:
            continue
        orders = sim.world.get(cid, Orders)
        if orders is None:
            sim.world.add(cid, Orders())
            orders = sim.world.get(cid, Orders)
        budget = sim.gold(cid) // 2
        for g in sorted(want):
            out_qty = sum(o.qty for o in orders.open if o.side == "buy" and o.good == g)
            qty = want[g] - unreserved(sim, cid, g) - out_qty
            if qty <= 0:
                continue
            ref = sim.metrics._last_price.get((cid, g)) or sim.cfg["pricing"]["initial_prices"][g] * sim.unit
            price = max(1, int(round(ref * (1.1 + orders.bid_premium.get(g, 0.0)))))
            qty = min(qty, budget // price)
            if qty > 0:
                out.append((cid, g, qty, price))
                budget -= qty * price
    return out
