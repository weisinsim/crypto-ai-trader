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


if __name__ == "__main__":
    unittest.main()
