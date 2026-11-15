"""Job kinds (REQ-5A.4): one object per kind of job, in one registry.

A kind knows how to offer jobs to a citizen (``raw_options``), start one (``start``), decide whether an urgent
need breaks it off (``interrupt_walk`` for the walk to it, ``interrupt_act`` while doing it) and run its
ticks (``run_tick``). The decision, interruption and action systems dispatch through the registry instead of
branching on component types, so a new kind of job is a class listed here.

``gather`` (resource nodes, REQ-3A.4) and ``work`` (public jobs, REQ-5.5) are adapters over the behavior
those systems always had; the golden hashes in the tests hold them to it. ``task`` (REQ-5A.8) is added in
``popsim.jobs.task``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from popsim.components import Branch, Workplace, Yield


@dataclass
class JobCtx:
    """What the kinds share while one citizen's options are being worked out."""

    d: dict          # the decision settings
    skills: dict
    tr: Any
    st: Any
    needs: Any
    eff: float       # work efficiency (sickness)


class JobKind:
    name: str = ""
    action: str = ""  # the ``Action.kind`` of a citizen doing this kind of job

    def owns(self, sim, target: int) -> bool:  # pragma: no cover - interface
        raise NotImplementedError

    def label(self, sim, target: int) -> str:
        """What a citizen doing this job at ``target`` is called in the jobs view (derived, never listed)."""
        return self.name

    def raw_options(self, sim, eid: int, city: int, ctx: JobCtx) -> list[tuple]:
        """(income per stamina, target, good, skill, income per hour) for each job offered in ``city``."""
        return []

    def start(self, sim, eid: int, act, city: int, nid: int, ticks: int) -> None:  # pragma: no cover
        raise NotImplementedError

    def interrupt_walk(self, sim, eid: int, act, needs, st) -> bool:
        return False

    def interrupt_act(self, sim, eid: int, act, needs, st) -> bool:
        return False

    def run_tick(self, sim, eids: list[int]) -> None:  # pragma: no cover
        raise NotImplementedError


class GatherKind(JobKind):
    name, action = "gather", "gather"

    def owns(self, sim, target):
        return sim.world.get(target, Yield) is not None

    def label(self, sim, target):
        return f"gather {sim.world.get(target, Yield).good}"

    def raw_options(self, sim, eid, city, ctx):
        from popsim.systems.agent import price_ref, work_stamina_per_tick
        from popsim.systems.movement import node_access, walk_distance, walk_stamina

        out = []
        for nid in sim.nodes_for(city):
            y = sim.world.get(nid, Yield)
            amount = sim.ledger.balance(nid, y.good)
            avail = min(1.0, amount / 10.0)
            s = ctx.skills.get(y.good, 1.0)
            work = work_stamina_per_tick(sim, eid, y)
            access = node_access(sim, city, nid) if sim.world.get(nid, Branch) is not None else None
            if access is not None:
                walk = access[1]  # an overworld node: the main road plus its branch, one way (REQ-3A.11)
            else:
                walk = walk_stamina(sim, walk_distance(sim, eid, nid))  # stamina to walk there (REQ-3A.6)
            if avail <= 0 or ctx.st.current < work + walk:
                continue
            price = price_ref(sim, eid, city, y.good)
            income_per_stamina = price * s * ctx.eff / y.stamina_cost * avail
            if walk > 0:
                # Income per stamina over the whole task, the walk counted both ways (REQ-3A.7).
                shift = work * sim.hours_to_ticks(ctx.d["shift_hours"])
                income_per_stamina *= shift / (shift + 2 * walk)
            # Income per hour counts only the hours at the job, never the commute (REQ-3A.7).
            income_per_hour = price * y.base_rate * s * ctx.eff * avail
            out.append((income_per_stamina, nid, y.good, s, income_per_hour))
        return out

    def start(self, sim, eid, act, city, nid, ticks):
        from popsim.systems.movement import start_trip, start_walk, walk_distance

        if sim.world.get(nid, Branch) is not None:  # an overworld node: out along the roads (REQ-3A.11)
            if act.target != nid:
                act.carry = 0.0
            start_trip(sim, eid, act, city, nid, ticks)
            return
        if walk_distance(sim, eid, nid) > 0:  # the plan is two steps: walk there, then work (REQ-3A.6)
            start_walk(sim, eid, act, nid, ("gather", nid, ticks))
            return
        if act.target != nid:  # resume partial progress only at the same node
            act.carry = 0.0
        act.kind, act.target = "gather", nid
        act.ticks_left = ticks

    def interrupt_walk(self, sim, eid, act, needs, st):
        u = needs.urgency
        y = sim.world.get(act.then[1], Yield)
        if y is None or u["rest"] >= 1.0:
            return True
        return u["food"] >= 1.0 and sim.inv(eid, "food") == 0 and y.good != "food"

    def interrupt_act(self, sim, eid, act, needs, st):
        from popsim.systems.agent import work_stamina_per_tick

        u = needs.urgency
        y = sim.world.get(act.target, Yield)
        if y is None:
            return True
        if st.current < work_stamina_per_tick(sim, eid, y) or u["rest"] >= 1.0:
            return True
        return u["food"] >= 1.0 and sim.inv(eid, "food") == 0 and y.good != "food"

    def run_tick(self, sim, eids):
        from popsim.components import Location, Stamina, Trip
        from popsim.components import Action
        from popsim.records import on_gather
        from popsim.systems.action import gather_tick

        # Shuffle so low entity IDs don't always get first pick of a depleted node.
        sim.rng.shuffle(eids)
        for eid in eids:
            units = gather_tick(sim, eid, sim.world.get(eid, Action), sim.world.get(eid, Stamina))
            if units:
                trip = sim.world.get(eid, Trip)
                on_gather(sim, eid, units, trip.origin if trip is not None else sim.world.get(eid, Location).city)


class WorkplaceKind(JobKind):
    name, action = "work", "work"

    def owns(self, sim, target):
        return sim.world.get(target, Workplace) is not None

    def label(self, sim, target):
        return f"work {sim.world.get(target, Workplace).task}"

    def raw_options(self, sim, eid, city, ctx):
        # Public jobs (REQ-5.5): a workplace pays a wage from the city's treasury, so it is offered only
        # while a slot is free and the treasury can fund a shift. The wage is after income tax, the walk
        # is counted both ways (REQ-3A.7) and, as for gathering, only hours at the job count as hours.
        from popsim.systems.fiscal import net_wage_per_hour, slots_of, workers_at
        from popsim.systems.movement import walk_distance, walk_stamina

        out, wc = [], None
        for wid in sim.workplaces_by_city.get(city, []):
            wp = sim.world.get(wid, Workplace)
            if wp is None or wp.wage <= 0:
                continue
            if wc is None:
                wc = getattr(sim, "_wc", None)
                if wc is None:
                    wc = workers_at(sim)
            if wc.get(wid, 0) >= slots_of(sim, wp) or sim.gold(city) < wp.wage * ctx.d["shift_hours"]:
                continue
            per_tick = wp.stamina_per_hour * sim.h
            walk = walk_stamina(sim, walk_distance(sim, eid, wid))
            if ctx.st.current < per_tick + walk:
                continue
            net = net_wage_per_hour(sim, wp)
            ips = net / max(wp.stamina_per_hour, 1e-9)
            if walk > 0:
                shift = per_tick * sim.hours_to_ticks(ctx.d["shift_hours"])
                ips *= shift / (shift + 2 * walk)
            out.append((ips, wid, wp.task, ctx.skills.get(wp.task, 1.0), net))
        return out

    def start(self, sim, eid, act, city, nid, ticks):
        from popsim.systems.movement import start_walk, walk_distance

        if sim._wc is not None:
            sim._wc[nid] = sim._wc.get(nid, 0) + 1  # the slot is taken from now on
        if walk_distance(sim, eid, nid) > 0:
            start_walk(sim, eid, act, nid, ("work", nid, ticks))
            return
        act.kind, act.target, act.ticks_left, act.carry = "work", nid, ticks, 0.0

    def interrupt_walk(self, sim, eid, act, needs, st):
        u = needs.urgency
        return sim.world.get(act.then[1], Workplace) is None or u["rest"] >= 1.0 \
            or (u["food"] >= 1.0 and sim.inv(eid, "food") == 0)

    def interrupt_act(self, sim, eid, act, needs, st):
        u = needs.urgency
        wp = sim.world.get(act.target, Workplace)
        return wp is None or st.current < wp.stamina_per_hour * sim.h or u["rest"] >= 1.0 \
            or (u["food"] >= 1.0 and sim.inv(eid, "food") == 0)

    def run_tick(self, sim, eids):
        from popsim.components import Action, Stamina
        from popsim.systems.action import work_tick

        if eids:  # fair order when the treasury cannot pay everyone; same seeded stream
            sim.rng.shuffle(eids)
            for eid in eids:
                work_tick(sim, eid, sim.world.get(eid, Action), sim.world.get(eid, Stamina))


KINDS: list[JobKind] = [GatherKind(), WorkplaceKind()]


def register(kind: JobKind) -> None:
    if all(k.name != kind.name for k in KINDS):
        KINDS.append(kind)


def by_action(name: str) -> JobKind | None:
    for k in KINDS:
        if k.action == name:
            return k
    return None


def actions() -> tuple[str, ...]:
    return tuple(k.action for k in KINDS)


def job_label(sim, eid: int) -> str:
    """The job a citizen is on right now: the kind's label for the target of the action, or the action itself
    (sleep, idle, travel...). A walk counts for the job it leads to."""
    from popsim.components import Action

    act = sim.world.get(eid, Action)
    if act is None:
        return "idle"
    kind, target = act.kind, act.target
    if kind == "walk" and act.then and act.then[0] in actions():
        kind, target = act.then[0], act.then[1]
    k = by_action(kind)
    if k is not None and target is not None:
        try:
            return k.label(sim, target)
        except AttributeError:  # the target is gone
            return k.name
    return kind


def kind_of(sim, target: int) -> JobKind:
    for k in KINDS:
        if k.owns(sim, target):
            return k
    return KINDS[0]


def _register_task_kind() -> None:
    from popsim.jobs.task import TaskKind

    register(TaskKind())


_register_task_kind()
