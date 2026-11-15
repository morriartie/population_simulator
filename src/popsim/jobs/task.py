"""Task jobs (REQ-5A.8): work for a site, such as planting a field.

A tag that waits for items or labor is an open task (``popsim.process.task_info``). Citizens choose it like any job,
by wage per stamina with the walk counted both ways; nobody is assigned. A claimed task is a plan on the citizen
(``TaskPlan``): walk to the office and ``withdraw`` the items there, go out along the roads (the trip of
REQ-3A.11), deliver the items, work shifts for a wage from the commissioning city's treasury, and come back.
The claim reserves the items and a slot, and is dropped if the citizen gives up (the tag then offers the task
again); a task nobody does is cancelled by the tag system after its timeout.
"""

from __future__ import annotations

from popsim import offices
from popsim.components import Action, Location, Stamina, Tags, TaskPlan
from popsim.jobs import JobCtx, JobKind
from popsim.ledger import GOLD
from popsim.process import tag_def, task_info, task_wage


def pay_task_wage(sim, eid: int, city: int, gross: int) -> int:
    """One tick's wage from the city's treasury, taxed as other wages (REQ-5.5). The net pay, or -1 when the
    treasury cannot pay and the task closes for now."""
    from popsim.systems.fiscal import pay_income_tax

    if gross <= 0:
        return 0
    if not sim.ledger.try_transfer(city, eid, GOLD, gross, reason="wage"):
        return -1
    tax = pay_income_tax(sim, eid, city, gross, "income_tax")
    sim.metrics.record_treasury(sim.city_name(city), "wages", gross)
    return gross - tax


def offer(sim, city: int, sid: int, tg: Tags):
    """The first task of site ``sid`` that can be started now, with its info, or None."""
    for tag in sorted(tg.tags):
        info = task_info(sim, sid, tag)
        if info is None or info["free"] <= 0:
            continue
        if info["items"]:
            if not info["service"] or offices.office_for(sim, city, info["service"]) is None:
                continue
            if any(offices.unreserved(sim, city, g) < n for g, n in info["items"].items()):
                continue
        return info
    return None


class TaskKind(JobKind):
    name, action = "task", "task"

    def owns(self, sim, target):
        return sim.world.get(target, Tags) is not None

    def label(self, sim, target):
        return f"{sim.world.get(target, Tags).process} task"

    def raw_options(self, sim, eid, city, ctx: JobCtx):
        from popsim.process import sites
        from popsim.systems.fiscal import income_tax_rate
        from popsim.systems.movement import node_access, walk_distance, walk_stamina

        out = []
        for sid, tg in sites(sim):
            if tg.city != city:
                continue
            info = offer(sim, city, sid, tg)
            if info is None:
                continue
            task = info["task"]
            wage = task_wage(sim, tg, info["tag"])
            if sim.gold(city) < wage * ctx.d["shift_hours"]:
                continue
            access = node_access(sim, city, sid)
            if access is None:
                continue
            walk = access[1]
            if info["items"]:
                office = offices.office_for(sim, city, info["service"])
                walk += walk_stamina(sim, walk_distance(sim, eid, office))
            per_tick = float(task["stamina_per_hour"]) * sim.h
            if ctx.st.current < per_tick + walk:
                continue
            net = wage * (1.0 - income_tax_rate(sim, city))
            ips = net / max(float(task["stamina_per_hour"]), 1e-9)
            if walk > 0:
                shift = per_tick * sim.hours_to_ticks(ctx.d["shift_hours"])
                ips *= shift / (shift + 2 * walk)
            label = str(task.get("label", "task"))
            out.append((ips, sid, f"task:{label}", ctx.skills.get(label, 1.0), net))
        return out

    def start(self, sim, eid, act, city, nid, ticks):
        from popsim.systems.movement import start_trip, start_walk, walk_distance

        tg = sim.world.get(nid, Tags)
        info = offer(sim, city, nid, tg)
        items = dict(info["items"])
        office = offices.office_for(sim, city, info["service"]) if items else None
        plan = TaskPlan(site=nid, tag=info["tag"], step="withdraw" if items else "work", ticks=ticks,
                        items=items, office=office)
        data = tg.tags[info["tag"]]
        data["claims"][eid] = {"items": dict(items), "taken": False, "tick": sim.tick}
        data["progress_tick"] = sim.tick
        sim.world.add(eid, plan)
        if items:
            if walk_distance(sim, eid, office) > 0:
                start_walk(sim, eid, act, office, ("task", nid, ticks))
            else:  # already at the office: the next tick withdraws
                act.kind, act.target, act.ticks_left, act.carry = "task", nid, ticks, 0.0
        else:
            start_trip(sim, eid, act, city, nid, ticks, then_kind="task")

    def interrupt_walk(self, sim, eid, act, needs, st):
        u = needs.urgency
        plan = sim.world.get(eid, TaskPlan)
        return plan is None or sim.world.get(plan.site, Tags) is None or u["rest"] >= 1.0 \
            or (u["food"] >= 1.0 and sim.inv(eid, "food") == 0)

    def interrupt_act(self, sim, eid, act, needs, st):
        u = needs.urgency
        plan = sim.world.get(eid, TaskPlan)
        tg = sim.world.get(plan.site, Tags) if plan is not None else None
        if tg is None:
            return True
        task = tag_def(sim, tg, plan.tag).get("task") or {}
        return st.current < float(task.get("stamina_per_hour", 3.5)) * sim.h or u["rest"] >= 1.0 \
            or (u["food"] >= 1.0 and sim.inv(eid, "food") == 0)

    # -- ticks -----------------------------------------------------------------------------------------
    def run_tick(self, sim, eids):
        from popsim.components import Stamina as St
        from popsim.systems.movement import start_trip

        for eid in eids:  # in id order: the treasury pays the first in line when it cannot pay everyone
            act, st = sim.world.get(eid, Action), sim.world.get(eid, St)
            plan = sim.world.get(eid, TaskPlan)
            tg = sim.world.get(plan.site, Tags) if plan is not None else None
            if tg is None or tg.done:
                self._end(sim, eid, act, plan)
                continue
            claim = tg.tags[plan.tag]["claims"].get(eid) if plan.tag in tg.tags else None
            if claim is None:
                self._end(sim, eid, act, plan)
                continue
            if plan.step == "withdraw":
                city = sim.world.get(eid, Location).city
                if city is None or not offices.issue_items(sim, tg.city, eid, plan.items):
                    self._drop(sim, eid, act, plan, tg)  # the office closed or the depot ran out
                    continue
                claim["taken"] = True
                plan.step = "work"
                start_trip(sim, eid, act, tg.city, plan.site, plan.ticks, then_kind="task")
                continue
            self._work(sim, eid, act, st, plan, tg, claim)

    def _work(self, sim, eid, act, st, plan, tg, claim):
        from popsim.records import on_work

        data = tg.tags[plan.tag]
        if plan.items:  # on arrival: deliver what was carried out
            for g, n in sorted(plan.items.items()):
                have = min(n, sim.inv(eid, g))
                if have:
                    sim.ledger.transfer(eid, plan.site, g, have, reason="deliver")
            plan.items, claim["items"] = {}, {}
            data["progress_tick"] = sim.tick
        req = tag_def(sim, tg, plan.tag).get("requires") or {}
        task = tag_def(sim, tg, plan.tag)["task"]
        if not req.get("labor_hours") or data.get("labor", 0.0) >= float(req["labor_hours"]) - 1e-9:
            self._finish(sim, eid, act, plan, tg)
            return
        cost = float(task["stamina_per_hour"]) * sim.h
        if st.current < cost:
            self._finish(sim, eid, act, plan, tg)
            return
        paid = pay_task_wage(sim, eid, tg.city, int(round(task_wage(sim, tg, plan.tag) * sim.h)))
        if paid < 0:  # the treasury is empty: the task closes for now
            self._finish(sim, eid, act, plan, tg)
            return
        st.current -= cost
        on_work(sim, eid, paid)
        data["labor"] = data.get("labor", 0.0) + sim.h
        data["progress_tick"] = sim.tick
        act.ticks_left -= 1
        if act.ticks_left <= 0 or data["labor"] >= float(req["labor_hours"]) - 1e-9:
            self._finish(sim, eid, act, plan, tg)

    @staticmethod
    def _finish(sim, eid, act, plan, tg):
        tg.tags.get(plan.tag, {}).get("claims", {}).pop(eid, None)
        sim.world.remove(eid, TaskPlan)
        act.kind, act.ticks_left = "idle", 0

    @staticmethod
    def _drop(sim, eid, act, plan, tg):
        TaskKind._finish(sim, eid, act, plan, tg)

    @staticmethod
    def _end(sim, eid, act, plan):
        if plan is not None:
            sim.world.remove(eid, TaskPlan)
        act.kind, act.ticks_left = "idle", 0
