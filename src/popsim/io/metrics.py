"""In-run metrics collection. Rows are plain dicts so they serialise to CSV/JSON."""

from __future__ import annotations

import statistics
from collections import defaultdict

from popsim.components import Health, Location, Metabolism, NPC, Role, Stamina, Travel
from popsim.ledger import GOLD


def gini(values: list[float]) -> float:
    vals = sorted(v for v in values if v >= 0)
    n = len(vals)
    total = sum(vals)
    if n == 0 or total == 0:
        return 0.0
    cum = sum((i + 1) * v for i, v in enumerate(vals))
    return (2 * cum) / (n * total) - (n + 1) / n


class Metrics:
    def __init__(self) -> None:
        self.tick_prices: list[dict] = []
        self.daily_prices: list[dict] = []
        self.daily: list[dict] = []
        self.flows: list[dict] = []
        self.deaths: list[dict] = []
        self.checks: list[dict] = []
        self.transit: list[dict] = []  # daily NPCs on the road, per route
        self.gather: dict[tuple[str, float], list[float]] = {}
        self.counters: dict[tuple[str, str], int] = defaultdict(int)
        self._day_trades: dict[tuple[int, str], list[int]] = {}
        self._last_price: dict[tuple[int, str], int] = {}
        self._day_counters_prev: dict[tuple[str, str], int] = {}
        self._prev_supply: dict[str, int] | None = None
        self._walk: dict[str, list[float]] = {}  # city -> [hours, stamina] walked today
        self._treasury: dict[str, dict[str, int]] = {}  # city -> {tax, wages, support} cents today

    def __setstate__(self, state):  # snapshots written before newer fields existed
        self.__dict__.update(state)
        self.__dict__.setdefault("transit", [])
        self.__dict__.setdefault("_walk", {})
        self.__dict__.setdefault("_treasury", {})

    # -- hooks --------------------------------------------------------------
    def count(self, kind: str, key: str, n: int) -> None:
        self.counters[(kind, key)] += n

    def record_market_tick(self, sim, city: int, volume: dict, prices: dict) -> None:
        name = sim.city_name(city)
        for good in sim.goods:
            v = volume.get(good, {"qty": 0, "value": 0})
            acc = self._day_trades.setdefault((city, good), [0, 0])
            acc[0] += v["qty"]
            acc[1] += v["value"]
            if good in prices:
                self._last_price[(city, good)] = prices[good]
            self.tick_prices.append({
                "tick": sim.tick, "day": sim.day(), "city": name, "good": good,
                "qty": v["qty"],
                "vwap": round(v["value"] / v["qty"] / sim.unit, 4) if v["qty"] else None,
                "last": round(prices[good] / sim.unit, 4) if good in prices else None,
            })

    def record_flow(self, sim, origin: int, dest: int, good: str, qty: int) -> None:
        self.flows.append({"tick": sim.tick, "day": sim.day(), "from": sim.city_name(origin),
                           "to": sim.city_name(dest), "good": good, "qty": qty})

    def record_death(self, sim, eid: int, cause: str, city: int | None) -> None:
        self.deaths.append({"tick": sim.tick, "day": sim.day(), "eid": eid, "cause": cause,
                            "city": sim.city_name(city) if city is not None else None})

    def record_treasury(self, city: str, kind: str, cents: int) -> None:
        acc = self._treasury.setdefault(city, {})
        acc[kind] = acc.get(kind, 0) + cents

    def record_walk(self, city: str, hours: float, stamina: float) -> None:
        acc = self._walk.setdefault(city, [0.0, 0.0])
        acc[0] += hours
        acc[1] += stamina

    def record_gather(self, good: str, units: int, stamina: float, skill: float) -> None:
        bucket = round(skill * 4) / 4  # 0.25-wide skill bins
        acc = self.gather.setdefault((good, bucket), [0.0, 0.0])
        acc[0] += units
        acc[1] += stamina

    # -- daily aggregation --------------------------------------------------
    def end_of_day(self, sim) -> None:
        day = sim.day()
        unit = sim.unit
        for name, cid in sorted(sim.city_ids.items()):
            for good in sim.goods:
                qty, value = self._day_trades.pop((cid, good), [0, 0])
                last = self._last_price.get((cid, good))
                self.daily_prices.append({
                    "day": day, "city": name, "good": good, "volume": qty,
                    "vwap": round(value / qty / unit, 4) if qty else None,
                    "last": round(last / unit, 4) if last is not None else None,
                })
            residents = [e for e, (_, loc) in sim.world.query(NPC, Location) if loc.city == cid]
            golds = [sim.gold(e) / unit for e in residents]
            sat = [sim.world.get(e, Metabolism).satiety for e in residents]
            dmax = [sim.world.get(e, Stamina).daily_max for e in residents]
            sick = sum(1 for e in residents if sim.world.get(e, Health).sick)
            merchants = sum(1 for e in residents if sim.world.get(e, Role).kind == "merchant")
            self.daily.append({
                "day": day, "city": name, "population": len(residents), "merchants": merchants,
                "mean_gold": round(statistics.fmean(golds), 3) if golds else 0.0,
                "median_gold": round(statistics.median(golds), 3) if golds else 0.0,
                "gini": round(gini(golds), 4),
                "treasury": sim.gold(cid) / unit,
                "mean_satiety": round(statistics.fmean(sat), 4) if sat else 0.0,
                "mean_daily_max": round(statistics.fmean(dmax), 3) if dmax else 0.0,
                "sick": sick,
                "walk_hours": round(self._walk.get(name, [0.0, 0.0])[0], 3),
                "walk_stamina": round(self._walk.get(name, [0.0, 0.0])[1], 3),
                "tax_collected": self._treasury.get(name, {}).get("tax", 0) / unit,
                "wages_paid": self._treasury.get(name, {}).get("wages", 0) / unit,
                "support_paid": self._treasury.get(name, {}).get("support", 0) / unit,
                "deaths_today": sum(1 for d in self.deaths if d["day"] == day and d["city"] == name),
                **{f"stock_{g}": sum(sim.inv(e, g) for e in residents) for g in sim.goods},
                **{f"node_{g}": sum(sim.ledger.balance(n, g) for n in sim.nodes_of(cid))
                   for g in sim.goods},
            })
        self._walk = {}
        self._treasury = {}
        travelling = sum(1 for _, (loc,) in sim.world.query(Location) if loc.city is None)
        routes: dict[tuple[str, str], list[int]] = {}
        for eid, (tr,) in sim.world.query(Travel):
            key = (sim.city_name(tr.origin), sim.city_name(tr.dest))
            acc = routes.setdefault(key, [0, 0])
            acc[0] += 1
            acc[1] += sum(sim.inv(eid, g) for g in sim.goods)
        for (a, b), (n, cargo) in sorted(routes.items()):
            self.transit.append({"day": day, "from": a, "to": b, "npcs": n, "cargo": cargo})

        # Conservation (REQ-5.1): every asset sums to zero over all accounts,
        # the gold supply only changes through the authority, and goods supply
        # changes exactly by regeneration minus consumption.
        supply = {a: sim.ledger.supply(a) for a in [GOLD] + sim.goods}
        goods_ok = True
        if self._prev_supply is not None:
            for g in sim.goods:
                d_regen = self.counters[("regen", g)] - self._day_counters_prev.get(("regen", g), 0)
                d_cons = self.counters[("consume", g)] - self._day_counters_prev.get(("consume", g), 0)
                d_inj = self.counters[("inject", g)] - self._day_counters_prev.get(("inject", g), 0)
                if supply[g] - self._prev_supply[g] != d_regen - d_cons + d_inj:
                    goods_ok = False
        self._prev_supply = supply
        self._day_counters_prev = dict(self.counters)
        self.checks.append({
            "day": day, "gold_supply": supply[GOLD] / unit,
            "authority_issued": -sim.ledger.balance("authority", GOLD) / unit,
            "totals_zero": not sim.ledger.check_totals(),
            "goods_balance_ok": goods_ok,
            "travelling": travelling,
            **{f"supply_{g}": supply[g] for g in sim.goods},
        })
