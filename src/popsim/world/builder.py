"""Builds the initial world from config: cities, resource nodes, roads, NPCs.

All gold is minted once here by the central authority and all starting goods
come from nature, so the ledger starts from zero and every unit is traceable.
"""

from __future__ import annotations

import math
import random

from popsim.components import (
    Account, Action, Area, City, Demographics, Health, Home, House, Location, Metabolism, MerchantState,
    NPC, Needs, Orders, Position, PriceMemory, RegenerationRate, Road, Role, Skills, Stamina, Traits,
    Workplace, Yield, new_record,
)
from popsim.ledger import GOLD
from popsim.world.rand import clamp, gauss_spec, lognormal_skill

NEED_NAMES = ("food", "rest", "shelter", "wealth")


def derive_road(sim, a: int, b: int, hours=None, stamina_cost=None, length=None) -> tuple:
    """Road parameters (REQ-3A.2): ``length`` defaults to the straight-line distance between the
    two city centres, ``hours`` and ``stamina_cost`` to what that length costs at the configured
    road speed and stamina rate. Explicit values always win."""
    from popsim.systems.movement import cfg, distance, pos

    if sim.spatial:
        straight = distance(pos(sim, a), pos(sim, b))
        if length is None:
            length = straight
        elif length < straight - 1e-9:
            raise ValueError(f"road length {length} is below the straight-line distance {straight:.3f}")
        m = cfg(sim)["road"]
        hours = length / m["speed"] if hours is None else hours
        stamina_cost = length * m["stamina_per_unit"] if stamina_cost is None else stamina_cost
    elif hours is None or stamina_cost is None:
        raise ValueError("a road needs hours and stamina_cost when cities have no coordinates")
    return (None if length is None else float(length)), float(hours), float(stamina_cost)


def add_road(sim, a: int, b: int, hours=None, stamina_cost=None, length=None) -> int:
    length, hours, stamina_cost = derive_road(sim, a, b, hours, stamina_cost, length)
    found = sim.find_road(a, b)
    if found:
        rid, road = found
        road.hours, road.stamina_cost, road.length = hours, stamina_cost, length
        return rid
    return sim.world.create(Road(a=a, b=b, hours=hours, stamina_cost=stamina_cost, length=length))


def _disk_point(prng: random.Random, cx: float, cy: float, radius: float) -> tuple[float, float]:
    """A uniformly random point in a disc, from the positions RNG (never the simulation RNG)."""
    r = radius * math.sqrt(prng.random())
    a = prng.random() * 2 * math.pi
    return cx + r * math.cos(a), cy + r * math.sin(a)


def make_npc(sim, city: int, idx: int, gold_cents: int, role: str, house: int | None = None,
             at: tuple[float, float] = (0.0, 0.0)) -> int:
    cfg, rng = sim.cfg, sim.rng
    st = cfg["physiology"]["stamina"]
    tr = cfg["traits"]
    nd = cfg["needs"]
    goods = sim.goods

    traits = Traits(
        risk=gauss_spec(rng, tr["risk"]),
        urgency_tolerance=gauss_spec(rng, tr["urgency_tolerance"]),
        thrift=gauss_spec(rng, tr["thrift"]),
        luxury=gauss_spec(rng, tr["luxury"]),
        forecast_noise=gauss_spec(rng, tr["forecast_noise"]),
        reactivity=gauss_spec(rng, tr["reactivity"]),
        need_weights={n: gauss_spec(rng, tr["need_weight"]) for n in NEED_NAMES},
        job_preference={g: gauss_spec(rng, tr["job_preference"]) for g in goods},
        stock_days=gauss_spec(rng, tr["stock_days"]),
    )
    comfort, urgent, critical = {}, {}, {}
    sd = nd["threshold_sd"]
    for n in NEED_NAMES:
        d = nd["defaults"][n]
        # Thrifty NPCs keep higher comfort levels for wealth; others vary by noise.
        shift = (traits.thrift - 1.0) * 0.3 if n == "wealth" else 0.0
        c = clamp(rng.gauss(d["comfort"] + shift, sd), 0.05, 1.5)
        u = clamp(rng.gauss(d["urgent"], sd), 0.02, c - 0.02)
        k = clamp(rng.gauss(d["critical"], sd / 2), 0.0, u - 0.01)
        comfort[n], urgent[n], critical[n] = c, u, k

    satiety = clamp(rng.uniform(0.6, 0.9), 0, 1)
    shelter = clamp(rng.uniform(0.6, 0.9), 0, 1)
    age_y = gauss_spec(rng, cfg["npc"]["age_years"])
    life_y = max(age_y + 1, gauss_spec(rng, cfg["npc"]["life_expectancy_years"]))
    hl = cfg["physiology"]["health"]
    immune = gauss_spec(rng, hl["immune"])

    eid = sim.world.create(
        NPC(name=f"{sim.city_name(city)}-{idx:03d}"),
        Account(kind="npc"),
        Location(city=city),
        Home(city=city, house=house),
        Position(x=at[0], y=at[1]),
        Role(kind=role),
        Stamina(current=st["absolute_max"] * rng.uniform(0.7, 1.0), daily_max=st["absolute_max"],
                absolute_max=st["absolute_max"]),
        Metabolism(satiety=satiety, base_burn=cfg["physiology"]["metabolism"]["base_burn_per_hour"]),
        Health(immune_strength=immune, base_immune=immune),
        Demographics(age_days=age_y * 365, gender=rng.choice(["f", "m"]),
                     life_expectancy_days=life_y * 365),
        Skills(levels={g: lognormal_skill(rng, cfg["skills"]) for g in goods}),
        traits,
        Needs(levels={"food": satiety, "rest": 1.0, "shelter": shelter, "wealth": 1.0},
              comfort=comfort, urgent=urgent, critical=critical,
              zero_hours={n: 0.0 for n in NEED_NAMES}),
        Action(),
        Orders(bid_premium={g: 0.0 for g in goods}, ask_discount={g: 0.0 for g in goods}),
        PriceMemory(),
    )
    if role == "merchant":
        sim.world.add(eid, MerchantState(visited=[city]))
    icfg = cfg.get("inspector") or {}
    if icfg.get("record", True):
        sim.world.add(eid, new_record(icfg.get("buffer", 500)))
    sim.ledger.transfer("authority", eid, GOLD, gold_cents, reason="genesis")
    for good, qty in cfg["npc"]["inventory"].items():
        sim.ledger.transfer("nature", eid, good, int(qty), reason="genesis")
    # Seed the NPC's price memory for its home city with the configured priors.
    mem = sim.world.get(eid, PriceMemory)
    for good in goods:
        mem.ema[(city, good)] = [cfg["pricing"]["initial_prices"][good] * sim.unit, -1]
    return eid


def _offset(spec) -> tuple[float, float] | None:
    """``at: [dx, dy]`` of a scenario entry, or None."""
    at = spec.get("at") if isinstance(spec, dict) else None
    return None if at is None else (float(at[0]), float(at[1]))


def _house_specs(c: dict) -> list[dict]:
    """A city's ``houses``: a list of ``{at: [dx, dy]}`` entries (``at`` optional) or ``{count: N}``."""
    h = c.get("houses") or []
    if isinstance(h, dict):
        return [{} for _ in range(int(h.get("count", 0)))]
    return [x or {} for x in h]


def build_world(sim, npcs: bool = True) -> None:
    """Create the world in two steps (REQ-3.8): first the places (cities, resource nodes, roads, houses,
    workplaces), then, as a separate step on the finished world, its citizens (``populate_world``).
    ``npcs=False`` stops after the places: the scenario editor uses that for its preview, with exactly the
    positions the full world gets."""
    cfg, rng = sim.cfg, sim.rng
    unit = sim.unit
    mv = cfg.get("movement") or {}
    # Spatial scenarios give every city coordinates (REQ-3A.9). Otherwise every entity sits at
    # (0, 0) and no distance ever matters, so results are those of the version before positions.
    sim.spatial = bool(cfg["cities"]) and all(c.get("x") is not None and c.get("y") is not None
                                              for c in cfg["cities"])
    # Generated positions come from their own stream so the simulation RNG is untouched.
    prng = sim.pos_rng = random.Random(f"popsim-positions:{cfg['seed']}")

    centres: dict[int, tuple[float, float, float]] = {}
    for c in cfg["cities"]:
        cx, cy = (float(c["x"]), float(c["y"])) if sim.spatial else (0.0, 0.0)
        radius = float(c.get("radius", mv.get("default_city_radius", 2.0))) if sim.spatial else 0.0
        cid = sim.world.create(City(name=c["name"]), Account(kind="city"), Position(cx, cy), Area(radius))
        sim.city_ids[c["name"]] = cid
        centres[cid] = (cx, cy, radius)
        for n in c.get("nodes", []):
            off = _offset(n)
            if not sim.spatial:
                nx, ny = 0.0, 0.0
            elif off is not None:
                nx, ny = cx + off[0], cy + off[1]
            else:
                nx, ny = _disk_point(prng, cx, cy, radius)
            nid = sim.world.create(
                Yield(good=n["good"], max_amount=int(n["max_amount"]), base_rate=float(n["base_rate"]),
                      stamina_cost=float(n["stamina_cost"])),
                RegenerationRate(per_hour=float(n["regen_per_hour"])),
                Location(city=cid),
                Position(nx, ny),
                Account(kind="node"),
            )
            sim.ledger.transfer("nature", nid, n["good"], int(n.get("initial", 0)), reason="genesis")
            sim.nodes_by_city.setdefault(cid, []).append(nid)

    for r in cfg.get("roads", []):
        add_road(sim, sim.city_ids[r["a"]], sim.city_ids[r["b"]], r.get("hours"), r.get("stamina_cost"),
                 r.get("length"))

    def place(spec, cid):
        cx, cy, radius = centres[cid]
        if not sim.spatial:
            return 0.0, 0.0
        off = _offset(spec)
        return (cx + off[0], cy + off[1]) if off is not None else _disk_point(prng, cx, cy, radius)

    for c in cfg["cities"]:
        cid = sim.city_ids[c["name"]]
        for spec in _house_specs(c):
            x, y = place(spec, cid)
            sim.world.create(House(city=cid), Position(x, y))
        for spec in c.get("workplaces") or []:
            x, y = place(spec, cid)
            pj = (cfg.get("fiscal") or {}).get("public_jobs") or {}
            wid = sim.world.create(
                Workplace(city=cid, task=str(spec.get("task", "work")),
                          wage=int(round(float(spec.get("wage", 0)) * unit)),
                          slots=int(spec.get("slots", pj.get("slots", 3))),
                          stamina_per_hour=float(spec.get("stamina_per_hour", pj.get("stamina_per_hour", 3.5)))),
                Position(x, y))
            sim.workplaces_by_city.setdefault(cid, []).append(wid)

    if npcs:
        populate_world(sim)


def _place(sim, spec: dict, cid: int) -> tuple[float, float]:
    """A position inside city ``cid``: the spec's own ``at`` offset, or a reproducible random one."""
    if not sim.spatial:
        return 0.0, 0.0
    c, area = sim.world.get(cid, Position), sim.world.get(cid, Area)
    off = _offset(spec)
    if off is not None:
        return c.x + off[0], c.y + off[1]
    return _disk_point(sim.pos_rng, c.x, c.y, area.radius)


def populate_world(sim, sample: int | None = None) -> None:
    """Step two of creating the world (REQ-3.8): put the citizens into the finished places. Each one gets
    a home city, a house if one is free (houses are limited, REQ-3.7) and a starting position, all chosen
    from what the world already holds. The money for them (and the treasuries) is minted once, here.

    ``sample`` creates only that many citizens per city (at least one merchant among them) and no
    treasury money: enough to run the citizen-making code, which is how the editor's checks catch
    mistakes in the settings without building thousands of people."""
    cfg, rng, unit = sim.cfg, sim.rng, sim.unit
    if sim.world.count(NPC):
        raise ValueError("the world already has citizens")
    houses: dict[int, list[int]] = {}
    for eid, (hs,) in sim.world.query(House):
        houses.setdefault(hs.city, []).append(eid)

    npc_total = 0
    pops = {}
    for c in cfg["cities"]:
        cid = sim.city_ids[c["name"]]
        pop = int(c["population"])
        pops[cid] = pop
        count = pop if sample is None else min(pop, sample)
        n_merchants = int(round(count * cfg["npc"]["merchant_share"]))
        if sample is not None and count >= 2:
            n_merchants = max(1, n_merchants)
        for i in range(count):
            role = "merchant" if i < n_merchants else "worker"
            g = gauss_spec(rng, cfg["npc"]["gold"])
            if role == "merchant":
                g *= cfg["npc"]["merchant_gold_multiplier"]
            cents = int(round(g * unit))
            npc_total += cents
            # Houses are limited (REQ-3.7): the first NPCs of a city get one, the rest sleep outdoors.
            house = houses.get(cid, [])[i] if i < len(houses.get(cid, [])) else None
            if house is not None:
                hp = sim.world.get(house, Position)
                at = (hp.x, hp.y)
            else:
                at = _place(sim, {}, cid)
            eid = make_npc(sim, cid, i, cents, role, house=house, at=at)
            if house is not None:
                hs = sim.world.get(house, House)
                hs.owner = hs.occupant = eid

    sim.npc_gold_start = npc_total
    if sample is not None:
        return
    share = float(cfg["money"]["treasury_share"])
    treasury_total = int(round(npc_total * share / (1 - share))) if share < 1 else 0
    total_pop = sum(pops.values()) or 1
    remaining = treasury_total
    city_list = sorted(pops)
    for i, cid in enumerate(city_list):
        amt = remaining if i == len(city_list) - 1 else int(treasury_total * pops[cid] / total_pop)
        remaining -= amt
        sim.ledger.transfer("authority", cid, GOLD, amt, reason="genesis")
