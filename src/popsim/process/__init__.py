"""Tagged sites (REQ-5A.7): an entity whose flags (tags) wait for requirements and change when they are met.

The tags, what each waits for and what comes next are config (``processes:``). A tag with ``requires`` waits for
``items`` delivered to the site, ``labor_hours`` worked there, and/or a ``timer_days``; when all are met its
``on_done`` removes and adds tags, and a tag's ``on_enter`` effects run when it is added (``spawn_node`` turns
the site into a resource node). Citizens supply items and labor by task jobs (``popsim.jobs.task``); a tag's
``task`` block says how (wage, stamina, slots, timeout, the ``service`` an office must provide).

Requirements are a registry (``REQUIREMENTS``) and effects another (``EFFECTS``): a new kind is a function listed
there. ``TagSystem`` checks the sites once a tick, in entity id order, and draws no random numbers.
"""

from __future__ import annotations

from popsim.components import Account, Branch, Position, Tags, TaskPlan
from popsim.ledger import GOLD


def defs(sim) -> dict:
    return sim.cfg.get("processes") or {}


def tag_def(sim, tags: Tags, tag: str) -> dict:
    return (defs(sim).get(tags.process) or {}).get(tag) or {}


def log_event(sim, tg: Tags | None, kind: str, **fields) -> None:
    pol = tg.meta.get("policy") if tg is not None else None
    sim.__dict__.setdefault("policy_events", []).append(
        {"tick": sim.tick, "day": sim.day(), "policy": pol, "kind": kind, **fields})


# -- creating sites ------------------------------------------------------------------------------------

def create_site(sim, cid: int, process: str, place: dict, rule: str | None = None, policy: str | None = None) -> int:
    """A new site of ``process`` commissioned by city ``cid`` at ``place`` (``at``, ``branch``, ``fertility``)."""
    from popsim.world.builder import make_branch

    if process not in defs(sim):
        raise ValueError(f"unknown process {process!r}")
    x, y, branch = make_branch(sim, {"at": place["at"], "branch": place["branch"]}, f"site {process}")
    meta = {"fertility": place.get("fertility"), "policy": policy}
    tg = Tags(process=process, city=cid, rule=rule, created_tick=sim.tick, meta=meta)
    eid = sim.world.create(tg, Position(x, y), Account(kind="site"), branch)
    for tag in defs(sim)[process].get("tags", []):
        enter_tag(sim, eid, tag, quiet=True)
    log_event(sim, tg, "planted", city=sim.city_name(cid), site=eid, process=process, x=x, y=y,
              fertility=None if meta["fertility"] is None else round(meta["fertility"], 3),
              tags=sorted(tg.tags), rule=rule)
    return eid


def timer_ticks(sim, tg: Tags, req: dict) -> int:
    days = float(req["timer_days"])
    if req.get("timer_scaled_by") == "fertility" and tg.meta.get("fertility") is not None:
        days *= 1.5 - float(tg.meta["fertility"])  # better land grows faster
    return max(1, int(round(days * 24 / sim.h)))


def enter_tag(sim, eid: int, tag: str, quiet: bool = False) -> None:
    tg = sim.world.get(eid, Tags)
    data = {"entered_tick": sim.tick, "progress_tick": sim.tick, "claims": {}}
    d = tag_def(sim, tg, tag)
    req = d.get("requires") or {}
    if "timer_days" in req:
        data["ready_tick"] = sim.tick + timer_ticks(sim, tg, req)
    if req.get("labor_hours"):
        data["labor"] = 0.0
    tg.tags[tag] = data
    for eff in d.get("on_enter") or []:
        for name, params in eff.items():
            EFFECTS[name](sim, eid, params)
    if not quiet:
        ready = data.get("ready_tick")
        log_event(sim, tg, "stage", city=sim.city_name(tg.city), site=eid, process=tg.process, tags=sorted(tg.tags),
                  added=tag, ready_day=None if ready is None else ready * sim.h / 24)


def spawn_node(sim, eid: int, p: dict) -> None:
    """The site becomes a resource node (REQ-3A.11), sized by its land's fertility."""
    from popsim.world.builder import node_components

    tg = sim.world.get(eid, Tags)
    f = tg.meta.get("fertility")
    k = 0.5 + float(f) if f is not None else 1.0
    top = max(1, int(round(float(p["max_amount"]) * k)))
    n = {**p, "max_amount": top, "regen_per_hour": float(p["regen_per_hour"]) * k,
         "initial": int(round(top * float(p.get("initial_frac", 0.15))))}
    for comp in node_components(n):
        sim.world.add(eid, comp)
    sim.ledger.transfer("nature", eid, n["good"], n["initial"], reason="genesis")
    sim.metrics.count("inject", n["good"], n["initial"])  # the daily goods check
    sim.overworld_nodes.append(eid)
    tg.done = True
    pos = sim.world.get(eid, Position)
    log_event(sim, tg, "matured", city=sim.city_name(tg.city), site=eid, node=eid, process=tg.process, x=pos.x, y=pos.y,
              fertility=None if f is None else round(f, 3), good=n["good"], max_amount=top,
              planted_day=tg.created_tick * sim.h / 24)


EFFECTS = {"spawn_node": spawn_node}


# -- requirements --------------------------------------------------------------------------------------

def items_delivered(sim, eid: int, good: str) -> int:
    return sim.ledger.balance(eid, good)


def claimed(data: dict, key: str) -> dict:
    """What the claims on a tag hold: items reserved or carried, summed per good."""
    out: dict[str, int] = {}
    for c in data.get("claims", {}).values():
        for g, n in (c.get(key) or {}).items():
            out[g] = out.get(g, 0) + n
    return out


def _status(sim, tg: Tags, tag: str) -> list[dict]:
    eid = next(e for e, (t,) in sim.world.query(Tags) if t is tg)
    return status(sim, eid, tag)


def status(sim, eid: int, tag: str) -> list[dict]:
    """Each requirement of a tag with what is there so far: ``{"kind", "label", "met", "have", "need"}``."""
    tg = sim.world.get(eid, Tags)
    data = tg.tags[tag]
    req = tag_def(sim, tg, tag).get("requires") or {}
    out = []
    for good, need in sorted((req.get("items") or {}).items()):
        have = items_delivered(sim, eid, good)
        out.append({"kind": "items", "label": f"{good} {min(have, need)}/{need}", "met": have >= need, "have": have, "need": need})
    if req.get("labor_hours"):
        have, need = float(data.get("labor", 0.0)), float(req["labor_hours"])
        out.append({"kind": "labor", "label": f"labor {have:.1f}/{need:g} h", "met": have >= need - 1e-9, "have": have, "need": need})
    if "timer_days" in req:
        left = max(0, data["ready_tick"] - sim.tick) * sim.h / 24
        out.append({"kind": "timer", "label": f"{left:.1f} days left", "met": sim.tick >= data["ready_tick"],
                    "have": sim.tick - data["entered_tick"], "need": data["ready_tick"] - data["entered_tick"]})
    return out


def task_info(sim, eid: int, tag: str) -> dict | None:
    """What a citizen can do for ``tag`` now (items to fetch and/or labor), or None when nothing is left.
    ``items`` are the units not yet delivered or already claimed; ``labor`` the hours not yet worked."""
    tg = sim.world.get(eid, Tags)
    d = tag_def(sim, tg, tag)
    task, req = d.get("task"), d.get("requires") or {}
    if not task or tg.done:
        return None
    data = tg.tags[tag]
    held = claimed(data, "items")
    items = {}
    for g, need in (req.get("items") or {}).items():
        left = need - items_delivered(sim, eid, g) - held.get(g, 0)
        if left > 0:
            items[g] = left
    labor = max(0.0, float(req.get("labor_hours", 0.0)) - float(data.get("labor", 0.0)))
    if not items and labor <= 1e-9:
        return None
    return {"tag": tag, "task": task, "items": items, "labor": labor, "slots": int(task.get("slots", 1)),
            "free": int(task.get("slots", 1)) - len(data["claims"]), "service": task.get("service")}


def task_wage(sim, tg: Tags, tag: str) -> int:
    """Cents per hour the commissioning city pays for ``tag``: the base wage times the raise so far (REQ-5A.8)."""
    task = tag_def(sim, tg, tag)["task"]
    return int(round(float(task["wage"]) * float(tg.tags[tag].get("wage_mult", 1.0)) * sim.unit))


def task_info_open(sim, tg: Tags, tag: str, data: dict) -> bool:
    """Whether the tag still waits for items or labor (so a better wage can help)."""
    req = tag_def(sim, tg, tag).get("requires") or {}
    return any(not s["met"] for s in _status(sim, tg, tag) if s["kind"] in ("items", "labor")) if req else False


def sites(sim):
    return [(eid, t) for eid, (t,) in sim.world.query(Tags) if not t.done]


def service_demands(sim) -> list[tuple[int, str, tuple | None]]:
    """(city, service, position) for each task that needs an office to hand out its items (REQ-5A.5)."""
    out = []
    for eid, tg in sites(sim):
        for tag in sorted(tg.tags):
            info = task_info(sim, eid, tag)
            if info and info["items"] and info["service"]:
                p = sim.world.get(eid, Position)
                out.append((tg.city, info["service"], (p.x, p.y)))
    return out


def blockers(sim, eid: int, tag: str) -> list[str]:
    """Why a task is not being done, in words, for the dashboard (REQ-5A.12)."""
    from popsim import offices

    tg = sim.world.get(eid, Tags)
    info = task_info(sim, eid, tag)
    if info is None:
        return []
    out = []
    if info["items"] and info["service"]:
        if not offices.office_exists(sim, tg.city, info["service"]):
            out.append(f"no {info['service'].replace('_', ' ')} office")
        elif not offices.staffed(sim, tg.city, info["service"]):
            out.append(f"{info['service'].replace('_', ' ')} office not staffed")
        for g, n in info["items"].items():
            if offices.unreserved(sim, tg.city, g) < n:
                out.append(f"no {g} in the depot")
    if sim.gold(tg.city) < float(info["task"].get("wage", 0)) * sim.unit * sim.cfg["decision"]["shift_hours"]:
        out.append("treasury short")
    if info["free"] <= 0:
        out.append("all slots taken")
    elif not out:
        out.append("waiting for someone to take it")
    return out


def site_view(sim, eid: int) -> dict:
    tg = sim.world.get(eid, Tags)
    p = sim.world.get(eid, Position)
    reqs = {t: status(sim, eid, t) for t in sorted(tg.tags) if (tag_def(sim, tg, t).get("requires"))}
    blk = {t: blockers(sim, eid, t) for t in reqs}
    return {"id": eid, "process": tg.process, "city": sim.city_name(tg.city), "x": p.x, "y": p.y, "tags": sorted(tg.tags),
            "requirements": reqs, "blockers": {t: b for t, b in blk.items() if b}, "rule": tg.rule,
            "policy": tg.meta.get("policy")}


# -- the system ----------------------------------------------------------------------------------------

def release_invalid_claims(sim, eid: int, tg: Tags) -> None:
    """A claim holds only while its citizen is alive and still on the task (REQ-5A.8)."""
    from popsim.components import Action

    for tag, data in tg.tags.items():
        for cit in list(data["claims"]):
            plan = sim.world.get(cit, TaskPlan) if sim.world.alive(cit) else None
            act = sim.world.get(cit, Action) if plan is not None else None
            on = plan is not None and plan.site == eid and act is not None and (
                (act.kind == "task" and act.target == eid)
                or (act.kind == "walk" and act.then is not None and act.then[0] == "task" and act.then[1] == eid))
            if not on:
                data["claims"].pop(cit, None)
                if plan is not None and plan.site == eid:
                    sim.world.remove(cit, TaskPlan)


def cancel_site(sim, eid: int, why: str) -> None:
    tg = sim.world.get(eid, Tags)
    for g, n in list(sim.ledger.holdings(eid).items()):
        sim.ledger.transfer(eid, tg.city, g, n, reason="site_returned")  # delivered items go back to the depot
    log_event(sim, tg, "cancelled", city=sim.city_name(tg.city), site=eid, process=tg.process, why=why,
              x=sim.world.get(eid, Position).x, y=sim.world.get(eid, Position).y)
    for data in tg.tags.values():
        for cit in list(data["claims"]):
            sim.world.remove(cit, TaskPlan)
    sim.world.destroy(eid)


class TagSystem:
    name = "tags"

    def run(self, sim) -> None:
        daily = sim.at_hour(float(sim.cfg["signals"]["hour"]))
        for eid, tg in sites(sim):
            if daily:
                self.raise_wages(sim, tg)
            release_invalid_claims(sim, eid, tg)
            if self.timed_out(sim, tg):
                cancel_site(sim, eid, "timeout")
                continue
            for tag in sorted(tg.tags):
                d = tag_def(sim, tg, tag)
                if tag not in tg.tags or not d.get("requires"):
                    continue
                if all(s["met"] for s in status(sim, eid, tag)):
                    self.complete(sim, eid, tg, tag, d)
                    if tg.done:
                        break

    @staticmethod
    def raise_wages(sim, tg: Tags) -> None:
        """A task nobody has claimed gets a better wage each day, up to its cap, so it gets done."""
        for tag, data in tg.tags.items():
            task = tag_def(sim, tg, tag).get("task")
            rw = (task or {}).get("raise_wage")
            if rw and not data["claims"] and task_info_open(sim, tg, tag, data):
                data["wage_mult"] = min(float(rw["max"]), float(data.get("wage_mult", 1.0)) * (1.0 + float(rw["step"])))

    @staticmethod
    def timed_out(sim, tg: Tags) -> bool:
        """A task nobody has made progress on for ``timeout_days`` is cancelled (REQ-5A.8)."""
        for tag, data in tg.tags.items():
            t = (tag_def(sim, tg, tag).get("task") or {}).get("timeout_days")
            if t is not None and not data["claims"] and (sim.tick - data["progress_tick"]) * sim.h / 24 >= float(t):
                return True
        return False

    @staticmethod
    def complete(sim, eid: int, tg: Tags, tag: str, d: dict) -> None:
        for g, n in ((d.get("requires") or {}).get("items") or {}).items():
            sim.ledger.transfer(eid, "nature", g, n, reason="planted")  # the seeds are used up
            sim.metrics.count("consume", g, n)
        done = d.get("on_done") or {}
        for t in done.get("remove") or []:
            tg.tags.pop(t, None)
        for t in done.get("add") or []:
            enter_tag(sim, eid, t)
