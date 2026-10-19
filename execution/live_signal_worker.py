"""Standalone, read-only polling worker for live entry-alert candidates.

The worker reads the dashboard snapshot over HTTP, validates public Binance
closed-candle timestamps, and stores eligible notification-only candidates in
SQLite. It never sends messages or places orders.
"""
from __future__ import annotations

import json
import os
import signal
import time
from urllib.request import Request, urlopen

from execution.alert_store import EntryAlertStore
from execution.live_signal_scanner import BinanceUsdMMarketData, scan_once


def fetch_snapshot(base_url, *, timeout=8):
    """Return a symbol-indexed snapshot only when the dashboard reports LIVE."""
    url = base_url.rstrip("/") + "/api/snapshot"
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "crypto-ai-alert-worker"})
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    status = payload.get("status") or {}
    if not isinstance(status, dict) or status.get("status") != "LIVE":
        raise RuntimeError("dashboard_not_live")
    rows = payload.get("data")
    if not isinstance(rows, list):
        raise RuntimeError("dashboard_snapshot_invalid")
    result = {}
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get("symbol"), str):
            result[row["symbol"]] = row
    return result


def run_worker(*, base_url, db_path, symbols, interval="1h", poll_seconds=30,
               max_age_ms=90 * 60 * 1000, stop_event=None, emit=print):
    """Poll until stop_event is set; dependencies are injectable for testing."""
    if not isinstance(poll_seconds, (int, float)) or isinstance(poll_seconds, bool) or poll_seconds < 5:
        raise ValueError("poll_seconds must be at least 5")
    stop_event = stop_event or _StopEvent()
    store = EntryAlertStore(db_path)
    market_data = BinanceUsdMMarketData()
    last_result = {"status": "STARTING"}
    try:
        while not stop_event.is_set():
            cycle_started = time.time()
            try:
                snapshots = fetch_snapshot(base_url)
                result = scan_once(
                    symbols=symbols,
                    interval=interval,
                    market_data=market_data,
                    snapshot_provider=lambda symbol: snapshots.get(symbol),
                    store=store,
                    now_ms=int(time.time() * 1000),
                    max_age_ms=max_age_ms,
                )
                last_result = {"status": "OK", **result, "pending": len(store.pending())}
            except Exception as exc:
                # Keep the worker alive, but never treat a failed cycle as healthy.
                last_result = {"status": "DEGRADED", "reason": type(exc).__name__}
            emit(json.dumps({"worker": "entry-alert-scan", "ts": time.time(), **last_result}, separators=(",", ":")))
            remaining = max(0, poll_seconds - (time.time() - cycle_started))
            if remaining and stop_event.wait(remaining):
                break
    finally:
        if store._memory_conn is not None:
            store._memory_conn.close()
    return last_result


class _StopEvent:
    """Minimal event compatible with threading.Event for the CLI path."""
    def __init__(self):
        self.stopped = False

    def is_set(self):
        return self.stopped

    def set(self):
        self.stopped = True

    def wait(self, seconds):
        # Event-driven stop is installed by signal handlers; bounded sleep
        # keeps shutdown responsive without spawning a background thread.
        time.sleep(seconds)
        return self.stopped


def main():
    base_url = os.environ.get("ALERT_DASHBOARD_URL", "").strip()
    if not base_url.startswith("https://") and not base_url.startswith("http://127.0.0.1"):
        raise SystemExit("ALERT_DASHBOARD_URL must use HTTPS (or loopback HTTP for local testing)")
    db_path = os.environ.get("ALERT_OUTBOX_DB", "data/entry-alerts.sqlite3")
    symbols = [item.strip().upper() for item in os.environ.get(
        "ALERT_SYMBOLS", "BTCUSDT,ETHUSDT,SOLUSDT,AVAXUSDT,XRPUSDT"
    ).split(",") if item.strip()]
    poll_seconds = int(os.environ.get("ALERT_POLL_SECONDS", "30"))
    stop = _StopEvent()

    def request_stop(signum, frame):
        stop.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    run_worker(base_url=base_url, db_path=db_path, symbols=symbols,
               poll_seconds=poll_seconds, stop_event=stop)


if __name__ == "__main__":
    main()
