"""Golden hashes (REQ-5A.4): every shipped scenario, run for a few days, must give byte-identical outputs
after refactors that are not meant to change behavior."""

import hashlib
import json
import tempfile
from pathlib import Path

from popsim.config import load_config
from popsim.io.logs import write_outputs
from popsim.world import Simulation

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "golden" / "hashes.json"
FILES = ("cities_daily.csv", "prices_daily.csv", "trade_flows.csv", "deaths.csv", "conservation_daily.csv",
         "events.csv", "transit.csv", "roads.csv")
SKIP: set[str] = set()


def scenario_names() -> list[str]:
    return sorted(p.stem for p in (ROOT / "scenarios").glob("*.yaml") if p.stem not in SKIP)


def run_hash(name: str, days: int = 8) -> str:
    cfg = load_config(ROOT / "scenarios" / f"{name}.yaml", {"time": {"days": days}})
    sim = Simulation(cfg)
    sim.run()
    h = hashlib.sha256()
    with tempfile.TemporaryDirectory() as d:
        write_outputs(sim, d)
        for f in FILES:
            h.update((Path(d) / f).read_bytes())
    h.update(json.dumps(sorted(sim.ledger.balances.items(), key=lambda kv: str(kv[0])), default=str).encode())
    return h.hexdigest()


if __name__ == "__main__":
    GOLDEN.write_text(json.dumps({n: run_hash(n) for n in scenario_names()}, indent=1, sort_keys=True) + "\n")
    print(GOLDEN.read_text())
