"""Durable local runtime risk state for paper/TESTNET execution only.

Uses SQLite transactions so risk state survives process restarts. Account exposure
must still be refreshed from the exchange; this store does not invent positions
or provide cross-host coordination.
"""
from datetime import datetime, timezone
import math
import sqlite3
from pathlib import Path
from typing import Optional

from execution.runtime_guard import RuntimeSnapshot


class RuntimeStateStore:
    def __init__(self, database_path):
        self.database_path = str(database_path)
        if self.database_path != ":memory:":
            Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def _initialize(self):
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS runtime_metrics (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    open_positions INTEGER NOT NULL,
                    symbol_notional_usdt REAL NOT NULL,
                    total_notional_usdt REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS realized_pnl (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    day_utc TEXT NOT NULL,
                    amount_usdt REAL NOT NULL,
                    created_at REAL NOT NULL
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS realized_pnl_events (
                    event_id TEXT PRIMARY KEY,
                    day_utc TEXT NOT NULL,
                    amount_usdt REAL NOT NULL,
                    created_at REAL NOT NULL
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS runtime_control (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    emergency_stop INTEGER NOT NULL,
                    consecutive_errors INTEGER NOT NULL,
                    updated_at REAL NOT NULL
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS order_attempts (
                    client_order_id TEXT PRIMARY KEY,
                    intent_fingerprint TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS entry_lock (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    token TEXT NOT NULL,
                    acquired_at REAL NOT NULL
                )
            """)
            db.execute(
                "INSERT OR IGNORE INTO runtime_control(id, emergency_stop, consecutive_errors, updated_at) VALUES(1, 0, 0, ?)",
                (datetime.now(timezone.utc).timestamp(),),
            )

    @staticmethod
    def _finite_number(value, field):
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{field} must be finite numeric") from exc
        if not math.isfinite(number):
            raise ValueError(f"{field} must be finite numeric")
        return number

    def update_exchange_metrics(self, open_positions, symbol_notional_usdt, total_notional_usdt, now_epoch=None):
        """Persist metrics sourced from a successful authoritative account snapshot."""
        if isinstance(open_positions, bool) or not isinstance(open_positions, int) or open_positions < 0:
            raise ValueError("open_positions must be a non-negative integer")
        symbol_value = self._finite_number(symbol_notional_usdt, "symbol_notional_usdt")
        total_value = self._finite_number(total_notional_usdt, "total_notional_usdt")
        if symbol_value < 0 or total_value < 0:
            raise ValueError("notional metrics must be non-negative")
        timestamp = self._finite_number(
            datetime.now(timezone.utc).timestamp() if now_epoch is None else now_epoch,
            "now_epoch",
        )
        with self._connect() as db:
            db.execute("""
                INSERT INTO runtime_metrics(id, open_positions, symbol_notional_usdt, total_notional_usdt, updated_at)
                VALUES(1, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    open_positions=excluded.open_positions,
                    symbol_notional_usdt=excluded.symbol_notional_usdt,
                    total_notional_usdt=excluded.total_notional_usdt,
                    updated_at=excluded.updated_at
            """, (open_positions, symbol_value, total_value, timestamp))

    def record_realized_pnl(self, amount_usdt, now=None, event_id=None):
        """Record realized PnL once per exchange fill/event when event_id is supplied.

        Legacy calls without an event ID remain append-only; live/testnet fill
        ingestion should always pass a stable exchange trade/fill identifier.
        Returns True for a newly recorded event and False for a duplicate ID.
        """
        amount = self._finite_number(amount_usdt, "amount_usdt")
        instant = now or datetime.now(timezone.utc)
        if instant.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        instant = instant.astimezone(timezone.utc)
        day, timestamp = instant.date().isoformat(), instant.timestamp()
        if event_id is not None:
            if not isinstance(event_id, str) or not event_id.strip() or len(event_id) > 200:
                raise ValueError("invalid_realized_pnl_event_id")
            with self._connect() as db:
                cursor = db.execute(
                    "INSERT OR IGNORE INTO realized_pnl_events(event_id, day_utc, amount_usdt, created_at) VALUES(?, ?, ?, ?)",
                    (event_id, day, amount, timestamp),
                )
                return cursor.rowcount == 1
        with self._connect() as db:
            db.execute(
                "INSERT INTO realized_pnl(day_utc, amount_usdt, created_at) VALUES(?, ?, ?)",
                (day, amount, timestamp),
            )
        return True

    def record_execution_result(self, success):
        if not isinstance(success, bool):
            raise ValueError("success must be boolean")
        with self._connect() as db:
            if success:
                db.execute("UPDATE runtime_control SET consecutive_errors=0, updated_at=? WHERE id=1",
                           (datetime.now(timezone.utc).timestamp(),))
            else:
                db.execute("UPDATE runtime_control SET consecutive_errors=consecutive_errors+1, updated_at=? WHERE id=1",
                           (datetime.now(timezone.utc).timestamp(),))

    def set_emergency_stop(self, enabled):
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be boolean")
        with self._connect() as db:
            db.execute("UPDATE runtime_control SET emergency_stop=?, updated_at=? WHERE id=1",
                       (int(enabled), datetime.now(timezone.utc).timestamp()))



    def claim_entry_lock(self, token):
        """Acquire a single-host SQLite entry lock; a crashed holder fails closed."""
        if not isinstance(token, str) or not token or len(token) > 36:
            raise ValueError("invalid_entry_lock_token")
        with self._connect() as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO entry_lock(id, token, acquired_at) VALUES(1, ?, ?)",
                (token, datetime.now(timezone.utc).timestamp()),
            )
            return cursor.rowcount == 1

    def release_entry_lock(self, token):
        """Release only the lock held by this exact token; never clear another holder."""
        if not isinstance(token, str) or not token:
            raise ValueError("invalid_entry_lock_token")
        with self._connect() as db:
            cursor = db.execute("DELETE FROM entry_lock WHERE id=1 AND token=?", (token,))
            return cursor.rowcount == 1

    def get_entry_lock(self):
        with self._connect() as db:
            row = db.execute("SELECT token, acquired_at FROM entry_lock WHERE id=1").fetchone()
        return None if row is None else {"token": row[0], "acquired_at": row[1]}

    def claim_order_attempt(self, client_order_id, intent_fingerprint):
        """Atomically reserve an order ID once; never auto-reuse after restart/ambiguity."""
        if not isinstance(client_order_id, str) or not client_order_id or len(client_order_id) > 36:
            raise ValueError("invalid_client_order_id")
        if not isinstance(intent_fingerprint, str) or len(intent_fingerprint) != 64:
            raise ValueError("invalid_intent_fingerprint")
        try:
            int(intent_fingerprint, 16)
        except ValueError as exc:
            raise ValueError("invalid_intent_fingerprint") from exc
        with self._connect() as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO order_attempts(client_order_id, intent_fingerprint, status, created_at) VALUES(?, ?, 'CLAIMED', ?)",
                (client_order_id, intent_fingerprint, datetime.now(timezone.utc).timestamp()),
            )
            return cursor.rowcount == 1

    def update_order_attempt(self, client_order_id, status):
        """Record outcome metadata; status changes never release the unique client ID."""
        if not isinstance(client_order_id, str) or not client_order_id:
            raise ValueError("invalid_client_order_id")
        if status not in {"CLAIMED", "REJECTED", "SUBMITTED", "RECONCILIATION_REQUIRED", "COMPLETED"}:
            raise ValueError("invalid_order_attempt_status")
        with self._connect() as db:
            cursor = db.execute(
                "UPDATE order_attempts SET status=? WHERE client_order_id=?",
                (status, client_order_id),
            )
            return cursor.rowcount == 1

    def get_order_attempt(self, client_order_id):
        with self._connect() as db:
            row = db.execute(
                "SELECT client_order_id, intent_fingerprint, status, created_at FROM order_attempts WHERE client_order_id=?",
                (client_order_id,),
            ).fetchone()
        if row is None:
            return None
        return {"client_order_id": row[0], "intent_fingerprint": row[1],
                "status": row[2], "created_at": row[3]}

    def load_snapshot(self, now_epoch=None) -> Optional[RuntimeSnapshot]:
        now = self._finite_number(
            datetime.now(timezone.utc).timestamp() if now_epoch is None else now_epoch,
            "now_epoch",
        )
        today = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
        with self._connect() as db:
            metrics = db.execute(
                "SELECT open_positions, symbol_notional_usdt, total_notional_usdt, updated_at FROM runtime_metrics WHERE id=1"
            ).fetchone()
            control = db.execute(
                "SELECT emergency_stop, consecutive_errors FROM runtime_control WHERE id=1"
            ).fetchone()
            pnl = db.execute(
                """SELECT
                    (SELECT COALESCE(SUM(amount_usdt), 0) FROM realized_pnl WHERE day_utc=?) +
                    (SELECT COALESCE(SUM(amount_usdt), 0) FROM realized_pnl_events WHERE day_utc=?)""",
                (today, today),
            ).fetchone()[0]
        # Missing exchange state is unsafe: caller receives None and must block.
        if metrics is None or control is None:
            return None
        age = max(0.0, now - float(metrics[3]))
        return RuntimeSnapshot(
            daily_realized_pnl_usdt=float(pnl),
            open_positions=int(metrics[0]),
            symbol_notional_usdt=float(metrics[1]),
            total_notional_usdt=float(metrics[2]),
            consecutive_execution_errors=int(control[1]),
            emergency_stop=bool(control[0]),
            snapshot_age_seconds=age,
        )
