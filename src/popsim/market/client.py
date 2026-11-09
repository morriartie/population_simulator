"""Market clients. The simulation only talks to the market through this interface,
so the in-process engine and the REST service are interchangeable."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Protocol

from popsim.market.service import MarketService


class MarketClient(Protocol):
    def submit_batch(self, world: str, city: str, tick: int, orders: list[dict]) -> dict: ...
    def prices(self, world: str, city: str) -> dict: ...
    def book(self, world: str, city: str, good: str, depth: int = 5) -> dict: ...
    def locked(self, world: str, city: str) -> dict: ...
    def reset_world(self, world: str) -> None: ...
    def save_snapshot(self, world: str, name: str, tick: int) -> dict: ...
    def restore_snapshot(self, world: str, name: str) -> dict: ...
    def export_world(self, world: str) -> dict: ...
    def import_world(self, world: str, state: dict) -> None: ...

    embedded: bool  # True when market state should travel inside sim snapshots


class LocalMarketClient:
    """Calls a MarketService in the same process.

    With ``json_roundtrip=True`` every request/response goes through JSON, which
    reproduces the REST wire format exactly (useful for parity tests).
    """

    embedded = True

    def __init__(self, service: MarketService | None = None, json_roundtrip: bool = False) -> None:
        self.service = service or MarketService()
        self.json_roundtrip = json_roundtrip

    def _rt(self, obj):
        return json.loads(json.dumps(obj)) if self.json_roundtrip else obj

    def submit_batch(self, world, city, tick, orders):
        return self._rt(self.service.submit_batch(world, city, tick, self._rt(orders)))

    def prices(self, world, city):
        return self._rt(self.service.prices(world, city))

    def book(self, world, city, good, depth=5):
        return self._rt(self.service.book(world, city, good, depth))

    def locked(self, world, city):
        return self._rt(self.service.locked(world, city))

    def reset_world(self, world):
        self.service.reset_world(world)

    def save_snapshot(self, world, name, tick):
        return self.service.save_snapshot(world, name, tick)

    def restore_snapshot(self, world, name):
        return self.service.restore_snapshot(world, name)

    def export_world(self, world):
        return self._rt(self.service.export_world(world))

    def import_world(self, world, state):
        self.service.import_world(world, self._rt(state))


class MarketAPIError(RuntimeError):
    pass


class HttpMarketClient:
    """Talks to ``popsim serve`` over REST. Snapshots are stored by the API (SQLite)."""

    embedded = False

    def __init__(self, base_url: str, timeout: float = 30.0) -> None:
        self.base = base_url.rstrip("/")
        self.timeout = timeout
        # Never route local API calls through an environment proxy.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _call(self, method: str, path: str, body=None, query: dict | None = None):
        url = self.base + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with self._opener.open(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            raise MarketAPIError(f"{method} {path}: {e.code} {e.read().decode(errors='replace')}") from e
        return json.loads(raw) if raw else None

    @staticmethod
    def _q(s: str) -> str:
        return urllib.parse.quote(s, safe="")

    def health(self):
        return self._call("GET", "/health")

    def submit_batch(self, world, city, tick, orders):
        return self._call("POST", f"/{self._q(world)}/{self._q(city)}/orders/batch",
                          {"tick": tick, "orders": orders})

    def prices(self, world, city):
        return self._call("GET", f"/{self._q(world)}/{self._q(city)}/prices")

    def book(self, world, city, good, depth=5):
        return self._call("GET", f"/{self._q(world)}/{self._q(city)}/book/{self._q(good)}",
                          query={"depth": depth})

    def locked(self, world, city):
        return self._call("GET", f"/{self._q(world)}/{self._q(city)}/locked")

    def reset_world(self, world):
        self._call("DELETE", f"/{self._q(world)}")

    def save_snapshot(self, world, name, tick):
        return self._call("POST", f"/{self._q(world)}/snapshots", {"name": name, "tick": tick})

    def restore_snapshot(self, world, name):
        return self._call("POST", f"/{self._q(world)}/snapshots/{self._q(name)}/restore", {})

    def export_world(self, world):
        return self._call("GET", f"/{self._q(world)}/state")

    def import_world(self, world, state):
        self._call("PUT", f"/{self._q(world)}/state", state)
