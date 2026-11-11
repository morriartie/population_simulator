"""Simulation context: owns the ECS world, ledger, RNG, market client and clock,
and runs the fixed system pipeline once per tick (REQ-1.5, REQ-1A.2)."""

from __future__ import annotations

import math
import random
from typing import Any

from popsim.components import (
    Action, City, Home, Location, MerchantState, NPC, Orders, PriceMemory, Road, Travel,
)
from popsim.ecs import World
from popsim.ledger import GOLD, Ledger, escrow_account
from popsim.market.client import LocalMarketClient, MarketClient


class Simulation:
    def __init__(self, cfg: dict, market: MarketClient | None = None, build: bool = True,
                 npcs: bool = True) -> None:
        from popsim.io.metrics import Metrics
        from popsim.systems import build_pipeline

        self.cfg = cfg
        self.world_name: str = cfg.get("world_name", "default")
        self.h: float = float(cfg["time"]["hours_per_tick"])
        if 24 % self.h:
            raise ValueError("hours_per_tick must divide 24")
        self.ticks_per_day: int = int(round(24 / self.h))
        self.unit: int = int(cfg["money"]["unit"])
        self.goods: list[str] = list(cfg["goods"])
        self.rng = random.Random(cfg["seed"])
        self.world = World()
        self.ledger = Ledger(keep_log=cfg.get("output", {}).get("keep_ledger_log", True))
        self.tick = 0
        self.metrics = Metrics()
        self.market: MarketClient = market or LocalMarketClient()
        self.city_ids: dict[str, int] = {}
        self.nodes_by_city: dict[int, list[int]] = {}
        self.overworld_nodes: list[int] = []  # resource nodes outside every city (REQ-3A.11)
        self.workplaces_by_city: dict[int, list[int]] = {}  # public jobs (REQ-5.5)
        self.board: dict[int, dict] = {}  # city -> good -> {"bid","ask"} (top of book)
        self.event_log: list[dict] = []
        self.spatial = False  # set by the builder: True when cities have coordinates (REQ-3A.9)
        self.pos_rng = None  # the stream for generated positions, kept for the citizens (REQ-3.8)
        self.graveyard: dict[int, dict] = {}  # dead citizens kept for the inspector (REQ-1C.6)
        self.player: int | None = None  # the user's trading account, opened on demand (REQ-1C.9)
        self.npc_gold_start: int | None = None  # gold the citizens were given (REQ-3.8)
        self.pipeline = build_pipeline(cfg["pipeline"])
        if build:
            from popsim.world.builder import build_world

            self.market.reset_world(self.world_name)
            build_world(self, npcs=npcs)

    def populate(self, sample: int | None = None) -> None:
        """Step two of creating the world: put the citizens into the places (REQ-3.8)."""
        from popsim.world.builder import populate_world

        populate_world(self, sample)

    # -- clock --------------------------------------------------------------
    def hour(self, tick: int | None = None) -> float:
        t = self.tick if tick is None else tick
        return (t * self.h) % 24

    def day(self, tick: int | None = None) -> int:
        t = self.tick if tick is None else tick
        return int(t * self.h // 24)

    def at_hour(self, hour: float) -> bool:
        """True on the tick at which the clock passes ``hour``."""
        return abs(self.hour() - hour) < 1e-9

    def is_night(self) -> bool:
        d = self.cfg["decision"]
        hr = self.hour()
        return hr >= d["night_start"] or hr < d["night_end"]

    def hours_to_ticks(self, hours: float) -> int:
        return max(1, int(math.ceil(hours / self.h - 1e-9)))

    # -- lookups ------------------------------------------------------------
    def city_name(self, cid: int) -> str:
        return self.world.get(cid, City).name

    def gold(self, eid) -> int:
        return self.ledger.balance(eid, GOLD)

    def inv(self, eid, good: str) -> int:
        return self.ledger.balance(eid, good)

    def roads_from(self, city: int) -> list[tuple[int, int, Road]]:
        out = []
        for rid, (road,) in self.world.query(Road):
            if road.a == city:
                out.append((road.b, rid, road))
            elif road.b == city:
                out.append((road.a, rid, road))
        return out

    def find_road(self, a: int, b: int) -> tuple[int, Road] | None:
        for rid, (road,) in self.world.query(Road):
            if {road.a, road.b} == {a, b}:
                return rid, road
        return None

    def nodes_for(self, city: int) -> list[int]:
        """The resource nodes a citizen of ``city`` can work: the ones inside the city (scenarios before
        REQ-3A.11) and the overworld nodes on a road that touches it, while that road exists."""
        from popsim.components import Branch

        out = list(self.nodes_by_city.get(city, []))
        for nid in self.overworld_nodes:
            br = self.world.get(nid, Branch)
            if br is not None and city in (br.a, br.b) and self.find_road(br.a, br.b) is not None:
                out.append(nid)
        return out

    def nodes_of(self, city: int) -> list[int]:
        """Nodes whose stock is booked to ``city`` in the daily table: the ones inside it, and the
        overworld nodes whose main road starts at it (each overworld node is counted once)."""
        from popsim.components import Branch

        return list(self.nodes_by_city.get(city, [])) + [
            n for n in self.overworld_nodes if getattr(self.world.get(n, Branch), "a", None) == city]

    def npcs_in(self, city: int) -> list[int]:
        return [eid for eid, (_, loc) in self.world.query(NPC, Location) if loc.city == city]

    # -- market helpers -----------------------------------------------------
    def cancel_all_orders(self, eid: int, refund_to=None) -> None:
        """Cancel every open order of ``eid`` immediately and refund the escrow."""
        from popsim.systems.market import settle_cancel

        orders = self.world.get(eid, Orders)
        if not orders or not orders.open:
            return
        by_city: dict[int, list] = {}
        for o in orders.open:
            by_city.setdefault(o.city, []).append(o)
        for city, olist in sorted(by_city.items()):
            ops = [{"op": "cancel", "order_id": o.order_id, "owner": eid, "ref": i}
                   for i, o in enumerate(olist)]
            resp = self.market.submit_batch(self.world_name, self.city_name(city), self.tick, ops)
            for res in resp["results"]:
                if res["ok"]:
                    settle_cancel(self, eid, city, res, refund_to=refund_to, adapt=False)
        orders.open.clear()

    # -- life ---------------------------------------------------------------
    def kill(self, eid: int, cause: str) -> None:
        if not self.world.alive(eid):
            return
        from popsim.records import archive

        archive(self, eid, cause)
        home = self.world.get(eid, Home)
        loc = self.world.get(eid, Location)
        heir = (loc.city if loc and loc.city is not None else home.city) if home else None
        self.cancel_all_orders(eid)
        if heir is not None:
            self.ledger.transfer_all(eid, heir, reason="estate")
        self.metrics.record_death(self, eid, cause, heir)
        self.world.destroy(eid)

    def start_travel(self, eid: int, dest: int) -> bool:
        loc = self.world.get(eid, Location)
        if loc is None or loc.city is None:
            return False
        found = self.find_road(loc.city, dest)
        if found is None:
            return False
        _, road = found
        self.cancel_all_orders(eid)
        ticks = self.hours_to_ticks(road.hours)
        self.world.add(eid, Travel(origin=loc.city, dest=dest, ticks_left=ticks,
                                   stamina_per_tick=road.stamina_cost / ticks, total_ticks=ticks))
        loc.city = None
        act = self.world.get(eid, Action)
        act.kind, act.target, act.ticks_left, act.carry = "travel", dest, ticks, 0.0
        act.walk_from = act.walk_to = act.then = None
        act.walk_total, act.walk_stamina = 0, 0.0
        ms = self.world.get(eid, MerchantState)
        if ms is not None and dest not in ms.visited:
            ms.visited.append(dest)
        return True

    # -- running ------------------------------------------------------------
    def step(self) -> None:
        self.ledger.tick = self.tick
        for system in self.pipeline:
            system.run(self)
        from popsim.records import after_step

        after_step(self)
        if self.player is not None:
            from popsim.player import after_step as player_after_step

            player_after_step(self)
        self.tick += 1

    def run(self, days: float | None = None, ticks: int | None = None, progress=None) -> None:
        if ticks is None:
            days = self.cfg["time"]["days"] if days is None else days
            ticks = int(round(days * self.ticks_per_day))
        for i in range(ticks):
            self.step()
            if progress and (self.tick % self.ticks_per_day == 0):
                progress(self)

    def escrow_matches_market(self) -> dict[str, Any]:
        """Escrow held in the ledger must equal what the market says is locked."""
        out = {}
        for name, cid in sorted(self.city_ids.items()):
            locked = self.market.locked(self.world_name, name)
            acct = escrow_account(cid)
            held = {a: v for a, v in self.ledger.holdings(acct).items() if v}
            want = {a: v for a, v in locked.items() if v}
            out[name] = {"ledger": held, "market": want, "ok": held == want}
        return out

    # -- snapshots ----------------------------------------------------------
    def __getstate__(self):
        state = self.__dict__.copy()
        state["market"] = None
        state["pipeline"] = None
        return state

    def __setstate__(self, state):
        from popsim.systems import build_pipeline

        self.__dict__.update(state)
        self.__dict__.setdefault("spatial", False)
        self.__dict__.setdefault("pos_rng", None)
        self.__dict__.setdefault("workplaces_by_city", {})
        self.__dict__.setdefault("overworld_nodes", [])
        self.__dict__.setdefault("graveyard", {})  # snapshots written before the inspector
        self.__dict__.setdefault("player", None)
        self.__dict__.setdefault("npc_gold_start", None)
        self.pipeline = build_pipeline(self.cfg["pipeline"])
