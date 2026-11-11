"""Walking inside a city and the geometry behind it (REQ-3A.3, 3A.5, 3A.6).

Distances are in the abstract units of ``movement`` in the config. In a scenario
without coordinates every entity sits at (0, 0), so every distance is zero and
nothing here ever changes a result.
"""

from __future__ import annotations

import math

from popsim.components import Action, Branch, Home, Position, Trip

DEFAULTS = {
    "walk": {"speed": 5.0, "stamina_per_unit": 0.8},
    "road": {"speed": 5.0, "stamina_per_unit": 0.5},
    "arrival_radius": 0.05,
    "home_rest_multiplier": 1.25,
    "default_city_radius": 2.0,
}


def cfg(sim) -> dict:
    """The ``movement`` config, tolerating snapshots written before it existed."""
    return sim.cfg.get("movement") or DEFAULTS


def pos(sim, eid: int) -> tuple[float, float] | None:
    p = sim.world.get(eid, Position)
    return None if p is None else (p.x, p.y)


def distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def distance_to(sim, eid: int, target: int) -> float:
    """Distance from an NPC to another entity (a node, a house, a workplace)."""
    a, b = pos(sim, eid), pos(sim, target)
    return 0.0 if a is None or b is None else distance(a, b)


def walk_distance(sim, eid: int, target: int) -> float:
    """Distance the NPC has to walk to be *at* ``target``; zero when already within arrival radius."""
    d = distance_to(sim, eid, target)
    return d if d > cfg(sim)["arrival_radius"] else 0.0


def walk_stamina(sim, dist: float) -> float:
    return dist * cfg(sim)["walk"]["stamina_per_unit"]


def walk_ticks(sim, dist: float) -> int:
    return sim.hours_to_ticks(dist / cfg(sim)["walk"]["speed"])


def start_walk(sim, eid: int, act: Action, target: int, then: tuple | None) -> None:
    """Commit to walking to entity ``target``; ``then`` (kind, target, ticks) starts on arrival."""
    here, there = pos(sim, eid), pos(sim, target)
    dist = distance(here, there)
    ticks = walk_ticks(sim, dist)
    act.kind, act.target, act.ticks_left, act.carry = "walk", target, ticks, 0.0
    act.walk_from, act.walk_to, act.walk_total = here, there, ticks
    act.walk_stamina = walk_stamina(sim, dist) / ticks
    act.then = then


def clear_walk(act: Action) -> None:
    act.walk_from = act.walk_to = act.then = None
    act.walk_total, act.walk_stamina = 0, 0.0


def at_home(sim, eid: int) -> bool:
    """True when the NPC owns a house and stands within arrival radius of it."""
    home = sim.world.get(eid, Home)
    if home is None or home.house is None:
        return False
    return walk_distance(sim, eid, home.house) == 0.0 and sim.world.alive(home.house)


# -- overworld nodes (REQ-3A.11) ---------------------------------------------------------------------


def node_access(sim, city: int, nid: int) -> tuple[float, float] | None:
    """(hours, stamina) one way from ``city`` to the overworld node ``nid`` along the main road to the
    junction and then the branch, or None when the node cannot be reached from ``city``: it hangs off a road
    that does not touch the city or that does not exist now."""
    br = sim.world.get(nid, Branch)
    if br is None or city not in (br.a, br.b):
        return None
    found = sim.find_road(br.a, br.b)
    if found is None:
        return None
    road = found[1]
    frac = br.t if city == br.a else 1.0 - br.t
    return road.hours * frac + br.hours, road.stamina_cost * frac + br.stamina_cost


def start_trip(sim, eid: int, act: Action, city: int, nid: int, ticks: int) -> None:
    """Leave ``city`` for the overworld node ``nid``: the out leg is a committed walk at the trip's own
    hours and stamina, then the shift starts (REQ-3A.11). The citizen is on the roads, in no city, and
    cannot trade until it is back."""
    from popsim.components import Location

    hours, stamina = node_access(sim, city, nid)
    sim.cancel_all_orders(eid)
    here, there = pos(sim, eid), pos(sim, nid)
    n = sim.hours_to_ticks(hours)
    act.kind, act.target, act.ticks_left, act.carry = "walk", nid, n, 0.0
    act.walk_from, act.walk_to, act.walk_total = here, there, n
    act.walk_stamina = stamina / n
    act.then = ("gather", nid, ticks)
    sim.world.add(eid, Trip(origin=city, node=nid))
    sim.world.get(eid, Location).city = None


def return_from_trip(sim, eid: int) -> None:
    """The way back from an overworld node: a Travel leg to the city the trip started from. It costs the same
    as the out leg but can never strand a tired citizen, because stamina is only floored at zero."""
    from popsim.components import Action as A, Location, Travel

    trip = sim.world.get(eid, Trip)
    sim.world.remove(eid, Trip)
    hours, stamina = node_access(sim, trip.origin, trip.node) or (0.0, 0.0)
    ticks = sim.hours_to_ticks(hours) if hours > 0 else 1
    here = pos(sim, eid)
    sim.world.add(eid, Travel(origin=trip.origin, dest=trip.origin, ticks_left=ticks,
                              stamina_per_tick=stamina / ticks, total_ticks=ticks, from_pos=here))
    act = sim.world.get(eid, A)
    act.kind, act.target, act.ticks_left, act.carry = "travel", trip.origin, ticks, 0.0
    clear_walk(act)
