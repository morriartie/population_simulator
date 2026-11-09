"""Live runs (REQ-1C.4): one Simulation stepped in a background thread, with
play/pause/step/speed controls and a frame feed for server-sent events.

Stepping in slices does not change results: the simulation is the same object a
``popsim run`` would drive, so a saved live run equals the CLI run (REQ-1.8).
"""

from __future__ import annotations

import queue
import threading
import time
from pathlib import Path

from popsim.components import NPC, Location, Travel

SERIES = ("prices_daily", "cities_daily", "flows", "deaths", "events", "checks", "transit")
SLICE_SECONDS = 0.1


class Conflict(RuntimeError):
    pass


def _series(sim) -> dict[str, list]:
    m = sim.metrics
    return {"prices_daily": m.daily_prices, "cities_daily": m.daily, "flows": m.flows,
            "deaths": m.deaths, "events": sim.event_log, "checks": m.checks, "transit": m.transit}


class Session:
    def __init__(self, scenario: str, cfg: dict, market, name: str, sim=None) -> None:
        from popsim.world import Simulation

        self.scenario = scenario
        self.name = name
        self.cfg = cfg
        self.sim = sim if sim is not None else Simulation(cfg, market=market)  # sim: resumed from a snapshot
        self.total_ticks = max(int(round(cfg["time"]["days"] * self.sim.ticks_per_day)), self.sim.tick)
        self.status = "finished" if self.sim.tick >= self.total_ticks else "paused"  # paused | playing | finished | error | closed
        self.speed = 2.0  # simulated days per second
        self.error: str | None = None
        self.saved_as: str | None = None
        self.lock = threading.RLock()
        self._wake = threading.Event()
        self._cursor = {k: 0 for k in SERIES}
        self._subs: list[queue.Queue] = []
        self._thread = threading.Thread(target=self._loop, name="popsim-session", daemon=True)
        self._thread.start()

    # -- state ----------------------------------------------------------------
    def now(self) -> dict:
        sim = self.sim
        cities = {}
        for name, cid in sorted(sim.city_ids.items()):
            prices = {}
            for g in sim.goods:
                p = sim.metrics._last_price.get((cid, g))
                prices[g] = None if p is None else round(p / sim.unit, 4)
            cities[name] = {"population": 0, "prices": prices, "treasury": sim.gold(cid) / sim.unit}
        for _, (_, loc) in sim.world.query(NPC, Location):
            if loc.city is not None:
                cities[sim.city_name(loc.city)]["population"] += 1
        routes: dict[tuple[str, str], int] = {}
        for _, (tr,) in sim.world.query(Travel):
            k = (sim.city_name(tr.origin), sim.city_name(tr.dest))
            routes[k] = routes.get(k, 0) + 1
        return {"tick": sim.tick, "day": sim.day(), "hour": sim.hour(), "cities": cities,
                "transit": [{"from": a, "to": b, "npcs": n} for (a, b), n in sorted(routes.items())]}

    def state(self) -> dict:
        with self.lock:
            return {"scenario": self.scenario, "name": self.name, "status": self.status, "speed": self.speed,
                    "tick": self.sim.tick, "total_ticks": self.total_ticks, "error": self.error,
                    "day": self.sim.day(), "hour": self.sim.hour(),
                    "total_days": self.total_ticks / self.sim.ticks_per_day,
                    "saved_as": self.saved_as, "world": self.sim.world_name,
                    "market": "api" if not getattr(self.sim.market, "embedded", True) else "in-process"}

    def snapshot(self) -> dict:
        """Everything so far, in the same shape as a saved run (``data.load_run``)."""
        with self.lock:
            data = {k: list(v) for k, v in _series(self.sim).items()}
            from popsim.io.logs import world_layout

            return {"name": self.name, "summary": None, "config": self.cfg, "world": world_layout(self.sim), **data,
                    "state": self.state(), "now": self.now()}

    # -- feed -------------------------------------------------------------------
    def subscribe(self) -> tuple[queue.Queue, dict]:
        with self.lock:
            q: queue.Queue = queue.Queue(maxsize=256)
            self._subs.append(q)
            return q, self.snapshot()

    def unsubscribe(self, q: queue.Queue) -> None:
        with self.lock:
            if q in self._subs:
                self._subs.remove(q)

    def _publish(self, kind: str = "frame") -> None:
        series = _series(self.sim)
        delta = {}
        for k, rows in series.items():
            start = self._cursor[k]
            if len(rows) > start:
                delta[k] = rows[start:]
            self._cursor[k] = len(rows)
        msg = {"type": kind, "state": self.state(), "now": self.now(), "delta": delta}
        for q in list(self._subs):
            try:
                q.put_nowait(msg)
            except queue.Full:  # slow client: drop it, it will reconnect and get a fresh snapshot
                self._subs.remove(q)

    # -- control ----------------------------------------------------------------
    def _step(self, n: int) -> None:
        for _ in range(n):
            if self.sim.tick >= self.total_ticks:
                break
            self.sim.step()
        if self.sim.tick >= self.total_ticks and self.status != "error":
            self.status = "finished"

    def control(self, action: str, value=None) -> dict:
        with self.lock:
            if self.status in ("error", "closed"):
                raise Conflict(f"session is {self.status}")
            if action == "play":
                if self.status != "finished":
                    self.status = "playing"
                    self._wake.set()
            elif action == "pause":
                if self.status == "playing":
                    self.status = "paused"
            elif action in ("step_hour", "step_day"):
                if self.status == "playing":
                    self.status = "paused"
                n = 1 if action == "step_hour" else self.sim.ticks_per_day
                try:
                    self._step(n)
                except Exception as e:  # noqa: BLE001
                    self._fail(e)
            elif action == "speed":
                v = float(value)
                if not 0.05 <= v <= 200:
                    raise ValueError("speed must be between 0.05 and 200 days per second")
                self.speed = v
            elif action == "extend":
                days = float(value)
                if days <= 0:
                    raise ValueError("extend needs a positive number of days")
                self.total_ticks += int(round(days * self.sim.ticks_per_day))
                if self.status == "finished":
                    self.status = "paused"
            else:
                raise ValueError(f"unknown action {action!r}")
            self._publish()
            return self.state()

    # -- player (REQ-1C.9) ---------------------------------------------------------
    def player_read(self, fn, *args, **kw):
        """Read the player's account or the markets while the run is paused or playing."""
        with self.lock:
            return fn(self.sim, *args, **kw)

    def player_act(self, fn, *args, orders: bool = False, **kw):
        """A user action on the account. It is applied between ticks (the session lock is held by
        whoever steps the run), logged with its tick, and published to the stream."""
        from popsim.player import PlayerError

        with self.lock:
            if self.status in ("error", "closed"):
                raise Conflict(f"session is {self.status}")
            if orders and self.status == "finished":
                raise PlayerError("finished", "the run is finished: add days to keep trading")
            out = fn(self.sim, *args, **kw)
            self._publish()
            return out

    def _fail(self, e: Exception) -> None:
        self.status = "error"
        self.error = f"{type(e).__name__}: {e}"

    def _loop(self) -> None:
        while True:
            self._wake.wait(timeout=0.5)
            with self.lock:
                if self.status == "closed":
                    return
                if self.status != "playing":
                    self._wake.clear()
                    continue
            t0 = time.monotonic()
            with self.lock:
                if self.status != "playing":
                    continue
                # At most one simulated day per slice, so pause/step stay responsive;
                # beyond that the speed is limited by how fast the CPU can step.
                ticks = max(1, min(self.sim.ticks_per_day,
                                   int(round(self.speed * self.sim.ticks_per_day * SLICE_SECONDS))))
                try:
                    self._step(ticks)
                except Exception as e:  # noqa: BLE001
                    self._fail(e)
                self._publish()
            time.sleep(max(0.0, SLICE_SECONDS - (time.monotonic() - t0)))

    def save(self, runs_dir: Path, name: str | None = None) -> str:
        from popsim.dashboard.data import check_name
        from popsim.io.logs import write_outputs
        from popsim.io.snapshot import save_snapshot

        with self.lock:
            if self.status == "playing":
                self.status = "paused"
            base = check_name(name or self.name)
            out_name, i = base, 2
            while (runs_dir / out_name).exists():
                out_name = f"{base}-{i}"
                i += 1
            out = runs_dir / out_name
            write_outputs(self.sim, out, scenario=self.scenario)
            save_snapshot(self.sim, out / "end.pkl")
            try:
                from popsim.io.plots import plot_run

                plot_run(out)
            except ImportError:
                pass
            self.saved_as = out_name
            self._publish()
            return out_name

    def close(self) -> None:
        with self.lock:
            self.status = "closed"
            self._wake.set()
            for q in list(self._subs):
                try:
                    q.put_nowait({"type": "closed"})
                except queue.Full:
                    pass
            self._subs.clear()


class SessionManager:
    """Holds at most one live session (REQ-1C.4, REQ-1A.6)."""

    def __init__(self, scenarios_dir: Path, runs_dir: Path, api_url: str | None = None) -> None:
        self.scenarios_dir = scenarios_dir
        self.runs_dir = runs_dir
        self.api_url = api_url
        self.session: Session | None = None
        self._lock = threading.Lock()

    def _market(self):
        from popsim.market.client import HttpMarketClient, LocalMarketClient

        return HttpMarketClient(self.api_url) if self.api_url else LocalMarketClient()

    def start(self, scenario: str | None, seed: int | None = None, days: float | None = None,
              name: str | None = None, replace: bool = False, resume: str | None = None) -> Session:
        from popsim.config import deep_merge
        from popsim.dashboard.data import BadRequest, NoSnapshot, check_name, find_snapshot, get_scenario

        with self._lock:
            if self.session is not None and self.session.status not in ("closed",):
                if not replace:
                    raise Conflict("a live run is already open; discard it first (one live run at a time)")
                self.session.close()
                self.session = None
            if resume:  # continue a saved run that has a snapshot (REQ-1A.10)
                import json

                from popsim.io.snapshot import load_snapshot

                path = find_snapshot(self.runs_dir, check_name(resume))
                if path is None:
                    raise NoSnapshot(f"run {resume!r} has no snapshot to resume from")
                sim = load_snapshot(path, self._market())
                try:
                    scen = json.loads((self.runs_dir / resume / "summary.json").read_text()).get("scenario")
                except (OSError, ValueError):
                    scen = None
                self.session = Session(scen or resume, sim.cfg, sim.market, check_name(name or f"{resume}-live"), sim=sim)
                return self.session
            cfg = get_scenario(self.scenarios_dir, scenario)["resolved"]
            over: dict = {}
            if seed is not None:
                over["seed"] = int(seed)
            if days is not None:
                if float(days) <= 0:
                    raise BadRequest("days must be positive")
                over["time"] = {"days": float(days)}
            cfg = deep_merge(cfg, over)
            run_name = check_name(name or f"{scenario}-live")
            self.session = Session(scenario, cfg, self._market(), run_name)
            return self.session

    def get(self) -> Session:
        from popsim.dashboard.data import NotFound

        if self.session is None or self.session.status == "closed":
            raise NotFound("no live run")
        return self.session

    def discard(self) -> None:
        with self._lock:
            if self.session is not None:
                self.session.close()
            self.session = None
