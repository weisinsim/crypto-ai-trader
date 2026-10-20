"""Tests for the standalone read-only alert worker."""
import json
import time
import unittest
from unittest.mock import patch

from execution import live_signal_worker


class Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class SnapshotFetchTests(unittest.TestCase):
    def test_fetch_snapshot_rejects_invalid_timeout_url_and_response_shape(self):
        with self.assertRaisesRegex(ValueError, "finite positive"):
            live_signal_worker.fetch_snapshot("https://example.invalid", timeout=float("nan"))
        for unsafe_url in (
            "http://example.invalid",
            "http://127.0.0.1.evil.example",
            "http://127.0.0.1@evil.example",
            "https://",
            "https://example.invalid:bad",
        ):
            with self.subTest(url=unsafe_url):
                with self.assertRaisesRegex(ValueError, "dashboard URL"):
                    live_signal_worker.fetch_snapshot(unsafe_url)
        with patch.object(live_signal_worker, "urlopen", return_value=Response([])):
            with self.assertRaisesRegex(RuntimeError, "dashboard_snapshot_invalid"):
                live_signal_worker.fetch_snapshot("https://example.invalid")

    def test_fetch_snapshot_requires_live_status(self):
        with patch.object(live_signal_worker, "urlopen", return_value=Response({
            "status": {"status": "DELAYED"},
            "data": [{"symbol": "BTCUSDT"}],
        })):
            with self.assertRaisesRegex(RuntimeError, "dashboard_not_live"):
                live_signal_worker.fetch_snapshot("https://example.invalid")

    def test_fetch_snapshot_rejects_stale_or_invalid_heartbeat(self):
        for updated in (time.time() - 121, time.time() + 10, float("nan"), None):
            with self.subTest(updated=updated):
                with patch.object(live_signal_worker, "urlopen", return_value=Response({
                    "status": {"status": "LIVE", "updated": updated},
                    "data": [{"symbol": "BTCUSDT"}],
                })):
                    with self.assertRaisesRegex(RuntimeError, "dashboard_heartbeat_stale"):
                        live_signal_worker.fetch_snapshot("https://example.invalid")

    def test_fetch_snapshot_indexes_valid_symbol_rows(self):
        with patch.object(live_signal_worker, "urlopen", return_value=Response({
            "status": {"status": "LIVE", "updated": time.time()},
            "data": [{"symbol": "BTCUSDT", "confirmed_signal": "LONG"}, None, {"name": "bad"}],
        })):
            result = live_signal_worker.fetch_snapshot("https://example.invalid/")
        self.assertEqual(result, {"BTCUSDT": {"symbol": "BTCUSDT", "confirmed_signal": "LONG"}})


class WorkerCycleTests(unittest.TestCase):
    def test_worker_rejects_non_finite_poll_intervals(self):
        for interval in (float("nan"), float("inf"), float("-inf"), 4.99, True):
            with self.subTest(interval=interval):
                with self.assertRaisesRegex(ValueError, "finite number"):
                    live_signal_worker.run_worker(
                        base_url="https://example.invalid",
                        db_path=":memory:",
                        symbols=["BTCUSDT"],
                        poll_seconds=interval,
                        store_factory=lambda _path: None,
                    )

    def test_worker_rejects_empty_duplicate_or_invalid_symbols(self):
        for symbols in (
            [], ["BTCUSDT", "BTCUSDT"], ["BTCUSD"], [None],
            ["btcusdt"], ["BTC/USDT"], ["BTC USDT"], ["BTCUSDT.evil"],
        ):
            with self.subTest(symbols=symbols):
                with self.assertRaisesRegex(ValueError, "symbols"):
                    live_signal_worker.run_worker(
                        base_url="https://example.invalid",
                        db_path=":memory:",
                        symbols=symbols,
                        poll_seconds=5,
                        store_factory=lambda _path: None,
                    )

    def test_partial_symbol_scan_errors_report_degraded_status(self):
        class StopAfterEmit:
            def is_set(self):
                return False
            def wait(self, seconds):
                return True

        emitted = []
        with patch.object(live_signal_worker, "scan_once", return_value={
            "scanned": 2, "queued": 1, "duplicates": 0, "rejected": 0,
            "errors": [{"symbol": "ETHUSDT", "reason": "MarketDataError"}],
        }):
            result = live_signal_worker.run_worker(
                base_url="https://example.invalid",
                db_path=":memory:",
                symbols=["BTCUSDT", "ETHUSDT"],
                poll_seconds=5,
                stop_event=StopAfterEmit(),
                snapshot_fetcher=lambda _url: {"BTCUSDT": {"symbol": "BTCUSDT"}},
                market_data=object(),
                store_factory=lambda _path: type("Store", (), {
                    "_memory_conn": None,
                    "pending": lambda self: [],
                })(),
                emit=lambda line: emitted.append(json.loads(line)),
            )
        self.assertEqual(result["status"], "DEGRADED")
        self.assertEqual(result["reason"], "symbol_scan_errors")
        self.assertEqual(emitted[0]["status"], "DEGRADED")

    def test_worker_runs_scan_and_reports_pending_count(self):
        class StopAfterEmit:
            def __init__(self):
                self.stopped = False
            def is_set(self):
                return self.stopped
            def set(self):
                self.stopped = True
            def wait(self, seconds):
                self.stopped = True
                return True

        class FakeStore:
            _memory_conn = None
            def __init__(self):
                self.queued = []
            def pending(self):
                return self.queued

        store = FakeStore()
        emitted = []
        stop = StopAfterEmit()
        scan_result = {"scanned": 1, "enqueued": 1, "duplicates": 0}
        with patch.object(live_signal_worker, "scan_once", return_value=scan_result) as scan:
            result = live_signal_worker.run_worker(
                base_url="https://example.invalid",
                db_path=":memory:",
                symbols=["BTCUSDT"],
                poll_seconds=5,
                stop_event=stop,
                snapshot_fetcher=lambda _url: {"BTCUSDT": {"symbol": "BTCUSDT"}},
                market_data=object(),
                store_factory=lambda _path: store,
                emit=lambda line: emitted.append(json.loads(line)),
            )
        self.assertEqual(result["status"], "OK")
        self.assertEqual(result["pending"], 0)
        scan.assert_called_once()
        self.assertEqual(emitted[0]["status"], "OK")
        self.assertEqual(emitted[0]["enqueued"], 1)

    def test_failed_snapshot_cycle_reports_degraded_and_stops_cleanly(self):
        class StopAfterEmit:
            def __init__(self):
                self.stopped = False
            def is_set(self):
                return self.stopped
            def set(self):
                self.stopped = True
            def wait(self, seconds):
                self.stopped = True
                return True

        emitted = []
        stop = StopAfterEmit()
        result = live_signal_worker.run_worker(
            base_url="https://example.invalid",
            db_path=":memory:",
            symbols=["BTCUSDT"],
            poll_seconds=5,
            stop_event=stop,
            snapshot_fetcher=lambda _url: (_ for _ in ()).throw(RuntimeError("offline")),
            store_factory=lambda _path: type("Store", (), {
                "_memory_conn": None,
                "pending": lambda self: [],
            })(),
            emit=lambda line: emitted.append(json.loads(line)),
        )
        self.assertEqual(result["status"], "DEGRADED")
        self.assertEqual(emitted[0]["reason"], "RuntimeError")
        self.assertEqual(emitted[0]["worker"], "entry-alert-scan")

if __name__ == "__main__":
    unittest.main()
