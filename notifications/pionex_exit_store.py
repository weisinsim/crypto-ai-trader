"""Local durable, fail-closed staging for confirmed exchange exit evidence.

This module only stores reconciliation events. It does not deliver alerts, make
HTTP requests, or execute orders. Delivery requires a separate reviewed gate.
"""
import json
import sqlite3
from pathlib import Path


class ExitStoreError(ValueError):
    pass


_REQUIRED = frozenset({
    "event_id", "source", "symbol", "position_side", "event_type",
    "closed_quantity", "remaining_quantity", "fill_ids",
    "classification", "notification_ready",
})


class PionexExitStore:
    def __init__(self, path):
        self.path = Path(path)
        if str(path) == ":memory:":
            raise ExitStoreError("Persistent storage required")
        if not self.path.parent.is_dir():
            raise ExitStoreError("Storage directory must exist")
        self._conn = sqlite3.connect(str(self.path), timeout=15)
        self._conn.execute("PRAGMA busy_timeout=15000")
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS pionex_exit_staging (
                event_id TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                state TEXT NOT NULL CHECK (state = 'STAGED'),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self._conn.commit()

    def close(self):
        self._conn.close()

    def stage(self, event):
        if not isinstance(event, dict) or set(event) != _REQUIRED:
            raise ExitStoreError("Invalid exit event contract")
        if (event["source"] != "PIONEX_EXECUTION"
                or event["notification_ready"] is not False
                or event["classification"] not in (\n                    "EXIT_REASON_UNVERIFIED", "TAKE_PROFIT", "STOP_LOSS",\n                    "MANUAL", "OTHER")
                or event["event_type"] not in ("FULL_EXIT", "PARTIAL_EXIT")
                or not isinstance(event["event_id"], str)
                or not event["event_id"].startswith("pionex-exit-")):
            raise ExitStoreError("Exit event is not eligible for safe staging")
        if not isinstance(event["fill_ids"], list) or not event["fill_ids"]:
            raise ExitStoreError("Missing exchange fill IDs")
        payload = json.dumps(event, sort_keys=True, separators=(",", ":"), allow_nan=False)
        with self._conn:
            inserted = self._conn.execute(
                "INSERT OR IGNORE INTO pionex_exit_staging "
                "(event_id, payload_json, state) VALUES (?, ?, 'STAGED')",
                (event["event_id"], payload),
            ).rowcount
            if not inserted:
                existing = self._conn.execute(
                    "SELECT payload_json FROM pionex_exit_staging WHERE event_id = ?",
                    (event["event_id"],),
                ).fetchone()
                if existing is None or existing[0] != payload:
                    raise ExitStoreError("Event ID collision with different evidence")
        return bool(inserted)

    def staged(self):
        return [json.loads(row[0]) for row in self._conn.execute(
            "SELECT payload_json FROM pionex_exit_staging ORDER BY created_at, event_id"
        ).fetchall()]
