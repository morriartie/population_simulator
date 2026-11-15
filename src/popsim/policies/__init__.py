"""Policies (REQ-5A.1): named switches for groups of generator rules.

A policy is an entry of the ``policies:`` block (a title, a description and ``enabled``). The generator rules
(``generators:``, ``popsim.generators``) that name it in their ``policy`` key run only while it is on. The scenario
sets the switch; the Play view can flip it during a live run, and the flip is logged as an event. With no policy on,
a run is exactly the run it was before policies existed. Nothing here draws random numbers.
"""

from __future__ import annotations


def names(sim) -> list[str]:
    return list((sim.cfg.get("policies") or {}))


def is_enabled(sim, name: str) -> bool:
    over = sim.__dict__.get("policy_overrides") or {}
    if name in over:
        return bool(over[name])
    return bool(((sim.cfg.get("policies") or {}).get(name) or {}).get("enabled", False))


def set_enabled(sim, name: str, enabled: bool) -> None:
    """Switch a policy on or off during a run. The change is logged as an event so a saved run shows it."""
    if name not in names(sim):
        raise ValueError(f"unknown policy {name!r} (policies are {', '.join(sorted(names(sim)))})")
    if is_enabled(sim, name) == bool(enabled):
        return
    sim.__dict__.setdefault("policy_overrides", {})[name] = bool(enabled)
    sim.event_log.append({"tick": sim.tick, "day": sim.day(), "hour": sim.hour(), "type": "policy",
                          "policy": name, "enabled": bool(enabled)})


def rules_of(sim, name: str) -> dict:
    return {r: spec for r, spec in sorted((sim.cfg.get("generators") or {}).items()) if spec.get("policy") == name}


def _words(spec: dict) -> str:
    w = spec["when"]
    test = f"share above {w['share_gt'] * 100:g}%" if "share_gt" in w else f"count of at least {w['count_gte']}"
    cr = spec["create"]
    what = cr.get("process") or (cr.get("workplace") or {}).get("task")
    return f"{w['signal']}: {test}, held {w.get('for_days', 0):g} d; creates {what}"


def view(sim, name: str) -> dict:
    """Extra data for the dashboard: the fertility map when a rule of the policy places sites on land."""
    from popsim.placement import PLACEMENTS  # noqa: F401
    from popsim.components import Area, Position
    from popsim.world import fertility as F

    if not any(r["create"].get("at") == "fertile_site" for r in rules_of(sim, name).values()):
        return {}
    s = sim.cfg["fertility"]
    pts = [(p.x, p.y, a.radius) for _, (p, a) in sim.world.query(Position, Area)]
    if not pts or not sim.spatial:
        return {"fertility": None}
    reach = float(s["max_offset"]) + 4.0
    x0, x1 = min(x - r for x, y, r in pts) - reach, max(x + r for x, y, r in pts) + reach
    y0, y1 = min(y - r for x, y, r in pts) - reach, max(y + r for x, y, r in pts) + reach
    cell = max(1.0, round(max(x1 - x0, y1 - y0) / 60.0, 1))
    g = F.grid(s, x0, y0, x1, y1, cell)
    g["min_fertility"] = float(s["min_fertility"])
    return {"fertility": g}


def describe(sim) -> list[dict]:
    """Every policy with its state, rules and extras, for the dashboard and the saved run."""
    out = []
    for name in names(sim):
        cfg = (sim.cfg.get("policies") or {}).get(name) or {}
        rules = rules_of(sim, name)
        settings = {f"rule {r}": _words(spec) for r, spec in rules.items()}
        for r, spec in rules.items():
            for k, v in (spec.get("limits") or {}).items():
                settings[f"{r} {k.replace('_', ' ')}"] = v
        out.append({"name": name, "title": cfg.get("title", name.title()), "description": cfg.get("description", ""),
                    "enabled": is_enabled(sim, name), "rules": sorted(rules), "settings": settings,
                    "threshold": next((s["when"].get("share_gt", s["when"].get("count_gte")) for s in rules.values()), None),
                    **view(sim, name)})
    return out


def snapshot(sim) -> dict:
    from popsim.process import site_view, sites

    return {"policies": describe(sim), "daily": list(sim.__dict__.get("policy_log", [])),
            "events": list(sim.__dict__.get("policy_events", [])),
            "signals": list(sim.__dict__.get("signal_log", [])),
            "sites": [site_view(sim, eid) for eid, _ in sites(sim)]}
