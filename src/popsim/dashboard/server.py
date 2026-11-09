"""Dashboard HTTP server (REQ-1C.1). Standard library only.

  GET    /                              single-page app (static/index.html)
  GET    /static/<file>
  GET    /api/health
  GET    /api/runs                      run list
  GET    /api/runs/{name}               all data of one run
  GET    /api/scenarios                 scenario list
  GET    /api/scenarios/{name}          {yaml, own, resolved}
  PUT    /api/scenarios/{name}          {"yaml": str, "overwrite": bool} -> validate + save
  POST   /api/scenarios/validate        {"yaml": str} -> {ok, errors, warnings, error_items, warning_items, resolved}
  POST   /api/scenarios/preview         {"yaml": str} -> the same plus "world": the layout the simulation will build
  POST   /api/scenarios/helper          {"data": {...}, "action": str, "params": {...}} -> {data, added, notes}
  POST   /api/yaml/parse                {"yaml": str} -> {"data": {...}}
  POST   /api/yaml/render               {"data": {...}} -> {"yaml": str}
  GET    /api/defaults                  built-in default config
  GET    /api/session                   live run state
  POST   /api/session                   {"scenario", "seed"?, "days"?, "name"?, "replace"?} or {"resume": run}
  DELETE /api/session                   discard the live run
  POST   /api/session/control           {"action": play|pause|step_hour|step_day|speed|extend, "value"?}
  POST   /api/session/save              {"name"?} -> writes runs/<name>/
  GET    /api/session/stream            server-sent events: "reset" snapshot, then "frame" deltas
  GET    /api/session/cities                                  cities with citizen counts (inspector)
  GET    /api/session/cities/{city}/citizens                  citizen list: sort, order, role, sick, gold_min,
                                                              gold_max, satiety_min, satiety_max, q, page, page_size
  GET    /api/session/cities/{city}/map                       the city's area, nodes, houses, workplaces, citizens
  GET    /api/session/citizens/{eid}                          one citizen in full
  GET    /api/session/market                                  world name, cities, goods, clock (Market tab)
  GET    /api/session/market/{city}?good=                     goods table, book, other cities, price series
  GET    /api/session/player                                  the player's account (open: false until opened)
  POST   /api/session/player                                  open the account (opening grant)
  POST   /api/session/player/topup                            {"gold"?, "goods"?, "city"?}
  POST   /api/session/player/quote                            {"city","good","side","qty","price","type"} -> estimate
  POST   /api/session/player/orders                           queue an order; it runs at the next tick
  PUT    /api/session/player/orders/{id}                      {"qty"?, "price"?} edit a queued order
  DELETE /api/session/player/orders/{id}                      cancel (queued: now; resting: next tick)
  DELETE /api/session/player/orders                           cancel all
  GET    /api/session/player/actions                          the replay log
  GET    /api/runs/{name}/cities | .../cities/{city}/citizens | .../citizens/{eid}
                                                              the same, read from the run's snapshot (409 without one)
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import queue
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from popsim import inspector as I
from popsim import player as P
from popsim.dashboard import data as D
from popsim.dashboard import layout as L
from popsim.dashboard.session import Conflict, SessionManager

STATIC = Path(__file__).with_name("static")


class _SnapshotCache:
    """The last few loaded snapshots, keyed by file and modification time, so paging
    through a saved run's citizens does not unpickle it on every request."""

    def __init__(self, size: int = 2) -> None:
        self.size = size
        self._items: dict[tuple[str, int], object] = {}
        self._lock = threading.Lock()

    def get(self, path: Path):
        from popsim.io.snapshot import read_world

        key = (str(path), path.stat().st_mtime_ns)
        with self._lock:
            sim = self._items.get(key)
            if sim is None:
                try:
                    sim = read_world(path)
                except Exception as e:  # noqa: BLE001 - corrupt or foreign file
                    raise D.NoSnapshot(f"the snapshot {path.name} cannot be read: {e}") from e
                self._items = {k: v for k, v in list(self._items.items())[-(self.size - 1):]} if self.size > 1 else {}
                self._items[key] = sim
            return sim


def _inspect_args(query: dict) -> dict:
    """Query string -> keyword arguments of ``inspector.list_citizens``."""
    def num(key, cast=float):
        if key not in query or query[key] == "":
            return None
        try:
            return cast(query[key])
        except ValueError as e:
            raise D.BadRequest(f"{key} must be a number") from e

    sick = query.get("sick")
    if sick not in (None, "", "true", "false"):
        raise D.BadRequest("sick must be true or false")
    return {"sort": query.get("sort", "name"), "order": query.get("order", "asc"),
            "role": query.get("role") or None, "sick": None if sick in (None, "") else sick == "true",
            "gold_min": num("gold_min"), "gold_max": num("gold_max"),
            "satiety_min": num("satiety_min"), "satiety_max": num("satiety_max"),
            "q": query.get("q"), "page": num("page", int) or 1, "page_size": num("page_size", int) or 50}


class _Handler(BaseHTTPRequestHandler):
    runs_dir: Path
    scenarios_dir: Path
    sessions: SessionManager
    snapshots: _SnapshotCache
    quiet = True
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # noqa: N802
        if not self.quiet:
            super().log_message(fmt, *args)

    # -- helpers --------------------------------------------------------------
    def _send(self, code: int, payload, ctype="application/json") -> None:
        body = payload if isinstance(payload, bytes) else json.dumps(payload, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            b = json.loads(self.rfile.read(n))
        except json.JSONDecodeError as e:
            raise D.BadRequest(f"invalid JSON body: {e}") from e
        if not isinstance(b, dict):
            raise D.BadRequest("JSON body must be an object")
        return b

    def _route(self, method: str) -> None:
        parsed = urllib.parse.urlparse(self.path)
        parts = [urllib.parse.unquote(p) for p in parsed.path.split("/") if p]
        query = dict(urllib.parse.parse_qsl(parsed.query))
        try:
            if parts[:2] == ["api", "session"] and parts[2:] == ["stream"] and method == "GET":
                return self._stream()
            result = self._dispatch(method, parts, query)
        except D.NotFound as e:
            return self._send(404, {"error": str(e).strip("'\"")})
        except (Conflict, D.NoSnapshot) as e:
            return self._send(409, {"error": str(e)})
        except P.PlayerError as e:
            return self._send(400, {"error": str(e), "code": e.code})
        except I.NotFound as e:
            return self._send(404, {"error": str(e).strip("'\"")})
        except (D.BadRequest, ValueError, TypeError) as e:
            return self._send(400, {"error": str(e)})
        except FileNotFoundError as e:
            return self._send(404, {"error": str(e)})
        if result is None:
            return self._send(404, {"error": f"no route {method} {parsed.path}"})
        if isinstance(result, tuple):  # (bytes, content type)
            return self._send(200, result[0], result[1])
        self._send(200, result)

    def _static(self, rel: str):
        path = (STATIC / rel).resolve()
        if STATIC.resolve() not in path.parents or not path.is_file():
            raise D.NotFound(rel)
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        return path.read_bytes(), ctype

    def _dispatch(self, method, parts, query):
        if method == "GET" and not parts:
            return self._static("index.html")
        if method == "GET" and parts[0] == "static" and len(parts) >= 2:
            return self._static("/".join(parts[1:]))
        if not parts or parts[0] != "api":
            return None
        p = parts[1:]
        if method == "GET" and p == ["health"]:
            return {"ok": True}
        if method == "GET" and p == ["runs"]:
            return D.list_runs(self.runs_dir)
        if method == "GET" and len(p) == 2 and p[0] == "runs":
            return D.load_run(self.runs_dir, p[1])
        if method == "GET" and len(p) >= 3 and p[0] == "runs":
            found = self._inspect(p[2:], query, lambda: self._run_sim(p[1]))
            if found is not None:
                return found
        if method == "GET" and p == ["defaults"]:
            return D.defaults()
        if p == ["scenarios"] and method == "GET":
            return D.list_scenarios(self.scenarios_dir)
        if p == ["scenarios", "validate"] and method == "POST":
            return D.validate(D.parse_yaml(str(self._body().get("yaml", ""))), self.scenarios_dir, full=False)
        if p == ["scenarios", "preview"] and method == "POST":
            return D.preview(D.parse_yaml(str(self._body().get("yaml", ""))), self.scenarios_dir)
        if p == ["scenarios", "helper"] and method == "POST":
            b = self._body()
            if not isinstance(b.get("data"), dict):
                raise D.BadRequest("data must be the scenario mapping")
            return L.run_helper(b["data"], str(b.get("action")), b.get("params") or {}, self.scenarios_dir)
        if len(p) == 2 and p[0] == "scenarios":
            if method == "GET":
                return D.get_scenario(self.scenarios_dir, p[1])
            if method == "PUT":
                b = self._body()
                return D.save_scenario(self.scenarios_dir, p[1], str(b.get("yaml", "")), bool(b.get("overwrite")))
        if p == ["yaml", "parse"] and method == "POST":
            return {"data": D.parse_yaml(str(self._body().get("yaml", "")))}
        if p == ["yaml", "render"] and method == "POST":
            d = self._body().get("data")
            if not isinstance(d, dict):
                raise D.BadRequest("data must be an object")
            return {"yaml": D.render_yaml(d)}
        if p == ["session"]:
            if method == "GET":
                s = self.sessions.session
                return s.state() if s is not None and s.status != "closed" else {"status": "none"}
            if method == "POST":
                b = self._body()
                if not b.get("scenario") and not b.get("resume"):
                    raise D.BadRequest("scenario is required")
                s = self.sessions.start(str(b["scenario"]) if b.get("scenario") else None, b.get("seed"),
                                        b.get("days"), b.get("name"), bool(b.get("replace")),
                                        resume=str(b["resume"]) if b.get("resume") else None)
                return s.state()
            if method == "DELETE":
                self.sessions.discard()
                return {"ok": True}
        if method == "GET" and len(p) >= 2 and p[0] == "session" and p[1] in ("cities", "citizens"):
            found = self._inspect(p[1:], query, lambda: self.sessions.get().sim, live=True)
            if found is not None:
                return found
        if len(p) >= 2 and p[0] == "session" and p[1] in ("market", "player"):
            found = self._player(method, p[1:], query)
            if found is not None:
                return found
        if p == ["session", "snapshot"] and method == "GET":
            return self.sessions.get().snapshot()
        if p == ["session", "control"] and method == "POST":
            b = self._body()
            return self.sessions.get().control(str(b.get("action")), b.get("value"))
        if p == ["session", "save"] and method == "POST":
            name = self.sessions.get().save(self.runs_dir, self._body().get("name"))
            return {"saved_as": name}
        return None

    def _player(self, method, p, query):
        """Market tab routes (REQ-1C.9): books of the live world and the player's account."""
        sess = self.sessions.get()
        if p == ["market"] and method == "GET":
            return sess.player_read(P.world_info)
        if len(p) == 2 and p[0] == "market" and method == "GET":
            return sess.player_read(P.market_view, p[1], query.get("good") or None)
        if p[0] != "player":
            return None
        if p == ["player"]:
            if method == "GET":
                return sess.player_read(P.state, int(query.get("history") or 200), query.get("city") or None)
            if method == "POST":
                sess.player_act(P.open_account)
                return sess.player_read(P.state)
        b = self._body() if method in ("POST", "PUT") else {}
        if p == ["player", "actions"] and method == "GET":
            return sess.player_read(lambda sim: P.account(sim)[1].actions if P.account(sim)[1] else [])
        if p == ["player", "topup"] and method == "POST":
            gold = P.to_cents(sess.sim, b["gold"], "gold") if b.get("gold") not in (None, "", 0) else 0
            sess.player_act(P.top_up, gold, b.get("goods") or {}, b.get("city") or None)
            return sess.player_read(P.state)
        if p == ["player", "quote"] and method == "POST":
            return sess.player_read(lambda sim: P.quote(sim, str(b.get("city")), str(b.get("good")),
                                    str(b.get("side")), b.get("qty"), P.to_cents(sim, b.get("price"), "price"),
                                    str(b.get("type") or "limit")))
        if p == ["player", "orders"]:
            if method == "POST":
                o = sess.player_act(lambda sim: P.queue_order(
                    sim, str(b.get("city")), str(b.get("good")), str(b.get("side")), b.get("qty"),
                    P.to_cents(sim, b.get("price"), "price"), str(b.get("type") or "limit"),
                    b.get("expires_hours")), orders=True)
                return P.order_view(sess.sim, o)
            if method == "DELETE":
                return {"cancelled": sess.player_act(P.cancel_all)}
        if len(p) == 3 and p[:2] == ["player", "orders"]:
            if method == "PUT":
                o = sess.player_act(lambda sim: P.edit_order(
                    sim, p[2], b.get("qty"),
                    P.to_cents(sim, b["price"], "price") if b.get("price") is not None else None), orders=True)
                return P.order_view(sess.sim, o)
            if method == "DELETE":
                o = sess.player_act(P.cancel_order, p[2])
                return P.order_view(sess.sim, o)
        return None

    def _run_sim(self, name: str):
        D.check_name(name)
        if not (self.runs_dir / name / "summary.json").exists():
            raise D.NotFound(f"no run {name!r}")
        path = D.find_snapshot(self.runs_dir, name)
        if path is None:
            raise D.NoSnapshot(f"run {name!r} was saved without a snapshot, so its citizens cannot be "
                               "inspected (run it with --save, or save it from Play)")
        return self.snapshots.get(path)

    def _inspect(self, p, query, get_sim, live: bool = False):
        """Inspector routes (REQ-1C.5): ``cities``, ``cities/{c}/citizens``, ``citizens/{id}``."""
        def run(fn):
            if live:  # the live simulation is stepped by another thread
                with self.sessions.get().lock:
                    return fn(self.sessions.get().sim)
            return fn(get_sim())

        if p == ["cities"]:
            return run(I.city_overview)
        if len(p) == 3 and p[0] == "cities" and p[2] == "map":
            return run(lambda sim: I.city_map(sim, p[1]))
        if len(p) == 3 and p[0] == "cities" and p[2] == "citizens":
            args = _inspect_args(query)
            return run(lambda sim: I.list_citizens(sim, p[1], **args))
        if len(p) == 2 and p[0] == "citizens":
            try:
                eid = int(p[1])
            except ValueError as e:
                raise D.BadRequest("citizen id must be an integer") from e
            return run(lambda sim: I.citizen_detail(sim, eid))
        return None

    def _stream(self) -> None:
        sess = self.sessions.get()
        q, snap = sess.subscribe()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        self.close_connection = True

        def emit(event: str, payload) -> None:
            self.wfile.write(f"event: {event}\ndata: {json.dumps(payload, default=str)}\n\n".encode())
            self.wfile.flush()

        try:
            emit("reset", snap)
            while True:
                try:
                    msg = q.get(timeout=10)
                except queue.Empty:
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
                    continue
                if msg.get("type") == "closed":
                    emit("closed", {})
                    return
                emit("frame", msg)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            sess.unsubscribe(q)

    def do_GET(self):  # noqa: N802
        self._route("GET")

    def do_POST(self):  # noqa: N802
        self._route("POST")

    def do_PUT(self):  # noqa: N802
        self._route("PUT")

    def do_DELETE(self):  # noqa: N802
        self._route("DELETE")


def make_server(host: str = "127.0.0.1", port: int = 8000, runs_dir: str | Path = "runs",
                scenarios_dir: str | Path = "scenarios", api_url: str | None = None,
                quiet: bool = True) -> ThreadingHTTPServer:
    runs, scen = Path(runs_dir), Path(scenarios_dir)
    runs.mkdir(parents=True, exist_ok=True)
    manager = SessionManager(scen, runs, api_url)
    handler = type("DashboardHandler", (_Handler,), {
        "runs_dir": runs, "scenarios_dir": scen, "sessions": manager, "snapshots": _SnapshotCache(),
        "quiet": quiet})
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    server.sessions = manager  # type: ignore[attr-defined]
    return server


def main(argv=None) -> int:
    env = os.environ
    p = argparse.ArgumentParser(description="popsim dashboard")
    p.add_argument("--host", default=env.get("POPSIM_DASHBOARD_HOST", "127.0.0.1"))
    p.add_argument("--port", type=int, default=int(env.get("POPSIM_DASHBOARD_PORT", "8000")))
    p.add_argument("--runs", default=env.get("POPSIM_RUNS_DIR", "runs"))
    p.add_argument("--scenarios", default=env.get("POPSIM_SCENARIOS_DIR", "scenarios"))
    p.add_argument("--api", default=env.get("POPSIM_API_URL") or None,
                   help="market API for live runs (default: $POPSIM_API_URL, else in-process)")
    p.add_argument("--verbose", action="store_true")
    a = p.parse_args(argv)
    server = make_server(a.host, a.port, a.runs, a.scenarios, a.api, quiet=not a.verbose)
    print(f"popsim dashboard on http://{a.host}:{a.port}  (runs: {a.runs}, scenarios: {a.scenarios}, "
          f"market: {a.api or 'in-process'})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
