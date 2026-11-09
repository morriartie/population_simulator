"""Command line: ``python -m popsim <command>``.

  run      run a scenario, write CSV/JSON (+ plots), optionally save a snapshot
  serve    start the REST market API
  dashboard  web dashboard: browse runs, edit scenarios, play live runs
  plot     re-plot a finished run directory
  compare  overlay prices from several run directories
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path


def _cmd_run(a) -> int:
    from popsim.config import load_config
    from popsim.io.logs import write_outputs
    from popsim.io.snapshot import load_snapshot, save_snapshot
    from popsim.market.client import HttpMarketClient, LocalMarketClient
    from popsim.world import Simulation

    market = HttpMarketClient(a.api) if a.api and not a.local else LocalMarketClient()
    if isinstance(market, HttpMarketClient):
        _wait_for_api(market)
    if a.resume:
        sim = load_snapshot(a.resume, market)
        print(f"resumed {sim.world_name} at tick {sim.tick} (day {sim.day()})")
    else:
        overrides = {}
        if a.seed is not None:
            overrides["seed"] = a.seed
        if a.world:
            overrides["world_name"] = a.world
        cfg = load_config(a.scenario, overrides)
        sim = Simulation(cfg, market=market)
    days = a.days if a.days is not None else sim.cfg["time"]["days"]
    t0 = time.time()

    def progress(s):
        if not a.quiet and s.day() % 5 == 0:
            from popsim.components import NPC

            print(f"  day {s.day():>4}  population {s.world.count(NPC)}  ({time.time() - t0:.1f}s)", flush=True)

    sim.run(days=days, progress=progress)
    out = Path(a.out or f"runs/{sim.world_name}")
    scenario = Path(a.scenario).stem if a.scenario else (None if a.resume else "default")
    write_outputs(sim, out, ledger_log=a.ledger_log, scenario=scenario)
    if not a.no_plots:
        try:
            from popsim.io.plots import plot_run

            plot_run(out)
        except ImportError:
            print("matplotlib not installed; skipping plots", file=sys.stderr)
    if a.save:
        info = save_snapshot(sim, a.save)
        print(f"snapshot saved: {info}")
    summary = json.loads((out / "summary.json").read_text())
    print(json.dumps({k: summary[k] for k in ("world", "days", "population", "deaths", "gold_supply",
                                                "conservation_daily_ok", "escrow_matches_market")}, indent=2))
    print(f"ledger ok: {summary['ledger']['ok']}  |  output: {out}  |  {time.time() - t0:.1f}s")
    return 0 if summary["ledger"]["ok"] and summary["conservation_daily_ok"] else 1


def _wait_for_api(client, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while True:
        try:
            client.health()
            return
        except Exception as e:  # noqa: BLE001 - connection refused, DNS not ready yet, ...
            if time.time() > deadline:
                raise SystemExit(f"market API at {client.base} not reachable: {e}")
            time.sleep(0.5)


def _cmd_serve(a) -> int:
    from popsim.market.server import main as serve_main

    argv = ["--host", a.host, "--port", str(a.port), "--db", a.db] + (["--verbose"] if a.verbose else [])
    return serve_main(argv)


def _cmd_dashboard(a) -> int:
    from popsim.dashboard.server import main as dash_main

    argv = ["--host", a.host, "--port", str(a.port), "--runs", a.runs, "--scenarios", a.scenarios]
    if a.api:
        argv += ["--api", a.api]
    return dash_main(argv + (["--verbose"] if a.verbose else []))


def _cmd_plot(a) -> int:
    from popsim.io.plots import plot_run

    for p in plot_run(a.run_dir):
        print(p)
    return 0


def _cmd_compare(a) -> int:
    from popsim.io.plots import plot_compare

    labels = a.labels.split(",") if a.labels else [Path(d).name for d in a.run_dirs]
    print(plot_compare(a.run_dirs, labels, a.out))
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="popsim", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run a scenario")
    r.add_argument("scenario", nargs="?", default=None, help="scenario YAML (default: built-in config)")
    r.add_argument("--days", type=float)
    r.add_argument("--seed", type=int)
    r.add_argument("--world", help="override world_name")
    r.add_argument("--out", help="output directory (default runs/<world>)")
    r.add_argument("--api", default=os.environ.get("POPSIM_API_URL") or None,
                   help="market API base URL, e.g. http://127.0.0.1:8080 "
                        "(default: $POPSIM_API_URL, else the in-process market)")
    r.add_argument("--local", action="store_true", help="force the in-process market even if POPSIM_API_URL is set")
    r.add_argument("--save", help="write a paired snapshot here at the end")
    r.add_argument("--resume", help="resume from a snapshot instead of building a new world")
    r.add_argument("--ledger-log", action="store_true", help="also write the full ledger log CSV")
    r.add_argument("--no-plots", action="store_true")
    r.add_argument("--quiet", action="store_true")
    r.set_defaults(fn=_cmd_run)

    s = sub.add_parser("serve", help="start the REST market API")
    s.add_argument("--host", default=os.environ.get("POPSIM_HOST", "127.0.0.1"))
    s.add_argument("--port", type=int, default=int(os.environ.get("POPSIM_PORT", "8080")))
    s.add_argument("--db", default=os.environ.get("POPSIM_DB", "popsim_api.sqlite"))
    s.add_argument("--verbose", action="store_true")
    s.set_defaults(fn=_cmd_serve)

    d = sub.add_parser("dashboard", help="start the web dashboard")
    d.add_argument("--host", default=os.environ.get("POPSIM_DASHBOARD_HOST", "127.0.0.1"))
    d.add_argument("--port", type=int, default=int(os.environ.get("POPSIM_DASHBOARD_PORT", "8000")))
    d.add_argument("--runs", default=os.environ.get("POPSIM_RUNS_DIR", "runs"))
    d.add_argument("--scenarios", default=os.environ.get("POPSIM_SCENARIOS_DIR", "scenarios"))
    d.add_argument("--api", default=os.environ.get("POPSIM_API_URL") or None)
    d.add_argument("--verbose", action="store_true")
    d.set_defaults(fn=_cmd_dashboard)

    pl = sub.add_parser("plot", help="plot a run directory")
    pl.add_argument("run_dir")
    pl.set_defaults(fn=_cmd_plot)

    c = sub.add_parser("compare", help="overlay prices from several runs")
    c.add_argument("run_dirs", nargs="+")
    c.add_argument("--labels")
    c.add_argument("--out", default="runs/compare.png")
    c.set_defaults(fn=_cmd_compare)

    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
