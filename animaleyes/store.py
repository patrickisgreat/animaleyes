"""SQLite persistence. Everything a restart must not forget lives here.

Tables: kv (state, counters), plates (which bowls are loaded), events (audit log),
llm_calls (every verdict with cost). A restart reads kv before doing anything, so a
lid that is already open is never opened again and MIN_GAP_MIN survives a crash.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

PLATES = (1, 2, 3)

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS plates (
  plate INTEGER PRIMARY KEY,
  status TEXT NOT NULL CHECK (status IN ('empty', 'loaded', 'eaten')),
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  at TEXT NOT NULL,
  kind TEXT NOT NULL,
  reason TEXT NOT NULL,
  data TEXT NOT NULL,
  frames TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS llm_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  at TEXT NOT NULL,
  purpose TEXT NOT NULL,
  model TEXT NOT NULL,
  verdict TEXT NOT NULL,
  usage TEXT NOT NULL,
  cost_usd REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS events_at ON events(at);
CREATE INDEX IF NOT EXISTS llm_calls_at ON llm_calls(at);
"""


@dataclass
class Event:
    id: int
    at: datetime
    kind: str
    reason: str
    data: dict
    frames: list[str]


class Store:
    def __init__(self, path: Path | str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            for plate in PLATES:
                self._conn.execute(
                    "INSERT OR IGNORE INTO plates VALUES (?, 'empty', ?)",
                    (plate, datetime.now().isoformat()),
                )
            self._conn.commit()

    # kv -----------------------------------------------------------------
    def get(self, key: str, default: str | None = None) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set(self, key: str, value: str | int | float | None) -> None:
        with self._lock:
            if value is None:
                self._conn.execute("DELETE FROM kv WHERE key = ?", (key,))
            else:
                self._conn.execute(
                    "INSERT INTO kv VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, str(value)),
                )
            self._conn.commit()

    def get_int(self, key: str, default: int = 0) -> int:
        value = self.get(key)
        return int(value) if value is not None else default

    def get_float(self, key: str, default: float | None = None) -> float | None:
        value = self.get(key)
        return float(value) if value is not None else default

    # plates -------------------------------------------------------------
    def plates(self) -> dict[int, str]:
        with self._lock:
            rows = self._conn.execute("SELECT plate, status FROM plates ORDER BY plate").fetchall()
        return {row["plate"]: row["status"] for row in rows}

    def set_plate(self, plate: int, status: str, now: datetime) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE plates SET status = ?, updated_at = ? WHERE plate = ?",
                (status, now.isoformat(), plate),
            )
            self._conn.commit()

    def loaded_plates(self) -> list[int]:
        return [plate for plate, status in self.plates().items() if status == "loaded"]

    # events -------------------------------------------------------------
    def add_event(
        self,
        now: datetime,
        kind: str,
        reason: str,
        data: dict | None = None,
        frames: list[str] | None = None,
    ) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO events (at, kind, reason, data, frames) VALUES (?, ?, ?, ?, ?)",
                (
                    now.isoformat(),
                    kind,
                    reason,
                    json.dumps(data or {}, default=str),
                    json.dumps(frames or []),
                ),
            )
            self._conn.commit()
            return int(cur.lastrowid or 0)

    def events(self, limit: int = 100, kinds: tuple[str, ...] | None = None) -> list[Event]:
        query = "SELECT * FROM events"
        params: tuple = ()
        if kinds:
            query += f" WHERE kind IN ({','.join('?' * len(kinds))})"
            params = kinds
        query += " ORDER BY id DESC LIMIT ?"
        with self._lock:
            rows = self._conn.execute(query, (*params, limit)).fetchall()
        return [self._event(row) for row in rows]

    def event(self, event_id: int) -> Event | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        return self._event(row) if row else None

    def last_event_at(self, kind: str) -> datetime | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT at FROM events WHERE kind = ? ORDER BY id DESC LIMIT 1", (kind,)
            ).fetchone()
        return datetime.fromisoformat(row["at"]) if row else None

    @staticmethod
    def _event(row: sqlite3.Row) -> Event:
        return Event(
            id=row["id"],
            at=datetime.fromisoformat(row["at"]),
            kind=row["kind"],
            reason=row["reason"],
            data=json.loads(row["data"]),
            frames=json.loads(row["frames"]),
        )

    # llm calls ----------------------------------------------------------
    def add_llm_call(
        self, now: datetime, purpose: str, model: str, verdict: dict, usage: dict, cost_usd: float
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO llm_calls (at, purpose, model, verdict, usage, cost_usd) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (now.isoformat(), purpose, model, json.dumps(verdict), json.dumps(usage), cost_usd),
            )
            self._conn.commit()

    def llm_totals_since(self, since: datetime) -> tuple[int, float]:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n, COALESCE(SUM(cost_usd), 0) AS usd "
                "FROM llm_calls WHERE at >= ?",
                (since.isoformat(),),
            ).fetchone()
        return int(row["n"]), float(row["usd"])

    def close(self) -> None:
        with self._lock:
            self._conn.close()
