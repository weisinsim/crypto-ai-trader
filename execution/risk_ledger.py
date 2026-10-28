"""Durable, cross-process risk reservations for testnet entry orders.

This ledger is a local safety primitive, not a transaction spanning SQLite and
an exchange. Callers must retain a reservation through ambiguous submission
outcomes and may externalize it only after an authoritative exchange snapshot
confirms the order is represented in the external exposure calculation.
"""
from __future__ import annotations

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
        parent.mkdir(parents=True, exist_ok=True)
        try:
            with self._connect() as connection:
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
        except (sqlite3.Error, OSError) as exc:
            raise RiskLedgerError("risk_ledger_initialization_failed") from exc

    def _connect(self):
        connection = sqlite3.connect(
            self.path, timeout=self.timeout_seconds, isolation_level=None
        )
        connection.execute(f"PRAGMA busy_timeout={int(self.timeout_seconds * 1000)}")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    @staticmethod
    def _validate_order_id(order_id):
        if not isinstance(order_id, str) or not order_id.strip() or len(order_id) > 128:
            raise RiskLedgerError("invalid_order_id")
        return order_id

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
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
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
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
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
            with self._connect() as connection:
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

    def active_reserved_notional(self):
        """Return unresolved reserved notional; never silently returns zero on DB errors."""
        try:
            with self._connect() as connection:
                placeholders = ",".join("?" for _ in _ACTIVE_STATES)
                rows = connection.execute(
                    f"SELECT notional_usdt FROM risk_reservations WHERE state IN ({placeholders})",
                    _ACTIVE_STATES,
                ).fetchall()
            return str(sum((Decimal(row[0]) for row in rows), Decimal("0")))
        except (sqlite3.Error, OSError, InvalidOperation) as exc:
            raise RiskLedgerError("risk_ledger_read_failed") from exc
