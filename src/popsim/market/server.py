"""REST market API (REQ-1A.6). Standard library only, so the Docker image needs
nothing beyond Python.

Routes (world and city are URL path segments):

  GET    /health
  GET    /worlds
  POST   /{world}/{city}/orders/batch     {"tick": int, "orders": [...]}
  GET    /{world}/{city}/prices
  GET    /{world}/{city}/book/{good}?depth=5
  GET    /{world}/{city}/orders?owner=ID
  GET    /{world}/{city}/locked
  GET    /{world}/state                   full market state of a world
  PUT    /{world}/state                   replace it
  DELETE /{world}                         drop a world's live state
  GET    /{world}/snapshots
  POST   /{world}/snapshots               {"name": str, "tick": int}  -> SQLite
  POST   /{world}/snapshots/{name}/restore

Live state is in memory; it is written to SQLite only when a snapshot is saved.
On startup the latest snapshot of every world is loaded.

Run: ``python -m popsim.market.server --port 8080 --db popsim_api.sqlite``
(defaults come from POPSIM_HOST, POPSIM_PORT and POPSIM_DB when set; REQ-1B.4)
"""

from __future__ import annotations

import argparse
import json
import os
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from popsim.market.service import MarketService
from popsim.market.store import SnapshotStore


class _Handler(BaseHTTPRequestHandler):
    service: MarketService
    lock: threading.Lock
    quiet: bool = True

    def log_message(self, fmt, *args):  # noqa: N802
        if not self.quiet:
            super().log_message(fmt, *args)

    def _send(self, code: int, payload) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n)) if n else {}

    def _route(self, method: str) -> None:
        parsed = urllib.parse.urlparse(self.path)
        parts = [urllib.parse.unquote(p) for p in parsed.path.split("/") if p]
        query = dict(urllib.parse.parse_qsl(parsed.query))
        svc = self.service
        try:
            with self.lock:
                result = self._dispatch(method, parts, query, svc)
        except KeyError as e:
            return self._send(404, {"error": str(e)})
        except (ValueError, TypeError, json.JSONDecodeError) as e:
            return self._send(400, {"error": str(e)})
        except RuntimeError as e:
            return self._send(409, {"error": str(e)})
        if result is _NOT_FOUND:
            return self._send(404, {"error": "no such route"})
        self._send(200, result)

    def _dispatch(self, method, parts, query, svc):
        if method == "GET" and parts == ["health"]:
            return {"ok": True}
        if method == "GET" and parts == ["worlds"]:
            return sorted(svc.worlds)
        if len(parts) == 1 and method == "DELETE":
            svc.reset_world(parts[0])
            return {"ok": True}
        if len(parts) == 2 and parts[1] == "state":
            if method == "GET":
                return svc.export_world(parts[0])
            if method == "PUT":
                svc.import_world(parts[0], self._body())
                return {"ok": True}
        if len(parts) >= 2 and parts[1] == "snapshots":
            world = parts[0]
            if len(parts) == 2 and method == "GET":
                return svc.list_snapshots(world)
            if len(parts) == 2 and method == "POST":
                b = self._body()
                return svc.save_snapshot(world, str(b["name"]), int(b.get("tick", 0)))
            if len(parts) == 4 and parts[3] == "restore" and method == "POST":
                return svc.restore_snapshot(world, parts[2])
            return _NOT_FOUND
        if len(parts) >= 3:
            world, city, rest = parts[0], parts[1], parts[2:]
            if method == "POST" and rest == ["orders", "batch"]:
                b = self._body()
                orders = b.get("orders")
                if not isinstance(orders, list):
                    raise ValueError("orders must be a list")
                return svc.submit_batch(world, city, int(b.get("tick", 0)), orders)
            if method == "GET" and rest == ["prices"]:
                return svc.prices(world, city)
            if method == "GET" and rest == ["locked"]:
                return svc.locked(world, city)
            if method == "GET" and rest == ["orders"]:
                owner = int(query["owner"]) if "owner" in query else None
                return svc.open_orders(world, city, owner)
            if method == "GET" and len(rest) == 2 and rest[0] == "book":
                return svc.book(world, city, rest[1], int(query.get("depth", 5)))
        return _NOT_FOUND

    def do_GET(self):  # noqa: N802
        self._route("GET")

    def do_POST(self):  # noqa: N802
        self._route("POST")

    def do_PUT(self):  # noqa: N802
        self._route("PUT")

    def do_DELETE(self):  # noqa: N802
        self._route("DELETE")


_NOT_FOUND = object()


def make_server(host: str = "127.0.0.1", port: int = 8080, db: str | None = "popsim_api.sqlite",
                quiet: bool = True) -> ThreadingHTTPServer:
    store = SnapshotStore(db) if db else None
    service = MarketService(store)
    handler = type("Handler", (_Handler,), {"service": service, "lock": threading.Lock(), "quiet": quiet})
    server = ThreadingHTTPServer((host, port), handler)
    server.service = service  # type: ignore[attr-defined]
    return server


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="popsim market API")
    p.add_argument("--host", default=os.environ.get("POPSIM_HOST", "127.0.0.1"))
    p.add_argument("--port", type=int, default=int(os.environ.get("POPSIM_PORT", "8080")))
    p.add_argument("--db", default=os.environ.get("POPSIM_DB", "popsim_api.sqlite"))
    p.add_argument("--verbose", action="store_true")
    a = p.parse_args(argv)
    server = make_server(a.host, a.port, a.db, quiet=not a.verbose)
    print(f"popsim market API on http://{a.host}:{a.port} (db: {a.db}); "
          f"loaded worlds: {sorted(server.service.worlds) or 'none'}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
