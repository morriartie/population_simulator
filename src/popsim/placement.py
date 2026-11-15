"""Placements (REQ-5A.6): where a generator puts what it creates. A registry of functions
``place(sim, city, params) -> dict | None``; none draws random numbers from the simulation's stream."""

from __future__ import annotations

import math
import random

from popsim.components import Area, Position, Tags
from popsim.world import fertility as F


def fertile_site(sim, cid: int, params: dict | None = None) -> dict | None:
    """The best spot for a site: outside every city, off a road that touches the city but near it, at least
    ``min_fertility``, and ``min_spacing`` from other nodes and sites; the best has the highest fertility less
    ``distance_penalty`` per hour of walking from the city (REQ-5A.2). Returns ``{"at", "branch", "fertility"}``."""
    s = {**sim.cfg["fertility"], **(params or {})}
    circles = [(p.x, p.y, a.radius) for _, (p, a) in sim.world.query(Position, Area)]
    taken = [(sim.world.get(n, Position).x, sim.world.get(n, Position).y) for n in sim.overworld_nodes]
    taken += [(sim.world.get(e, Position).x, sim.world.get(e, Position).y)
              for e, (t,) in sim.world.query(Tags) if not t.done]
    speed = ((sim.cfg.get("movement") or {}).get("road") or {"speed": 5.0})["speed"]
    best = None
    for _other, _rid, road in sim.roads_from(cid):
        a, b = road.a, road.b
        pa, pb = sim.world.get(a, Position), sim.world.get(b, Position)
        dx, dy = pb.x - pa.x, pb.y - pa.y
        L = math.hypot(dx, dy)
        if L <= 0:
            continue
        nx, ny = -dy / L, dx / L
        for ti in range(1, 10):
            t = ti / 10.0
            jx, jy = pa.x + dx * t, pa.y + dy * t
            frac = t if cid == a else 1.0 - t
            off = 2.0
            while off <= float(s["max_offset"]) + 1e-9:
                for side in (1, -1):
                    x, y = jx + nx * off * side, jy + ny * off * side
                    if any(math.hypot(x - cx, y - cy) <= r + float(s["city_margin"]) for cx, cy, r in circles):
                        continue
                    if any(math.hypot(x - tx, y - ty) < float(s["min_spacing"]) for tx, ty in taken):
                        continue
                    f = F.fertility(s, x, y)
                    if f < float(s["min_fertility"]):
                        continue
                    hours = road.hours * frac + off / speed
                    score = f - float(s["distance_penalty"]) * hours
                    key = (-round(score, 9), ti, -side, off)
                    if best is None or key < best[0]:
                        best = (key, x, y, a, b, t, f)
                off += 2.0
    if best is None:
        return None
    _, x, y, a, b, t, f = best
    return {"at": [round(x, 4), round(y, 4)], "branch": {"road": [sim.city_name(a), sim.city_name(b)], "t": t},
            "fertility": f}


def in_city(sim, cid: int, params: dict | None = None) -> dict:
    """A position inside the city, reproducible from the seed, the city and a serial number."""
    serial = (params or {}).get("serial", 0)
    c, area = sim.world.get(cid, Position), sim.world.get(cid, Area)
    rng = random.Random(f"popsim-office:{sim.cfg['seed']}:{cid}:{serial}")
    r = (area.radius if area else 0.0) * 0.7 * math.sqrt(rng.random())
    a = rng.random() * 2 * math.pi
    return {"at": [round(c.x + r * math.cos(a), 4), round(c.y + r * math.sin(a), 4)]}


PLACEMENTS = {"fertile_site": fertile_site, "in_city": in_city}
