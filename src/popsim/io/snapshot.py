"""Paired save/resume (REQ-1A.6, REQ-1A.10).

A world snapshot is a pickle of the Simulation (ECS world, ledger, RNG state,
metrics). The matching market state is paired with it:

* in-process market: the market state is embedded in the same pickle;
* REST market: the API saves its state for the world to SQLite under the same
  snapshot name, and loading restores it.

Snapshot name convention: ``<world>@t<tick>`` unless a name is given.
"""

from __future__ import annotations

import pickle
from pathlib import Path

FORMAT = 1


def default_name(sim) -> str:
    return f"{sim.world_name}@t{sim.tick}"


def save_snapshot(sim, path: str | Path, name: str | None = None) -> dict:
    name = name or default_name(sim)
    payload = {"format": FORMAT, "name": name, "world": sim.world_name, "tick": sim.tick,
               "sim": pickle.dumps(sim), "market_state": None}
    if getattr(sim.market, "embedded", True):
        payload["market_state"] = sim.market.export_world(sim.world_name)
    else:
        sim.market.save_snapshot(sim.world_name, name, sim.tick)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        pickle.dump(payload, fh)
    return {"path": str(path), "name": name, "tick": sim.tick}


def load_snapshot(path: str | Path, market=None):
    from popsim.market.client import LocalMarketClient

    with Path(path).open("rb") as fh:
        payload = pickle.load(fh)
    if payload.get("format") != FORMAT:
        raise ValueError(f"unsupported snapshot format {payload.get('format')}")
    sim = pickle.loads(payload["sim"])
    sim.market = market or LocalMarketClient()
    if payload["market_state"] is not None:
        sim.market.import_world(sim.world_name, payload["market_state"])
    else:
        sim.market.restore_snapshot(sim.world_name, payload["name"])
    return sim


def read_world(path: str | Path):
    """Unpickle just the Simulation from a snapshot, without a market (read-only
    inspection, REQ-1C.5). The returned simulation must not be stepped."""
    with Path(path).open("rb") as fh:
        payload = pickle.load(fh)
    if payload.get("format") != FORMAT:
        raise ValueError(f"unsupported snapshot format {payload.get('format')}")
    return pickle.loads(payload["sim"])
