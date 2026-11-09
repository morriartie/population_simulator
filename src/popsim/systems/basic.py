"""Events, regeneration, physiology, needs, consumption and aging systems."""

from __future__ import annotations

from popsim.components import (
    Action, Demographics, Health, Home, Location, Metabolism, NPC, Needs, RegenerationRate,
    Road, Stamina, Traits, Yield,
)
from popsim.ledger import GOLD
from popsim.systems.agent import urgency, wealth_target
from popsim.systems.movement import at_home, cfg as movement_cfg
from popsim.world.rand import clamp


# ------------------------------------------------------------------ events


def event_tick(sim, ev: dict) -> int:
    if "tick" in ev:
        return int(ev["tick"])
    return int(round(float(ev["day"]) * sim.ticks_per_day + float(ev.get("hour", 0)) / sim.h))


def apply_event(sim, ev: dict) -> dict:
    from popsim.world.builder import add_road

    kind = ev["type"]
    rec = {"tick": sim.tick, "day": sim.day(), "type": kind}
    if kind == "stimulus":
        names = sorted(sim.city_ids) if ev.get("city", "all") == "all" else [ev["city"]]
        per_npc = int(round(float(ev["per_npc"]) * sim.unit))
        paid = 0
        for name in names:
            cid = sim.city_ids[name]
            residents = [e for e in sim.npcs_in(cid)]
            if not residents:
                continue
            each = min(per_npc, sim.gold(cid) // len(residents))  # treasury is finite
            for e in residents:
                sim.ledger.transfer(cid, e, GOLD, each, reason="stimulus")
                paid += each
        rec.update(cities=names, total_gold=paid / sim.unit)
    elif kind == "mint":
        # The central authority is the only account that can create money.
        cid = sim.city_ids[ev["city"]]
        amt = int(round(float(ev["amount"]) * sim.unit))
        sim.ledger.transfer("authority", cid, GOLD, amt, reason="mint")
        rec.update(city=ev["city"], amount=amt / sim.unit)
    elif kind in ("add_road", "set_road"):
        rid = add_road(sim, sim.city_ids[ev["a"]], sim.city_ids[ev["b"]], ev.get("hours"),
                       ev.get("stamina_cost"), ev.get("length"))
        road = sim.world.get(rid, Road)
        # Explicit values are logged as given; omitted ones are the derived values (REQ-3A.2).
        rec.update(a=ev["a"], b=ev["b"], hours=ev.get("hours", road.hours),
                   stamina_cost=ev.get("stamina_cost", road.stamina_cost))
        if road.length is not None:
            rec["length"] = road.length
    elif kind == "remove_road":
        found = sim.find_road(sim.city_ids[ev["a"]], sim.city_ids[ev["b"]])
        if found:
            sim.world.destroy(found[0])
        rec.update(a=ev["a"], b=ev["b"])
    else:
        raise ValueError(f"unknown event type {kind!r}")
    return rec


class EventSystem:
    name = "events"

    def run(self, sim) -> None:
        for ev in sim.cfg.get("events", []) or []:
            if event_tick(sim, ev) == sim.tick:
                sim.event_log.append(apply_event(sim, ev))


# ------------------------------------------------------------ regeneration


class RegenerationSystem:
    name = "regeneration"

    def run(self, sim) -> None:
        for nid, (y, regen) in sim.world.query(Yield, RegenerationRate):
            regen.carry += regen.per_hour * sim.h
            add = int(regen.carry)
            regen.carry -= add
            room = y.max_amount - sim.ledger.balance(nid, y.good)
            add = max(0, min(add, room))
            if add:
                sim.ledger.transfer("nature", nid, y.good, add, reason="regen")
                sim.metrics.count("regen", y.good, add)


# -------------------------------------------------------------- physiology


def daily_stamina_update(st: Stamina, scfg: dict) -> None:
    """Spring elasticity (REQ-2.1.1): missing sleep lowers daily_max quickly; it
    recovers slowly, faster with each consecutive day of good rest."""
    deficit = max(0.0, scfg["good_sleep_hours"] - st.sleep_hours_today)
    if deficit > 0:
        st.daily_max -= scfg["deprivation_penalty_per_hour"] * deficit
        st.good_rest_streak = 0
    else:
        st.good_rest_streak += 1
        st.daily_max += scfg["recovery_per_streak_day"] * st.good_rest_streak
    st.daily_max = clamp(st.daily_max, scfg["min_daily_max"], st.absolute_max)
    st.current = min(st.current, st.daily_max)
    st.sleep_hours_today = 0.0


def sickness_probability(st: Stamina, hl: Health, shelter_critical: bool, hcfg: dict, h: float) -> float:
    fatigue = 1.0 - st.daily_max / st.absolute_max
    p = hcfg["base_sickness_per_hour"] * (1 + hcfg["fatigue_sickness_factor"] * fatigue ** 2)
    if shelter_critical:
        p *= 1 + hcfg["shelter_sickness_factor"]
    p *= 1.5 - hl.immune_strength
    return min(1.0, p * h)


class PhysiologySystem:
    name = "physiology"

    def run(self, sim) -> None:
        cfg = sim.cfg
        scfg = cfg["physiology"]["stamina"]
        mcfg = cfg["physiology"]["metabolism"]
        hcfg = cfg["physiology"]["health"]
        shcfg = cfg["shelter"]
        h = sim.h
        noon = sim.at_hour(12)
        rng = sim.rng
        mults = {"sleep": mcfg["sleep_multiplier"], "gather": mcfg["work_multiplier"],
                 "travel": mcfg["travel_multiplier"], "walk": mcfg["travel_multiplier"],
                 "work": mcfg["work_multiplier"]}
        home_mult = movement_cfg(sim)["home_rest_multiplier"]
        dead = []
        for eid, (st, met, hl, needs, act) in sim.world.query(Stamina, Metabolism, Health, Needs, Action):
            sleeping = act.kind == "sleep"
            mult = mults.get(act.kind, 1.0) * (1.1 if hl.sick else 1.0)
            met.burn_multiplier = mult
            met.satiety = max(0.0, met.satiety - met.base_burn * mult * h)
            needs.levels["food"] = met.satiety
            needs.levels["shelter"] = max(0.0, needs.levels["shelter"] - shcfg["decay_per_hour"] * h)
            shelter_crit = needs.levels["shelter"] <= needs.critical["shelter"]

            if sleeping:
                restore = scfg["sleep_restore_per_hour"] * h
                if at_home(sim, eid):  # resting in one's own house recovers faster (REQ-3A.8)
                    restore *= home_mult
                if shelter_crit:
                    restore *= shcfg["sleep_penalty_when_critical"]
                if hl.sick:
                    restore *= 0.8
                st.current = min(st.daily_max, st.current + restore)
                st.sleep_hours_today += h
            else:
                drain = scfg["awake_drain_per_hour"] * h * (hcfg["sick_drain_multiplier"] if hl.sick else 1.0)
                st.current = max(0.0, st.current - drain)

            if noon:
                daily_stamina_update(st, scfg)
                # Immunity falls when daily_max is low and recovers toward baseline otherwise.
                if st.daily_max / st.absolute_max < 0.7:
                    hl.immune_strength = max(0.05, hl.immune_strength - hcfg["immune_drift_per_day"])
                else:
                    hl.immune_strength = min(hl.base_immune, hl.immune_strength + hcfg["immune_drift_per_day"] / 2)

            hl.sickness_probability = sickness_probability(st, hl, shelter_crit, hcfg, h)
            if not hl.sick:
                if rng.random() < hl.sickness_probability:
                    hl.sick = True
                    sim.metrics.count("sick_onset", "npc", 1)
            else:
                rec = hcfg["recovery_per_hour"] * h * (hcfg["recovery_sleep_bonus"] if sleeping else 1.0)
                rec *= 0.5 + hl.immune_strength
                if rng.random() < rec:
                    hl.sick = False
                elif rng.random() < hcfg["sick_death_per_hour"] * h:
                    dead.append(eid)
        for eid in dead:
            sim.kill(eid, "sickness")


# ------------------------------------------------------------------- needs


class NeedsSystem:
    name = "needs"

    def run(self, sim) -> None:
        nd = sim.cfg["needs"]
        k = nd["urgency_exponent"]
        death_hours = nd["death_hours_at_zero"]
        dead = []
        for eid, (needs, st, hl, loc, home) in sim.world.query(Needs, Stamina, Health, Location, Home):
            needs.levels["rest"] = st.current / st.absolute_max * (0.85 if hl.sick else 1.0)
            city = loc.city if loc.city is not None else home.city
            target = max(1.0, wealth_target(sim, eid, city))
            needs.levels["wealth"] = sim.gold(eid) / target
            for n, level in needs.levels.items():
                needs.urgency[n] = urgency(level, needs.comfort[n], needs.critical[n], k)
            for n, limit in death_hours.items():
                if needs.levels.get(n, 1.0) <= 0.0:
                    needs.zero_hours[n] = needs.zero_hours.get(n, 0.0) + sim.h
                    if needs.zero_hours[n] >= limit:
                        dead.append((eid, "starvation" if n == "food" else f"{n}_failure"))
                        break
                else:
                    needs.zero_hours[n] = 0.0
        for eid, cause in dead:
            sim.kill(eid, cause)


# ------------------------------------------------------------- consumption


class ConsumptionSystem:
    """Eating food and using wood are instant actions taken whenever the level is
    below comfort and the NPC holds the good (REQ-4.1.4 eat/consume)."""

    name = "consumption"

    def run(self, sim) -> None:
        per_food = sim.cfg["physiology"]["metabolism"]["satiety_per_food"]
        per_wood = sim.cfg["shelter"]["per_wood"]
        for eid, (needs, met) in sim.world.query(Needs, Metabolism):
            for good, need, per in (("food", "food", per_food), ("wood", "shelter", per_wood)):
                level = needs.levels[need]
                if level >= needs.comfort[need]:
                    continue
                n = 2 if level <= needs.critical[need] else 1
                for _ in range(n):
                    if sim.inv(eid, good) < 1 or needs.levels[need] + per > 1.1:
                        break
                    sim.ledger.transfer(eid, "nature", good, 1, reason="consume")
                    sim.metrics.count("consume", good, 1)
                    needs.levels[need] = min(1.0, needs.levels[need] + per)
                if need == "food":
                    met.satiety = needs.levels["food"]


# ------------------------------------------------------------------- aging


class AgingSystem:
    """Ages NPCs once a day and removes those past their life expectancy.
    Births are deferred past the MVP."""

    name = "aging"

    def run(self, sim) -> None:
        if not sim.at_hour(0) or sim.tick == 0:
            return
        dead = []
        for eid, (demo,) in sim.world.query(Demographics):
            demo.age_days += 1
            if demo.age_days >= demo.life_expectancy_days:
                dead.append(eid)
        for eid in dead:
            sim.kill(eid, "old_age")
