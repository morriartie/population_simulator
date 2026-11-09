"""The player's trading account (REQ-1C.9): the user buys and sells by hand on the live world.

The player is an entity that owns gold and goods in the ledger, like any other account, but it is
not an NPC: no needs, no death, not counted in any population figure. It is "god mode": it trades
in any city and never travels.

Orders are *scheduled*. ``queue_order`` only locks the funds and records the order; the market
system (``systems/market.py``) sends the queued orders to the market API at the next tick, mixed into
the same seeded shuffle as the NPCs' orders, and settles every fill through ``settle_fill``.
Money and goods enter the account only through the opening grant and top-ups, both explicit
central-authority injections (REQ-5.1) that are logged in the ledger and in the action log.

Nothing here draws from ``sim.rng``. Every user action is appended to ``Player.actions`` with the
tick it was applied at, so ``replay`` can reproduce a run (REQ-1.8).
"""

from __future__ import annotations

from popsim.components import Player, PlayerOrder
from popsim.ledger import GOLD, escrow_account

ACTIVE = ("queued", "resting", "cancelling")
MAX_FILLS = 20000  # bounded history
MAX_DONE = 300  # finished orders kept for display


class PlayerError(ValueError):
    """A refused player action; ``code`` says why (the dashboard shows it)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# ----------------------------------------------------------------------- lookups


def account(sim) -> tuple[int | None, Player | None]:
    eid = getattr(sim, "player", None)
    if eid is None or not sim.world.alive(eid):
        return None, None
    return eid, sim.world.get(eid, Player)


def is_player(sim, eid: int) -> bool:
    return eid is not None and getattr(sim, "player", None) == eid


def _need(sim) -> tuple[int, Player]:
    eid, p = account(sim)
    if p is None:
        raise PlayerError("no_account", "the player account is not open yet")
    return eid, p


def _city(sim, name: str) -> int:
    cid = sim.city_ids.get(name)
    if cid is None:
        raise PlayerError("unknown_city", f"unknown city {name!r}")
    return cid


def _good(sim, good: str) -> str:
    if good not in sim.goods:
        raise PlayerError("unknown_good", f"unknown good {good!r}")
    return good


def _order(p: Player, oid) -> PlayerOrder:
    try:
        oid = int(oid)
    except (TypeError, ValueError):
        raise PlayerError("unknown_order", f"unknown order {oid!r}") from None
    for o in p.orders:
        if o.id == oid:
            return o
    raise PlayerError("unknown_order", f"no order {oid}")


def _log(sim, p: Player, kind: str, **fields) -> None:
    p.actions.append({"tick": sim.tick, "day": sim.day(), "kind": kind, **fields})


def to_cents(sim, value, field: str) -> int:
    """Gold units from the dashboard to whole cents (REQ-1A.4): refuse fractions of a cent."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PlayerError("bad_" + field, f"{field} must be a number")
    cents = round(value * sim.unit)
    if abs(value * sim.unit - cents) > 1e-6:
        raise PlayerError("bad_" + field, f"{field} must be a whole number of cents (1/{sim.unit} gold)")
    return int(cents)


def _int(value, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        if isinstance(value, float) and value == int(value):
            return int(value)
        raise PlayerError("bad_" + field, f"{field} must be a whole number")
    return value


# ----------------------------------------------------------------------- prices


def ref_price(sim, good: str, city: int | None = None) -> int:
    """A price in cents to value a good at: the mean of the cities' last prices, else the scenario's
    starting price. It does not depend on the city being viewed, so net worth, the injected value
    and the profit always use the same prices. (``city`` is accepted and ignored.)"""
    last = sim.metrics._last_price
    seen = [v for (c, g), v in sorted(last.items()) if g == good]
    if seen:
        return int(round(sum(seen) / len(seen)))
    return int(round(sim.cfg["pricing"]["initial_prices"].get(good, 0) * sim.unit))


# ----------------------------------------------------------------------- account


def _inject(sim, eid: int, p: Player, gold: int, goods: dict, reason: str, city: int | None) -> None:
    """Central-authority injection (REQ-5.1): the only way money or goods enter the account."""
    if gold > 0:
        sim.ledger.transfer("authority", eid, GOLD, gold, reason=reason)
        p.injected_gold += gold
        p.injected_value += gold
    for g, qty in sorted(goods.items()):
        if qty <= 0:
            continue
        sim.ledger.transfer("nature", eid, g, qty, reason=reason)
        sim.metrics.count("inject", g, qty)
        value = ref_price(sim, g, city)
        p.injected_goods[g] = p.injected_goods.get(g, 0) + qty
        p.injected_value += qty * value
        b = p.basis.setdefault(g, [0, 0])
        b[0] += qty
        b[1] += qty * value


def open_account(sim) -> int:
    """Create the player and give it the opening grant. Does nothing if it already exists."""
    eid, p = account(sim)
    if p is not None:
        return eid
    pc = sim.cfg.get("player") or {}
    if pc.get("gold") is not None:
        gold = int(round(float(pc["gold"]) * sim.unit))
    else:
        base = sim.npc_gold_start
        if base is None:  # a world saved before the figure was kept
            from popsim.components import NPC

            base = sum(sim.gold(e) for e in sim.world.ids(NPC))
        gold = int(round(base * float(pc.get("gold_share", 0.5))))
    goods = {g: int(q) for g, q in sorted((pc.get("goods") or {}).items()) if g in sim.goods}
    p = Player(opened_tick=sim.tick)
    eid = sim.world.create(p)
    sim.player = eid
    _log(sim, p, "open")
    _inject(sim, eid, p, gold, goods, "player_grant", None)
    p.actions[-1].update(gold=gold, goods=goods)
    _record_networth(sim, eid, p)
    return eid


def top_up(sim, gold: int = 0, goods: dict | None = None, city: str | None = None) -> dict:
    """Add gold (cents) and goods to the account: an explicit injection, applied at once."""
    eid, p = _need(sim)
    gold = _int(gold, "gold")
    goods = {_good(sim, g): _int(q, "goods") for g, q in sorted((goods or {}).items())}
    if gold < 0 or any(q < 0 for q in goods.values()):
        raise PlayerError("bad_amount", "amounts cannot be negative")
    if gold == 0 and not any(goods.values()):
        raise PlayerError("bad_amount", "nothing to add")
    cid = _city(sim, city) if city else None
    _inject(sim, eid, p, gold, goods, "player_topup", cid)
    _log(sim, p, "topup", gold=gold, goods={g: q for g, q in goods.items() if q}, city=city)
    return {"gold": gold, "goods": goods}


# ----------------------------------------------------------------------- orders


def _funds(sim, eid: int, side: str, good: str, qty: int, price: int) -> None:
    if side == "buy":
        if sim.gold(eid) < qty * price:
            raise PlayerError("not_enough_gold", f"needs {qty * price / sim.unit:g} gold, the wallet has "
                              f"{sim.gold(eid) / sim.unit:g}")
    elif sim.inv(eid, good) < qty:
        raise PlayerError("not_enough_goods", f"needs {qty} {good}, the wallet has {sim.inv(eid, good)}")


def _lock(sim, eid: int, cid: int, side: str, good: str, qty: int, price: int) -> None:
    esc = escrow_account(cid)
    if side == "buy":
        sim.ledger.transfer(eid, esc, GOLD, qty * price, reason="escrow_lock")
    else:
        sim.ledger.transfer(eid, esc, good, qty, reason="escrow_lock")


def _unlock(sim, eid: int, cid: int, side: str, good: str, qty: int, price: int) -> None:
    esc = escrow_account(cid)
    if side == "buy":
        sim.ledger.transfer(esc, eid, GOLD, qty * price, reason="escrow_release")
    else:
        sim.ledger.transfer(esc, eid, good, qty, reason="escrow_release")


def _trim(p: Player) -> None:
    done = [o for o in p.orders if o.status not in ACTIVE]
    if len(done) > MAX_DONE:
        drop = {o.id for o in done[: len(done) - MAX_DONE]}
        p.orders = [o for o in p.orders if o.id not in drop]


def queue_order(sim, city: str, good: str, side: str, qty, price, kind: str = "limit",
                expires_hours=None) -> PlayerOrder:
    """Queue an order (price in cents). It is matched at the next tick, not now (REQ-1C.9)."""
    eid, p = _need(sim)
    cid = _city(sim, city)
    _good(sim, good)
    if side not in ("buy", "sell"):
        raise PlayerError("bad_side", "side must be buy or sell")
    if kind not in ("limit", "market"):
        raise PlayerError("bad_type", "order type must be limit or market")
    qty, price = _int(qty, "qty"), _int(price, "price")
    if qty <= 0:
        raise PlayerError("bad_qty", "quantity must be at least 1")
    if price <= 0:
        raise PlayerError("bad_price", "price must be positive")
    if expires_hours is not None:
        if isinstance(expires_hours, bool) or not isinstance(expires_hours, (int, float)) or expires_hours <= 0:
            raise PlayerError("bad_expiry", "expiry must be a positive number of hours")
        if kind == "market":
            raise PlayerError("bad_expiry", "a market order never stays on the book, so it has no expiry")
    _funds(sim, eid, side, good, qty, price)
    _lock(sim, eid, cid, side, good, qty, price)
    o = PlayerOrder(id=p.next_id, city=cid, side=side, good=good, qty=qty, price=price, kind=kind,
                    expires_hours=expires_hours, queued_tick=sim.tick, left=qty)
    p.next_id += 1
    p.orders.append(o)
    _log(sim, p, "queue", city=city, good=good, side=side, qty=qty, price=price, order_type=kind,
         expires_hours=expires_hours)
    _trim(p)
    return o


def edit_order(sim, oid, qty=None, price=None) -> PlayerOrder:
    """Change a queued order. A resting one cannot be edited: cancel it and queue a new one."""
    eid, p = _need(sim)
    o = _order(p, oid)
    if o.status != "queued":
        raise PlayerError("not_queued", "only an order that is still queued can be edited")
    new_qty = o.qty if qty is None else _int(qty, "qty")
    new_price = o.price if price is None else _int(price, "price")
    if new_qty <= 0:
        raise PlayerError("bad_qty", "quantity must be at least 1")
    if new_price <= 0:
        raise PlayerError("bad_price", "price must be positive")
    _unlock(sim, eid, o.city, o.side, o.good, o.qty, o.price)
    try:
        _funds(sim, eid, o.side, o.good, new_qty, new_price)
    except PlayerError:
        _lock(sim, eid, o.city, o.side, o.good, o.qty, o.price)
        raise
    _lock(sim, eid, o.city, o.side, o.good, new_qty, new_price)
    o.qty = o.left = new_qty
    o.price = new_price
    _log(sim, p, "edit", id=o.id, qty=new_qty, price=new_price)
    return o


def cancel_order(sim, oid) -> PlayerOrder:
    """A queued order is cancelled at once; a resting one is cancelled at the next tick."""
    eid, p = _need(sim)
    o = _order(p, oid)
    if o.status == "queued":
        _unlock(sim, eid, o.city, o.side, o.good, o.left, o.price)
        o.status, o.note = "cancelled", "cancelled before it ran"
    elif o.status == "resting":
        o.status = "cancelling"
    elif o.status == "cancelling":
        return o
    else:
        raise PlayerError("not_open", f"order {o.id} is already {o.status}")
    _log(sim, p, "cancel", id=o.id)
    return o


def cancel_all(sim) -> int:
    eid, p = _need(sim)
    n = 0
    for o in list(p.orders):
        if o.status in ("queued", "resting"):
            cancel_order(sim, o.id)
            n += 1
    return n


# ----------------------------------------------------------------------- hooks for the market system


def cancel_ops(sim) -> dict[int, list[dict]]:
    """Cancellations to send at the start of the tick: the ones the user asked for and expired orders."""
    eid, p = account(sim)
    out: dict[int, list[dict]] = {}
    if p is None:
        return out
    for o in p.orders:
        if o.status == "resting" and o.expires_hours is not None and o.placed_tick is not None \
                and sim.tick - o.placed_tick >= sim.hours_to_ticks(o.expires_hours):
            o.status, o.note = "cancelling", "expired"
        if o.status == "cancelling" and o.order_id is not None:
            out.setdefault(o.city, []).append(
                {"op": "cancel", "order_id": o.order_id, "owner": eid, "ref": eid, "po": o.id})
    return out


def place_ops(sim) -> dict[int, list[dict]]:
    """The queued orders, per city, in the order the user queued them."""
    eid, p = account(sim)
    out: dict[int, list[dict]] = {}
    if p is None:
        return out
    for o in p.orders:
        if o.status == "queued":
            out.setdefault(o.city, []).append(
                {"op": "place", "owner": eid, "side": o.side, "good": o.good, "qty": o.qty,
                 "price": o.price, "ref": eid, "po": o.id})
    return out


def merge_shuffled(sim, npc_ops: list[dict], player_ops: list[dict]) -> list[dict]:
    """Mix the player's orders into the NPCs' orders with the seeded shuffle; the player's own orders
    keep the order they were queued in."""
    ops = npc_ops + player_ops
    sim.rng.shuffle(ops)
    if player_ops:
        slots = [i for i, op in enumerate(ops) if "po" in op]
        for i, op in zip(slots, player_ops):
            ops[i] = op
    return ops


def on_place_result(sim, city: int, op: dict, res: dict) -> bool:
    """Handle the market's answer to a queued order. Returns True if a market order has a rest to cancel."""
    eid, p = account(sim)
    o = _order(p, op["po"])
    if not res["ok"]:
        _unlock(sim, eid, city, o.side, o.good, o.left, o.price)
        o.status, o.note = "rejected", res.get("error", "rejected")
        return False
    o.order_id = res["order_id"]
    o.placed_tick = sim.tick
    return True


def after_place(sim, city: int, op: dict, res: dict) -> bool:
    """Called after the fills of the order were settled. Returns True if the unfilled rest of a
    market order must be cancelled now."""
    eid, p = account(sim)
    o = _order(p, op["po"])
    if o.status == "filled":
        return False
    if res["remaining"] == 0 or o.left == 0:
        o.status = "filled"
        return False
    if o.kind == "market":
        return True
    o.status = "resting"
    return False


def on_cancel_result(sim, city: int, op: dict, res: dict, market_rest: bool = False) -> None:
    eid, p = account(sim)
    o = _order(p, op["po"])
    if res["ok"]:
        _unlock(sim, eid, city, o.side, o.good, res["remaining"], res["price"])
        if market_rest:
            o.status, o.note = "cancelled", "unfilled rest of a market order cancelled"
        elif o.note == "expired":
            o.status = "expired"
        else:
            o.status, o.note = "cancelled", "cancelled"
    else:  # a fill came first
        o.note = "filled before the cancel could run"
        o.status = "filled" if o.left <= 0 else "resting"


def on_fill(sim, city: int, f: dict, tax_buyer: int, tax_seller: int) -> None:
    """A fill the player took part in (called from ``settle_fill`` after settlement)."""
    eid, p = account(sim)
    if p is None:
        return
    qty, price, good = f["qty"], f["price"], f["good"]
    for side, order_id, tax, other in (("buy", f["buy_order"], tax_buyer, f["seller"]),
                                       ("sell", f["sell_order"], tax_seller, f["buyer"])):
        owner = f["buyer"] if side == "buy" else f["seller"]
        if owner != eid:
            continue
        o = next((x for x in p.orders if x.order_id == order_id and x.city == city), None)
        if o is not None:
            o.filled += qty
            o.value += qty * price
            o.left -= qty
            if o.left <= 0:
                o.status = "filled"
        b = p.basis.setdefault(good, [0, 0])
        if side == "buy":
            b[0] += qty
            b[1] += qty * price
        else:
            avg = b[1] / b[0] if b[0] > 0 else 0.0
            used = min(qty, b[0])
            b[0] -= used
            b[1] -= int(round(avg * used))
            p.realized[good] = p.realized.get(good, 0) + int(round(qty * price - avg * qty))
        p.taxes += tax
        p.fills.append({"tick": sim.tick, "day": sim.day(), "city": sim.city_name(city), "good": good,
                        "side": side, "qty": qty, "price": price, "total": qty * price, "tax": tax,
                        "counterparty": other,
                        "role": "maker" if f["maker_order"] == order_id else "taker",
                        "order": o.id if o is not None else None})
    if len(p.fills) > MAX_FILLS:
        del p.fills[: len(p.fills) - MAX_FILLS]


# ----------------------------------------------------------------------- numbers


def locked(sim, p: Player) -> tuple[int, dict]:
    gold, goods = 0, {}
    for o in p.orders:
        if o.status in ACTIVE:
            if o.side == "buy":
                gold += o.left * o.price
            else:
                goods[o.good] = goods.get(o.good, 0) + o.left
    return gold, goods


def worth(sim, eid: int, p: Player, city: int | None = None) -> dict:
    """Wallet, locked funds and net worth in cents (goods at the city's last price)."""
    lg, lgoods = locked(sim, p)
    gold = sim.gold(eid)
    goods = {g: sim.inv(eid, g) for g in sim.goods}
    held = {g: goods[g] + lgoods.get(g, 0) for g in sim.goods}
    value = {g: held[g] * ref_price(sim, g, city) for g in sim.goods}
    net = gold + lg + sum(value.values())
    return {"gold": gold, "goods": goods, "locked_gold": lg, "locked_goods": lgoods, "value": value,
            "net_worth": net, "profit": net - p.injected_value}


def _record_networth(sim, eid: int, p: Player) -> None:
    w = worth(sim, eid, p)
    row = {"day": sim.day(), "tick": sim.tick, "gold": w["gold"], "net_worth": w["net_worth"],
           "injected": p.injected_value, "profit": w["profit"]}
    if p.networth and p.networth[-1]["day"] == row["day"]:
        p.networth[-1] = row
    else:
        p.networth.append(row)


def after_step(sim) -> None:
    """End of tick: one net-worth row per day."""
    eid, p = account(sim)
    if p is not None and (sim.tick + 1) % sim.ticks_per_day == 0:
        _record_networth(sim, eid, p)


# ----------------------------------------------------------------------- views


def _g(sim, cents) -> float:
    return round(cents / sim.unit, 4)


def order_view(sim, o: PlayerOrder) -> dict:
    return {"id": o.id, "city": sim.city_name(o.city), "good": o.good, "side": o.side, "qty": o.qty,
            "left": o.left, "filled": o.filled, "price": _g(sim, o.price), "type": o.kind,
            "status": o.status, "expires_hours": o.expires_hours, "note": o.note,
            "queued_tick": o.queued_tick, "placed_tick": o.placed_tick,
            "age_ticks": sim.tick - (o.queued_tick if o.placed_tick is None else o.placed_tick),
            "avg_price": _g(sim, o.value / o.filled) if o.filled else None}


def state(sim, history: int = 200, city: str | None = None) -> dict:
    """Everything the Market tab shows about the account (prices in gold units)."""
    eid, p = account(sim)
    if p is None:
        return {"open": False, "unit": sim.unit}
    cid = sim.city_ids.get(city) if city else None
    w = worth(sim, eid, p, cid)
    per_good: dict[str, dict] = {}
    for f in p.fills:
        t = per_good.setdefault(f["good"], {"bought": 0, "sold": 0, "spent": 0, "earned": 0})
        if f["side"] == "buy":
            t["bought"] += f["qty"]
            t["spent"] += f["total"]
        else:
            t["sold"] += f["qty"]
            t["earned"] += f["total"]
    totals = {g: {"bought": t["bought"], "sold": t["sold"], "spent": _g(sim, t["spent"]),
                  "earned": _g(sim, t["earned"]),
                  "avg_buy": _g(sim, t["spent"] / t["bought"]) if t["bought"] else None,
                  "avg_sell": _g(sim, t["earned"] / t["sold"]) if t["sold"] else None,
                  "realized": _g(sim, p.realized.get(g, 0))} for g, t in sorted(per_good.items())}
    return {
        "open": True, "id": eid, "unit": sim.unit, "opened_tick": p.opened_tick,
        "gold": _g(sim, w["gold"]), "goods": w["goods"],
        "locked": {"gold": _g(sim, w["locked_gold"]), "goods": w["locked_goods"]},
        "value": {g: _g(sim, v) for g, v in w["value"].items()},
        "net_worth": _g(sim, w["net_worth"]),
        "injected": {"gold": _g(sim, p.injected_gold), "goods": dict(sorted(p.injected_goods.items())),
                     "value": _g(sim, p.injected_value)},
        "profit": _g(sim, w["profit"]),
        "realized": _g(sim, sum(p.realized.values())), "taxes": _g(sim, p.taxes),
        "orders": [order_view(sim, o) for o in p.orders if o.status in ACTIVE],
        "finished": [order_view(sim, o) for o in reversed(p.orders) if o.status not in ACTIVE][:30],
        "history": [{**f, "price": _g(sim, f["price"]), "total": _g(sim, f["total"]), "tax": _g(sim, f["tax"])}
                    for f in reversed(p.fills[-history:])],
        "fills": len(p.fills), "totals": totals,
        "networth": [{"day": r["day"], "net_worth": _g(sim, r["net_worth"]), "profit": _g(sim, r["profit"]),
                      "injected": _g(sim, r["injected"])} for r in p.networth],
        "actions": len(p.actions),
    }


def _levels(rows: list, mine: set, n: int = 10) -> list[list]:
    """[price, qty] per order -> [price, qty, own] per price level, best first."""
    out: list[list] = []
    for price, qty in rows:
        if out and out[-1][0] == price:
            out[-1][1] += qty
        else:
            out.append([price, qty, False])
        if price in mine:
            out[-1][2] = True
    return out[:n]


def market_view(sim, city: str, good: str | None = None) -> dict:
    """Goods table, the book of one good, the same good in every city, and its price series."""
    cid = _city(sim, city)
    eid, p = account(sim)
    good = _good(sim, good) if good else sim.goods[0]
    held = worth(sim, eid, p, cid) if p is not None else None
    daily: dict[str, list] = {}
    for r in sim.metrics.daily_prices:
        if r["city"] == city:
            daily.setdefault(r["good"], []).append(r)
    table = []
    for g in sim.goods:
        top = sim.board.get(cid, {}).get(g, {})
        bid, ask = top.get("bid"), top.get("ask")
        rows = daily.get(g, [])
        last = sim.metrics._last_price.get((cid, g))
        prev = next((r["last"] for r in reversed(rows[:-1]) if r["last"] is not None), None) if rows else None
        today = next((r["last"] for r in reversed(rows) if r["last"] is not None), None)
        table.append({
            "good": g, "last": _g(sim, last) if last is not None else None,
            "bid": _g(sim, bid) if bid else None, "ask": _g(sim, ask) if ask else None,
            "spread": _g(sim, ask - bid) if bid and ask else None,
            "volume": rows[-1]["volume"] if rows else 0,
            "change": round(today - prev, 4) if today is not None and prev is not None else None,
            "held": held["goods"][g] if held else None,
            "locked": held["locked_goods"].get(g, 0) if held else None})
    depth = sim.market.book(sim.world_name, city, good, 60)
    mine_b = {o.price for o in (p.orders if p else []) if o.city == cid and o.good == good
              and o.side == "buy" and o.status in ("resting", "cancelling")}
    mine_a = {o.price for o in (p.orders if p else []) if o.city == cid and o.good == good
              and o.side == "sell" and o.status in ("resting", "cancelling")}
    book = {"good": good,
            "bids": [[_g(sim, a), b, c] for a, b, c in _levels(depth["bids"], mine_b)],
            "asks": [[_g(sim, a), b, c] for a, b, c in _levels(depth["asks"], mine_a)]}
    cross = []
    for name, c2 in sorted(sim.city_ids.items()):
        top = sim.board.get(c2, {}).get(good, {})
        last = sim.metrics._last_price.get((c2, good))
        cross.append({"city": name, "last": _g(sim, last) if last is not None else None,
                      "bid": _g(sim, top["bid"]) if top.get("bid") else None,
                      "ask": _g(sim, top["ask"]) if top.get("ask") else None})
    series = [{"day": r["day"], "last": r["last"], "vwap": r["vwap"], "volume": r["volume"]}
              for r in daily.get(good, [])]
    return {"city": city, "good": good, "tick": sim.tick, "day": sim.day(), "goods": table, "book": book,
            "cross": cross, "series": series}


def world_info(sim) -> dict:
    return {"world": sim.world_name, "cities": sorted(sim.city_ids), "goods": list(sim.goods),
            "unit": sim.unit, "tick": sim.tick, "day": sim.day(), "hour": sim.hour(),
            "open": account(sim)[1] is not None}


def quote(sim, city: str, good: str, side: str, qty, price, kind: str = "limit") -> dict:
    """What an order would do against the book as it is now. An estimate: the book can change before
    the order runs at the next tick."""
    eid, p = _need(sim)
    cid = _city(sim, city)
    _good(sim, good)
    qty, price = _int(qty, "qty"), _int(price, "price")
    if side not in ("buy", "sell"):
        raise PlayerError("bad_side", "side must be buy or sell")
    if qty <= 0 or price <= 0:
        raise PlayerError("bad_qty" if qty <= 0 else "bad_price", "quantity and price must be positive")
    from popsim.systems.fiscal import policy

    depth = sim.market.book(sim.world_name, city, good, 200)
    rows = depth["asks"] if side == "buy" else depth["bids"]
    left, gross = qty, 0
    for lvl, q in rows:
        if (side == "buy" and lvl > price) or (side == "sell" and lvl < price):
            break
        take = min(left, q)
        gross += take * lvl
        left -= take
        if not left:
            break
    got = qty - left
    pol = policy(sim, cid)
    tax = int(round(gross * (pol["sales_tax"] if side == "buy" else pol["income_tax"])))
    lock_gold = qty * price if side == "buy" else 0
    funds = None
    try:
        _funds(sim, eid, side, good, qty, price)
    except PlayerError as e:
        funds = {"code": e.code, "message": str(e)}
    gold_now = sim.gold(eid)
    return {"fill_qty": got, "rest_qty": left, "avg_price": _g(sim, gross / got) if got else None,
            "gross": _g(sim, gross), "tax": _g(sim, tax), "locked_gold": _g(sim, lock_gold),
            "wallet_after_lock": _g(sim, gold_now - lock_gold),
            "wallet_after_fill": _g(sim, gold_now - lock_gold + (lock_gold - gross - tax if side == "buy" else gross - tax)),
            "rests": kind == "limit" and left > 0, "funds_error": funds,
            "tax_rate": pol["sales_tax"] if side == "buy" else pol["income_tax"],
            "estimate": True}


# ----------------------------------------------------------------------- output and replay


def write_outputs(sim, out) -> None:
    """``player_trades.csv`` and the action log of a saved run (REQ-1C.9, REQ-1.9)."""
    import json
    from pathlib import Path

    from popsim.io.logs import write_csv

    eid, p = account(sim)
    if p is None:
        return
    out = Path(out)
    write_csv([{**f, "price": f["price"] / sim.unit, "total": f["total"] / sim.unit,
                "tax": f["tax"] / sim.unit} for f in p.fills], out / "player_trades.csv")
    (out / "player_actions.json").write_text(json.dumps(p.actions, indent=1, default=str))


def apply_action(sim, a: dict) -> None:
    """Apply one logged action (used to replay a run)."""
    k = a["kind"]
    if k == "open":
        open_account(sim)
    elif k == "topup":
        top_up(sim, a.get("gold", 0), a.get("goods"), a.get("city"))
    elif k == "queue":
        queue_order(sim, a["city"], a["good"], a["side"], a["qty"], a["price"], a.get("order_type", "limit"),
                    a.get("expires_hours"))
    elif k == "edit":
        edit_order(sim, a["id"], a.get("qty"), a.get("price"))
    elif k == "cancel":
        cancel_order(sim, a["id"])
    else:
        raise ValueError(f"unknown player action {k!r}")


def replay(sim, actions: list[dict], ticks: int) -> None:
    """Step ``ticks`` ticks, applying each logged action at the tick it was originally applied at."""
    pending = sorted(range(len(actions)), key=lambda i: (actions[i]["tick"], i))
    i = 0
    for _ in range(ticks):
        while i < len(pending) and actions[pending[i]]["tick"] <= sim.tick:
            apply_action(sim, actions[pending[i]])
            i += 1
        sim.step()
