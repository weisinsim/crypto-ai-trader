"""Atomic STAGED event + verified watermark commit in ONE SQLite database.

Offline-only. No HTTP, no notifications, no trading. The caller must
independently prove interval completeness, position identity, and execution
coverage. A watermark may be committed without an event only for a fully
verified interval with no reduction.
"""
import json
import sqlite3
from pathlib import Path

from notifications.pionex_exit_store import _REQUIRED


class AtomicCheckpointError(ValueError):
    pass


class AtomicExitCheckpointStore:
    def __init__(self, path):
        path = Path(path)
        if str(path) == ":memory:" or not path.parent.is_dir():
            raise AtomicCheckpointError("Existing persistent directory required")
        self._conn = sqlite3.connect(str(path), timeout=15, isolation_level=None)
        self._conn.execute("PRAGMA busy_timeout=15000")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS pionex_exit_staging (
                event_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state = 'STAGED'),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS pionex_poll_checkpoint (
                account_scope TEXT NOT NULL, symbol TEXT NOT NULL,
                last_verified_ms INTEGER NOT NULL,
                PRIMARY KEY(account_scope, symbol))
        """)

    def close(self):
        self._conn.close()

    def checkpoint(self, account_scope, symbol):
        row = self._conn.execute(
            "SELECT last_verified_ms FROM pionex_poll_checkpoint "
            "WHERE account_scope=? AND symbol=?", (account_scope, symbol)
        ).fetchone()
        return row[0] if row else None

    def commit_verified_interval(self, account_scope, symbol, end_ms, event=None, *,
                                 pages_complete=False, positions_verified=False,
                                 fills_reconciled=False, order_evidence_verified=False):
        if (pages_complete is not True or positions_verified is not True
                or fills_reconciled is not True or order_evidence_verified is not True):
            raise AtomicCheckpointError("Incomplete evidence; transaction blocked")
        if (not isinstance(account_scope, str) or not account_scope.strip()
                or not isinstance(symbol, str) or not symbol.endswith("_USDT_PERP")
                or type(end_ms) is not int or end_ms <= 0):
            raise AtomicCheckpointError("Invalid checkpoint identity or timestamp")
        payload = None
        if event is not None:
            if (not isinstance(event, dict) or set(event) != _REQUIRED
                    or event["symbol"] != symbol
                    or event["source"] != "PIONEX_EXECUTION"
                    or event["notification_ready"] is not False
                    or event["event_type"] not in ("FULL_EXIT", "PARTIAL_EXIT")
                    or event["classification"] not in (
                        "EXIT_REASON_UNVERIFIED", "TAKE_PROFIT", "STOP_LOSS", "MANUAL", "OTHER")
                    or not isinstance(event["event_id"], str)
                    or not event["event_id"].startswith("pionex-exit-")
                    or not isinstance(event["fill_ids"], list)
                    or not event["fill_ids"]):
                raise AtomicCheckpointError("Invalid STAGED-only event")
            payload = json.dumps(event, sort_keys=True, separators=(",", ":"), allow_nan=False)
        conn = self._conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            previous = self.checkpoint(account_scope, symbol)
            if previous is not None and end_ms < previous:
                raise AtomicCheckpointError("Checkpoint regression")
            if payload is not None:
                existing = conn.execute(
                    "SELECT payload_json FROM pionex_exit_staging WHERE event_id=?",
                    (event["event_id"],)).fetchone()
                if existing is not None and existing[0] != payload:
                    raise AtomicCheckpointError("Conflicting event ID")
                if existing is None:
                    conn.execute(
                        "INSERT INTO pionex_exit_staging(event_id,payload_json,state) "
                        "VALUES(?,?,'STAGED')", (event["event_id"], payload))
            if previous is None or end_ms > previous:
                conn.execute(
                    "INSERT INTO pionex_poll_checkpoint "
                    "(account_scope,symbol,last_verified_ms) VALUES(?,?,?) "
                    "ON CONFLICT(account_scope,symbol) DO UPDATE SET "
                    "last_verified_ms=excluded.last_verified_ms",
                    (account_scope, symbol, end_ms))
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        return {"checkpoint_ms": end_ms, "event_staged": payload is not None}
