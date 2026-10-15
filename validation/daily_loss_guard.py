"""Persistent daily realized-loss circuit breaker for paper/research workflows only."""
from __future__ import annotations

import json
import os
from math import isfinite
from pathlib import Path
from tempfile import NamedTemporaryFile


_SCHEMA_VERSION = 1


def _finite_number(value, field):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be finite")
    try:
        if not isfinite(value):
            raise ValueError(f"{field} must be finite")
        converted = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{field} must be finite") from exc
    if not isfinite(converted):
        raise ValueError(f"{field} must be finite")
    return converted


def _utc_date(value):
    if not isinstance(value, str) or len(value) != 10:
        raise ValueError("utc_date must be YYYY-MM-DD")
    try:
        from datetime import date
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("utc_date must be a valid YYYY-MM-DD date") from exc
    if parsed.isoformat() != value:
        raise ValueError("utc_date must be a valid YYYY-MM-DD date")
    return value


class DailyLossGuard:
    """Track realized PnL by explicit UTC date and lock that date at the loss limit.

    This is a deterministic local guard for testing. It does not observe exchange
    fills, unrealized PnL, fees/funding unless callers include them, or coordinate
    multiple processes. The caller must supply authoritative realized PnL events.
    """

    def __init__(self, daily_loss_limit, storage_path=None):
        limit = _finite_number(daily_loss_limit, "daily_loss_limit")
        if limit <= 0:
            raise ValueError("daily_loss_limit must be positive")
        self.daily_loss_limit = limit
        self.storage_path = Path(storage_path) if storage_path is not None else None
        self.days = {}
        if self.storage_path is not None and self.storage_path.exists():
            self._load()

    def _load(self):
        try:
            payload = json.loads(self.storage_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("daily-loss ledger unreadable; refusing to start unlocked") from exc
        if not isinstance(payload, dict) or payload.get("schema_version") != _SCHEMA_VERSION:
            raise ValueError("unsupported or invalid daily-loss ledger schema")
        days = payload.get("days")
        if not isinstance(days, dict):
            raise ValueError("invalid daily-loss ledger days")
        restored = {}
        for day, record in days.items():
            _utc_date(day)
            if not isinstance(record, dict) or set(record) != {"realized_pnl", "locked", "events"}:
                raise ValueError("invalid daily-loss record")
            pnl = _finite_number(record["realized_pnl"], "realized_pnl")
            if not isinstance(record["locked"], bool) or not isinstance(record["events"], dict):
                raise ValueError("invalid daily-loss lock/events")
            event_total = 0.0
            for event_id, event_pnl in record["events"].items():
                if not isinstance(event_id, str) or not event_id.strip():
                    raise ValueError("invalid daily-loss event id")
                event_total += _finite_number(event_pnl, "event pnl")
                if not isfinite(event_total):
                    raise ValueError("daily-loss ledger event total must remain finite")
            if not isfinite(event_total) or abs(event_total - pnl) > 1e-8:
                raise ValueError("daily-loss ledger total does not match event records")
            expected_locked = pnl <= -self.daily_loss_limit
            if record["locked"] != expected_locked and record["locked"] is False:
                raise ValueError("daily-loss ledger lock state is inconsistent")
            restored[day] = {"realized_pnl": pnl, "locked": record["locked"], "events": dict(record["events"])}
        self.days = restored

    def _save(self):
        if self.storage_path is None:
            return
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"schema_version": _SCHEMA_VERSION, "days": self.days}
        temp_name = None
        try:
            with NamedTemporaryFile("w", encoding="utf-8", dir=self.storage_path.parent,
                                    prefix=f".{self.storage_path.name}.", suffix=".tmp",
                                    delete=False) as handle:
                temp_name = handle.name
                json.dump(payload, handle, sort_keys=True, allow_nan=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.storage_path)
        except (OSError, TypeError, ValueError):
            if temp_name:
                try:
                    os.unlink(temp_name)
                except OSError:
                    pass
            raise

    def status(self, utc_date):
        day = _utc_date(utc_date)
        record = self.days.get(day, {"realized_pnl": 0.0, "locked": False, "events": {}})
        return {
            "utc_date": day,
            "realized_pnl": record["realized_pnl"],
            "daily_loss_limit": self.daily_loss_limit,
            "locked": record["locked"],
            "allow_new_entries": not record["locked"],
            "event_count": len(record["events"]),
        }

    def record_realized_pnl(self, event_id, pnl, utc_date):
        day = _utc_date(utc_date)
        if not isinstance(event_id, str) or not event_id.strip():
            raise ValueError("event_id is required")
        pnl = _finite_number(pnl, "pnl")
        before = self.days.get(day)
        if before is None:
            before = {"realized_pnl": 0.0, "locked": False, "events": {}}
        existing = before["events"].get(event_id)
        if existing is not None:
            if existing != pnl:
                raise ValueError("event_id reused with different pnl")
            return self.status(day)

        new_total = before["realized_pnl"] + pnl
        if not isfinite(new_total):
            raise ValueError("realized PnL total must remain finite")
        updated = {
            "realized_pnl": new_total,
            "locked": before["locked"] or (new_total <= -self.daily_loss_limit),
            "events": {**before["events"], event_id: pnl},
        }
        self.days[day] = updated
        try:
            self._save()
        except Exception:
            if day in self.days and before is not None:
                # Restore the original record, or remove a newly-created date.
                if day in self.days and not before["events"] and before["realized_pnl"] == 0.0 and not before["locked"]:
                    self.days.pop(day, None)
                else:
                    self.days[day] = before
            raise
        return self.status(day)
