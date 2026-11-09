"""Minimal Entity-Component-System core (REQ-1.3, REQ-1.4, REQ-1A.1).

Entities are integer IDs. Components are plain dataclasses stored in one dict per
component type, keyed by entity ID. Systems live in ``popsim.systems`` and hold all
logic. Every query returns entities in ascending ID order, so iteration is
deterministic (REQ-1.8).
"""

from __future__ import annotations

from typing import Any, Iterator, TypeVar

T = TypeVar("T")


class World:
    def __init__(self) -> None:
        self.next_id: int = 1
        self.stores: dict[type, dict[int, Any]] = {}
        self.entities: dict[int, None] = {}  # insertion-ordered set of live IDs

    # -- entities ---------------------------------------------------------
    def create(self, *components: Any) -> int:
        eid = self.next_id
        self.next_id += 1
        self.entities[eid] = None
        for comp in components:
            self.add(eid, comp)
        return eid

    def destroy(self, eid: int) -> None:
        for store in self.stores.values():
            store.pop(eid, None)
        self.entities.pop(eid, None)

    def alive(self, eid: int) -> bool:
        return eid in self.entities

    # -- components -------------------------------------------------------
    def add(self, eid: int, comp: Any) -> None:
        if eid not in self.entities:
            raise KeyError(f"entity {eid} does not exist")
        self.stores.setdefault(type(comp), {})[eid] = comp

    def remove(self, eid: int, ctype: type) -> None:
        self.stores.get(ctype, {}).pop(eid, None)

    def get(self, eid: int, ctype: type[T]) -> T | None:
        return self.stores.get(ctype, {}).get(eid)

    def has(self, eid: int, ctype: type) -> bool:
        return eid in self.stores.get(ctype, {})

    def ids(self, ctype: type) -> list[int]:
        return sorted(self.stores.get(ctype, {}))

    def query(self, *ctypes: type) -> Iterator[tuple[int, tuple[Any, ...]]]:
        """Yield ``(eid, (comp1, comp2, ...))`` for entities having every type."""
        stores = [self.stores.get(t, {}) for t in ctypes]
        if not stores:
            return
        smallest = min(stores, key=len)
        for eid in sorted(smallest):
            if all(eid in s for s in stores):
                yield eid, tuple(s[eid] for s in stores)

    def count(self, ctype: type) -> int:
        return len(self.stores.get(ctype, {}))
