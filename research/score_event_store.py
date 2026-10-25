"""SQLite-backed, append-only research ledger for entry scores and outcomes.

Research-only. No exchange connectivity, order placement, or trading authorization.
The caller remains responsible for supplying genuine entry-time events and net returns.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from research.score_calibration import summarize_score_calibration
from research.score_event_ledger import _finite_number, validate_score_event


class ScoreEventStore:
    """Durable local ledger with transactional writes and unique signal IDs."""

    def __init__(self, database_path):
        self.database_path = str(database_path)
        if self.database_path != ":memory:":
            Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self):
        conn = sqlite3.connect(self.database_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _initialize(self):
        with closing(self._connect()) as conn, conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS score_events (
                    signal_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    signal_ts INTEGER NOT NULL,
                    score REAL NOT NULL,
                    raw_json TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS realized_trades (
                    signal_id TEXT PRIMARY KEY REFERENCES score_events(signal_id),
                    exit_ts INTEGER NOT NULL,
                    net_return REAL NOT NULL,
                    recorded_json TEXT NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_time ON score_events(signal_ts)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_exit ON realized_trades(exit_ts)")

    def record_score_event(self, event):
        """Persist a frozen score event once; duplicates are rejected, never overwritten."""
        normalized = validate_score_event(event)
        try:
            with closing(self._connect()) as conn, conn:
                conn.execute(
                    "INSERT INTO score_events(signal_id,symbol,side,signal_ts,score,raw_json) VALUES(?,?,?,?,?,?)",
                    (normalized["signal_id"], normalized["symbol"], normalized["side"],
                     normalized["signal_ts"], normalized["score"],
                     json.dumps(event, sort_keys=True, separators=(",", ":"))),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"score event already exists or violates ledger constraints: {normalized['signal_id']}") from exc
        return {"status": "RECORDED_RESEARCH_ONLY", "signal_id": normalized["signal_id"],
                "auto_trade_authorized": False}

    def record_realized_trade(self, trade):
        """Persist one realized outcome only if it exactly matches a frozen score event."""
        if not isinstance(trade, dict):
            raise ValueError("trade must be a mapping")
        signal_id = trade.get("signal_id")
        if not isinstance(signal_id, str) or not signal_id.strip():
            raise ValueError("trade requires a non-empty signal_id")
        signal_id = signal_id.strip()
        exit_ts = trade.get("exit_ts")
        if isinstance(exit_ts, bool) or not isinstance(exit_ts, int) or exit_ts < 0:
            raise ValueError("exit_ts must be a non-negative integer millisecond timestamp")
        net_return = _finite_number(trade.get("net_return"), "net_return")
        if net_return <= -1:
            raise ValueError("net_return must be greater than -1")
        with closing(self._connect()) as conn, conn:
            event = conn.execute(
                "SELECT symbol,side,signal_ts FROM score_events WHERE signal_id=?", (signal_id,)
            ).fetchone()
            if event is None:
                raise ValueError(f"realized trade has no frozen score event: {signal_id}")
            if trade.get("symbol") != event["symbol"] or trade.get("side") != event["side"]:
                raise ValueError(f"symbol/side mismatch for signal_id: {signal_id}")
            if exit_ts < event["signal_ts"]:
                raise ValueError(f"exit_ts precedes signal_ts for signal_id: {signal_id}")
            try:
                conn.execute(
                    "INSERT INTO realized_trades(signal_id,exit_ts,net_return,recorded_json) VALUES(?,?,?,?)",
                    (signal_id, exit_ts, net_return,
                     json.dumps(trade, sort_keys=True, separators=(",", ":"))),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError(f"realized trade already exists for signal_id: {signal_id}") from exc
        return {"status": "RECORDED_RESEARCH_ONLY", "signal_id": signal_id,
                "auto_trade_authorized": False}

    def calibration_rows(self):
        """Return completed trades with frozen scores plus coverage diagnostics."""
        with closing(self._connect()) as conn, conn:
            rows = conn.execute("""
                SELECT e.signal_id,e.symbol,e.side,e.signal_ts,e.score,
                       t.exit_ts,t.net_return
                FROM score_events e
                JOIN realized_trades t USING(signal_id)
                ORDER BY t.exit_ts,e.signal_id
            """).fetchall()
            total_events = conn.execute("SELECT COUNT(*) FROM score_events").fetchone()[0]
            total_trades = conn.execute("SELECT COUNT(*) FROM realized_trades").fetchone()[0]
        trades = [dict(row) for row in rows]
        return {
            "status": "RESEARCH_ONLY",
            "score_event_count": total_events,
            "completed_trade_count": total_trades,
            "uncompleted_score_event_count": total_events - total_trades,
            "trades": trades,
            "auto_trade_authorized": False,
        }

    def calibration_report(self, *, min_samples=30):
        """Summarize persisted completed outcomes without hiding incomplete events."""
        ledger = self.calibration_rows()
        summary = summarize_score_calibration(ledger["trades"], min_samples=min_samples, split_by_side=True)
        return {
            "status": "RESEARCH_ONLY",
            "coverage": {
                "score_event_count": ledger["score_event_count"],
                "completed_trade_count": ledger["completed_trade_count"],
                "uncompleted_score_event_count": ledger["uncompleted_score_event_count"],
            },
            "calibration": summary,
            "auto_trade_authorized": False,
        }
