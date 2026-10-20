"""SQLite-backed idempotent outbox for entry-alert events.

Stores notification candidates for a single-host worker. It does not deliver
messages, execute trades, or treat an unapproved strategy as production-ready.
"""
from __future__ import annotations

import json
import math
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


class EntryAlertStore:
    def __init__(self, path):
        self.path = str(path)
        self._memory_conn = None
        if self.path == ":memory:":
            self._memory_conn = sqlite3.connect(":memory:", timeout=10)
            self._memory_conn.row_factory = sqlite3.Row
        else:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS entry_alert_outbox (
                    event_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    eligible INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    delivered_at REAL
                )
            """)

    @contextmanager
    def _connect(self):
        """Commit/rollback each operation and close file-backed connections.

        The in-memory connection is intentionally kept open for the store's
        lifetime so successive operations share the same SQLite database.
        """
        if self._memory_conn is not None:
            with self._memory_conn:
                yield self._memory_conn
            return
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def enqueue(self, event, *, now=None):
        """Persist one eligible alert once; returns False for duplicate/invalid events."""
        if not isinstance(event, dict) or event.get("eligible") is not True:
            return False
        event_id = event.get("event_id")
        symbol = event.get("symbol")
        side = event.get("side")
        if not all(isinstance(x, str) and x for x in (event_id, symbol, side)):
            return False
        # The outbox is strictly for human-facing alerts, never an execution queue.
        if (
            side not in ("LONG", "SHORT")
            or event.get("notification_only") is not True
            or event.get("auto_trade_authorized") is not False
        ):
            return False
        try:
            payload = json.dumps(event, sort_keys=True, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError, OverflowError):
            return False
        try:
            if isinstance(now, bool):
                return False
            created_at = time.time() if now is None else float(now)
        except (TypeError, ValueError, OverflowError):
            return False
        if not math.isfinite(created_at) or created_at <= 0:
            return False
        with self._connect() as conn:
            cursor = conn.execute(
                """INSERT OR IGNORE INTO entry_alert_outbox
                   (event_id, symbol, side, eligible, payload_json, status, created_at)
                   VALUES (?, ?, ?, 1, ?, 'PENDING', ?)""",
                (event_id, symbol, side, payload, created_at),
            )
            if cursor.rowcount == 1:
                return True
            existing = conn.execute(
                "SELECT symbol, side, payload_json FROM entry_alert_outbox WHERE event_id=?",
                (event_id,),
            ).fetchone()
            if (existing is None or existing["symbol"] != symbol
                    or existing["side"] != side or existing["payload_json"] != payload):
                raise ValueError("entry_alert_event_id_payload_conflict")
            return False

    def pending(self, limit=100):
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValueError("limit must be a positive integer")
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT event_id, payload_json, created_at FROM entry_alert_outbox
                   WHERE status='PENDING' ORDER BY created_at, event_id LIMIT ?""",
                (limit,),
            ).fetchall()
        return [
            {"event_id": row["event_id"], "event": json.loads(row["payload_json"]),
             "created_at": row["created_at"]}
            for row in rows
        ]

    def mark_delivered(self, event_id, *, now=None):
        try:
            if isinstance(now, bool):
                return False
            delivered_at = time.time() if now is None else float(now)
        except (TypeError, ValueError, OverflowError):
            return False
        if not math.isfinite(delivered_at) or delivered_at <= 0:
            return False
        with self._connect() as conn:
            cursor = conn.execute(
                """UPDATE entry_alert_outbox SET status='DELIVERED', delivered_at=?
                   WHERE event_id=? AND status='PENDING' AND created_at <= ?""",
                (delivered_at, event_id, delivered_at),
            )
            return cursor.rowcount == 1
