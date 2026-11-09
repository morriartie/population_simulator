"""Continuous double auction order book (REQ-4.4).

Price-time priority. An incoming order trades immediately against resting orders
on the other side, at the resting (maker) order's price; any remainder rests.
Prices are integer cents and quantities integer units (REQ-1A.4).
"""

from __future__ import annotations

import bisect
from dataclasses import asdict, dataclass


@dataclass
class Order:
    order_id: int
    owner: int
    side: str  # "buy" | "sell"
    good: str
    qty: int
    price: int
    seq: int
    tick: int


def _bid_key(o: Order) -> tuple[int, int]:
    return (-o.price, o.seq)


def _ask_key(o: Order) -> tuple[int, int]:
    return (o.price, o.seq)


class OrderBook:
    def __init__(self, good: str) -> None:
        self.good = good
        self.bids: list[Order] = []  # best (highest) first
        self.asks: list[Order] = []  # best (lowest) first

    def add(self, order: Order) -> list[dict]:
        """Match ``order`` against the book; rest any remainder. Returns fills."""
        fills: list[dict] = []
        if order.side == "buy":
            book, crosses = self.asks, (lambda maker: maker.price <= order.price)
        else:
            book, crosses = self.bids, (lambda maker: maker.price >= order.price)
        while order.qty > 0 and book and crosses(book[0]):
            maker = book[0]
            q = min(order.qty, maker.qty)
            buy, sell = (order, maker) if order.side == "buy" else (maker, order)
            fills.append(
                {
                    "good": self.good,
                    "qty": q,
                    "price": maker.price,
                    "buy_order": buy.order_id,
                    "sell_order": sell.order_id,
                    "buyer": buy.owner,
                    "seller": sell.owner,
                    "buy_limit": buy.price,
                    "maker_order": maker.order_id,
                    "taker_order": order.order_id,
                }
            )
            order.qty -= q
            maker.qty -= q
            fills[-1]["maker_remaining"] = maker.qty
            if maker.qty == 0:
                book.pop(0)
        if order.qty > 0:
            if order.side == "buy":
                bisect.insort(self.bids, order, key=_bid_key)
            else:
                bisect.insort(self.asks, order, key=_ask_key)
        return fills

    def remove(self, order_id: int) -> Order | None:
        for book in (self.bids, self.asks):
            for i, o in enumerate(book):
                if o.order_id == order_id:
                    return book.pop(i)
        return None

    def depth(self, n: int = 5) -> dict:
        return {
            "bids": [[o.price, o.qty] for o in self.bids[:n]],
            "asks": [[o.price, o.qty] for o in self.asks[:n]],
        }

    def export(self) -> dict:
        return {"bids": [asdict(o) for o in self.bids], "asks": [asdict(o) for o in self.asks]}

    @classmethod
    def from_state(cls, good: str, state: dict) -> "OrderBook":
        book = cls(good)
        book.bids = [Order(**o) for o in state["bids"]]
        book.asks = [Order(**o) for o in state["asks"]]
        return book
