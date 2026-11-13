"""Fail-closed durable polling checkpoints for Pionex read-only monitoring.

A checkpoint records only a fully verified reconciliation boundary. It does
not make HTTP calls, assume API cursor semantics, emit alerts or place orders.
Incomplete or failed polls leave the last checkpoint unchanged.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path


class CheckpointError(ValueError):
    pass


class PionexPollCheckpoint:
    def __init__(self, path):
        self.path = Path(path)
        if str(path) == ":memory:" or not self.path.parent.is_dir():
            raise CheckpointError("Persistent existing storage directory required")
        self._conn = sqlite3.connect(str(self.path), timeout=15)
        self._conn.execute("PRAGMA busy_timeout=15000")
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS pionex_poll_checkpoint (
                account_scope TEXT NOT NULL,
                symbol TEXT NOT NULL,
                last_verified_ms INTEGER NOT NULL,
                PRIMARY KEY (account_scope, symbol)
            )
        """)
        self._conn.commit()

    def close(self):
        self._conn.close()

    @staticmethod
    def _validate(account_scope, symbol):
        if not isinstance(account_scope, str) or not account_scope.strip():
            raise CheckpointError("Account scope required")
        if not isinstance(symbol, str) or not symbol.endswith("_USDT_PERP"):
            raise CheckpointError("Perpetual symbol required")

    def last_verified(self, account_scope, symbol):
        self._validate(account_scope, symbol)
        row = self._conn.execute(
            "SELECT last_verified_ms FROM pionex_poll_checkpoint "
            "WHERE account_scope = ? AND symbol = ?",
            (account_scope, symbol),
        ).fetchone()
        return row[0] if row else None

    def advance(self, account_scope, symbol, timestamp_ms, *,
                pages_complete=False, positions_verified=False,
                fills_reconciled=False):
        """Advance only after every required evidence gate is explicitly true.

        Timestamp is a verified reconciliation boundary, NOT the local poll
        clock. Callers must independently verify source timestamps and data
        coverage. No advance on gaps, API errors or missing history.
        """
        self._validate(account_scope, symbol)
        if (pages_complete is not True or positions_verified is not True
                or fills_reconciled is not True):
            raise CheckpointError("Unverified poll must not advance watermark")
        if type(timestamp_ms) is not int or timestamp_ms <= 0:
            raise CheckpointError("Invalid verified timestamp")
        with self._conn:
            previous = self.last_verified(account_scope, symbol)
            if previous is not None and timestamp_ms < previous:
                raise CheckpointError("Checkpoint regression")
            if previous == timestamp_ms:
                return False
            self._conn.execute(
                "INSERT INTO pionex_poll_checkpoint "
                "(account_scope, symbol, last_verified_ms) VALUES (?, ?, ?) "
                "ON CONFLICT(account_scope, symbol) DO UPDATE SET "
                "last_verified_ms=excluded.last_verified_ms",
                (account_scope, symbol, timestamp_ms),
            )
            return True
