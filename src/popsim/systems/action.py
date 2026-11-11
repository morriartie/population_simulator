"""Action execution: sleeping, gathering at resource nodes, travelling."""

from __future__ import annotations

from popsim.components import Action, Health, Location, Position, Skills, Stamina, Travel, Trip, Workplace, Yield
from popsim.records import on_gather, on_walk, on_work
from popsim.systems.fiscal import pay_wage
from popsim.systems.agent import stamina_per_unit
from popsim.systems.merchant import on_arrival
from popsim.systems.movement import clear_walk, return_from_trip


def gather_tick(sim, eid: int, act: Action, st: Stamina) -> int:
    """One tick of work at node ``act.target``. Returns units gathered."""
    y = sim.world.get(act.target, Yield)
    if y is None:
        act.kind, act.ticks_left = "idle", 0
        return 0
    skill = sim.world.get(eid, Skills).levels.get(y.good, 1.0)
    hl = sim.world.get(eid, Health)
    eff = sim.cfg["physiology"]["health"]["sick_work_efficiency"] if hl and hl.sick else 1.0
    cpu = stamina_per_unit(y.stamina_cost, skill)
    progress = y.base_rate * skill * eff * sim.h  # units of work done this tick
    stamina = progress * cpu                      # charged continuously, per unit of work
    avail = sim.ledger.balance(act.target, y.good)
    units = 0
    if st.current >= stamina and avail > 0:
        st.current -= stamina
        act.carry += progress
        units = min(int(act.carry), avail)
        act.carry -= units
        if units:
            sim.ledger.transfer(act.target, eid, y.good, units, reason="gather")
        sim.metrics.record_gather(y.good, progress, stamina, skill)
    else:
        act.ticks_left = 0
    act.ticks_left -= 1
    if act.ticks_left <= 0 or st.current < stamina or avail - units <= 0:
        # Keep partial progress (carry) so a follow-up shift at the same node resumes it.
        act.kind, act.ticks_left = "idle", 0
    return units


def work_tick(sim, eid: int, act: Action, st: Stamina) -> None:
    """One tick of public work (REQ-5.5): effort for a wage from the city's treasury."""
    wp = sim.world.get(act.target, Workplace)
    cost = wp.stamina_per_hour * sim.h if wp is not None else 0.0
    if wp is None or st.current < cost:
        act.kind, act.ticks_left = "idle", 0
        return
    paid = pay_wage(sim, eid, act.target)
    if paid < 0:  # the treasury is empty: the job closes for now
        act.kind, act.ticks_left = "idle", 0
        return
    st.current -= cost
    on_work(sim, eid, paid)
    act.ticks_left -= 1
    if act.ticks_left <= 0:
        act.kind, act.ticks_left = "idle", 0


def walk_tick(sim, eid: int, act: Action, st: Stamina) -> None:
    """One tick of walking inside a city (REQ-3A.5): move along the straight line and pay the stamina."""
    pos = sim.world.get(eid, Position)
    if st.current < act.walk_stamina:  # too tired to go on: stay where it is
        act.kind, act.target, act.ticks_left = "idle", None, 0
        clear_walk(act)
        return
    st.current -= act.walk_stamina
    on_walk(sim, eid, act)
    done = act.walk_total - act.ticks_left + 1
    frac = done / act.walk_total
    (x0, y0), (x1, y1) = act.walk_from, act.walk_to
    pos.x, pos.y = x0 + (x1 - x0) * frac, y0 + (y1 - y0) * frac
    act.ticks_left -= 1
    loc = sim.world.get(eid, Location)
    if loc is not None and loc.city is not None:
        sim.metrics.record_walk(sim.city_name(loc.city), sim.h, act.walk_stamina)
    if act.ticks_left > 0:
        return
    pos.x, pos.y = x1, y1  # arrived exactly
    then = act.then
    clear_walk(act)
    if then is None:
        act.kind, act.target, act.ticks_left = "idle", None, 0
    elif then[0] in ("gather", "work"):
        act.kind, act.target, act.ticks_left, act.carry = then[0], then[1], then[2], 0.0
    else:
        act.kind, act.target, act.ticks_left = "sleep", None, then[2]


class ActionSystem:
    name = "action"

    def run(self, sim) -> None:
        gatherers = []
        walkers = []
        workers = []
        for eid, (act, st) in sim.world.query(Action, Stamina):
            if act.kind == "sleep":
                act.ticks_left -= 1
                if act.ticks_left <= 0 or st.current >= st.daily_max:
                    act.kind, act.ticks_left = "idle", 0
            elif act.kind == "gather":
                gatherers.append(eid)
            elif act.kind == "work":
                workers.append(eid)
            elif act.kind == "walk":
                walkers.append(eid)
        # Shuffle so low entity IDs don't always get first pick of a depleted node.
        sim.rng.shuffle(gatherers)
        for eid in gatherers:
            units = gather_tick(sim, eid, sim.world.get(eid, Action), sim.world.get(eid, Stamina))
            if units:
                trip = sim.world.get(eid, Trip)
                on_gather(sim, eid, units, trip.origin if trip is not None else sim.world.get(eid, Location).city)

        if workers:  # fair order when the treasury cannot pay everyone; same seeded stream
            sim.rng.shuffle(workers)
            for eid in workers:
                work_tick(sim, eid, sim.world.get(eid, Action), sim.world.get(eid, Stamina))

        for eid in walkers:  # a walker that arrives starts its task next tick
            walk_tick(sim, eid, sim.world.get(eid, Action), sim.world.get(eid, Stamina))

        arrived = []
        for eid, (tr, act, st, loc) in sim.world.query(Travel, Action, Stamina, Location):
            st.current = max(0.0, st.current - tr.stamina_per_tick)
            tr.ticks_left -= 1
            act.ticks_left = tr.ticks_left
            if tr.total_ticks:  # place the traveller along the road between the two city centres
                p, a, b = sim.world.get(eid, Position), sim.world.get(tr.origin, Position), sim.world.get(tr.dest, Position)
                if p is not None and a is not None and b is not None:
                    f = 1.0 - tr.ticks_left / tr.total_ticks
                    ax, ay = tr.from_pos if tr.from_pos is not None else (a.x, a.y)
                    p.x, p.y = ax + (b.x - ax) * f, ay + (b.y - ay) * f
            if tr.ticks_left <= 0:
                arrived.append((eid, tr.dest))
        for eid, dest in arrived:
            sim.world.remove(eid, Travel)
            sim.world.get(eid, Location).city = dest
            p, c = sim.world.get(eid, Position), sim.world.get(dest, Position)
            if p is not None and c is not None:
                p.x, p.y = c.x, c.y
            act = sim.world.get(eid, Action)
            act.kind, act.target, act.ticks_left, act.carry = "idle", None, 0, 0.0
            on_arrival(sim, eid, dest)

        # A citizen out at an overworld node whose shift or walk is over heads back (REQ-3A.11).
        for eid, (trip, act, loc) in list(sim.world.query(Trip, Action, Location)):
            if loc.city is None and act.kind == "idle" and sim.world.get(eid, Travel) is None:
                return_from_trip(sim, eid)
