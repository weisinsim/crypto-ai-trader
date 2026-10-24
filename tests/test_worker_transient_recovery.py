"""Regression tests for alert-worker recovery after transient failures."""
import json
import unittest
from unittest.mock import patch

from execution import live_signal_worker


class StopAfterCycles:
    def __init__(self, cycles):
        self.cycles = cycles
        self.waited = 0

    def is_set(self):
        return self.waited >= self.cycles

    def wait(self, _seconds):
        self.waited += 1
        return self.is_set()


class FakeStore:
    _memory_conn = None

    def pending(self):
        return []


class WorkerTransientRecoveryTests(unittest.TestCase):
    def test_worker_recovers_after_one_snapshot_fetch_failure(self):
        attempts = {"count": 0}

        def fetch(_url):
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise TimeoutError("transient dashboard timeout")
            return {"BTCUSDT": {"symbol": "BTCUSDT"}}

        emitted = []
        stop = StopAfterCycles(2)
        with patch.object(live_signal_worker, "scan_once", return_value={
            "scanned": 1, "enqueued": 0, "duplicates": 0, "rejected": 0,
            "errors": [],
        }) as scan:
            result = live_signal_worker.run_worker(
                base_url="https://example.invalid",
                db_path=":memory:",
                symbols=["BTCUSDT"],
                poll_seconds=5,
                stop_event=stop,
                snapshot_fetcher=fetch,
                market_data=object(),
                store_factory=lambda _path: FakeStore(),
                emit=lambda line: emitted.append(json.loads(line)),
            )

        self.assertEqual(attempts["count"], 2)
        self.assertEqual([item["status"] for item in emitted], ["DEGRADED", "OK"])
        self.assertEqual(emitted[0]["reason"], "TimeoutError")
        self.assertEqual(result["status"], "OK")
        scan.assert_called_once()


if __name__ == "__main__":
    unittest.main()
