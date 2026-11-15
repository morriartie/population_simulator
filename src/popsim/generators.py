"""Generators (REQ-5A.6): config rules that create jobs when a demand signal is strong enough.

A rule (``generators:``) says ``when`` a signal (``popsim.signals``) is above a threshold for ``for_days``, ``create``
a process site (``process``) placed by a placement, or a workplace (``workplace``), within ``limits``, paid for by the
city treasury. A rule that names a ``policy`` runs only while that policy is on. Once a day, after the signals are
sampled, each rule is tested for each city and what it decided is logged for the dashboard, with the reason when it
did nothing. Offices made by a rule close after ``close_when.idle_days`` with no demand; offices the scenario lists
are never touched. No random numbers are drawn.
"""

from __future__ import annotations

from popsim import signals
from popsim.components import Workplace
from popsim.placement import PLACEMENTS
from popsim.policies import is_enabled


def rules(sim) -> dict:
    return sim.cfg.get("generators") or {}


def rule_enabled(sim, rule: dict) -> bool:
    pol = rule.get("policy")
    return pol is None or is_enabled(sim, pol)


def active(sim) -> bool:
    return any(rule_enabled(sim, r) for r in rules(sim).values())


def _state(sim, name: str, cid: int) -> dict:
    return sim.__dict__.setdefault("gen_state", {}).setdefault(f"{name}|{cid}", {
        "since": None, "last_tick": None, "idle_since": None, "serial": 0, "made": []})


def _condition(w: dict, g: dict) -> tuple[bool, float]:
    if "share_gt" in w:
        return g["share"] > float(w["share_gt"]) and g["count"] > 0, float(w["share_gt"])
    return g["count"] >= int(w["count_gte"]), float(w["count_gte"])


class GeneratorSystem:
    name = "generators"

    def run(self, sim) -> None:
        if not sim.at_hour(float(sim.cfg["signals"]["hour"])):
            return
        for name in sorted(rules(sim)):
            rule = rules(sim)[name]
            if not rule_enabled(sim, rule):
                continue
            for cid in sorted(sim.city_ids.values()):
                self.evaluate(sim, name, rule, cid)

    def evaluate(self, sim, name: str, rule: dict, cid: int) -> None:
        day = sim.day()
        st = _state(sim, name, cid)
        w = rule["when"]
        g = signals.gauge(sim, cid, w["signal"])
        ok, threshold = _condition(w, g)
        ok = ok and g["residents"] >= int(w.get("min_population", 0))
        create = rule["create"]
        if "workplace" in create:
            self.close_idle(sim, name, rule, cid, st, g)
            self.raise_wage(sim, rule, st, g)
        st["since"] = (st["since"] if st["since"] is not None else day) if ok else None
        held = 0 if st["since"] is None else day - st["since"]
        action = "idle"
        if ok:
            need = float(w.get("for_days", 0))
            action = f"waiting: {need - held:g} more day(s)" if held < need else self.create(sim, name, rule, cid, st)
        if rule.get("policy") is not None or g["count"] > 0 or action != "idle":
            active_n = len(self.made(sim, name, cid)) if "process" in create else 0
            sim.__dict__.setdefault("policy_log", []).append({
                "day": day, "policy": rule.get("policy"), "rule": name, "city": sim.city_name(cid),
                "signal": w["signal"], "urgent": g["count"], "residents": g["residents"], "share": round(g["share"], 4),
                "threshold": threshold, "triggered": ok, "held_days": held, "action": action,
                "plots_growing": active_n, "treasury": sim.gold(cid) / sim.unit})

    @staticmethod
    def made(sim, name: str, cid: int) -> list[int]:
        from popsim.process import sites

        return [eid for eid, tg in sites(sim) if tg.rule == name and tg.city == cid]

    def create(self, sim, name: str, rule: dict, cid: int, st: dict) -> str:
        c, lim = rule["create"], rule.get("limits") or {}
        if "process" in c:
            from popsim.process import create_site

            if len(self.made(sim, name, cid)) >= int(lim.get("max_active", 10**9)):
                return "waiting: fields growing"
            last = st["last_tick"]
            if last is not None and (sim.tick - last) * sim.h / 24 < float(lim.get("cooldown_days", 0)):
                return "waiting: cooldown"
            if sim.gold(cid) < int(round(float(c.get("reserve", 0)) * sim.unit)):
                return "blocked: treasury short"
            place = PLACEMENTS[c.get("at", "fertile_site")](sim, cid, c.get("params"))
            if place is None:
                return "blocked: no fertile land"
            create_site(sim, cid, c["process"], place, rule=name, policy=rule.get("policy"))
            st["last_tick"] = sim.tick
            return "created"
        spec = c["workplace"]
        from popsim import offices

        if len(offices.offices(sim, cid, spec["task"])) >= int(lim.get("max_per_city", 1)):
            return "exists"
        unit = sim.unit
        wage = int(round(float(spec.get("wage", 0)) * unit))
        if sim.gold(cid) < wage * sim.cfg["decision"]["shift_hours"] * float(c.get("treasury_days", 1)):
            return "blocked: treasury short"
        place = PLACEMENTS[c.get("at", "in_city")](sim, cid, {"serial": st["serial"]})
        st["serial"] += 1
        from popsim.components import Position

        wid = sim.world.create(Workplace(city=cid, task=spec["task"], wage=wage, slots=int(spec.get("slots", 1)),
                                         stamina_per_hour=float(spec.get("stamina_per_hour", 3.5))),
                               Position(*place["at"]))
        sim.workplaces_by_city.setdefault(cid, []).append(wid)
        st["made"].append(wid)
        sim.__dict__.setdefault("policy_events", []).append(
            {"tick": sim.tick, "day": sim.day(), "policy": rule.get("policy"), "kind": "office_opened",
             "city": sim.city_name(cid), "site": wid, "task": spec["task"], "x": place["at"][0], "y": place["at"][1],
             "rule": name})
        return "created"

    @staticmethod
    def raise_wage(sim, rule: dict, st: dict, g: dict) -> None:
        """An office nobody staffs while tasks wait for it gets a better wage each day, up to its cap."""
        from popsim import offices

        spec = rule["create"]["workplace"]
        rw = spec.get("raise_wage")
        if not rw or g["count"] <= 0:
            return
        for wid in st["made"]:
            wp = sim.world.get(wid, Workplace)
            if wp is not None and not offices.staffed(sim, wp.city, wp.task):
                cap = int(round(float(spec["wage"]) * float(rw["max"]) * sim.unit))
                wp.wage = min(cap, int(round(wp.wage * (1.0 + float(rw["step"])))))

    @staticmethod
    def close_idle(sim, name: str, rule: dict, cid: int, st: dict, g: dict) -> None:
        idle_days = (rule.get("close_when") or {}).get("idle_days")
        st["made"] = [w for w in st["made"] if sim.world.alive(w)]
        if idle_days is None or not st["made"]:
            st["idle_since"] = None
            return
        if g["count"] > 0:
            st["idle_since"] = None
            return
        if st["idle_since"] is None:
            st["idle_since"] = sim.day()
        if sim.day() - st["idle_since"] >= float(idle_days):
            for wid in st["made"]:
                wp = sim.world.get(wid, Workplace)
                lst = sim.workplaces_by_city.get(wp.city, [])
                if wid in lst:
                    lst.remove(wid)
                sim.world.destroy(wid)
                sim.__dict__.setdefault("policy_events", []).append(
                    {"tick": sim.tick, "day": sim.day(), "policy": rule.get("policy"), "kind": "office_closed",
                     "city": sim.city_name(cid), "site": wid, "task": wp.task, "rule": name})
            st["made"], st["idle_since"] = [], None
