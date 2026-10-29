"""Durable, cross-process risk reservations for testnet entry orders.

This ledger is a local safety primitive, not a transaction spanning SQLite and
an exchange. Callers must retain a reservation through ambiguous submission
outcomes and may externalize it only after an authoritative exchange snapshot
confirms the order is represented in the external exposure calculation.
"""
from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
import os
from pathlib import Path
import sqlite3
import time


_ACTIVE_STATES = ("RESERVED", "SUBMITTING", "UNKNOWN")
_ALLOWED_STATES = {"RESERVED", "SUBMITTING", "UNKNOWN", "EXTERNALIZED", "RELEASED"}


class RiskLedgerError(RuntimeError):
    """Raised when durable risk state cannot safely be read or updated."""


def _decimal(value, field):
    if isinstance(value, bool):
        raise RiskLedgerError(f"invalid_{field}")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise RiskLedgerError(f"invalid_{field}") from exc
    if not result.is_finite() or result < 0:
        raise RiskLedgerError(f"invalid_{field}")
    return result


class SQLiteRiskLedger:
    """SQLite-backed cross-process reservation ledger.

    Use a stable absolute path on durable storage shared by every process that
    can submit entries for the same account. A per-process temporary path does
    not provide cross-process protection. Never delete/reset the database as a
    shortcut to recover an ambiguous order.
    """

    def __init__(self, path, *, timeout_seconds=5.0):
        if not isinstance(path, (str, os.PathLike)) or not str(path).strip():
            raise RiskLedgerError("risk_ledger_path_required")
        self.path = str(Path(path).expanduser().resolve())
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
            raise RiskLedgerError("invalid_timeout")
        self.timeout_seconds = float(timeout_seconds)
        parent = Path(self.path).parent
        try:
            parent.mkdir(parents=True, exist_ok=True)
            with self._connection() as connection:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA synchronous=FULL")
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS risk_reservations (
                        order_id TEXT PRIMARY KEY,
                        notional_usdt TEXT NOT NULL,
                        state TEXT NOT NULL CHECK (
                            state IN ('RESERVED','SUBMITTING','UNKNOWN','EXTERNALIZED','RELEASED')
                        ),
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL,
                        detail TEXT NOT NULL DEFAULT ''
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS entry_control (
                        singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
                        kill_switch_active INTEGER NOT NULL CHECK (kill_switch_active IN (0, 1)),
                        reason TEXT NOT NULL,
                        updated_at REAL NOT NULL
                    )
                    """
                )
                # A fresh ledger starts with the kill switch engaged. An
                # operator must explicitly clear it after checking readiness.
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS recovery_audit (
                        audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        created_at REAL NOT NULL,
                        order_id TEXT NOT NULL,
                        symbol TEXT NOT NULL DEFAULT '',
                        state_before TEXT NOT NULL DEFAULT '',
                        outcome TEXT NOT NULL,
                        reason TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    """INSERT OR IGNORE INTO entry_control
                    (singleton_id, kill_switch_active, reason, updated_at)
                    VALUES (1, 1, 'default_fail_closed', ?)""",
                    (time.time(),),
                )
        except (sqlite3.Error, OSError) as exc:
            raise RiskLedgerError("risk_ledger_initialization_failed") from exc

    def _connect(self):
        connection = sqlite3.connect(
            self.path, timeout=self.timeout_seconds, isolation_level=None
        )
        connection.execute(f"PRAGMA busy_timeout={int(self.timeout_seconds * 1000)}")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    @staticmethod
    def _validate_order_id(order_id):
        if not isinstance(order_id, str) or not order_id.strip() or len(order_id) > 128:
            raise RiskLedgerError("invalid_order_id")
        return order_id

    def kill_switch_status(self):
        """Read the persistent entry kill switch; database errors fail closed."""
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "SELECT kill_switch_active, reason, updated_at FROM entry_control WHERE singleton_id = 1"
                ).fetchone()
        except (sqlite3.Error, OSError) as exc:
            raise RiskLedgerError("kill_switch_state_unavailable") from exc
        if row is None or row[0] not in (0, 1):
            raise RiskLedgerError("kill_switch_state_invalid")
        return {"active": row[0] == 1, "reason": row[1], "updated_at": row[2]}

    def set_kill_switch(self, active, *, reason):
        """Persist operator-controlled entry stop/start state.

        Activating is always permitted. Deactivating requires a non-empty
        operator-supplied reason to create an auditable record in the ledger.
        """
        if not isinstance(active, bool):
            raise RiskLedgerError("invalid_kill_switch_value")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 500:
            raise RiskLedgerError("invalid_kill_switch_reason")
        now = time.time()
        try:
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """INSERT INTO entry_control
                    (singleton_id, kill_switch_active, reason, updated_at)
                    VALUES (1, ?, ?, ?)
                    ON CONFLICT(singleton_id) DO UPDATE SET
                    kill_switch_active = excluded.kill_switch_active,
                    reason = excluded.reason,
                    updated_at = excluded.updated_at""",
                    (1 if active else 0, reason.strip(), now),
                )
                connection.execute("COMMIT")
        except (sqlite3.Error, OSError) as exc:
            raise RiskLedgerError("kill_switch_update_failed") from exc
        return {"active": active, "reason": reason.strip(), "updated_at": now}

    def reserve(self, order_id, notional_usdt, external_exposure_usdt, max_account_notional_usdt):
        """Atomically reserve exposure, counting every unresolved reservation.

        external_exposure_usdt must be an authoritative account snapshot that
        excludes the unresolved reservations still counted in this ledger.
        The caller must not assume an order was rejected merely because the
        request timed out.
        """
        order_id = self._validate_order_id(order_id)
        notional = _decimal(notional_usdt, "notional")
        external = _decimal(external_exposure_usdt, "external_exposure")
        cap = _decimal(max_account_notional_usdt, "account_cap")
        if notional <= 0 or cap <= 0:
            raise RiskLedgerError("non_positive_reservation_or_cap")
        now = time.time()
        try:
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                control = connection.execute(
                    "SELECT kill_switch_active FROM entry_control WHERE singleton_id = 1"
                ).fetchone()
                if control is None or control[0] != 0:
                    connection.execute("ROLLBACK")
                    return {"ok": False, "reason": "operator_kill_switch_active_or_unavailable"}
                existing = connection.execute(
                    "SELECT notional_usdt, state FROM risk_reservations WHERE order_id = ?",
                    (order_id,),
                ).fetchone()
                if existing:
                    existing_notional, state = existing
                    if Decimal(existing_notional) != notional:
                        connection.execute("ROLLBACK")
                        return {"ok": False, "reason": "reservation_id_conflict", "state": state}
                    if state in _ACTIVE_STATES:
                        connection.execute("COMMIT")
                        return {"ok": True, "reason": "existing_reservation", "state": state}
                    connection.execute("ROLLBACK")
                    return {"ok": False, "reason": "reservation_not_reusable", "state": state}
                placeholders = ",".join("?" for _ in _ACTIVE_STATES)
                rows = connection.execute(
                    f"SELECT notional_usdt FROM risk_reservations WHERE state IN ({placeholders})",
                    _ACTIVE_STATES,
                ).fetchall()
                reserved = sum((Decimal(row[0]) for row in rows), Decimal("0"))
                projected = external + reserved + notional
                if projected > cap:
                    connection.execute("ROLLBACK")
                    return {
                        "ok": False,
                        "reason": "account_notional_limit_exceeded",
                        "external_exposure_usdt": str(external),
                        "existing_reservations_usdt": str(reserved),
                        "proposed_notional_usdt": str(notional),
                        "projected_exposure_usdt": str(projected),
                        "max_account_notional_usdt": str(cap),
                    }
                connection.execute(
                    """INSERT INTO risk_reservations
                    (order_id, notional_usdt, state, created_at, updated_at, detail)
                    VALUES (?, ?, 'RESERVED', ?, ?, '')""",
                    (order_id, str(notional), now, now),
                )
                connection.execute("COMMIT")
                return {
                    "ok": True,
                    "reason": "risk_reserved",
                    "state": "RESERVED",
                    "projected_exposure_usdt": str(projected),
                    "max_account_notional_usdt": str(cap),
                }
        except (sqlite3.Error, OSError, InvalidOperation) as exc:
            raise RiskLedgerError("risk_ledger_reservation_failed") from exc

    def transition(self, order_id, new_state, *, detail=""):
        """Move a reservation forward without automatically releasing risk."""
        order_id = self._validate_order_id(order_id)
        if new_state not in _ALLOWED_STATES:
            raise RiskLedgerError("invalid_reservation_state")
        if not isinstance(detail, str) or len(detail) > 500:
            raise RiskLedgerError("invalid_reservation_detail")
        transitions = {
            "RESERVED": {"SUBMITTING", "RELEASED"},
            "SUBMITTING": {"UNKNOWN", "EXTERNALIZED", "RELEASED"},
            "UNKNOWN": {"EXTERNALIZED", "RELEASED"},
            "EXTERNALIZED": {"RELEASED"},
            "RELEASED": set(),
        }
        now = time.time()
        try:
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                # Recheck the persistent kill switch in the same SQLite write
                # transaction that begins submission. If an operator activates
                # it after reservation but before this transition, no exchange
                # POST should follow from this reservation.
                if new_state == "SUBMITTING":
                    control = connection.execute(
                        "SELECT kill_switch_active FROM entry_control WHERE singleton_id = 1"
                    ).fetchone()
                    if control is None or control[0] != 0:
                        connection.execute("ROLLBACK")
                        return {
                            "ok": False,
                            "reason": "operator_kill_switch_active_before_submission",
                        }
                row = connection.execute(
                    "SELECT state FROM risk_reservations WHERE order_id = ?", (order_id,)
                ).fetchone()
                if row is None:
                    connection.execute("ROLLBACK")
                    return {"ok": False, "reason": "reservation_not_found"}
                current = row[0]
                if new_state == current:
                    connection.execute("COMMIT")
                    return {"ok": True, "reason": "state_unchanged", "state": current}
                if new_state == "EXTERNALIZED" and current in {"SUBMITTING", "UNKNOWN"}:
                    if not detail.startswith("confirmed_snapshot:"):
                        connection.execute("ROLLBACK")
                        return {
                            "ok": False,
                            "reason": "externalization_requires_snapshot_confirmation",
                            "state": current,
                        }
                if new_state == "RELEASED" and current == "RESERVED":
                    # A RESERVED row may survive a crash around the boundary
                    # before SUBMITTING is committed. Release only when the
                    # caller has independently established that no POST began.
                    if not detail.startswith("confirmed_pre_submit:"):
                        connection.execute("ROLLBACK")
                        return {
                            "ok": False,
                            "reason": "reserved_release_requires_pre_submit_confirmation",
                            "state": current,
                        }
                if new_state == "RELEASED" and current in {"SUBMITTING", "UNKNOWN", "EXTERNALIZED"}:
                    if not detail.startswith("confirmed_terminal:"):
                        connection.execute("ROLLBACK")
                        return {
                            "ok": False,
                            "reason": "release_requires_terminal_confirmation",
                            "state": current,
                        }
                if new_state not in transitions[current]:
                    connection.execute("ROLLBACK")
                    return {
                        "ok": False,
                        "reason": "invalid_reservation_transition",
                        "state": current,
                        "requested_state": new_state,
                    }
                connection.execute(
                    "UPDATE risk_reservations SET state = ?, updated_at = ?, detail = ? WHERE order_id = ?",
                    (new_state, now, detail, order_id),
                )
                connection.execute("COMMIT")
                return {"ok": True, "reason": "state_transitioned", "state": new_state}
        except (sqlite3.Error, OSError) as exc:
            raise RiskLedgerError("risk_ledger_transition_failed") from exc

    def get(self, order_id):
        order_id = self._validate_order_id(order_id)
        try:
            with self._connection() as connection:
                row = connection.execute(
                    """SELECT order_id, notional_usdt, state, created_at, updated_at, detail
                    FROM risk_reservations WHERE order_id = ?""",
                    (order_id,),
                ).fetchone()
        except (sqlite3.Error, OSError) as exc:
            raise RiskLedgerError("risk_ledger_read_failed") from exc
        if row is None:
            return None
        return {
            "order_id": row[0],
            "notional_usdt": row[1],
            "state": row[2],
            "created_at": row[3],
            "updated_at": row[4],
            "detail": row[5],
        }

    def list_unresolved(self, *, limit=100):
        """List unresolved reservations for recovery tooling without mutating state.

        Results are ordered oldest-first so a recovery worker can process the
        longest-lived ambiguity first. This method never infers an exchange
        outcome and never releases capacity.
        """
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise RiskLedgerError("invalid_unresolved_limit")
        placeholders = ",".join("?" for _ in _ACTIVE_STATES)
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    f"""SELECT order_id, notional_usdt, state, created_at, updated_at, detail
                    FROM risk_reservations
                    WHERE state IN ({placeholders})
                    ORDER BY created_at ASC, order_id ASC
                    LIMIT ?""",
                    (*_ACTIVE_STATES, limit),
                ).fetchall()
        except (sqlite3.Error, OSError) as exc:
            raise RiskLedgerError("risk_ledger_read_failed") from exc
        return [
            {
                "order_id": row[0],
                "notional_usdt": row[1],
                "state": row[2],
                "created_at": row[3],
                "updated_at": row[4],
                "detail": row[5],
            }
            for row in rows
        ]

    def record_recovery_audit(self, *, order_id, symbol="", state_before="", outcome, reason):
        """Append a bounded-detail recovery outcome record to the durable ledger."""
        order_id = self._validate_order_id(order_id)
        for value, field, max_length in (
            (symbol, "symbol", 32),
            (state_before, "state_before", 32),
            (outcome, "outcome", 32),
            (reason, "reason", 200),
        ):
            if not isinstance(value, str) or len(value) > max_length:
                raise RiskLedgerError(f"invalid_recovery_audit_{field}")
        if not outcome.strip() or not reason.strip():
            raise RiskLedgerError("invalid_recovery_audit_outcome_or_reason")
        try:
            with self._connection() as connection:
                cursor = connection.execute(
                    """INSERT INTO recovery_audit
                    (created_at, order_id, symbol, state_before, outcome, reason)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                    (time.time(), order_id, symbol, state_before, outcome, reason),
                )
                return {"ok": True, "audit_id": cursor.lastrowid}
        except (sqlite3.Error, OSError) as exc:
            raise RiskLedgerError("recovery_audit_write_failed") from exc

    def list_recovery_audit(self, *, limit=100):
        """Read recent recovery audit events newest-first without mutating state."""
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise RiskLedgerError("invalid_recovery_audit_limit")
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    """SELECT audit_id, created_at, order_id, symbol, state_before, outcome, reason
                    FROM recovery_audit ORDER BY audit_id DESC LIMIT ?""",
                    (limit,),
                ).fetchall()
        except (sqlite3.Error, OSError) as exc:
            raise RiskLedgerError("recovery_audit_read_failed") from exc
        return [
            {
                "audit_id": row[0], "created_at": row[1], "order_id": row[2],
                "symbol": row[3], "state_before": row[4], "outcome": row[5], "reason": row[6],
            }
            for row in rows
        ]

    def active_reserved_notional(self):
        """Return unresolved reserved notional; never silently returns zero on DB errors."""
        try:
            with self._connection() as connection:
                placeholders = ",".join("?" for _ in _ACTIVE_STATES)
                rows = connection.execute(
                    f"SELECT notional_usdt FROM risk_reservations WHERE state IN ({placeholders})",
                    _ACTIVE_STATES,
                ).fetchall()
            return str(sum((Decimal(row[0]) for row in rows), Decimal("0")))
        except (sqlite3.Error, OSError, InvalidOperation) as exc:
            raise RiskLedgerError("risk_ledger_read_failed") from exc
