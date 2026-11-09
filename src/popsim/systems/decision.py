"""Decision (AI) system (REQ-4.2, REQ-4.1.x).

Every tick each NPC in a city either continues its committed action or picks a
new one. Committed actions (sleep, a work shift, travel) run to completion unless
an urgent need interrupts them. Buying, selling and eating are instant side
actions handled by the market and consumption systems.

Conflict resolution (REQ-4.1.10): when a need is past its critical level the
survival > health > wealth order is strict (interrupts, starving NPCs gather
food); below critical, needs are weighed by per-NPC trait weights.
"""

from __future__ import annotations

from popsim.components import Action, Location, MerchantState, Needs, Orders, Role, Skills, Stamina, Traits, Yield
from popsim.components import Home, Workplace
from popsim.records import set_reason
from popsim.systems.agent import GOOD_NEED, job_options, reservation_wage, stock_target, work_stamina_per_tick
from popsim.systems.fiscal import workers_at
from popsim.systems.merchant import merchant_decide
from popsim.systems.movement import cfg as movement_cfg, start_walk, walk_distance, walk_stamina


class DecisionSystem:
    name = "decision"

    def run(self, sim) -> None:
        # Who works where right now: public jobs have a limited number of slots (REQ-5.5).
        sim._wc = workers_at(sim) if sim.workplaces_by_city else None
        try:
            for eid, (act, needs, st, tr, loc) in sim.world.query(Action, Needs, Stamina, Traits, Location):
                if loc.city is None:  # travelling: committed
                    continue
                if act.ticks_left > 0 and not self.interrupt(sim, eid, act, needs, st):
                    continue
                self.choose(sim, eid, act, needs, st, tr, loc.city)
        finally:
            sim._wc = None

    @staticmethod
    def interrupt(sim, eid, act, needs, st) -> bool:
        u = needs.urgency
        no_food = sim.inv(eid, "food") == 0
        if act.kind == "sleep":
            return u["food"] >= 1.0 and no_food  # wake up hungry to go buy food
        if act.kind == "walk":
            # An urgent need breaks off a walk (REQ-3A.5); it stays where it is.
            if st.current < act.walk_stamina:
                return True
            if act.then is not None and act.then[0] == "work":
                return sim.world.get(act.then[1], Workplace) is None or u["rest"] >= 1.0 \
                    or (u["food"] >= 1.0 and no_food)
            if act.then is not None and act.then[0] == "gather":
                y = sim.world.get(act.then[1], Yield)
                if y is None or (u["rest"] >= 1.0):
                    return True
                return u["food"] >= 1.0 and no_food and y.good != "food"
            return u["food"] >= 1.0 and no_food
        if act.kind == "work":
            wp = sim.world.get(act.target, Workplace)
            return wp is None or st.current < wp.stamina_per_hour * sim.h or u["rest"] >= 1.0 \
                or (u["food"] >= 1.0 and no_food)
        if act.kind == "gather":
            y = sim.world.get(act.target, Yield)
            if y is None:
                return True
            if st.current < work_stamina_per_tick(sim, eid, y) or u["rest"] >= 1.0:
                return True
            if u["food"] >= 1.0 and no_food and y.good != "food":
                return True
        return False

    @staticmethod
    def home_worth_walking(sim, eid, st):
        """The NPC's house if it is away from it and walking home pays: the extra stamina the home
        bonus restores over the planned sleep must exceed the stamina the walk costs (REQ-3A.8)."""
        home = sim.world.get(eid, Home)
        if home is None or home.house is None or not sim.world.alive(home.house):
            return None
        dist = walk_distance(sim, eid, home.house)
        if dist <= 0:
            return None
        mult = movement_cfg(sim)["home_rest_multiplier"]
        base = sim.cfg["physiology"]["stamina"]["sleep_restore_per_hour"]
        hours = min(sim.cfg["decision"]["max_sleep_hours"], max(0.0, st.daily_max - st.current) / (base * mult))
        cost = walk_stamina(sim, dist)
        return home.house if (mult - 1.0) * base * hours > cost and st.current >= cost else None

    @staticmethod
    def choose(sim, eid, act, needs, st, tr, city) -> None:
        d = sim.cfg["decision"]
        u = needs.urgency
        w = tr.need_weights
        night = sim.is_night()
        rested = st.current >= st.daily_max - 1.0

        s_sleep = 0.0 if rested else w["rest"] * u["rest"]
        if night and not rested:
            s_sleep += d["night_sleep_bonus"] * w["rest"]

        # Starving with no food: stay awake at the market to buy while bids are
        # still getting filled; once they keep failing, fall through to gathering.
        if u["food"] >= 1.0 and sim.inv(eid, "food") == 0 and sim.gold(eid) > 0:
            if sim.world.get(eid, Orders).bid_premium.get("food", 0.0) < d["starving_buy_patience"]:
                act.kind, act.ticks_left = "trade", 0
                set_reason(sim, eid, "trade", note="starving with no food: staying at the market to buy")
                return

        role = sim.world.get(eid, Role)
        survival_critical = u["food"] >= 1.0 or u["rest"] >= 1.0
        if role and role.kind == "merchant" and not survival_critical and s_sleep < 0.8:
            if merchant_decide(sim, eid, city):
                ms = sim.world.get(eid, MerchantState)
                set_reason(sim, eid, "merchant", note=f"merchant plan: {ms.mode} {ms.good or ''}".strip())
                return

        opts = job_options(sim, eid, city)
        s_work = 0.0
        if opts:
            # Work motive fades as savings approach the NPC's own target, so a
            # population that feels rich supplies less labour (and vice versa).
            gap = max(0.0, min(1.0, 1.0 - needs.levels["wealth"] / needs.comfort["wealth"]))
            s_work = d["work_drive"] * gap + w["wealth"] * u["wealth"]
            s_work *= d["night_work_factor"] if night else 1.0
            # Working for money is only attractive if the best job pays close to
            # the NPC's reservation wage.
            wage_ratio = opts[0][3] / max(1e-9, reservation_wage(sim, eid, city))
            s_work *= min(1.0, wage_ratio)
            if u["food"] >= 1.0 and sim.inv(eid, "food") == 0 and opts[0][2] == "food":
                s_work += d["survival_food_job_bonus"]
            # Self-provision: short on a need good and the market isn't delivering
            # (bid premium grows on unfilled bids) -> go gather it yourself.
            orders = sim.world.get(eid, Orders)
            best_self = (0.0, None)
            for good, need in GOOD_NEED.items():
                if sim.inv(eid, good) >= stock_target(sim, eid, good):
                    continue
                sc = d["self_supply_weight"] * w[need] * u[need] * (1 + 2 * orders.bid_premium.get(good, 0.0))
                if sc > best_self[0]:
                    best_self = (sc, good)
            if best_self[1] is not None and best_self[0] > s_work:
                node = next((o for o in opts if o[2] == best_self[1]), None)
                if node is not None:
                    opts = [node] + [o for o in opts if o is not node]
                    s_work = best_self[0]
            # Strict priority at the critical threshold (REQ-4.1.10): starving with
            # no food in hand beats every other motive.
            if u["food"] >= 1.0 and sim.inv(eid, "food") == 0:
                node = next((o for o in opts if o[2] == "food"), None)
                if node is not None:
                    opts = [node] + [o for o in opts if o is not node]
                    s_work = max(s_work, d["survival_food_job_bonus"] * w["food"] * u["food"])
        s_idle = d["idle_score"]

        best = max((s_sleep, 2, "sleep"), (s_work, 1, "work"), (s_idle, 0, "idle"))
        choice = best[2]
        set_reason(sim, eid, choice, {"sleep": round(s_sleep, 3), "work": round(s_work, 3),
                                      "idle": round(s_idle, 3)},
                   note="highest score wins" + ("; night" if night else ""))
        if choice == "sleep":
            ticks = sim.hours_to_ticks(d["max_sleep_hours"])
            house = DecisionSystem.home_worth_walking(sim, eid, st)
            if house is not None:
                start_walk(sim, eid, act, house, ("sleep", None, ticks))
            else:
                act.kind = "sleep"
                act.ticks_left = ticks
        elif choice == "work":
            nid = opts[0][1]
            ticks = sim.hours_to_ticks(d["shift_hours"])
            public = sim.world.get(nid, Workplace) is not None
            kind = "work" if public else "gather"
            if public and sim._wc is not None:
                sim._wc[nid] = sim._wc.get(nid, 0) + 1  # the slot is taken from now on
            if walk_distance(sim, eid, nid) > 0:  # the plan is two steps: walk there, then work (REQ-3A.6)
                start_walk(sim, eid, act, nid, (kind, nid, ticks))
                return
            if public:
                act.kind, act.target, act.ticks_left, act.carry = "work", nid, ticks, 0.0
                return
            if act.target != nid:  # resume partial progress only at the same node
                act.carry = 0.0
            act.kind, act.target = "gather", nid
            act.ticks_left = ticks
        else:
            act.kind, act.ticks_left = "idle", 0
