"""Durable, deduplicated email notification outbox.

SMTP delivery is at-least-once: a process can crash after SMTP accepts a message
but before mark_sent commits. Consumers must not claim exactly-once delivery.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Mapping


class EmailOutbox:
    # Persist only fields needed to render an informational signal email.
    # Arbitrary caller metadata (credentials, headers, SMTP settings) is discarded.
    EVENT_FIELDS = frozenset({
        "event_id", "event", "symbol", "direction", "price", "entry", "sl",
        "tp1", "tp2", "level", "score", "signal_time",
    })
    def __init__(self, path: str | os.PathLike | None = None, *, max_attempts: int = 5,
                 lease_seconds: int = 120, retry_base_seconds: int = 30,
                 retry_max_seconds: int = 1800):
        self.path = str(path or os.environ.get("EMAIL_OUTBOX_DB_PATH", "data/email_outbox.sqlite3"))
        self.max_attempts = max(1, int(max_attempts))
        self.lease_seconds = max(1, int(lease_seconds))
        self.retry_base_seconds = max(1, int(retry_base_seconds))
        self.retry_max_seconds = max(self.retry_base_seconds, int(retry_max_seconds))
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=10000")
        return db

    def _initialize(self):
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS email_outbox (
                    event_id TEXT PRIMARY KEY,
                    event_json TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('PENDING','SENDING','SENT','FAILED')),
                    attempts INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    next_attempt_at REAL NOT NULL,
                    lease_token TEXT,
                    lease_until REAL,
                    sent_at REAL,
                    last_error TEXT
                )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS idx_email_outbox_due ON email_outbox(status,next_attempt_at,created_at)")

    @staticmethod
    def _event_id(event: Mapping) -> str:
        value = event.get("event_id")
        if not isinstance(value, str) or not value.strip():
            raise ValueError("email event requires a non-empty event_id")
        return value.strip()

    def enqueue(self, event: Mapping, *, now: float | None = None) -> bool:
        event_id = self._event_id(event)
        timestamp = time.time() if now is None else float(now)
        safe_event = {key: value for key, value in event.items() if key in self.EVENT_FIELDS}
        payload = json.dumps(safe_event, sort_keys=True, separators=(",", ":"), allow_nan=False)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute("""
                INSERT OR IGNORE INTO email_outbox
                (event_id,event_json,status,attempts,created_at,updated_at,next_attempt_at)
                VALUES(?,?,'PENDING',0,?,?,?)
            """, (event_id, payload, timestamp, timestamp, timestamp))
            return cursor.rowcount == 1

    def claim_due(self, *, now: float | None = None, limit: int = 10) -> list[dict]:
        timestamp = time.time() if now is None else float(now)
        limit = max(1, min(int(limit), 100))
        claimed = []
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""
                UPDATE email_outbox
                SET status=CASE WHEN attempts >= ? THEN 'FAILED' ELSE 'PENDING' END,
                    lease_token=NULL, lease_until=NULL, updated_at=?,
                    last_error=COALESCE(last_error,'sending lease expired')
                WHERE status='SENDING' AND lease_until <= ?
            """, (self.max_attempts, timestamp, timestamp))
            rows = db.execute("""
                SELECT event_id,event_json,attempts FROM email_outbox
                WHERE status='PENDING' AND next_attempt_at <= ?
                ORDER BY created_at,event_id LIMIT ?
            """, (timestamp, limit)).fetchall()
            for row in rows:
                token = uuid.uuid4().hex
                changed = db.execute("""
                    UPDATE email_outbox SET status='SENDING',attempts=attempts+1,
                    lease_token=?,lease_until=?,updated_at=?
                    WHERE event_id=? AND status='PENDING'
                """, (token, timestamp + self.lease_seconds, timestamp, row["event_id"])).rowcount
                if changed:
                    claimed.append({"event_id": row["event_id"],
                                    "event": json.loads(row["event_json"]),
                                    "attempt": row["attempts"] + 1,
                                    "lease_token": token})
        return claimed

    def mark_sent(self, event_id: str, lease_token: str, *, now: float | None = None) -> bool:
        timestamp = time.time() if now is None else float(now)
        with self._connect() as db:
            cursor = db.execute("""
                UPDATE email_outbox SET status='SENT',sent_at=?,updated_at=?,
                lease_token=NULL,lease_until=NULL,last_error=NULL
                WHERE event_id=? AND status='SENDING' AND lease_token=?
            """, (timestamp, timestamp, event_id, lease_token))
            return cursor.rowcount == 1

    def mark_failed(self, event_id: str, lease_token: str, error: object,
                    *, now: float | None = None) -> bool:
        timestamp = time.time() if now is None else float(now)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""
                SELECT attempts FROM email_outbox WHERE event_id=?
                AND status='SENDING' AND lease_token=?
            """, (event_id, lease_token)).fetchone()
            if row is None:
                return False
            attempts = int(row["attempts"])
            terminal = attempts >= self.max_attempts
            delay = min(self.retry_base_seconds * (2 ** max(0, attempts - 1)),
                        self.retry_max_seconds)
            db.execute("""
                UPDATE email_outbox SET status=?,next_attempt_at=?,updated_at=?,
                lease_token=NULL,lease_until=NULL,last_error=?
                WHERE event_id=? AND status='SENDING' AND lease_token=?
            """, ("FAILED" if terminal else "PENDING",
                  timestamp if terminal else timestamp + delay, timestamp,
                  str(error)[:500], event_id, lease_token))
            return True

    def list_recent(self, *, limit: int = 20) -> list[dict]:
        limit = max(1, min(int(limit), 100))
        with self._connect() as db:
            rows = db.execute("""
                SELECT event_id,event_json,status,attempts,created_at,updated_at,
                       next_attempt_at,sent_at,last_error
                FROM email_outbox ORDER BY created_at DESC,event_id DESC LIMIT ?
            """, (limit,)).fetchall()
        result = []
        for row in rows:
            event = json.loads(row["event_json"])
            result.append({
                "event_id": row["event_id"], "event": event.get("event"),
                "symbol": event.get("symbol"), "direction": event.get("direction"),
                "status": row["status"], "attempts": row["attempts"],
                "created_at": row["created_at"], "updated_at": row["updated_at"],
                "next_attempt_at": row["next_attempt_at"], "sent_at": row["sent_at"],
                "last_error": row["last_error"],
            })
        return result


async def dispatch_outbox_batch(outbox: EmailOutbox, send_async, *, configured: bool,
                                limit: int = 10) -> str:
    """Dispatch a claimed batch using an async sender; suitable for isolated tests."""
    if not configured:
        return "NOT_CONFIGURED"
    try:
        claimed = outbox.claim_due(limit=limit)
    except Exception:
        return "QUEUE_FAILED"
    if not claimed:
        return "IDLE"
    result = "SENT_TO_SMTP"
    for item in claimed:
        try:
            accepted = await send_async(item["event"])
            if accepted:
                if not outbox.mark_sent(item["event_id"], item["lease_token"]):
                    result = "DELIVERY_STATE_UNKNOWN"
            else:
                outbox.mark_failed(item["event_id"], item["lease_token"],
                                   "SMTP sender returned not accepted")
                result = "FAILED"
        except Exception as exc:
            error = f"{type(exc).__name__}: {str(exc)[:350]}"
            try:
                outbox.mark_failed(item["event_id"], item["lease_token"], error)
            except Exception:
                result = "QUEUE_FAILED"
                continue
            result = "FAILED"
    return result
