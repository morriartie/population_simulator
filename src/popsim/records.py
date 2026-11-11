"""Write side of the inspector (REQ-1C.6): fills, committed actions, work tasks,
daily totals and decision reasons are copied into each NPC's ``Record``.

Everything here only reads state the systems have already computed. It never
draws from ``sim.rng`` and never changes a decision, so a run gives the same
results with recording on or off (REQ-1.8).
"""

from __future__ import annotations

from popsim.components import Action, Branch, House, Location, Record, Workplace, Yield


def _rec(sim, eid: int) -> Record | None:
    return sim.world.get(eid, Record)


def on_fill(sim, city: int, f: dict) -> None:
    """One market fill, seen from both sides (called after settlement)."""
    qty, price, good = f["qty"], f["price"], f["good"]
    buyer, seller = f["buyer"], f["seller"]
    rec = _rec(sim, buyer)
    if rec is not None:
        rec.fills.append((sim.tick, city, good, "buy", qty, price, seller))
        rec.spent_today += qty * price
    rec = _rec(sim, seller)
    if rec is not None:
        rec.fills.append((sim.tick, city, good, "sell", qty, price, buyer))
        rec.earned_today += qty * price


def on_gather(sim, eid: int, units: int, city: int | None) -> None:
    """Units just gathered, valued at the NPC's own expected price."""
    rec = _rec(sim, eid)
    if rec is None or units <= 0:
        return
    from popsim.systems.agent import price_ref

    act = sim.world.get(eid, Action)
    y = sim.world.get(act.target, Yield) if act is not None and act.target is not None else None
    rec.pending_units += units
    if y is not None and city is not None:
        rec.pending_value += units * price_ref(sim, eid, city, y.good)


def on_work(sim, eid: int, net_cents: int) -> None:
    """Wages just received for a tick of public work, after tax."""
    rec = _rec(sim, eid)
    if rec is not None:
        rec.pending_value += net_cents


def on_walk(sim, eid: int, act: Action) -> None:
    """A tick of walking. Short walks finish inside the tick, so the end-of-tick state never
    shows them; remember it here so the action history still gets its entry."""
    rec = _rec(sim, eid)
    if rec is not None:
        rec.walked = {"tick": sim.tick, "target": act.target, "label": _label(sim, act),
                      "city": sim.city_name(sim.world.get(eid, Location).city)
                      if sim.world.get(eid, Location).city is not None else None}


def set_reason(sim, eid: int, choice: str, scores: dict | None = None, note: str = "") -> None:
    rec = _rec(sim, eid)
    if rec is not None:
        rec.reason = {"tick": sim.tick, "choice": choice, "scores": scores or {}, "note": note}


def _label(sim, act: Action) -> str | None:
    if act.target is None:
        return None
    if act.kind == "work":
        wp = sim.world.get(act.target, Workplace)
        return f"{wp.task} at {sim.city_name(wp.city)}" if wp is not None else None
    if act.kind == "gather":
        y = sim.world.get(act.target, Yield)
        loc = sim.world.get(act.target, Location)
        where = sim.city_name(loc.city) if loc is not None and loc.city is not None else (
            "the overworld" if sim.world.get(act.target, Branch) is not None else "?")
        return f"{y.good} at {where}" if y is not None else None
    if act.kind == "travel":
        return f"to {sim.city_name(act.target)}"
    if act.kind == "walk":
        y = sim.world.get(act.target, Yield)
        if y is not None:
            return f"to the {y.good} node"
        return "home" if sim.world.get(act.target, House) is not None else "somewhere"
    return None


def after_step(sim) -> None:
    """Called once at the end of every tick: open, extend and close history entries."""
    tpd = sim.ticks_per_day
    end_of_day = (sim.tick + 1) % tpd == 0
    for eid, (rec, act, loc) in sim.world.query(Record, Action, Location):
        t = sim.tick
        # -- work tasks: add what was gathered this tick, then follow the action
        cur = rec.cur_task
        if rec.pending_units and cur is not None:
            cur["units"] += rec.pending_units
            cur["value"] += rec.pending_value
        gathering = act.kind in ("gather", "work") and act.target is not None
        if cur is not None and (not gathering or cur["node"] != act.target):
            rec.cur_task = cur = None
        if gathering:
            if cur is None:
                y = sim.world.get(act.target, Yield)
                nloc = sim.world.get(act.target, Location)
                wp = sim.world.get(act.target, Workplace)
                cur = {"node": act.target, "good": y.good if y else (wp.task if wp else None),
                       "city": sim.city_name(nloc.city) if nloc and nloc.city is not None
                       else (sim.city_name(wp.city) if wp else ("overworld" if y else None)),
                       "start": t, "end": t, "units": rec.pending_units, "value": rec.pending_value}
                rec.tasks.append(cur)
                rec.cur_task = cur
            cur["end"] = t
        rec.pending_units, rec.pending_value = 0, 0.0

        # -- committed actions
        ca = rec.cur_action
        w, rec.walked = rec.walked, None
        if w is not None and not (act.kind == "walk" and act.target == w["target"]):
            # the walk began and ended within this tick: give it its own entry
            if ca is not None and ca["kind"] == "walk" and ca["target"] == w["target"]:
                ca["end"] = t
            else:
                r = rec.reason
                reason = dict(r) if r is not None and r["tick"] == t else {
                    "tick": t, "choice": "walk", "scores": {}, "note": "walking to a position"}
                ca = {"kind": "walk", "target": w["target"], "label": w["label"], "city": w["city"],
                      "start": t, "end": t, "reason": reason}
                rec.actions.append(ca)
            rec.cur_action = ca
        if ca is None or ca["kind"] != act.kind or ca["target"] != act.target:
            r = rec.reason
            if r is not None and r["tick"] == t:
                reason = dict(r)
            elif ca is not None and ca["kind"] in ("travel", "walk"):
                reason = {"tick": t, "choice": "arrived", "scores": {}, "note": "reached the destination"}
            else:
                reason = {"tick": t, "choice": "ended", "scores": {},
                          "note": "the previous action finished or was interrupted"}
            ca = {"kind": act.kind, "target": act.target, "label": _label(sim, act),
                  "city": sim.city_name(loc.city) if loc.city is not None else None,
                  "start": t, "end": t, "reason": reason}
            rec.actions.append(ca)
            rec.cur_action = ca
        ca["end"] = t

        if end_of_day:
            rec.daily.append({"day": sim.day(), "spent": rec.spent_today, "earned": rec.earned_today,
                              "gold": sim.gold(eid), "taxes": rec.taxes_today, "support": rec.support_today})
            rec.spent_today = rec.earned_today = rec.taxes_today = rec.support_today = 0

    if end_of_day:
        keep = (sim.cfg.get("inspector") or {}).get("dead_keep_days", 30)
        today = sim.day()
        for eid in [e for e, g in sim.graveyard.items() if today - g["day"] > keep]:
            del sim.graveyard[eid]


def archive(sim, eid: int, cause: str) -> None:
    """Freeze a dying NPC's final state so it can still be inspected (REQ-1C.5)."""
    from popsim.inspector import citizen_detail

    try:
        detail = citizen_detail(sim, eid)
    except Exception:  # noqa: BLE001 - never let the inspector break a run
        return
    detail["alive"] = False
    detail["died"] = {"day": sim.day(), "tick": sim.tick, "cause": cause}
    sim.graveyard[eid] = {"day": sim.day(), "cause": cause, "detail": detail}
