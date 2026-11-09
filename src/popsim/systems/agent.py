"""Shared NPC reasoning helpers: urgency curves, budgets, price expectations,
reservation prices and job scoring (REQ-4.1.3, 4.1.5, 4.1.7, 4.2.1)."""

from __future__ import annotations

import math

from popsim.components import (
    Health, Needs, Orders, PriceMemory, Skills, Stamina, Traits, Workplace, Yield,
)
from popsim.systems.fiscal import net_wage_per_hour, slots_of, workers_at
from popsim.systems.movement import walk_distance, walk_stamina
from popsim.world.rand import clamp

# Which need each good refills, and back.
GOOD_NEED = {"food": "food", "wood": "shelter"}
NEED_GOOD = {v: k for k, v in GOOD_NEED.items()}


def urgency(level: float, comfort: float, critical: float, k: float) -> float:
    """0 at or above comfort, 1 at the critical level, rising steeply beyond it."""
    if level >= comfort:
        return 0.0
    span = max(1e-6, comfort - critical)
    return ((comfort - level) / span) ** k


def stamina_per_unit(base_cost: float, skill: float) -> float:
    """Higher skill means less stamina spent per unit gathered (REQ-2.2.3)."""
    return base_cost / max(skill, 1e-6)


def work_stamina_per_tick(sim, eid: int, y: Yield) -> float:
    """Stamina one tick of work at a node costs. Output per tick scales with skill
    while stamina per unit scales with 1/skill, so higher skill = more units for
    the same effort."""
    hl = sim.world.get(eid, Health)
    eff = sim.cfg["physiology"]["health"]["sick_work_efficiency"] if hl and hl.sick else 1.0
    s = sim.world.get(eid, Skills).levels.get(y.good, 1.0)
    return y.base_rate * s * eff * sim.h * stamina_per_unit(y.stamina_cost, s)


def price_ref(sim, eid: int, city: int, good: str) -> float:
    """The NPC's expected price (cents) for ``good`` in ``city``: its own EMA memory,
    falling back to the configured prior."""
    mem = sim.world.get(eid, PriceMemory)
    entry = mem.ema.get((city, good)) if mem else None
    if entry is not None:
        return entry[0]
    return sim.cfg["pricing"]["initial_prices"][good] * sim.unit


def known_price(sim, eid: int, city: int, good: str) -> float | None:
    mem = sim.world.get(eid, PriceMemory)
    entry = mem.ema.get((city, good)) if mem else None
    return None if entry is None else entry[0]


def observe_price(sim, eid: int, city: int, good: str, price: float) -> None:
    mem = sim.world.get(eid, PriceMemory)
    tr = sim.world.get(eid, Traits)
    alpha = 1 - (1 - sim.cfg["pricing"]["ema_alpha_per_hour"]) ** sim.h
    alpha = clamp(alpha * (tr.reactivity if tr else 1.0), 0.0, 1.0)
    entry = mem.ema.get((city, good))
    if entry is None:
        mem.ema[(city, good)] = [float(price), sim.tick]
    else:
        entry[0] += alpha * (price - entry[0])
        entry[1] = sim.tick


def expected_daily_spend(sim, eid: int, city: int) -> float:
    units = sim.cfg["needs"]["daily_units"]
    return sum(units[g] * price_ref(sim, eid, city, g) for g in sim.goods if g in units)


def wealth_target(sim, eid: int, city: int) -> float:
    tr = sim.world.get(eid, Traits)
    return sim.cfg["needs"]["savings_days"] * tr.thrift * expected_daily_spend(sim, eid, city)


def stock_target(sim, eid: int, good: str) -> int:
    tr = sim.world.get(eid, Traits)
    per_day = sim.cfg["needs"]["daily_units"].get(good, 0.0)
    return int(math.ceil(per_day * tr.stock_days))


def need_urgency_for_good(sim, eid: int, good: str) -> float:
    needs = sim.world.get(eid, Needs)
    need = GOOD_NEED.get(good)
    return needs.urgency.get(need, 0.0) if need else 0.0


def bid_price(sim, eid: int, city: int, good: str, noise: float | None = None) -> int:
    """Reservation buy price from urgency, wealth, memory and recent fill history.
    ``noise`` replaces the random forecast error (the inspector passes 0.0 so that
    reading a price never draws from the simulation RNG)."""
    p = sim.cfg["pricing"]
    tr = sim.world.get(eid, Traits)
    needs = sim.world.get(eid, Needs)
    orders = sim.world.get(eid, Orders)
    ref = price_ref(sim, eid, city, good)
    if noise is None:
        noise = sim.rng.gauss(0.0, tr.forecast_noise)
    u = min(need_urgency_for_good(sim, eid, good), 3.0)
    wl = max(needs.levels.get("wealth", 1.0), 1e-3)
    lo, hi = p["wealth_bid_bounds"]
    wealth_factor = clamp(wl ** p["wealth_bid_elasticity"], lo, hi)
    mult = (1 + noise) * (1 + p["bid_urgency_markup"] * u * tr.urgency_tolerance) * wealth_factor
    mult *= 1 + orders.bid_premium.get(good, 0.0)
    price = ref * mult
    # Marginal-utility cap: never spend more than an urgency-scaled share of
    # current gold on a single unit.
    lo_s, hi_s = p["max_unit_budget_share"]
    share = clamp(lo_s + p["unit_budget_urgency_slope"] * u, lo_s, hi_s)
    price = min(price, sim.gold(eid) * share)
    return max(1, int(round(price)))


def ask_price(sim, eid: int, city: int, good: str, noise: float | None = None) -> int:
    """Reservation sell price: rich sellers mark up, desperate ones undercut."""
    p = sim.cfg["pricing"]
    tr = sim.world.get(eid, Traits)
    needs = sim.world.get(eid, Needs)
    orders = sim.world.get(eid, Orders)
    ref = price_ref(sim, eid, city, good)
    if noise is None:
        noise = sim.rng.gauss(0.0, tr.forecast_noise)
    wl = needs.levels.get("wealth", 1.0)
    u_w = min(needs.urgency.get("wealth", 0.0), 1.0)
    mult = (1 + noise) * (1 + p["ask_wealth_markup"] * clamp(wl - 1.0, 0.0, 2.0)
                          - p["ask_desperation"] * u_w * tr.risk)
    mult *= 1 - orders.ask_discount.get(good, 0.0)
    return max(1, int(round(ref * max(mult, 0.05))))


def job_options(sim, eid: int, city: int) -> list[tuple[float, int, str]]:
    """Score gathering jobs in ``city``: mostly expected income per stamina, plus
    smaller weights for preference and skill (REQ-4.1.7). Best first."""
    d = sim.cfg["decision"]
    w = d["job_weights"]
    skills = sim.world.get(eid, Skills).levels
    tr = sim.world.get(eid, Traits)
    st = sim.world.get(eid, Stamina)
    needs = sim.world.get(eid, Needs)
    hl = sim.world.get(eid, Health)
    eff = sim.cfg["physiology"]["health"]["sick_work_efficiency"] if hl and hl.sick else 1.0
    raw = []
    for nid in sim.nodes_by_city.get(city, []):
        y = sim.world.get(nid, Yield)
        amount = sim.ledger.balance(nid, y.good)
        avail = min(1.0, amount / 10.0)
        s = skills.get(y.good, 1.0)
        work = work_stamina_per_tick(sim, eid, y)
        walk = walk_stamina(sim, walk_distance(sim, eid, nid))  # stamina to walk there (REQ-3A.6)
        if avail <= 0 or st.current < work + walk:
            continue
        price = price_ref(sim, eid, city, y.good)
        income_per_stamina = price * s * eff / y.stamina_cost * avail
        if walk > 0:
            # Income per stamina over the whole task, the walk counted both ways (REQ-3A.7).
            shift = work * sim.hours_to_ticks(d["shift_hours"])
            income_per_stamina *= shift / (shift + 2 * walk)
        # Income per hour counts only the hours at the job, never the commute (REQ-3A.7).
        income_per_hour = price * y.base_rate * s * eff * avail
        raw.append((income_per_stamina, nid, y.good, s, income_per_hour))
    # Public jobs (REQ-5.5): a workplace pays a wage from the city's treasury, so it is offered only
    # while a slot is free and the treasury can fund a shift. The wage is after income tax, the walk
    # is counted both ways (REQ-3A.7) and, as for gathering, only hours at the job count as hours.
    wc = None
    for wid in sim.workplaces_by_city.get(city, []):
        wp = sim.world.get(wid, Workplace)
        if wp is None or wp.wage <= 0:
            continue
        if wc is None:
            wc = getattr(sim, "_wc", None)
            if wc is None:
                wc = workers_at(sim)
        if wc.get(wid, 0) >= slots_of(sim, wp) or sim.gold(city) < wp.wage * d["shift_hours"]:
            continue
        per_tick = wp.stamina_per_hour * sim.h
        walk = walk_stamina(sim, walk_distance(sim, eid, wid))
        if st.current < per_tick + walk:
            continue
        net = net_wage_per_hour(sim, wp)
        ips = net / max(wp.stamina_per_hour, 1e-9)
        if walk > 0:
            shift = per_tick * sim.hours_to_ticks(d["shift_hours"])
            ips *= shift / (shift + 2 * walk)
        raw.append((ips, wid, wp.task, skills.get(wp.task, 1.0), net))
    if not raw:
        return []
    max_inc = max(r[0] for r in raw) or 1.0
    max_s = max(r[3] for r in raw) or 1.0
    starving_broke = (
        needs.urgency.get("food", 0.0) >= 1.0
        and sim.inv(eid, "food") == 0
        and sim.gold(eid) < price_ref(sim, eid, city, "food")
    )
    scored = []
    for inc, nid, good, s, per_hour in raw:
        score = (w["income"] * inc / max_inc + w["preference"] * tr.job_preference.get(good, 0.0)
                 + w["skill"] * s / max_s)
        if starving_broke and good == "food":
            score += d["survival_food_job_bonus"]
        scored.append((score, nid, good, per_hour))
    scored.sort(key=lambda t: (-t[0], t[1]))
    return scored


def reservation_wage(sim, eid: int, city: int) -> float:
    """Hourly income (cents) at which a work shift is worth it: a day's expected
    spending earned over ``work_hours_per_day`` hours."""
    return expected_daily_spend(sim, eid, city) / sim.cfg["decision"]["work_hours_per_day"]
