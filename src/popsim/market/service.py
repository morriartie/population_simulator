"""Market service: owns the boards and order books of every city in every world
(REQ-1A.6). Transport-agnostic; ``server.py`` exposes it over REST and
``client.LocalMarketClient`` calls it in-process.

Isolation is by ``(world, city)``; a caller only ever addresses one city at a
time, so an NPC only sees the city it is in.

Escrow (REQ-1A.7): the simulation locks gold (bids) or goods (asks) in its ledger
before submitting. The service reports, per order, what remains locked, and per
fill the buyer's limit so the caller can refund the price improvement.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from popsim.market.orderbook import Order, OrderBook

RESERVED_CITY_NAMES = {"snapshots"}


@dataclass
class CityMarket:
    books: dict[str, OrderBook] = field(default_factory=dict)
    last_price: dict[str, int] = field(default_factory=dict)
    index: dict[int, str] = field(default_factory=dict)  # order_id -> good

    def book(self, good: str) -> OrderBook:
        if good not in self.books:
            self.books[good] = OrderBook(good)
        return self.books[good]

    def export(self) -> dict:
        return {
            "books": {g: b.export() for g, b in sorted(self.books.items())},
            "last_price": dict(sorted(self.last_price.items())),
        }

    @classmethod
    def from_state(cls, state: dict) -> "CityMarket":
        cm = cls()
        for good, bstate in state["books"].items():
            cm.books[good] = OrderBook.from_state(good, bstate)
            for o in cm.books[good].bids + cm.books[good].asks:
                cm.index[o.order_id] = good
        cm.last_price = dict(state["last_price"])
        return cm


@dataclass
class WorldMarket:
    next_order_id: int = 1
    seq: int = 0
    cities: dict[str, CityMarket] = field(default_factory=dict)

    def city(self, name: str) -> CityMarket:
        if name in RESERVED_CITY_NAMES:
            raise ValueError(f"city name {name!r} is reserved")
        if name not in self.cities:
            self.cities[name] = CityMarket()
        return self.cities[name]

    def export(self) -> dict:
        return {
            "next_order_id": self.next_order_id,
            "seq": self.seq,
            "cities": {n: c.export() for n, c in sorted(self.cities.items())},
        }

    @classmethod
    def from_state(cls, state: dict) -> "WorldMarket":
        wm = cls(next_order_id=state["next_order_id"], seq=state["seq"])
        wm.cities = {n: CityMarket.from_state(c) for n, c in state["cities"].items()}
        return wm


def _validate_place(op: dict) -> str | None:
    if op.get("side") not in ("buy", "sell"):
        return "bad_side"
    if not isinstance(op.get("good"), str) or not op["good"]:
        return "bad_good"
    for k in ("qty", "price", "owner"):
        v = op.get(k)
        if not isinstance(v, int) or isinstance(v, bool):
            return f"bad_{k}"
    if op["qty"] <= 0:
        return "bad_qty"
    if op["price"] <= 0:
        return "bad_price"
    return None


class MarketService:
    def __init__(self, store=None) -> None:
        self.worlds: dict[str, WorldMarket] = {}
        self.store = store
        if store is not None:
            for world, state in store.load_latest_all().items():
                self.worlds[world] = WorldMarket.from_state(state)

    def world(self, name: str) -> WorldMarket:
        if name not in self.worlds:
            self.worlds[name] = WorldMarket()
        return self.worlds[name]

    # -- trading ----------------------------------------------------------
    def submit_batch(self, world: str, city: str, tick: int, orders: list[dict]) -> dict:
        """Process orders in the given sequence. Each one succeeds or fails on its own."""
        wm = self.world(world)
        cm = wm.city(city)
        results: list[dict] = []
        traded: dict[str, list[int]] = {}
        for op in orders:
            kind = op.get("op")
            ref = op.get("ref")
            if kind == "cancel":
                results.append(self._cancel(cm, op) | {"op": "cancel", "ref": ref})
            elif kind == "place":
                err = _validate_place(op)
                if err:
                    results.append({"op": "place", "ref": ref, "ok": False, "error": err})
                    continue
                wm.seq += 1
                order = Order(
                    order_id=wm.next_order_id,
                    owner=op["owner"],
                    side=op["side"],
                    good=op["good"],
                    qty=op["qty"],
                    price=op["price"],
                    seq=wm.seq,
                    tick=tick,
                )
                wm.next_order_id += 1
                fills = cm.book(order.good).add(order)
                for f in fills:
                    cm.last_price[f["good"]] = f["price"]
                    t = traded.setdefault(f["good"], [0, 0])
                    t[0] += f["qty"]
                    t[1] += f["qty"] * f["price"]
                    if f["maker_remaining"] == 0:
                        cm.index.pop(f["maker_order"], None)
                if order.qty > 0:
                    cm.index[order.order_id] = order.good
                results.append(
                    {
                        "op": "place",
                        "ref": ref,
                        "ok": True,
                        "order_id": order.order_id,
                        "remaining": order.qty,
                        "fills": fills,
                    }
                )
            else:
                results.append({"op": kind, "ref": ref, "ok": False, "error": "bad_op"})
        return {
            "results": results,
            "prices": dict(sorted(cm.last_price.items())),
            "volume": {g: {"qty": v[0], "value": v[1]} for g, v in sorted(traded.items())},
            "top": self._top(cm),
        }

    @staticmethod
    def _top(cm: CityMarket) -> dict:
        """Best bid and ask per good: public board information for the city."""
        return {
            g: {"bid": b.bids[0].price if b.bids else None, "ask": b.asks[0].price if b.asks else None}
            for g, b in sorted(cm.books.items())
        }

    def _cancel(self, cm: CityMarket, op: dict) -> dict:
        oid = op.get("order_id")
        good = cm.index.get(oid)
        if good is None:
            return {"ok": False, "error": "unknown_order", "order_id": oid}
        book = cm.books[good]
        target = next((o for o in book.bids + book.asks if o.order_id == oid), None)
        if target is None:
            del cm.index[oid]
            return {"ok": False, "error": "unknown_order", "order_id": oid}
        if target.owner != op.get("owner"):
            return {"ok": False, "error": "not_owner", "order_id": oid}
        book.remove(oid)
        del cm.index[oid]
        return {
            "ok": True,
            "order_id": oid,
            "side": target.side,
            "good": good,
            "remaining": target.qty,
            "price": target.price,
        }

    # -- reads ------------------------------------------------------------
    def prices(self, world: str, city: str) -> dict:
        return dict(sorted(self.world(world).city(city).last_price.items()))

    def book(self, world: str, city: str, good: str, depth: int = 5) -> dict:
        return self.world(world).city(city).book(good).depth(depth)

    def open_orders(self, world: str, city: str, owner: int | None = None) -> list[dict]:
        cm = self.world(world).city(city)
        out = []
        for good in sorted(cm.books):
            b = cm.books[good]
            for o in b.bids + b.asks:
                if owner is None or o.owner == owner:
                    out.append({"order_id": o.order_id, "owner": o.owner, "side": o.side,
                                "good": o.good, "qty": o.qty, "price": o.price})
        return out

    def locked(self, world: str, city: str) -> dict:
        """What the city's escrow should hold: gold for bids, goods for asks."""
        cm = self.world(world).city(city)
        out: dict[str, int] = {"gold": 0}
        for good, b in cm.books.items():
            out["gold"] += sum(o.qty * o.price for o in b.bids)
            out[good] = out.get(good, 0) + sum(o.qty for o in b.asks)
        return out

    # -- state ------------------------------------------------------------
    def export_world(self, world: str) -> dict:
        return self.world(world).export()

    def import_world(self, world: str, state: dict) -> None:
        self.worlds[world] = WorldMarket.from_state(state)

    def reset_world(self, world: str) -> None:
        self.worlds.pop(world, None)

    def save_snapshot(self, world: str, name: str, tick: int) -> dict:
        if self.store is None:
            raise RuntimeError("no snapshot store configured")
        self.store.save(world, name, tick, self.export_world(world))
        return {"world": world, "name": name, "tick": tick}

    def restore_snapshot(self, world: str, name: str) -> dict:
        if self.store is None:
            raise RuntimeError("no snapshot store configured")
        snap = self.store.load(world, name)
        if snap is None:
            raise KeyError(f"no snapshot {name!r} for world {world!r}")
        self.import_world(world, snap["state"])
        return {"world": world, "name": name, "tick": snap["tick"]}

    def list_snapshots(self, world: str) -> list[dict]:
        return [] if self.store is None else self.store.list(world)
