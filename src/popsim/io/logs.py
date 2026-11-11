"""Headless run output (REQ-1.9): CSV tables plus a JSON summary."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from popsim.components import NPC


def write_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("")
        return
    fields: dict[str, None] = {}
    for r in rows:
        for k in r:
            fields[k] = None
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(fields))
        w.writeheader()
        w.writerows(rows)


def read_csv(path: Path) -> list[dict]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        for k, v in r.items():
            if v == "":
                r[k] = None
                continue
            try:
                r[k] = int(v)
            except ValueError:
                try:
                    r[k] = float(v)
                except ValueError:
                    pass
    return rows


def summary(sim, scenario: str | None = None) -> dict:
    m = sim.metrics
    causes: dict[str, int] = {}
    for d in m.deaths:
        causes[d["cause"]] = causes.get(d["cause"], 0) + 1
    gather = [
        {"good": g, "skill": s, "units": round(v[0], 2), "stamina": round(v[1], 2),
         "stamina_per_unit": round(v[1] / v[0], 3) if v[0] else None}
        for (g, s), v in sorted(m.gather.items())
    ]
    return {
        "world": sim.world_name,
        "scenario": scenario,
        "seed": sim.cfg["seed"],
        "ticks": sim.tick,
        "days": sim.tick / sim.ticks_per_day,
        "population": sim.world.count(NPC),
        "deaths": causes,
        "events": sim.event_log,
        "ledger": sim.ledger.verify(),
        "escrow_matches_market": all(v["ok"] for v in sim.escrow_matches_market().values()),
        "conservation_daily_ok": all(c["totals_zero"] and c["goods_balance_ok"] for c in m.checks),
        "gold_supply": m.checks[-1]["gold_supply"] if m.checks else None,
        "trade_flows": _flow_totals(m.flows),
        "stamina_per_unit_by_skill": gather,
    }


def _flow_totals(flows: list[dict]) -> list[dict]:
    tot: dict[tuple, int] = {}
    for f in flows:
        key = (f["from"], f["to"], f["good"])
        tot[key] = tot.get(key, 0) + f["qty"]
    return [{"from": a, "to": b, "good": g, "qty": q} for (a, b, g), q in sorted(tot.items())]


def world_layout(sim) -> dict:
    """Positions and road lengths as the dashboard draws them (REQ-3A.10): cities, resource nodes,
    houses, workplaces and roads in their final form, including generated positions and derived
    road values. Without coordinates every position is (0, 0) and ``spatial`` is false."""
    from popsim.components import Area, City, House, Position, Road, Workplace, Yield

    def at(eid):
        p = sim.world.get(eid, Position)
        return {"x": round(p.x, 4), "y": round(p.y, 4)} if p is not None else {"x": 0.0, "y": 0.0}

    cities = []
    for name, cid in sorted(sim.city_ids.items()):
        area = sim.world.get(cid, Area)
        cities.append({"name": name, **at(cid), "radius": area.radius if area else 0.0})
    nodes = [{"id": nid, "city": sim.city_name(cid), "good": sim.world.get(nid, Yield).good, **at(nid)}
             for cid, ids in sorted(sim.nodes_by_city.items()) for nid in ids]
    from popsim.components import Branch

    for nid in getattr(sim, "overworld_nodes", []):
        br = sim.world.get(nid, Branch)
        nodes.append({"id": nid, "city": None, "overworld": True, "good": sim.world.get(nid, Yield).good,
                      **at(nid), "branch": {"a": sim.city_name(br.a), "b": sim.city_name(br.b), "t": br.t,
                                            "jx": round(br.jx, 4), "jy": round(br.jy, 4),
                                            "length": round(br.length, 4), "hours": round(br.hours, 4),
                                            "stamina_cost": round(br.stamina_cost, 4)}})
    houses = [{"id": eid, "city": sim.city_name(h.city), "occupant": h.occupant, **at(eid)}
              for eid, (h,) in sim.world.query(House)]
    workplaces = [{"id": eid, "city": sim.city_name(w.city), "task": w.task, "wage": w.wage / sim.unit, **at(eid)}
                  for eid, (w,) in sim.world.query(Workplace)]
    roads = [{"a": sim.city_name(r.a), "b": sim.city_name(r.b), "length": r.length, "hours": r.hours,
              "stamina_cost": r.stamina_cost} for _, (r,) in sim.world.query(Road)]
    return {"spatial": bool(getattr(sim, "spatial", False)), "cities": cities, "nodes": nodes,
            "houses": houses, "workplaces": workplaces, "roads": roads}


def write_outputs(sim, out_dir: str | Path, ledger_log: bool = False, scenario: str | None = None) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    m = sim.metrics
    write_csv(m.tick_prices, out / "prices_tick.csv")
    write_csv(m.daily_prices, out / "prices_daily.csv")
    write_csv(m.daily, out / "cities_daily.csv")
    write_csv(m.flows, out / "trade_flows.csv")
    write_csv(m.deaths, out / "deaths.csv")
    write_csv(m.checks, out / "conservation_daily.csv")
    write_csv(sim.event_log, out / "events.csv")
    write_csv(m.transit, out / "transit.csv")
    (out / "config.json").write_text(json.dumps(sim.cfg, indent=1, default=str))
    (out / "world.json").write_text(json.dumps(world_layout(sim), indent=1))
    write_csv([{"a": r["a"], "b": r["b"], "length": r["length"], "hours": r["hours"],
                "stamina_cost": r["stamina_cost"]} for r in world_layout(sim)["roads"]], out / "roads.csv")
    if ledger_log and sim.ledger.keep_log:
        rows = [{"tick": t, "src": s, "dst": d, "asset": a, "amount": n, "reason": r}
                for t, s, d, a, n, r in sim.ledger.log]
        write_csv(rows, out / "ledger_log.csv")
    if getattr(sim, "player", None) is not None:
        from popsim.player import write_outputs as write_player

        write_player(sim, out)
    (out / "summary.json").write_text(json.dumps(summary(sim, scenario), indent=2, default=str))
    return out
