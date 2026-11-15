"""The resource contract (REQ-5B): one place that says, for each good, which need it satisfies, how much of the
need a unit restores, how much of it a citizen is expected to use, which process supplies it and which signal
should trigger new supply.

``Resources`` is a read-only view of the ``resources:`` config block. ``validate`` checks the block against the
rest of the config (needs, processes, signals, policies) and ``expand`` fills generator rules written as
``{from_resource: <good>}``. Nothing here draws random numbers or moves gold.
"""

from __future__ import annotations

from typing import Any

from popsim.config import deep_merge


class ConfigError(ValueError):
    """A config that cannot be run; the message lists every problem found."""


def _entries(cfg: dict) -> dict[str, dict]:
    res = cfg.get("resources")
    return {g: e for g, e in res.items() if isinstance(e, dict)} if isinstance(res, dict) else {}


def _legacy_entries(cfg: dict) -> dict[str, dict]:
    """The contract of a config from before REQ-5B (an old snapshot): food and wood from their scattered keys."""
    units = (cfg.get("needs") or {}).get("daily_units") or {}
    out = {
        "food": {"satisfies": {"need": "food",
                               "per_unit": cfg["physiology"]["metabolism"]["satiety_per_food"]}},
        "wood": {"satisfies": {"need": "shelter", "per_unit": cfg["shelter"]["per_wood"]}},
    }
    for g, e in out.items():
        if g in units:
            e["daily_units"] = units[g]
    return out


class Resources:
    """Lookups the systems use every tick, built once per simulation (see ``of``)."""

    def __init__(self, cfg: dict) -> None:
        ent = _entries(cfg) if "resources" in cfg else _legacy_entries(cfg)
        self.entries = ent
        #: good -> need it satisfies, in the order of the block
        self.good_need: dict[str, str] = {}
        self.need_good: dict[str, str] = {}
        self._per_unit: dict[str, float] = {}
        #: good -> expected units per citizen per day (budgets and stock targets)
        self.daily_units: dict[str, float] = {}
        for g, e in ent.items():
            sat = e.get("satisfies")
            if isinstance(sat, dict) and sat.get("need"):
                self.good_need[g] = sat["need"]
                self.need_good.setdefault(sat["need"], g)
                self._per_unit[g] = float(sat.get("per_unit", 0.0))
            if e.get("daily_units") is not None:
                self.daily_units[g] = float(e["daily_units"])

    def per_unit(self, good: str) -> float:
        return self._per_unit.get(good, 0.0)

    def consumption(self) -> list[tuple[str, str, float]]:
        """(good, need, restored per unit) for every good that satisfies a need."""
        return [(g, n, self._per_unit[g]) for g, n in self.good_need.items()]


def of(sim) -> Resources:
    """The simulation's contract, cached on it (a snapshot that predates the cache builds it on first use)."""
    r = sim.__dict__.get("_resources")
    if r is None:
        r = sim.__dict__["_resources"] = Resources(sim.cfg)
    return r


# ----------------------------------------------------------------- generator rules


def rule_defaults(good: str, entry: dict) -> dict:
    """What a rule ``{from_resource: good}`` gets from the resource: ``policy`` and ``create`` and ``limits``
    from ``supply``, ``when`` from ``trigger``."""
    sup = entry.get("supply") or {}
    out: dict[str, Any] = {}
    if sup.get("policy"):
        out["policy"] = sup["policy"]
    if entry.get("trigger"):
        out["when"] = dict(entry["trigger"])
    create = {k: sup[k] for k in ("process", "at", "reserve") if k in sup}
    if create:
        out["create"] = create
    if sup.get("limits"):
        out["limits"] = dict(sup["limits"])
    return out


def expand(cfg: dict) -> dict:
    """Fill the generator rules that name a resource (``from_resource``), in place. A key written in the rule
    wins, nested mappings merging key by key. The marker is removed, so expanding twice changes nothing."""
    rules = cfg.get("generators")
    ent = _entries(cfg)
    if not isinstance(rules, dict):
        return cfg
    for name, rule in list(rules.items()):
        if not isinstance(rule, dict) or "from_resource" not in rule:
            continue
        good = rule["from_resource"]
        own = {k: v for k, v in rule.items() if k != "from_resource"}
        entry = ent.get(good)
        # An unknown resource is left as written so ``validate`` can name it.
        rules[name] = own if entry is None else deep_merge(rule_defaults(good, entry), own)
        if entry is None:
            rules[name]["from_resource"] = good
    return cfg


# ----------------------------------------------------------------- validation

LEGACY = (("physiology", "metabolism", "satiety_per_food", "resources.food.satisfies.per_unit"),
          ("shelter", "per_wood", None, "resources.wood.satisfies.per_unit"),
          ("needs", "daily_units", None, "resources.<good>.daily_units"))


def _walk_effects(node) -> list[dict]:
    """Every ``spawn_node`` effect anywhere in a process definition."""
    out: list[dict] = []
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "spawn_node" and isinstance(v, dict):
                out.append(v)
            out.extend(_walk_effects(v))
    elif isinstance(node, list):
        for v in node:
            out.extend(_walk_effects(v))
    return out


def _required_items(node) -> dict[str, float]:
    """Goods a process asks citizens to deliver (``requires: {items: {good: n}}``)."""
    out: dict[str, float] = {}
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "requires" and isinstance(v, dict) and isinstance(v.get("items"), dict):
                out.update(v["items"])
            else:
                out.update(_required_items(v))
    elif isinstance(node, list):
        for v in node:
            out.update(_required_items(v))
    return out


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def validate(cfg: dict) -> tuple[list[str], list[str]]:
    """Check the contract against the rest of the config. Returns (errors, warnings); errors stop a load."""
    errors: list[str] = []
    warnings: list[str] = []
    res = cfg.get("resources", {})
    if "resources" in cfg and not isinstance(res, dict):
        return ["resources: must be a mapping of good to entry"], warnings
    ent = _entries(cfg)
    for g, e in (res or {}).items():
        if e is not None and not isinstance(e, dict):  # null removes an entry inherited from the default
            errors.append(f"resources.{g}: must be a mapping")
    needs = set(((cfg.get("needs") or {}).get("defaults") or {}))
    sig_needs = set((cfg.get("signals") or {}).get("needs") or [])
    processes = cfg.get("processes") or {}
    policies = set(cfg.get("policies") or {})
    goods = set(cfg.get("goods") or []) | set(ent)
    world_goods = set(cfg.get("goods") or [])

    # legacy keys that moved into the contract
    for a, b, c, new in LEGACY:
        cur = cfg.get(a)
        if isinstance(cur, dict) and b in cur:
            cur = cur[b]
            if c is not None:
                cur = cur.get(c) if isinstance(cur, dict) else None
                path = f"{a}.{b}.{c}"
            else:
                path = f"{a}.{b}"
            if cur is not None:
                errors.append(f"{path}: moved into the resource contract, set {new} instead")

    taken: dict[str, str] = {}
    for g, e in ent.items():
        where = f"resources.{g}"
        sat = e.get("satisfies")
        if sat is not None:
            if not isinstance(sat, dict) or "need" not in sat:
                errors.append(f"{where}.satisfies: must be {{need, per_unit}}")
            else:
                if sat["need"] not in needs:
                    errors.append(f"{where}.satisfies.need: {sat['need']!r} is not a need "
                                  f"(needs are {', '.join(sorted(needs))})")
                if not (_is_number(sat.get("per_unit")) and sat["per_unit"] > 0):
                    errors.append(f"{where}.satisfies.per_unit: must be a positive number")
                if sat["need"] in taken:
                    errors.append(f"{where}.satisfies.need: {taken[sat['need']]!r} already satisfies "
                                  f"{sat['need']!r} (one good per need)")
                taken.setdefault(sat["need"], g)
        if e.get("daily_units") is not None and not (_is_number(e["daily_units"]) and e["daily_units"] >= 0):
            errors.append(f"{where}.daily_units: must be a number, zero or more")
        sup, trig = e.get("supply"), e.get("trigger")
        if sup is not None:
            if not isinstance(sup, dict) or "process" not in sup:
                errors.append(f"{where}.supply: must name a process")
            else:
                if sup["process"] not in processes:
                    errors.append(f"{where}.supply.process: {sup['process']!r} is not in processes")
                elif not any(s.get("good") == g for s in _walk_effects(processes[sup["process"]])):
                    errors.append(f"{where}.supply.process: {sup['process']!r} never makes a {g!r} node "
                                  f"(no spawn_node for {g})")
                if sup.get("policy") and sup["policy"] not in policies:
                    warnings.append(f"{where}.supply.policy: {sup['policy']!r} is not in policies")
        if trig is not None:
            sig = trig.get("signal") if isinstance(trig, dict) else None
            if not isinstance(sig, str):
                errors.append(f"{where}.trigger: must have a signal")
            elif sig.startswith("need:"):
                n = sig[5:]
                if n not in sig_needs:
                    errors.append(f"{where}.trigger.signal: {sig!r} is not a signal (signals.needs is "
                                  f"{', '.join(sorted(sig_needs))})")
                elif isinstance(sat, dict) and sat.get("need") != n:
                    errors.append(f"{where}.trigger.signal: {sig!r} is about {n}, but {g} satisfies "
                                  f"{sat.get('need')!r}: the rule would not close the loop")
                elif not isinstance(sat, dict):
                    errors.append(f"{where}.trigger: {g} satisfies no need, so {sig!r} cannot be served by it")
            elif not sig.startswith("service:"):
                errors.append(f"{where}.trigger.signal: {sig!r} is not a signal (need:<name> or service:<task>)")
            if sup is None:
                warnings.append(f"{where}.trigger: no supply, so nothing is created when it fires")
        if e.get("input"):
            by = e.get("byproduct_of")
            if by is not None and by not in goods:
                errors.append(f"{where}.byproduct_of: {by!r} is not a good")
            used = e.get("consumed_by") or []
            if not isinstance(used, list):
                errors.append(f"{where}.consumed_by: must be a list of processes")
                used = []
            for p in used:
                if p not in processes:
                    errors.append(f"{where}.consumed_by: {p!r} is not in processes")
                elif g not in _required_items(processes[p]):
                    warnings.append(f"{where}.consumed_by: {p!r} does not require {g}")
            made = by is not None or any(
                s.get("good") == g for p in processes.values() for s in _walk_effects(p))
            if not made:
                warnings.append(f"{where}: nothing produces {g} (no byproduct_of, no process makes it)")
            if not used:
                warnings.append(f"{where}: {g} is an input but no process consumes it")

    # what processes need but the world cannot trade (only for processes an enabled rule can create)
    def on(rule):
        pol = rule.get("policy") if isinstance(rule, dict) else None
        return pol is None or bool(((cfg.get("policies") or {}).get(pol) or {}).get("enabled"))

    live = {(r.get("create") or {}).get("process") for r in (cfg.get("generators") or {}).values()
            if isinstance(r, dict) and isinstance(r.get("create"), dict) and on(r)}
    for pname, body in processes.items():
        for g in _required_items(body):
            if pname in live and g not in world_goods:
                warnings.append(f"processes.{pname}: needs {g!r}, which is not in goods, so it can never "
                                f"be delivered")
            elif g in ent and ent[g].get("input") and pname not in (ent[g].get("consumed_by") or []):
                warnings.append(f"processes.{pname}: requires {g}, but resources.{g}.consumed_by does not list it")

    # generator rules
    served = set()
    for name, rule in (cfg.get("generators") or {}).items():
        if not isinstance(rule, dict):
            continue
        if "from_resource" in rule:
            errors.append(f"generators.{name}.from_resource: {rule['from_resource']!r} has no entry in resources")
            continue
        sig = (rule.get("when") or {}).get("signal") if isinstance(rule.get("when"), dict) else None
        proc = (rule.get("create") or {}).get("process") if isinstance(rule.get("create"), dict) else None
        if isinstance(sig, str) and sig.startswith("need:") and proc in processes:
            n = sig[5:]
            made = {s.get("good") for s in _walk_effects(processes[proc])}
            satisfied = {g for g, e in ent.items() if isinstance(e.get("satisfies"), dict)
                         and e["satisfies"].get("need") == n}
            if satisfied and not (made & satisfied):
                errors.append(f"generators.{name}: reacts to {sig} but {proc!r} makes "
                              f"{', '.join(sorted(map(str, made))) or 'no node'}, not "
                              f"{', '.join(sorted(satisfied))}")
            served.add(n)
        elif isinstance(sig, str) and sig.startswith("need:"):
            served.add(sig[5:])
    for n in sorted(served):
        if not any(isinstance(e.get("satisfies"), dict) and e["satisfies"].get("need") == n for e in ent.values()):
            warnings.append(f"signals: a rule reacts to need:{n} but no resource satisfies {n}")
    return errors, warnings
