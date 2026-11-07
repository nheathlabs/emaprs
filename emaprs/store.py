# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Nicholas Heath
"""SQLite mirror of the per-station packet history, so a restart resumes.

One row per packet held in a station's history. The Hub writes every change
through as it happens (new packet, packet dropped by the per-station cap,
expiry, killed object), so the file always matches memory. On startup the
Hub reloads it and drops anything already past expiry.

Write errors are logged, never raised: losing persistence must not stop
packets reaching the map.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1


class Store:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.db = self._open()
        except sqlite3.OperationalError:
            raise  # can't open / locked / read-only: a setup problem, not corruption
        except sqlite3.DatabaseError as e:
            # Corrupt or not a database: keep it for inspection, start fresh.
            aside = self.path.with_name(f"{self.path.name}.bad-{int(time.time())}")
            log.error("database %s unreadable (%s); moved to %s, starting empty",
                      self.path, e, aside)
            self.path.rename(aside)
            for ext in ("-wal", "-shm"):
                Path(f"{self.path}{ext}").unlink(missing_ok=True)
            self.db = self._open()

    def _open(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path)
        try:
            # WAL + NORMAL: each commit survives a crash; a power cut can lose
            # at most the last few commits, never corrupt the file.
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=NORMAL")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, SCHEMA_VERSION):
                raise sqlite3.DatabaseError(f"unknown schema version {version}")
            db.execute(
                """CREATE TABLE IF NOT EXISTS packets (
                       id   INTEGER PRIMARY KEY,  -- Hub packet id (arrival order)
                       name TEXT NOT NULL,        -- station / object name
                       ts   REAL NOT NULL,        -- unix time received
                       data TEXT NOT NULL         -- the packet dict as JSON
                   )"""
            )
            db.execute("CREATE INDEX IF NOT EXISTS packets_ts ON packets (ts)")
            db.execute("CREATE INDEX IF NOT EXISTS packets_name ON packets (name)")
            db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            db.commit()
        except sqlite3.DatabaseError:
            db.close()
            raise
        return db

    def _write(self, sql: str, args=()) -> None:
        try:
            with self.db:
                if isinstance(args, list):
                    self.db.executemany(sql, args)
                else:
                    self.db.execute(sql, args)
        except sqlite3.Error as e:
            log.error("database write failed: %s", e)

    def load(self, cutoff: float | None) -> list[dict]:
        """Delete packets older than cutoff, return the rest oldest first."""
        if cutoff is not None:
            self._write("DELETE FROM packets WHERE ts < ?", (cutoff,))
        rows = self.db.execute("SELECT data FROM packets ORDER BY id").fetchall()
        return [json.loads(r[0]) for r in rows]

    def add(self, pkt: dict) -> None:
        self._write(
            "INSERT OR REPLACE INTO packets (id, name, ts, data) VALUES (?, ?, ?, ?)",
            (pkt["id"], pkt["name"], pkt["ts"], json.dumps(pkt)),
        )

    def delete_ids(self, ids: list[int]) -> None:
        if ids:
            self._write("DELETE FROM packets WHERE id = ?", [(i,) for i in ids])

    def delete_station(self, name: str) -> None:
        self._write("DELETE FROM packets WHERE name = ?", (name,))

    def expire(self, cutoff: float) -> None:
        self._write("DELETE FROM packets WHERE ts < ?", (cutoff,))

    def close(self) -> None:
        self.db.close()
