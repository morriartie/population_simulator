"""Demand signals (REQ-5A.5): the points that needs and blocked tasks put on a city's board.

Once a day, at ``signals.hour``, every emitter posts points for each city; a signal's **gauge** is the sum of
its live points, recomputed at each sampling, so a thousand hungry citizens cost one number. The board also
keeps a capped list of point positions for drawing them on the map. Generators (``popsim.generators``) read
the gauges; nothing else in the simulation does, so signals change no result by themselves.

Emitters are a registry (``EMITTERS``): a function ``emit(sim, post)`` that calls ``post(city, kind, at=None)``.
They visit entities in id order and draw no random numbers.
"""

from __future__ import annotations

from popsim.components import Location, NPC, Needs, Position, Trip


def residents(sim, cid: int) -> list[int]:
    """Citizens of ``cid``: in the city now, or out on a gathering trip that started there (REQ-3A.11)."""
    out = []
    for eid, (_, loc, _n) in sim.world.query(NPC, Location, Needs):
        if loc.city == cid:
            out.append(eid)
        elif loc.city is None:
            trip = sim.world.get(eid, Trip)
            if trip is not None and trip.origin == cid:
                out.append(eid)
    return out


def board(sim) -> dict:
    """city id -> signal kind -> {"count", "residents", "share", "points"}; replaced at each sampling."""
    return sim.__dict__.setdefault("demand", {})


def gauge(sim, cid: int, kind: str) -> dict:
    g = board(sim).get(cid, {}).get(kind)
    return g if g is not None else {"count": 0, "residents": len(residents(sim, cid)), "share": 0.0, "points": []}


def need_emitter(sim, post) -> None:
    """One ``need:<name>`` point per resident whose need is at or below its urgent level (the test support
    payments use, REQ-5.5), at the citizen's position."""
    for name in sim.cfg["signals"]["needs"]:
        for cid in sorted(sim.city_ids.values()):
            for eid in residents(sim, cid):
                n = sim.world.get(eid, Needs)
                if n.levels[name] <= n.urgent[name]:
                    p = sim.world.get(eid, Position)
                    post(cid, f"need:{name}", (p.x, p.y) if p is not None else None)


def requirement_emitter(sim, post) -> None:
    """One ``service:<task>`` point per open site task that needs an office to hand out its items (REQ-5A.7)."""
    from popsim.process import service_demands

    for cid, service, at in service_demands(sim):
        post(cid, f"service:{service}", at)


EMITTERS = [need_emitter, requirement_emitter]


def sample(sim) -> None:
    """Recompute every gauge from the emitters and add one row per city and signal to the log."""
    pts: dict[tuple[int, str], list] = {}

    def post(cid, kind, at=None):
        pts.setdefault((cid, kind), []).append(at)

    for emit in EMITTERS:
        emit(sim, post)
    cap = int(sim.cfg["signals"].get("max_points", 40))
    new: dict[int, dict] = {}
    kinds = [f"need:{n}" for n in sim.cfg["signals"]["needs"]]
    kinds += sorted({k for (_c, k) in pts if k not in kinds})
    log = sim.__dict__.setdefault("signal_log", [])
    for cid in sorted(sim.city_ids.values()):
        res = len(residents(sim, cid))
        for kind in kinds:
            ps = pts.get((cid, kind), [])
            share = len(ps) / res if res else 0.0
            new.setdefault(cid, {})[kind] = {"count": len(ps), "residents": res, "share": share,
                                             "points": [list(p) for p in ps if p is not None][:cap]}
            log.append({"day": sim.day(), "city": sim.city_name(cid), "signal": kind, "count": len(ps),
                        "residents": res, "share": round(share, 4)})
    sim.demand = new


class SignalSystem:
    name = "signals"

    def run(self, sim) -> None:
        from popsim.generators import active

        if sim.at_hour(float(sim.cfg["signals"]["hour"])) and active(sim):
            sample(sim)
