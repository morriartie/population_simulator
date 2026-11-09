"""Server-side helpers of the scenario map editor (REQ-1C.8).

Every helper takes the scenario's *own* mapping (what the file says) plus the resolved scenario (with
whatever it inherits) and returns the new own mapping together with a plain list of what was added, so
the editor can show the change before applying it. They are deterministic: the same input always gives
the same output, which also makes them easy to test.

Lists replace inherited lists (see ``popsim.config``), so a helper that touches ``cities`` or ``roads``
first copies the inherited list into the scenario, exactly like the form editor does.
"""

from __future__ import annotations

import copy
import math
import random
from pathlib import Path

from popsim.config import resolve

MAX_CITIES, MAX_NODES, MAX_WORKPLACES, MAX_HOUSES = 50, 200, 100, 5000
DEFAULT_NODE = {"initial": 250, "max_amount": 350, "regen_per_hour": 4.0, "base_rate": 0.7, "stamina_cost": 5.0}
HELPERS = ("arrange_cities", "connect_cities", "fill_resources", "fill_workplaces", "fill_houses",
           "scatter", "remove_positions")


class HelperError(ValueError):
    pass


# ------------------------------------------------------------------- basics


def _own_list(own: dict, resolved: dict, key: str) -> list:
    if own.get(key) is None:
        own[key] = copy.deepcopy(resolved.get(key) or [])
    return own[key]


def _default_radius(resolved: dict) -> float:
    return float((resolved.get("movement") or {}).get("default_city_radius", 2.0))


def _has_xy(c: dict) -> bool:
    return c.get("x") is not None and c.get("y") is not None


def _round(v: float) -> float:
    return round(float(v), 2)


def _num(params: dict, key: str, default=None, lo=None, hi=None, integer=False):
    v = params.get(key, default)
    if v is None:
        raise HelperError(f"{key} is required")
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise HelperError(f"{key} must be a number")
    if integer and int(v) != v:
        raise HelperError(f"{key} must be a whole number")
    if lo is not None and v < lo:
        raise HelperError(f"{key} must be at least {lo}")
    if hi is not None and v > hi:
        raise HelperError(f"{key} must be at most {hi}")
    return int(v) if integer else float(v)


def _city_index(cities: list, name) -> int:
    for i, c in enumerate(cities):
        if c.get("name") == name:
            return i
    raise HelperError(f"unknown city {name!r}")


def _targets(cities: list, which) -> list[dict]:
    if which in (None, "all"):
        return cities
    return [cities[_city_index(cities, which)]]


# ------------------------------------------------------------ arrange cities


def arrange_cities(own: dict, resolved: dict, params: dict) -> tuple[list[str], list[str]]:
    """Give cities without coordinates positions that do not overlap, spaced by their radii."""
    gap = _num(params, "gap", 10.0, lo=0.0, hi=1000.0)
    cities = _own_list(own, resolved, "cities")
    if not cities:
        raise HelperError("there are no cities to arrange")
    default_r = _default_radius(resolved)
    radius = [float(c.get("radius", default_r)) for c in cities]
    missing = [i for i, c in enumerate(cities) if not _has_xy(c)]
    if not missing:
        return [], ["Every city already has a position."]
    added: list[str] = []

    def put(i: int, x: float, y: float) -> None:
        cities[i]["x"], cities[i]["y"] = _round(x), _round(y)
        added.append(f"{cities[i].get('name')} at ({cities[i]['x']}, {cities[i]['y']})")

    placed = [i for i, c in enumerate(cities) if _has_xy(c)]
    if not placed:
        n = len(cities)
        if n == 1:
            put(0, 0.0, 0.0)
        elif n == 2:
            d = radius[0] + radius[1] + gap
            put(0, -d / 2, 0.0)
            put(1, d / 2, 0.0)
        else:
            # a ring wide enough that neighbouring cities (the closest pairs) keep the gap
            ring = max((radius[i] + radius[(i + 1) % n] + gap) / (2 * math.sin(math.pi / n)) for i in range(n))
            for i in range(n):
                a = 2 * math.pi * i / n
                put(i, ring * math.cos(a), ring * math.sin(a))
    else:
        # some cities already have positions: line the others up to the right of them
        right = max(cities[i]["x"] + radius[i] for i in placed)
        y0 = cities[placed[0]]["y"]
        for i in missing:
            right += gap + radius[i]
            put(i, right, y0)
            right += radius[i]
    return added, []


# ------------------------------------------------------------ connect cities


def _pair(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a <= b else (b, a)


def connect_cities(own: dict, resolved: dict, params: dict) -> tuple[list[str], list[str]]:
    """Add roads so that no city is cut off (a minimum spanning tree of the straight-line distances,
    counting roads and road events the scenario already has), plus optionally each city's nearest
    neighbours."""
    nearest = _num(params, "nearest", 0, lo=0, hi=5, integer=True)
    cities = _own_list(own, resolved, "cities")
    if len(cities) < 2:
        raise HelperError("at least two cities are needed to connect them")
    if not all(_has_xy(c) for c in cities):
        raise HelperError("give every city a position first (Arrange cities)")
    names = [c["name"] for c in cities]
    pos = {c["name"]: (float(c["x"]), float(c["y"])) for c in cities}
    roads = _own_list(own, resolved, "roads")
    have = {_pair(r["a"], r["b"]) for r in roads}
    have |= {_pair(ev["a"], ev["b"]) for ev in (resolved.get("events") or []) if ev.get("type") in ("add_road", "set_road")}

    parent = {n: n for n in names}

    def find(n):
        while parent[n] != n:
            parent[n] = parent[parent[n]]
            n = parent[n]
        return n

    for a, b in have:
        if a in parent and b in parent:
            parent[find(a)] = find(b)

    def dist(a, b):
        return math.hypot(pos[a][0] - pos[b][0], pos[a][1] - pos[b][1])

    edges = sorted(((dist(a, b), a, b) for i, a in enumerate(names) for b in names[i + 1:]),
                   key=lambda e: (e[0], e[1], e[2]))
    added: list[str] = []

    def add(a, b, why):
        if _pair(a, b) in have:
            return
        have.add(_pair(a, b))
        roads.append({"a": a, "b": b})
        added.append(f"road {a} to {b}, {dist(a, b):.1f} km ({why})")

    for _, a, b in edges:
        if find(a) != find(b):
            parent[find(a)] = find(b)
            add(a, b, "joins a cut-off city")
    for n in names if nearest else []:
        for other in sorted((m for m in names if m != n), key=lambda m: (dist(n, m), m))[:nearest]:
            add(n, other, "nearest neighbour")
    return added, ([] if added else ["Every city is already connected."])


# -------------------------------------------------------------------- fill in


def _check_room(c: dict, key: str, extra: int, cap: int, label: str) -> None:
    have = len(c.get(key) or [])
    if have + extra > cap:
        raise HelperError(f"{c.get('name')} would have {have + extra} {label}; at most {cap} are allowed")


def fill_resources(own: dict, resolved: dict, params: dict) -> tuple[list[str], list[str]]:
    """One node of every good a city lacks, with default stats and an automatic position."""
    goods = list(resolved.get("goods") or [])
    cities = _own_list(own, resolved, "cities")
    added: list[str] = []
    for c in cities:
        have = {n.get("good") for n in c.get("nodes") or []}
        for g in goods:
            if g not in have:
                _check_room(c, "nodes", 1, MAX_NODES, "resource nodes")
                c.setdefault("nodes", []).append({"good": g, **DEFAULT_NODE})
                added.append(f"{c.get('name')}: a {g} node")
    return added, ([] if added else ["Every city already has a node for every good."])


def fill_workplaces(own: dict, resolved: dict, params: dict) -> tuple[list[str], list[str]]:
    count = _num(params, "count", 1, lo=1, hi=MAX_WORKPLACES, integer=True)
    task = str(params.get("task") or "guard")
    wage = _num(params, "wage", 2.0, lo=0.0)
    cities = _own_list(own, resolved, "cities")
    added: list[str] = []
    for c in _targets(cities, params.get("city")):
        _check_room(c, "workplaces", count, MAX_WORKPLACES, "workplaces")
        c.setdefault("workplaces", []).extend({"task": task, "wage": wage} for _ in range(count))
        added.append(f"{c.get('name')}: {count} {task} workplace{'s' if count != 1 else ''} at {wage} gold per hour")
    return added, []


def _houses_as_list(c: dict) -> list:
    """The city's houses as a list of entries, turning ``{count: N}`` into N automatic houses."""
    h = c.get("houses")
    if h is None:
        c["houses"] = []
    elif isinstance(h, dict):
        c["houses"] = [{} for _ in range(int(h.get("count", 0)))]
    return c["houses"]


def fill_houses(own: dict, resolved: dict, params: dict) -> tuple[list[str], list[str]]:
    count = _num(params, "count", 1, lo=1, hi=MAX_HOUSES, integer=True)
    cities = _own_list(own, resolved, "cities")
    added: list[str] = []
    for c in _targets(cities, params.get("city")):
        h = c.get("houses")
        have = int(h.get("count", 0)) if isinstance(h, dict) else len(h or [])
        if have + count > MAX_HOUSES:
            raise HelperError(f"{c.get('name')} would have {have + count} houses; at most {MAX_HOUSES} are allowed")
        if isinstance(h, list):
            h.extend({} for _ in range(count))
        else:
            c["houses"] = {"count": have + count}
        added.append(f"{c.get('name')}: {count} house{'s' if count != 1 else ''} (placed automatically)")
    return added, []


# ------------------------------------------------------------------- scatter


def scatter_points(seed_key: str, cx: float, cy: float, radius: float, count: int,
                   city_radius: float) -> list[tuple[float, float]]:
    """``count`` reproducible points inside a circle (offsets from the city centre), kept inside the
    city and spread out: a point is retried a few times when it lands too close to an earlier one."""
    rng = random.Random(f"scatter:{seed_key}")
    spacing = 0.6 * radius / math.sqrt(max(count, 1))
    pts: list[tuple[float, float]] = []
    for _ in range(count):
        best = None
        for _try in range(40):
            r = radius * math.sqrt(rng.random())
            a = rng.random() * 2 * math.pi
            x, y = cx + r * math.cos(a), cy + r * math.sin(a)
            if math.hypot(x, y) > city_radius:
                continue
            best = (x, y)
            if all(math.hypot(x - px, y - py) >= spacing for px, py in pts):
                break
        if best is None:
            raise HelperError("the area does not overlap the city")
        pts.append(best)
    return [(_round(x), _round(y)) for x, y in pts]


def scatter(own: dict, resolved: dict, params: dict) -> tuple[list[str], list[str]]:
    """Fill a circle inside a city with resource nodes, workplaces or houses (REQ-1C.8). The area is a
    way of authoring: the scenario stores the individual items."""
    kind = params.get("kind")
    if kind not in ("resource", "workplace", "house"):
        raise HelperError("kind must be resource, workplace or house")
    cities = _own_list(own, resolved, "cities")
    city = cities[_city_index(cities, params.get("city"))]
    if not _has_xy(city):
        raise HelperError("give the cities positions first (Arrange cities)")
    count = _num(params, "count", 5, lo=1, hi={"resource": MAX_NODES, "workplace": MAX_WORKPLACES, "house": MAX_HOUSES}[kind],
                 integer=True)
    radius = _num(params, "radius", lo=0.01)
    center = params.get("center")
    if not (isinstance(center, (list, tuple)) and len(center) == 2 and all(isinstance(v, (int, float)) for v in center)):
        raise HelperError("center must be [dx, dy], an offset from the city center")
    richness = _num(params, "richness", 1.0, lo=0.1, hi=10.0)
    city_r = float(city.get("radius", _default_radius(resolved)))
    key = f"{city['name']}:{kind}:{params.get('what')}:{center[0]:.3f}:{center[1]:.3f}:{radius:.3f}:{count}"
    pts = scatter_points(key, float(center[0]), float(center[1]), radius, count, city_r)
    label = {"resource": "nodes", "workplace": "workplaces", "house": "houses"}[kind]
    if kind == "resource":
        good = params.get("what")
        if good not in (resolved.get("goods") or []):
            raise HelperError(f"what must be one of {resolved.get('goods')}")
        _check_room(city, "nodes", count, MAX_NODES, "resource nodes")
        stats = {k: (v * richness if k in ("initial", "max_amount", "regen_per_hour") else v) for k, v in DEFAULT_NODE.items()}
        stats["initial"], stats["max_amount"] = round(stats["initial"]), round(stats["max_amount"])
        stats["regen_per_hour"] = round(stats["regen_per_hour"], 2)
        city.setdefault("nodes", []).extend({"good": good, **stats, "at": [x, y]} for x, y in pts)
    elif kind == "workplace":
        _check_room(city, "workplaces", count, MAX_WORKPLACES, "workplaces")
        task, wage = str(params.get("what") or "guard"), _num(params, "wage", 2.0, lo=0.0)
        city.setdefault("workplaces", []).extend({"task": task, "wage": wage, "at": [x, y]} for x, y in pts)
    else:
        houses = _houses_as_list(city)
        if len(houses) + count > MAX_HOUSES:
            raise HelperError(f"{city['name']} would have {len(houses) + count} houses; at most {MAX_HOUSES} are allowed")
        houses.extend({"at": [x, y]} for x, y in pts)
    return [f"{city['name']}: {count} {label} scattered in a circle of {radius:g} km"], []


# ----------------------------------------------------------- remove positions


def remove_positions(own: dict, resolved: dict, params: dict) -> tuple[list[str], list[str]]:
    """Go back to a scenario without coordinates. Roads keep the hours and stamina they have now, written
    out explicitly, so travel between cities does not change."""
    from popsim.world import Simulation
    from popsim.world.builder import derive_road

    cities = _own_list(own, resolved, "cities")
    if not any(_has_xy(c) for c in cities):
        return [], ["The scenario has no positions."]
    sim = Simulation(copy.deepcopy(resolved), npcs=False)

    def explicit(r: dict) -> None:
        a, b = sim.city_ids.get(r.get("a")), sim.city_ids.get(r.get("b"))
        if a is None or b is None:
            return
        _, hours, stamina = derive_road(sim, a, b, r.get("hours"), r.get("stamina_cost"), r.get("length"))
        r["hours"], r["stamina_cost"] = round(hours, 2), round(stamina, 2)
        r.pop("length", None)

    for r in _own_list(own, resolved, "roads"):
        explicit(r)
    if resolved.get("events"):
        for ev in _own_list(own, resolved, "events"):
            if ev.get("type") in ("add_road", "set_road"):
                explicit(ev)
    removed = 0
    for c in cities:
        for k in ("x", "y", "radius"):
            if c.pop(k, None) is not None:
                removed += 1
        for group in ("nodes", "workplaces"):
            for item in c.get(group) or []:
                if item.pop("at", None) is not None:
                    removed += 1
        if isinstance(c.get("houses"), list):
            for item in c["houses"]:
                if item and item.pop("at", None) is not None:
                    removed += 1
    return [f"removed {removed} position values; roads keep their current hours and stamina"], [
        "The scenario is no longer spatial: people no longer walk, so results differ from the spatial version."]


# --------------------------------------------------------------------- entry


def run_helper(data: dict, action: str, params: dict, scen_dir: Path) -> dict:
    """Apply a helper to a scenario's own mapping. Returns ``{data, added, notes}``; ``data`` is a new
    mapping, the input is never changed."""
    if action not in HELPERS:
        raise HelperError(f"unknown helper {action!r}; known: {list(HELPERS)}")
    if not isinstance(params, dict):
        raise HelperError("params must be an object")
    try:
        resolved = resolve(data, scen_dir)
    except Exception as e:  # noqa: BLE001
        raise HelperError(f"cannot resolve scenario: {e}") from e
    own = copy.deepcopy(data)
    added, notes = globals()[action](own, resolved, params)
    return {"data": own, "added": added, "notes": notes}
