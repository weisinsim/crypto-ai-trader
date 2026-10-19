import unittest
from datetime import datetime, timezone

from execution.entry_alerts import build_entry_alert
from execution.replay_alert_adapter import replay_row_to_snapshot


class ReplayAlertAdapterTests(unittest.TestCase):
    def setUp(self):
        self.row = {
            "timestamp": "2026-10-19T10:00:00Z",
            "model": "BTCUSDT",
            "model_version": "replay-v2",
            "side": "LONG",
            "entry": 100.0,
            "stop": 95.0,
            "target": 110.0,
            "target2": 115.0,
            "score": 80,
        }
        self.now_ms = int(datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc).timestamp() * 1000)

    def test_uses_hourly_candle_close_not_open(self):
        snapshot = replay_row_to_snapshot(self.row, analysis_ts=self.now_ms / 1000)
        self.assertEqual(snapshot["confirmed_signal_time"], int(datetime(2026, 10, 19, 11, 0, tzinfo=timezone.utc).timestamp() * 1000))

    def test_valid_replay_entry_becomes_research_only_candidate(self):
        snapshot = replay_row_to_snapshot(self.row, analysis_ts=self.now_ms / 1000)
        event = build_entry_alert(snapshot, now_ms=self.now_ms)
        self.assertTrue(event["eligible"], event["blockers"])
        self.assertTrue(event["research_only"])
        self.assertFalse(event["auto_trade_authorized"])

    def test_watch_row_is_not_actionable(self):
        row = dict(self.row, side="", status="WATCH", signal="WATCH-LONG")
        snapshot = replay_row_to_snapshot(row, analysis_ts=self.now_ms / 1000)
        event = build_entry_alert(snapshot, now_ms=self.now_ms)
        self.assertFalse(event["eligible"])
        self.assertIn("not_actionable_direction", event["blockers"])

    def test_rejects_naive_timestamp(self):
        row = dict(self.row, timestamp="2026-10-19T10:00:00")
        with self.assertRaises(ValueError):
            replay_row_to_snapshot(row, analysis_ts=self.now_ms / 1000)


if __name__ == "__main__":
    unittest.main()
