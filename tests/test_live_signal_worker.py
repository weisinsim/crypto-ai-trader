"""Tests for the standalone read-only alert worker."""
import json
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
    def test_fetch_snapshot_requires_live_status(self):
        with patch.object(live_signal_worker, "urlopen", return_value=Response({
            "status": {"status": "DELAYED"},
            "data": [{"symbol": "BTCUSDT"}],
        })):
            with self.assertRaisesRegex(RuntimeError, "dashboard_not_live"):
                live_signal_worker.fetch_snapshot("https://example.invalid")

    def test_fetch_snapshot_indexes_valid_symbol_rows(self):
        with patch.object(live_signal_worker, "urlopen", return_value=Response({
            "status": {"status": "LIVE"},
            "data": [{"symbol": "BTCUSDT", "confirmed_signal": "LONG"}, None, {"name": "bad"}],
        })):
            result = live_signal_worker.fetch_snapshot("https://example.invalid/")
        self.assertEqual(result, {"BTCUSDT": {"symbol": "BTCUSDT", "confirmed_signal": "LONG"}})


class WorkerCycleTests(unittest.TestCase):
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
