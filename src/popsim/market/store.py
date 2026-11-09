"""SQLite persistence for market snapshots (REQ-1A.6 storage)."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    world TEXT NOT NULL,
    name TEXT NOT NULL,
    tick INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    state TEXT NOT NULL,
    UNIQUE(world, name)
);
"""


class SnapshotStore:
    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def save(self, world: str, name: str, tick: int, state: dict) -> None:
        with self._lock:
            # Re-saving a name replaces it and makes it the latest snapshot.
            self._conn.execute("DELETE FROM snapshots WHERE world=? AND name=?", (world, name))
            self._conn.execute(
                "INSERT INTO snapshots(world, name, tick, created_at, state) VALUES (?,?,?,?,?)",
                (world, name, tick, datetime.now(timezone.utc).isoformat(), json.dumps(state)),
            )
            self._conn.commit()

    def load(self, world: str, name: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT tick, state FROM snapshots WHERE world=? AND name=?", (world, name)
            ).fetchone()
        return None if row is None else {"tick": row[0], "state": json.loads(row[1])}

    def list(self, world: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT name, tick, created_at FROM snapshots WHERE world=? ORDER BY id", (world,)
            ).fetchall()
        return [{"name": r[0], "tick": r[1], "created_at": r[2]} for r in rows]

    def load_latest_all(self) -> dict[str, dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT world, state FROM snapshots WHERE id IN "
                "(SELECT MAX(id) FROM snapshots GROUP BY world)"
            ).fetchall()
        return {w: json.loads(s) for w, s in rows}

    def close(self) -> None:
        self._conn.close()
