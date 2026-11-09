"""Dashboard API (REQ-1C.5): runs, scenarios, validation, live sessions, stream,
and live-run == CLI-run equivalence."""

import json
import threading
import time
import urllib.error
import urllib.request

import pytest
import yaml

from popsim.config import load_config
from popsim.dashboard.server import make_server
from popsim.io.logs import read_csv, write_outputs
from popsim.world import Simulation

from conftest import SCENARIOS

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def small_scenario() -> dict:
    cfg = load_config(SCENARIOS / "arbitrage.yaml")
    for c in cfg["cities"]:
        c["population"] = 12
    cfg["time"]["days"] = 4
    cfg["events"] = [{"day": 1, "type": "add_road", "a": "A", "b": "B", "hours": 4, "stamina_cost": 10}]
    cfg["world_name"] = "small"
    return cfg


@pytest.fixture
def dash(tmp_path):
    runs, scen = tmp_path / "runs", tmp_path / "scenarios"
    scen.mkdir()
    for name in ("baseline", "arbitrage"):
        (scen / f"{name}.yaml").write_text((SCENARIOS / f"{name}.yaml").read_text())
    (scen / "small.yaml").write_text(yaml.safe_dump(small_scenario(), sort_keys=False))
    server = make_server("127.0.0.1", 0, runs, scen)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def call(method, path, body=None, raw=False):
        req = urllib.request.Request(base + path, method=method,
                                     data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with OPENER.open(req, timeout=30) as r:
                data = r.read()
                return r.status, (data if raw else json.loads(data))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    call.base, call.runs, call.scen = base, runs, scen
    yield call
    server.sessions.discard()
    server.shutdown()
    server.server_close()


def test_static_app_and_path_traversal(dash):
    st, body = dash("GET", "/", raw=True)
    assert st == 200 and b"popsim" in body
    st, body = dash("GET", "/static/app.js", raw=True)
    assert st == 200 and b"route" in body
    assert dash("GET", "/static/../server.py")[0] == 404
    assert dash("GET", "/api/nope")[0] == 404


def test_runs_list_and_detail(dash):
    assert dash("GET", "/api/runs") == (200, [])
    sim = Simulation(small_scenario())
    sim.run(days=2)
    write_outputs(sim, dash.runs / "r1", scenario="small")
    st, runs = dash("GET", "/api/runs")
    assert st == 200 and runs[0]["name"] == "r1" and runs[0]["ok"] and runs[0]["scenario"] == "small"
    st, run = dash("GET", "/api/runs/r1")
    assert st == 200
    assert {"summary", "config", "prices_daily", "cities_daily", "flows", "deaths", "events", "checks", "transit"} <= set(run)
    assert run["config"]["world_name"] == "small"
    assert len(run["cities_daily"]) == 2 * 2
    assert dash("GET", "/api/runs/missing")[0] == 404
    assert dash("GET", "/api/runs/..%2Fetc")[0] == 400


def test_scenarios_validate_save_and_yaml_roundtrip(dash):
    st, lst = dash("GET", "/api/scenarios")
    assert st == 200 and {s["name"] for s in lst} == {"baseline", "arbitrage", "small"}
    st, sc = dash("GET", "/api/scenarios/arbitrage")
    assert sc["own"]["extends"] == "baseline.yaml" and sc["resolved"]["cities"][0]["name"] == "A"

    ok = dash("POST", "/api/scenarios/validate", {"yaml": sc["yaml"]})[1]
    assert ok["ok"] and not ok["errors"]
    bad_yaml = ("extends: baseline.yaml\nroads: [{a: A, b: Z, hours: 2, stamina_cost: 1}]\n"
                "events: [{day: 99, type: stimulus, city: all, per_npc: 5}, {day: 1, type: boom}]\n")
    bad = dash("POST", "/api/scenarios/validate", {"yaml": bad_yaml})[1]
    assert not bad["ok"]
    assert any("unknown city 'Z'" in e for e in bad["errors"])
    assert any("events[1].type" in e for e in bad["errors"])
    assert any("never fires" in w for w in bad["warnings"])
    assert dash("POST", "/api/scenarios/validate", {"yaml": "extends: nope.yaml\n"})[1]["ok"] is False
    assert dash("POST", "/api/scenarios/validate", {"yaml": "- a list"})[0] == 400

    data = {"extends": "baseline.yaml", "world_name": "mine", "time": {"days": 5}}
    text = dash("POST", "/api/yaml/render", {"data": data})[1]["yaml"]
    assert dash("POST", "/api/yaml/parse", {"yaml": text})[1]["data"] == data

    st, res = dash("PUT", "/api/scenarios/mine", {"yaml": text})
    assert st == 200 and res["saved"]
    assert (dash.scen / "mine.yaml").exists()
    assert dash("PUT", "/api/scenarios/mine", {"yaml": text})[0] == 400  # exists, no overwrite
    assert dash("PUT", "/api/scenarios/mine", {"yaml": text, "overwrite": True})[1]["saved"]
    st, res = dash("PUT", "/api/scenarios/broken", {"yaml": bad_yaml})
    assert st == 200 and not res["saved"] and not (dash.scen / "broken.yaml").exists()
    assert dash("PUT", "/api/scenarios/..%2Fevil", {"yaml": text})[0] == 400


def test_live_session_controls_and_save(dash):
    assert dash("GET", "/api/session")[1] == {"status": "none"}
    st, s = dash("POST", "/api/session", {"scenario": "small", "days": 3})
    assert st == 200 and s["status"] == "paused" and s["total_ticks"] == 72
    assert dash("POST", "/api/session", {"scenario": "small"})[0] == 409  # one at a time
    assert dash("POST", "/api/session/control", {"action": "step_hour"})[1]["tick"] == 1
    assert dash("POST", "/api/session/control", {"action": "step_day"})[1]["tick"] == 25
    assert dash("POST", "/api/session/control", {"action": "speed", "value": 50})[1]["speed"] == 50
    assert dash("POST", "/api/session/control", {"action": "speed", "value": 0})[0] == 400
    assert dash("POST", "/api/session/control", {"action": "fly"})[0] == 400
    dash("POST", "/api/session/control", {"action": "play"})
    deadline = time.time() + 20
    while dash("GET", "/api/session")[1]["status"] != "finished" and time.time() < deadline:
        time.sleep(0.1)
    s = dash("GET", "/api/session")[1]
    assert s["status"] == "finished" and s["tick"] == 72
    s = dash("POST", "/api/session/control", {"action": "extend", "value": 1})[1]
    assert s["status"] == "paused" and s["total_ticks"] == 96

    st, r = dash("POST", "/api/session/save", {"name": "live1"})
    assert st == 200 and r["saved_as"] == "live1"
    assert dash("POST", "/api/session/save", {"name": "live1"})[1]["saved_as"] == "live1-2"
    assert {x["name"] for x in dash("GET", "/api/runs")[1]} == {"live1", "live1-2"}
    assert (dash.runs / "live1" / "end.pkl").exists()
    assert dash("DELETE", "/api/session")[0] == 200
    assert dash("GET", "/api/session")[1] == {"status": "none"}


def test_stream_sends_snapshot_then_frames(dash):
    dash("POST", "/api/session", {"scenario": "small"})
    resp = OPENER.open(dash.base + "/api/session/stream", timeout=10)

    def read_event():
        ev, data = None, None
        while True:
            line = resp.readline().decode().rstrip("\n")
            if line.startswith("event: "):
                ev = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
            elif line == "" and ev:
                return ev, data

    ev, snap = read_event()
    assert ev == "reset" and snap["state"]["tick"] == 0 and snap["config"]["world_name"] == "small"
    dash("POST", "/api/session/control", {"action": "step_day"})
    ev, frame = read_event()
    assert ev == "frame" and frame["state"]["tick"] == 24
    assert len(frame["delta"]["cities_daily"]) == 2  # one daily row per city
    assert set(frame["now"]["cities"]) == {"A", "B"}
    resp.close()


def test_live_run_equals_cli_run(dash, tmp_path):
    dash("POST", "/api/session", {"scenario": "small"})
    # Mix hour steps, day steps and free play: slicing must not change results.
    for _ in range(5):
        dash("POST", "/api/session/control", {"action": "step_hour"})
    dash("POST", "/api/session/control", {"action": "step_day"})
    dash("POST", "/api/session/control", {"action": "speed", "value": 20})
    dash("POST", "/api/session/control", {"action": "play"})
    deadline = time.time() + 20
    while dash("GET", "/api/session")[1]["status"] != "finished" and time.time() < deadline:
        time.sleep(0.1)
    dash("POST", "/api/session/save", {"name": "live"})

    sim = Simulation(load_config(dash.scen / "small.yaml"))
    sim.run()
    write_outputs(sim, tmp_path / "cli", scenario="small")
    for f in ("prices_daily.csv", "cities_daily.csv", "trade_flows.csv", "conservation_daily.csv"):
        assert read_csv(dash.runs / "live" / f) == read_csv(tmp_path / "cli" / f), f


# ------------------------------------------------------------ inspector (REQ-1C.5)


def test_inspector_endpoints_live_session(dash):
    assert dash("GET", "/api/session/cities")[0] == 404  # no live run yet
    dash("POST", "/api/session", {"scenario": "small"})
    dash("POST", "/api/session/control", {"action": "step_day"})
    st, cities = dash("GET", "/api/session/cities")
    assert st == 200 and [c["name"] for c in cities] == ["A", "B", "travelling"]
    assert sum(c["citizens"] for c in cities) == 24

    st, lst = dash("GET", "/api/session/cities/A/citizens?sort=gold&order=desc&page_size=5")
    assert st == 200 and lst["total"] == 12 and len(lst["rows"]) == 5 and lst["pages"] == 3
    golds = [r["gold"] for r in lst["rows"]]
    assert golds == sorted(golds, reverse=True)
    st, f = dash("GET", "/api/session/cities/A/citizens?role=merchant&sick=false&gold_min=1")
    assert st == 200 and all(r["role"] == "merchant" and not r["sick"] for r in f["rows"])

    eid = lst["rows"][0]["id"]
    st, d = dash("GET", f"/api/session/citizens/{eid}")
    assert st == 200 and d["id"] == eid and d["alive"] and d["plan"]["goods"]
    assert {"status", "needs", "wallet", "traits", "skills", "action", "orders", "plan", "jobs",
            "history", "memory"} <= set(d)

    # the same citizen is found again after the world moves on (the panel follows it)
    dash("POST", "/api/session/control", {"action": "step_day"})
    assert dash("GET", f"/api/session/citizens/{eid}")[1]["day"] == 2

    assert dash("GET", "/api/session/cities/Nowhere/citizens")[0] == 404
    assert dash("GET", "/api/session/citizens/999999")[0] == 404
    assert dash("GET", "/api/session/citizens/abc")[0] == 400
    assert dash("GET", "/api/session/cities/A/citizens?sort=bogus")[0] == 400
    assert dash("GET", "/api/session/cities/A/citizens?page_size=100000")[0] == 400
    assert dash("GET", "/api/session/cities/A/citizens?gold_min=lots")[0] == 400
    assert dash("GET", "/api/session/cities/A/citizens?sick=maybe")[0] == 400


def test_inspector_saved_run_uses_snapshot(dash):
    dash("POST", "/api/session", {"scenario": "small", "days": 3})
    dash("POST", "/api/session/control", {"action": "step_day"})
    dash("POST", "/api/session/control", {"action": "step_day"})
    dash("POST", "/api/session/save", {"name": "snap"})
    live = dash("GET", "/api/session/cities/A/citizens?sort=id&page_size=200")[1]
    dash("POST", "/api/session/control", {"action": "step_day"})  # the live run moves on; the saved one must not
    assert [r for r in dash("GET", "/api/runs")[1] if r["name"] == "snap"][0]["inspectable"] is True

    st, saved = dash("GET", "/api/runs/snap/cities/A/citizens?sort=id&page_size=200")
    assert st == 200 and saved == live
    assert dash("GET", "/api/runs/snap/cities")[1][0] == {"name": "A", "citizens": 12}
    eid = saved["rows"][0]["id"]
    st, d = dash("GET", f"/api/runs/snap/citizens/{eid}")
    assert st == 200 and d["history"]["recorded"] and d["day"] == 2
    assert dash("GET", "/api/runs/snap/citizens/999999")[0] == 404
    assert dash("GET", "/api/runs/missing/cities")[0] == 404
    assert dash("GET", "/api/runs/..%2Fetc/cities")[0] == 400


def test_inspector_saved_run_without_snapshot_is_a_clear_error(dash):
    sim = Simulation(small_scenario())
    sim.run(days=1)
    write_outputs(sim, dash.runs / "nosnap", scenario="small")
    assert dash("GET", "/api/runs")[1][0]["inspectable"] is False
    st, err = dash("GET", "/api/runs/nosnap/cities/A/citizens")
    assert st == 409 and "snapshot" in err["error"]
    st, err = dash("GET", "/api/runs/nosnap/citizens/1")
    assert st == 409 and "snapshot" in err["error"]
    (dash.runs / "nosnap" / "bad.pkl").write_bytes(b"not a pickle")
    st, err = dash("GET", "/api/runs/nosnap/cities")
    assert st == 409 and "cannot be read" in err["error"]


# ------------------------------------------------------------ positions (REQ-3A, REQ-1C.2)


def spatial_scenario() -> dict:
    cfg = small_scenario()
    cfg["world_name"] = "spatial"
    cfg["cities"][0].update(x=0, y=0, radius=3, houses={"count": 4},
                            workplaces=[{"task": "guard", "wage": 1.5, "at": [1, 1]}])
    cfg["cities"][1].update(x=30, y=40, radius=3)
    cfg["roads"] = []
    cfg["events"] = [{"day": 1, "type": "add_road", "a": "A", "b": "B"}]
    return cfg


def test_spatial_scenario_validates_runs_and_serves_positions(dash):
    data = spatial_scenario()
    rep = dash("POST", "/api/scenarios/validate", {"yaml": yaml.safe_dump(data, sort_keys=False)})[1]
    assert rep["ok"], rep["errors"]
    bad = json.loads(json.dumps(data))
    del bad["cities"][1]["x"]
    assert not dash("POST", "/api/scenarios/validate", {"yaml": yaml.safe_dump(bad)})[1]["ok"]
    assert dash("PUT", "/api/scenarios/spatial", {"yaml": yaml.safe_dump(data, sort_keys=False)})[1]["saved"]

    dash("POST", "/api/session", {"scenario": "spatial", "days": 3})
    snap = dash("GET", "/api/session/snapshot")[1]
    assert snap["world"]["spatial"] and {c["name"]: (c["x"], c["y"]) for c in snap["world"]["cities"]} == {"A": (0.0, 0.0), "B": (30.0, 40.0)}
    assert len(snap["world"]["houses"]) == 4 and snap["world"]["workplaces"][0]["task"] == "guard"
    dash("POST", "/api/session/control", {"action": "step_day"})
    dash("POST", "/api/session/control", {"action": "step_day"})  # day 1 has passed: the road exists
    st, m = dash("GET", "/api/session/cities/A/map")
    assert st == 200 and m["spatial"] and m["radius"] == 3 and len(m["citizens"]) == 12 and len(m["houses"]) == 4
    assert all(abs(c["x"]) <= 3.5 and abs(c["y"]) <= 3.5 for c in m["citizens"])
    assert dash("GET", "/api/session/cities/Nowhere/map")[0] == 404
    ev = [e for e in dash("GET", "/api/session/snapshot")[1]["events"] if e["type"] == "add_road"]
    assert ev and ev[0]["length"] == 50.0 and ev[0]["hours"] == 10.0

    dash("POST", "/api/session/save", {"name": "sp"})
    run = dash("GET", "/api/runs/sp")[1]
    assert run["world"]["roads"][0]["length"] == 50.0 and run["world"]["spatial"]
    assert dash("GET", "/api/runs/sp/cities/B/map")[1]["center"] == {"x": 30.0, "y": 40.0}
    eid = dash("GET", "/api/runs/sp/cities/A/citizens?page_size=1")[1]["rows"][0]["id"]
    d = dash("GET", f"/api/runs/sp/citizens/{eid}")[1]
    assert d["status"]["position"] is not None and d["status"]["house"] is None or d["status"]["house"]["position"]
    assert any(o["distance"] is not None for o in d["jobs"]["options"])


# ------------------------------------------------------- public finance (REQ-5.5)


def test_fiscal_scenario_validates_runs_and_serves_the_numbers(dash):
    data = small_scenario()
    data["world_name"] = "taxed"
    data["fiscal"] = {"income_tax": 0.1, "sales_tax": 0.02, "support": {"amount": 3.0, "hour": 8}}
    data["cities"][0]["workplaces"] = [{"task": "guard", "wage": 8.0, "slots": 3}]
    data["cities"][1]["fiscal"] = {"income_tax": 0.2}
    rep = dash("POST", "/api/scenarios/validate", {"yaml": yaml.safe_dump(data, sort_keys=False)})[1]
    assert rep["ok"], rep["errors"]
    bad = json.loads(json.dumps(data))
    bad["fiscal"]["income_tax"] = 3
    bad["cities"][0]["workplaces"][0]["slots"] = 0
    errors = dash("POST", "/api/scenarios/validate", {"yaml": yaml.safe_dump(bad)})[1]["errors"]
    assert any("fiscal.income_tax" in e for e in errors) and any("slots" in e for e in errors)

    assert dash("PUT", "/api/scenarios/taxed", {"yaml": yaml.safe_dump(data, sort_keys=False)})[1]["saved"]
    dash("POST", "/api/session", {"scenario": "taxed", "days": 3})
    for _ in range(3):
        dash("POST", "/api/session/control", {"action": "step_day"})
    snap = dash("GET", "/api/session/snapshot")[1]
    rows = [r for r in snap["cities_daily"] if r["city"] == "A"]
    assert len(rows) == 3 and {"tax_collected", "wages_paid", "support_paid", "treasury"} <= set(rows[0])
    assert sum(r["tax_collected"] for r in rows) > 0
    eid = dash("GET", "/api/session/cities/A/citizens?page_size=1")[1]["rows"][0]["id"]
    d = dash("GET", f"/api/session/citizens/{eid}")[1]
    assert d["plan"]["fiscal"]["income_tax"] == 0.1 and d["plan"]["fiscal"]["support_per_day"] == 3.0
    other = dash("GET", "/api/session/cities/B/citizens?page_size=1")[1]["rows"][0]["id"]
    assert dash("GET", f"/api/session/citizens/{other}")[1]["plan"]["fiscal"]["income_tax"] == 0.2  # B's own rate
    dash("POST", "/api/session/save", {"name": "taxed-run"})
    run = dash("GET", "/api/runs/taxed-run")[1]
    assert run["summary"]["ledger"]["ok"] and run["summary"]["conservation_daily_ok"]


# ------------------------------------------------------------- map editor (REQ-1C.8)


def test_preview_and_helper_endpoints(dash):
    # start from a scenario with no coordinates, give it positions and roads through the helpers, as the map does
    data = {"extends": "baseline.yaml", "world_name": "mapped", "time": {"days": 3}}
    st, out = dash("POST", "/api/scenarios/helper", {"data": data, "action": "arrange_cities", "params": {"gap": 12}})
    assert st == 200 and len(out["added"]) == 2 and all("x" in c for c in out["data"]["cities"])
    assert "cities" not in data  # the request never changes anything on the server
    st, out = dash("POST", "/api/scenarios/helper", {"data": out["data"], "action": "connect_cities", "params": {}})
    assert st == 200 and out["data"]["roads"] == [{"a": "A", "b": "B"}]
    st, out = dash("POST", "/api/scenarios/helper", {"data": out["data"], "action": "fill_houses", "params": {"count": 5}})
    own = out["data"]

    st, rep = dash("POST", "/api/scenarios/preview", {"yaml": yaml.safe_dump(own, sort_keys=False)})
    assert st == 200 and rep["ok"], rep
    world = rep["world"]
    assert world["spatial"] and len(world["houses"]) == 10 and world["roads"][0]["length"] > 0
    assert all("ci" in h and "i" in h for h in world["nodes"] + world["houses"])
    inside = {c["name"]: c for c in world["cities"]}
    assert all(((n["x"] - inside[n["city"]]["x"]) ** 2 + (n["y"] - inside[n["city"]]["y"]) ** 2) ** 0.5 <= inside[n["city"]]["radius"] + 1e-6
               for n in world["nodes"] + world["houses"])  # automatic positions are inside their city
    assert dash("POST", "/api/scenarios/preview", {"yaml": yaml.safe_dump(own, sort_keys=False)})[1]["world"] == world

    # problems come with the path of the item they belong to
    own["cities"][0]["nodes"][0]["at"] = [99, 99]
    st, rep = dash("POST", "/api/scenarios/validate", {"yaml": yaml.safe_dump(own, sort_keys=False)})
    assert st == 200 and not rep["ok"]
    assert any(i["path"] == "cities[0].nodes[0].at" for i in rep["error_items"])
    assert dash("POST", "/api/scenarios/preview", {"yaml": yaml.safe_dump(own, sort_keys=False)})[1]["world"] is None

    # bad requests are reported, not crashes
    assert dash("POST", "/api/scenarios/helper", {"data": own, "action": "explode"})[0] == 400
    assert dash("POST", "/api/scenarios/helper", {"data": "nope", "action": "arrange_cities"})[0] == 400
    st, err = dash("POST", "/api/scenarios/helper", {"data": {"extends": "baseline.yaml", "cities": [{"name": "A", "population": 1}]},
                                                      "action": "connect_cities"})
    assert st == 400 and "at least two" in err["error"]
    st, err = dash("POST", "/api/scenarios/helper", {"data": {"extends": "baseline.yaml"}, "action": "scatter",
                                                      "params": {"city": "A", "kind": "house", "count": 1, "center": [0, 0], "radius": 1}})
    assert st == 400 and "position" in err["error"]
