"""Read run folders and scenario files; validate scenarios (REQ-1C.2, REQ-1C.3)."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import yaml

from popsim.config import DEFAULT_PATH, resolve

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
EVENT_TYPES = ("stimulus", "mint", "add_road", "set_road", "remove_road")


class NotFound(KeyError):
    pass


class BadRequest(ValueError):
    pass


class NoSnapshot(RuntimeError):
    """The run has no paired snapshot, so its citizens cannot be inspected."""


def check_name(name: str) -> str:
    if not isinstance(name, str) or not NAME_RE.match(name) or ".." in name:
        raise BadRequest(f"invalid name {name!r}: use letters, digits, '_', '-' or '.' (max 64)")
    return name


# ------------------------------------------------------------------ runs


def _read_csv(path: Path) -> list[dict]:
    from popsim.io.logs import read_csv

    return read_csv(path)


def find_snapshot(runs_dir: Path, name: str) -> Path | None:
    """The run's snapshot file: ``end.pkl`` (the dashboard's name) or any ``*.pkl``."""
    d = runs_dir / check_name(name)
    if (d / "end.pkl").is_file():
        return d / "end.pkl"
    found = sorted(d.glob("*.pkl")) if d.is_dir() else []
    return found[0] if found else None


def list_runs(runs_dir: Path) -> list[dict]:
    out = []
    if not runs_dir.exists():
        return out
    for d in sorted(p for p in runs_dir.iterdir() if p.is_dir()):
        summ = d / "summary.json"
        if not summ.exists():
            continue
        try:
            s = json.loads(summ.read_text())
        except json.JSONDecodeError:
            continue
        out.append({
            "name": d.name,
            "scenario": s.get("scenario"),
            "world": s.get("world"),
            "seed": s.get("seed"),
            "days": s.get("days"),
            "population": s.get("population"),
            "deaths": sum((s.get("deaths") or {}).values()),
            "ok": bool(s.get("ledger", {}).get("ok") and s.get("conservation_daily_ok")
                       and s.get("escrow_matches_market")),
            "inspectable": find_snapshot(runs_dir, d.name) is not None,
            "modified": datetime.fromtimestamp(summ.stat().st_mtime, timezone.utc).isoformat(),
        })
    out.sort(key=lambda r: r["modified"], reverse=True)
    return out


def load_run(runs_dir: Path, name: str) -> dict:
    d = runs_dir / check_name(name)
    if not (d / "summary.json").exists():
        raise NotFound(f"no run {name!r}")
    config = json.loads((d / "config.json").read_text()) if (d / "config.json").exists() else None
    return {
        "name": name,
        "inspectable": find_snapshot(runs_dir, name) is not None,
        "summary": json.loads((d / "summary.json").read_text()),
        "config": config,
        "world": json.loads((d / "world.json").read_text()) if (d / "world.json").exists() else None,
        "prices_daily": _read_csv(d / "prices_daily.csv"),
        "cities_daily": _read_csv(d / "cities_daily.csv"),
        "flows": _read_csv(d / "trade_flows.csv"),
        "deaths": _read_csv(d / "deaths.csv"),
        "events": _read_csv(d / "events.csv"),
        "checks": _read_csv(d / "conservation_daily.csv"),
        "transit": _read_csv(d / "transit.csv"),
    }


# ------------------------------------------------------------- scenarios


def list_scenarios(scen_dir: Path) -> list[dict]:
    out = []
    if not scen_dir.exists():
        return out
    for p in sorted(scen_dir.glob("*.yaml")):
        try:
            own = yaml.safe_load(p.read_text()) or {}
            cfg = resolve(own, scen_dir)
            out.append({"name": p.stem, "extends": own.get("extends"), "world_name": cfg.get("world_name"),
                        "days": cfg["time"]["days"], "cities": [c["name"] for c in cfg.get("cities", [])],
                        "events": len(cfg.get("events") or []), "error": None})
        except Exception as e:  # noqa: BLE001 - list broken files too, with their error
            out.append({"name": p.stem, "extends": None, "world_name": None, "days": None,
                        "cities": [], "events": 0, "error": str(e)})
    return out


def scenario_path(scen_dir: Path, name: str) -> Path:
    return scen_dir / f"{check_name(name)}.yaml"


def get_scenario(scen_dir: Path, name: str) -> dict:
    p = scenario_path(scen_dir, name)
    if not p.exists():
        raise NotFound(f"no scenario {name!r}")
    text = p.read_text()
    own = yaml.safe_load(text) or {}
    return {"name": name, "yaml": text, "own": own, "resolved": resolve(own, scen_dir)}


def defaults() -> dict:
    return yaml.safe_load(DEFAULT_PATH.read_text())


def parse_yaml(text: str) -> dict:
    try:
        data = yaml.safe_load(text) if text.strip() else {}
    except yaml.YAMLError as e:
        raise BadRequest(f"YAML error: {e}") from e
    if not isinstance(data, dict):
        raise BadRequest("a scenario must be a YAML mapping")
    return data


def render_yaml(data: dict) -> str:
    return yaml.safe_dump(data, sort_keys=False, default_flow_style=None, width=100, allow_unicode=True)


def _num(errors, path, v, lo=None, hi=None, integer=False, lo_open=False):
    ok_type = isinstance(v, int) if integer else isinstance(v, (int, float))
    if isinstance(v, bool) or not ok_type:
        errors.append(f"{path}: must be {'an integer' if integer else 'a number'} (got {v!r})")
        return
    if lo is not None and (v <= lo if lo_open else v < lo):
        errors.append(f"{path}: must be {'>' if lo_open else '>='} {lo} (got {v})")
    if hi is not None and v > hi:
        errors.append(f"{path}: must be <= {hi} (got {v})")


PATH_RE = re.compile(r"^([a-z_]+(?:\[\d+\])?(?:\.[a-z_]+(?:\[\d+\])?)*):")
MAX_CITIES, MAX_NODES, MAX_WORKPLACES, MAX_HOUSES, MAX_OVERWORLD_NODES = 50, 200, 100, 5000, 500


def with_paths(messages: list[str]) -> list[dict]:
    """Each message with the scenario path it belongs to (``cities[1].nodes[0].at``), or None.
    The map editor uses the path to outline the item a problem concerns (REQ-1C.8)."""
    out = []
    for m in messages:
        found = PATH_RE.match(m)
        out.append({"path": found.group(1) if found else None,
                    "message": m[found.end():].strip() if found else m})
    return out


def validate(data: dict, scen_dir: Path, want_world: bool = False, full: bool = True) -> dict:
    """Check a scenario mapping. Returns ``{ok, errors, warnings, resolved}`` plus ``error_items`` and
    ``warning_items`` (message with its path). ``full=False`` builds the places and only a couple of
    citizens per city, which is much faster for checks as the user types; ``want_world`` adds the built
    layout (positions of the places)."""
    errors: list[str] = []
    warnings: list[str] = []
    try:
        cfg = resolve(data, scen_dir)
    except Exception as e:  # noqa: BLE001
        msg = f"cannot resolve scenario: {e}"
        return {"ok": False, "errors": [msg], "warnings": [], "resolved": None,
                "error_items": with_paths([msg]), "warning_items": []}

    goods = cfg.get("goods") or []
    _num(errors, "time.days", cfg.get("time", {}).get("days"), 0, lo_open=True)
    _num(errors, "time.hours_per_tick", cfg.get("time", {}).get("hours_per_tick"), 0, 24, lo_open=True)
    _num(errors, "seed", cfg.get("seed"), integer=True)
    _num(errors, "money.treasury_share", cfg.get("money", {}).get("treasury_share"), 0, 0.95)
    _num(errors, "npc.merchant_share", cfg.get("npc", {}).get("merchant_share"), 0, 1)
    if not isinstance(cfg.get("world_name"), str) or not cfg.get("world_name"):
        errors.append("world_name: must be a non-empty string")

    def check_fiscal(path, f, city=False):
        """Tax and support policy (REQ-5.5): shares in [0, 1), non-negative amounts."""
        if not isinstance(f, dict):
            errors.append(f"{path}: must be a mapping")
            return
        for key in ("income_tax", "sales_tax"):
            if key in f:
                _num(errors, f"{path}.{key}", f[key], 0, 0.95)
        sup = f.get("support")
        if sup is not None:
            if not isinstance(sup, dict):
                errors.append(f"{path}.support: must be a mapping")
            else:
                if "amount" in sup:
                    _num(errors, f"{path}.support.amount", sup["amount"], 0)
                if "hour" in sup:
                    _num(errors, f"{path}.support.hour", sup["hour"], 0, 23.999)
        pj = f.get("public_jobs")
        if pj is not None and not city:
            if not isinstance(pj, dict):
                errors.append(f"{path}.public_jobs: must be a mapping")
            else:
                if "stamina_per_hour" in pj:
                    _num(errors, f"{path}.public_jobs.stamina_per_hour", pj["stamina_per_hour"], 0)
                if "slots" in pj:
                    _num(errors, f"{path}.public_jobs.slots", pj["slots"], 1, integer=True)

    check_fiscal("fiscal", cfg.get("fiscal") or {})
    pl = cfg.get("player") or {}
    if not isinstance(pl, dict):
        errors.append("player: must be a mapping")
    else:
        if pl.get("gold_share") is not None:
            _num(errors, "player.gold_share", pl["gold_share"], 0, 100)
        if pl.get("gold") is not None:
            _num(errors, "player.gold", pl["gold"], 0)
        pg = pl.get("goods") or {}
        if not isinstance(pg, dict):
            errors.append("player.goods: must be a mapping of good to quantity")
        else:
            for g, q in pg.items():
                if g not in goods:
                    errors.append(f"player.goods.{g}: unknown good (goods are {', '.join(goods)})")
                else:
                    _num(errors, f"player.goods.{g}", q, 0, integer=True)
    coords: dict[str, tuple] = {}
    mv = cfg.get("movement") or {}
    for key in ("walk", "road"):
        for sub in ("speed", "stamina_per_unit"):
            if key in mv:
                _num(errors, f"movement.{key}.{sub}", (mv.get(key) or {}).get(sub), 0, lo_open=(sub == "speed"))
    if "arrival_radius" in mv:
        _num(errors, "movement.arrival_radius", mv["arrival_radius"], 0)
    if "home_rest_multiplier" in mv:
        _num(errors, "movement.home_rest_multiplier", mv["home_rest_multiplier"], 1)

    if cfg.get("housing_pct") is not None:
        _num(errors, "housing_pct", cfg["housing_pct"], 0, 1000)
    cities = cfg.get("cities") or []
    if isinstance(cities, list) and len(cities) > MAX_CITIES:
        errors.append(f"cities: at most {MAX_CITIES} cities (got {len(cities)})")
    if not isinstance(cities, list) or not cities:
        errors.append("cities: at least one city is required")
        cities = []
    names: list[str] = []
    for i, c in enumerate(cities):
        p = f"cities[{i}]"
        if not isinstance(c, dict):
            errors.append(f"{p}: must be a mapping")
            continue
        n = c.get("name")
        if not isinstance(n, str) or not n:
            errors.append(f"{p}.name: required")
        elif n in names:
            errors.append(f"{p}.name: duplicate city {n!r}")
        elif n == "snapshots":
            errors.append(f"{p}.name: 'snapshots' is reserved")
        else:
            names.append(n)
        _num(errors, f"{p}.population", c.get("population"), 1, 5000, integer=True)
        has_xy = [c.get("x") is not None, c.get("y") is not None]
        if any(has_xy) and not all(has_xy):
            errors.append(f"{p}: give both x and y, or neither")
        elif all(has_xy):
            _num(errors, f"{p}.x", c.get("x"))
            _num(errors, f"{p}.y", c.get("y"))
            coords[n if isinstance(n, str) else f"#{i}"] = (c.get("x"), c.get("y"))
        if c.get("radius") is not None:
            _num(errors, f"{p}.radius", c.get("radius"), 0, lo_open=True)
        radius = c.get("radius", (cfg.get("movement") or {}).get("default_city_radius", 2.0))
        city_xy_ok = all(has_xy) and isinstance(radius, (int, float))

        def check_at(path, spec, _ok=city_xy_ok, _r=radius):
            at = spec.get("at") if isinstance(spec, dict) else None
            if at is None:
                return
            if not (isinstance(at, (list, tuple)) and len(at) == 2
                    and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in at)):
                errors.append(f"{path}.at: must be [dx, dy], an offset from the city center")
            elif not _ok:
                errors.append(f"{path}.at: give the city x and y first (positions need coordinates)")
            elif (at[0] ** 2 + at[1] ** 2) ** 0.5 > _r + 1e-9:
                errors.append(f"{path}.at: {list(at)} is outside the city radius {_r}")

        houses = c.get("houses") or []
        if isinstance(houses, dict):
            _num(errors, f"{p}.houses.count", houses.get("count"), 0, 5000, integer=True)
            house_n = houses.get("count") if isinstance(houses.get("count"), int) else 0
        elif isinstance(houses, list):
            for j, hs in enumerate(houses):
                if hs is not None and not isinstance(hs, dict):
                    errors.append(f"{p}.houses[{j}]: must be a mapping (or empty)")
                else:
                    check_at(f"{p}.houses[{j}]", hs or {})
            house_n = len(houses)
        else:
            errors.append(f"{p}.houses: must be a list of houses or {{count: N}}")
            house_n = 0
        if c.get("housing_pct") is not None:
            _num(errors, f"{p}.housing_pct", c["housing_pct"], 0, 1000)
        pct = c.get("housing_pct", cfg.get("housing_pct", 0))
        generated = 0
        if isinstance(c.get("population"), int) and isinstance(pct, (int, float)) and not isinstance(pct, bool):
            generated = max(0, int(c["population"] * pct / 100.0 + 0.5))
        hp = c.get("house_positions") or []
        if not isinstance(hp, list):
            errors.append(f"{p}.house_positions: must be a list of {{index, at}}")
        else:
            for j, item in enumerate(hp):
                q = f"{p}.house_positions[{j}]"
                if not isinstance(item, dict) or not isinstance(item.get("index"), int) or isinstance(item.get("index"), bool) \
                        or item["index"] < 0 or item["index"] >= generated:
                    errors.append(f"{q}.index: must be the number of a generated house (0 to {max(generated - 1, 0)}); "
                                  f"this city generates {generated}")
                    continue
                check_at(q, item)
        if isinstance(c.get("population"), int) and house_n > c["population"]:
            warnings.append(f"{p}: {house_n} houses for {c['population']} people; the extra houses stay empty")
        if c.get("fiscal") is not None:
            check_fiscal(f"{p}.fiscal", c["fiscal"], city=True)
        for j, wp in enumerate(c.get("workplaces") or []):
            q = f"{p}.workplaces[{j}]"
            if not isinstance(wp, dict):
                errors.append(f"{q}: must be a mapping")
                continue
            if not isinstance(wp.get("task", "work"), str):
                errors.append(f"{q}.task: must be text")
            _num(errors, f"{q}.wage", wp.get("wage", 0), 0)
            if "slots" in wp:
                _num(errors, f"{q}.slots", wp["slots"], 1, integer=True)
            if "stamina_per_hour" in wp:
                _num(errors, f"{q}.stamina_per_hour", wp["stamina_per_hour"], 0)
            if (wp.get("wage") or 0) > 0 and (cfg.get("money") or {}).get("treasury_share", 0) == 0:
                warnings.append(f"{q}: treasury_share is 0, so the city has no gold to pay this wage")
            check_at(q, wp)
        if len(c.get("nodes") or []) > MAX_NODES:
            errors.append(f"{p}.nodes: at most {MAX_NODES} resource nodes per city (got {len(c['nodes'])})")
        if len(c.get("workplaces") or []) > MAX_WORKPLACES:
            errors.append(f"{p}.workplaces: at most {MAX_WORKPLACES} workplaces per city (got {len(c['workplaces'])})")
        if house_n > MAX_HOUSES:
            errors.append(f"{p}.houses: at most {MAX_HOUSES} houses per city (got {house_n})")
        for j, nd in enumerate(c.get("nodes") or []):
            q = f"{p}.nodes[{j}]"
            check_at(q, nd)
            if nd.get("good") not in goods:
                errors.append(f"{q}.good: must be one of {goods} (got {nd.get('good')!r})")
            for key, lo, open_ in (("initial", 0, False), ("max_amount", 0, False), ("regen_per_hour", 0, False),
                                   ("base_rate", 0, True), ("stamina_cost", 0, True)):
                _num(errors, f"{q}.{key}", nd.get(key, 0 if key == "initial" else None), lo, lo_open=open_)
            if isinstance(nd.get("initial"), (int, float)) and isinstance(nd.get("max_amount"), (int, float)) \
                    and nd["initial"] > nd["max_amount"]:
                errors.append(f"{q}: initial ({nd['initial']}) exceeds max_amount ({nd['max_amount']})")
        produced = {nd.get("good") for nd in c.get("nodes") or []} | {
            nd.get("good") for nd in cfg.get("nodes") or [] if isinstance(nd, dict)}
        for g in goods:
            if g not in produced:
                warnings.append(f"cities[{i}]: city {n!r} has no {g} node, so it must import {g} or go without")

    n_with = sum(1 for c in cities if isinstance(c, dict) and c.get("x") is not None and c.get("y") is not None)
    spatial = bool(cities) and n_with == len(cities)
    if 0 < n_with < len(cities):
        errors.append("cities: either every city has x and y or none does (a mix is not allowed)")
    names_xy = list(coords.items())
    default_r = (cfg.get("movement") or {}).get("default_city_radius", 2.0)
    index = {c.get("name"): i for i, c in enumerate(cities) if isinstance(c, dict)}
    for i, (na, (xa, ya)) in enumerate(names_xy):
        for nb, (xb, yb) in names_xy[i + 1:]:
            if not all(isinstance(v, (int, float)) for v in (xa, ya, xb, yb)):
                continue
            gap = ((xa - xb) ** 2 + (ya - yb) ** 2) ** 0.5
            if gap < 1e-9:
                errors.append(f"cities[{index.get(nb)}]: {nb!r} is at the same position as {na!r}")
                continue
            ra = cities[index[na]].get("radius", default_r)
            rb = cities[index[nb]].get("radius", default_r)
            if isinstance(ra, (int, float)) and isinstance(rb, (int, float)) and gap < ra + rb:
                warnings.append(f"cities[{index.get(nb)}]: {nb!r} overlaps {na!r} ({gap:.1f} km apart, radii add up to {ra + rb:.1f} km)")

    def straight(a, b):
        (xa, ya), (xb, yb) = coords[a], coords[b]
        return ((xa - xb) ** 2 + (ya - yb) ** 2) ** 0.5

    def road_values(path, r):
        """hours and stamina_cost are required without coordinates, optional (derived) with them."""
        for key in ("hours", "stamina_cost"):
            if r.get(key) is None and spatial:
                continue
            _num(errors, f"{path}.{key}", r.get(key), 0, lo_open=(key == "hours"))
        if r.get("length") is not None:
            _num(errors, f"{path}.length", r.get("length"), 0, lo_open=True)
            if spatial and r.get("a") in coords and r.get("b") in coords and isinstance(r["length"], (int, float)) \
                    and r["length"] < straight(r["a"], r["b"]) - 1e-9:
                errors.append(f"{path}.length: {r['length']} is below the straight-line distance "
                              f"{straight(r['a'], r['b']):.3f} between {r['a']} and {r['b']}")

    def city_ref(path, v, allow_all=False):
        if allow_all and v == "all":
            return
        if v not in names:
            errors.append(f"{path}: unknown city {v!r}")

    for i, r in enumerate(cfg.get("roads") or []):
        p = f"roads[{i}]"
        city_ref(f"{p}.a", r.get("a"))
        city_ref(f"{p}.b", r.get("b"))
        if r.get("a") == r.get("b"):
            errors.append(f"{p}: a road needs two different cities")
        road_values(p, r)

    # Resource nodes on the overworld (REQ-3A.11).
    wnodes = cfg.get("nodes") or []
    if not isinstance(wnodes, list):
        errors.append("nodes: must be a list of resource nodes")
        wnodes = []
    if wnodes and not spatial:
        errors.append("nodes: resource nodes outside cities need cities with coordinates")
    if len(wnodes) > MAX_OVERWORLD_NODES:
        errors.append(f"nodes: at most {MAX_OVERWORLD_NODES} resource nodes (got {len(wnodes)})")
    circles = {}
    for c in cities:
        if isinstance(c, dict) and isinstance(c.get("x"), (int, float)) and isinstance(c.get("y"), (int, float)):
            r = c.get("radius", default_r)
            circles[c.get("name")] = (c["x"], c["y"], r if isinstance(r, (int, float)) else default_r)
    known: list[tuple] = []
    for r in list(cfg.get("roads") or []) + [e for e in cfg.get("events") or []
                                              if isinstance(e, dict) and e.get("type") in ("add_road", "set_road")]:
        if isinstance(r, dict) and (r.get("a"), r.get("b")) not in known and (r.get("b"), r.get("a")) not in known:
            known.append((r.get("a"), r.get("b")))
    for i, nd in enumerate(wnodes[:MAX_OVERWORLD_NODES]):
        p = f"nodes[{i}]"
        if not isinstance(nd, dict):
            errors.append(f"{p}: must be a mapping")
            continue
        if nd.get("good") not in goods:
            errors.append(f"{p}.good: must be one of {goods} (got {nd.get('good')!r})")
        for key, lo, open_ in (("initial", 0, False), ("max_amount", 0, False), ("regen_per_hour", 0, False),
                               ("base_rate", 0, True), ("stamina_cost", 0, True)):
            _num(errors, f"{p}.{key}", nd.get(key, 0 if key == "initial" else None), lo, lo_open=open_)
        if isinstance(nd.get("initial"), (int, float)) and isinstance(nd.get("max_amount"), (int, float)) \
                and nd["initial"] > nd["max_amount"]:
            errors.append(f"{p}: initial ({nd['initial']}) exceeds max_amount ({nd['max_amount']})")
        at = nd.get("at")
        at_ok = isinstance(at, (list, tuple)) and len(at) == 2 and all(
            isinstance(v, (int, float)) and not isinstance(v, bool) for v in at)
        if at is not None and not at_ok:
            errors.append(f"{p}.at: must be [x, y], absolute coordinates")
        elif at_ok:
            inside = [n for n, (cx, cy, cr) in circles.items() if ((at[0] - cx) ** 2 + (at[1] - cy) ** 2) ** 0.5 <= cr + 1e-9]
            if inside:
                errors.append(f"{p}.at: {list(at)} is inside {inside[0]!r}; resource nodes belong on the overworld, outside every city")
        br = nd.get("branch")
        road = None
        if br is not None:
            road = br.get("road") if isinstance(br, dict) else None
            if not (isinstance(road, (list, tuple)) and len(road) == 2):
                errors.append(f"{p}.branch.road: must be [city, city], two different cities")
                road = None
            elif road[0] == road[1]:
                errors.append(f"{p}.branch.road: the two cities must be different")
                road = None
            elif any(x not in names for x in road):
                bad = [x for x in road if x not in names]
                errors.append(f"{p}.branch.road: unknown city {bad[0]!r} (cities: {', '.join(map(str, names))})")
                road = None
            t = br.get("t") if isinstance(br, dict) else None
            _num(errors, f"{p}.branch.t", 0.5 if t is None else t, 0, 1)
        if br is None and at is None:
            errors.append(f"{p}: give a position (at) or a branch")
        elif br is None and at_ok and spatial:
            if not any(a in circles and b in circles for a, b in known):
                errors.append(f"{p}.branch: there is no road to branch from; add a road between two cities first")
        if nd.get("branch_length") is not None:
            _num(errors, f"{p}.branch_length", nd["branch_length"], 0, lo_open=True)
        for key in ("branch_hours", "branch_stamina_cost"):
            if nd.get(key) is not None:
                _num(errors, f"{p}.{key}", nd[key], 0, lo_open=(key == "branch_hours"))
        if road and at_ok and spatial and road[0] in circles and road[1] in circles and isinstance(br, dict) \
                and isinstance(br.get("t"), (int, float)) and isinstance(nd.get("branch_length"), (int, float)):
            (x0, y0, _), (x1, y1, _) = circles[road[0]], circles[road[1]]
            jx, jy = x0 + (x1 - x0) * br["t"], y0 + (y1 - y0) * br["t"]
            straight_len = ((at[0] - jx) ** 2 + (at[1] - jy) ** 2) ** 0.5
            if nd["branch_length"] < straight_len - 1e-9:
                errors.append(f"{p}.branch_length: {nd['branch_length']} is below the straight-line distance {straight_len:.3f}")
        if isinstance(nd, dict) and not nd.get("initial") and not nd.get("regen_per_hour"):
            warnings.append(f"{p}: starts empty and never regrows, so no one can gather from it")
        if road is not None and tuple(road) not in known and tuple(road)[::-1] not in known:
            warnings.append(f"{p}.branch: there is no road between {road[0]} and {road[1]}, so the node cannot be reached")

    days = cfg.get("time", {}).get("days") or 0
    share = cfg.get("money", {}).get("treasury_share") or 0
    for i, ev in enumerate(cfg.get("events") or []):
        p = f"events[{i}]"
        t = ev.get("type")
        if t not in EVENT_TYPES:
            errors.append(f"{p}.type: must be one of {list(EVENT_TYPES)} (got {t!r})")
            continue
        if "tick" in ev:
            _num(errors, f"{p}.tick", ev["tick"], 0, integer=True)
        else:
            _num(errors, f"{p}.day", ev.get("day"), 0)
            if "hour" in ev:
                _num(errors, f"{p}.hour", ev["hour"], 0, 23.999)
            if isinstance(ev.get("day"), (int, float)) and ev["day"] >= days:
                warnings.append(f"{p}: day {ev['day']} is after the run ends (day {days}); it never fires")
        if t == "stimulus":
            city_ref(f"{p}.city", ev.get("city", "all"), allow_all=True)
            _num(errors, f"{p}.per_npc", ev.get("per_npc"), 0)
            if share == 0:
                warnings.append(f"{p}: treasury_share is 0, so the stimulus has no gold to pay out")
        elif t == "mint":
            city_ref(f"{p}.city", ev.get("city"))
            _num(errors, f"{p}.amount", ev.get("amount"), 0, lo_open=True)
        else:
            city_ref(f"{p}.a", ev.get("a"))
            city_ref(f"{p}.b", ev.get("b"))
            if t != "remove_road":
                road_values(p, ev)

    # A city cut off from the others while some roads exist (isolation on purpose, with no roads at
    # all, is the REQ-3.4 starting point and not worth a warning).
    connected = set()
    for r in cfg.get("roads") or []:
        if isinstance(r, dict):
            connected.update([r.get("a"), r.get("b")])
    for ev in cfg.get("events") or []:
        if isinstance(ev, dict) and ev.get("type") in ("add_road", "set_road"):
            connected.update([ev.get("a"), ev.get("b")])
    if connected and len(cities) > 1:
        for i, c in enumerate(cities):
            if isinstance(c, dict) and c.get("name") not in connected:
                warnings.append(f"cities[{i}]: {c.get('name')!r} has no road and no road event, so it stays cut off")
    if spatial:
        reach = (cfg.get("physiology") or {}).get("stamina", {}).get("absolute_max", 100.0) * 0.5
        rate = ((cfg.get("movement") or {}).get("walk") or {}).get("stamina_per_unit", 0.8)
        for i, c in enumerate(cities):
            if not isinstance(c, dict):
                continue
            for j, nd in enumerate(c.get("nodes") or []):
                at = nd.get("at") if isinstance(nd, dict) else None
                if isinstance(at, (list, tuple)) and len(at) == 2 and all(isinstance(v, (int, float)) for v in at):
                    if 2 * (at[0] ** 2 + at[1] ** 2) ** 0.5 * rate > reach:
                        warnings.append(f"cities[{i}].nodes[{j}]: very far from the city center; few people will have the "
                                        "stamina to walk there and back")
            for j, nd in enumerate(c.get("nodes") or []):
                if isinstance(nd, dict) and not nd.get("initial") and not nd.get("regen_per_hour"):
                    warnings.append(f"cities[{i}].nodes[{j}]: starts empty and never regrows, so no one can gather from it")

    world = None
    if not errors:
        # Final check: the world must actually build (without citizens unless asked for the full check).
        try:
            from popsim.world import Simulation

            sim = Simulation(cfg, npcs=False)      # step one: the places
            if want_world:
                from popsim.io.logs import world_layout

                world = world_layout(sim)           # the layout is the places; citizens never move them
            # Step two: the citizens (REQ-3.8). A quick check makes just a couple per city, enough to run
            # the code that creates them, so a mistake in the citizen settings shows up as you edit.
            sim.populate(sample=None if full else 2)
        except Exception as e:  # noqa: BLE001
            errors.append(f"world failed to build: {type(e).__name__}: {e}")
    out = {"ok": not errors, "errors": errors, "warnings": warnings, "resolved": cfg,
           "error_items": with_paths(errors), "warning_items": with_paths(warnings)}
    if want_world:
        out["world"] = indexed_world(world, cfg) if world else None
    return out


def indexed_world(world: dict, cfg: dict) -> dict:
    """Mark each node, house and workplace with its city index and its place in that city's list, so the
    map editor can tell which scenario entry a position belongs to (they are built in list order)."""
    order = {c["name"]: i for i, c in enumerate(cfg.get("cities") or [])}
    over = 0
    for item in world["nodes"]:
        if item.get("overworld"):
            item["oi"] = over
            over += 1
    for key in ("nodes", "houses", "workplaces"):
        seen: dict[str, int] = {}
        for item in world[key]:
            if item.get("overworld"):
                continue
            item["ci"] = order.get(item["city"])
            item["i"] = seen.get(item["city"], 0)
            seen[item["city"]] = item["i"] + 1
    return world


def preview(data: dict, scen_dir: Path) -> dict:
    """Validation plus the world layout as the simulation will build it (REQ-1C.8): the positions of
    everything the scenario leaves automatic."""
    return validate(data, scen_dir, want_world=True, full=False)


def save_scenario(scen_dir: Path, name: str, text: str, overwrite: bool = False) -> dict:
    p = scenario_path(scen_dir, name)
    data = parse_yaml(text)
    report = validate(data, scen_dir)
    if not report["ok"]:
        return {"saved": False, **report}
    if p.exists() and not overwrite:
        raise BadRequest(f"scenario {name!r} already exists")
    scen_dir.mkdir(parents=True, exist_ok=True)
    p.write_text(text if text.endswith("\n") else text + "\n")
    return {"saved": True, "name": name, **report}
