import os
import tempfile
import unittest

from research.score_event_store import ScoreEventStore


class ScoreEventStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.temp.name, "ledger.sqlite3")
        self.event = {
            "signal_id": "SOL-1700000000000-SHORT", "symbol": "SOLUSDT",
            "side": "SHORT", "signal_ts": 1700000000000, "score": 91,
        }
        self.trade = {
            "signal_id": "SOL-1700000000000-SHORT", "symbol": "SOLUSDT",
            "side": "SHORT", "exit_ts": 1700003600000, "net_return": 0.025,
        }
        self.store = ScoreEventStore(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def test_persists_across_store_restart_and_joins_frozen_score(self):
        self.store.record_score_event(self.event)
        self.store.record_realized_trade(self.trade)
        restarted = ScoreEventStore(self.path)
        report = restarted.calibration_rows()
        self.assertEqual(report["score_event_count"], 1)
        self.assertEqual(report["completed_trade_count"], 1)
        self.assertEqual(report["uncompleted_score_event_count"], 0)
        self.assertEqual(report["trades"][0]["score"], 91)
        self.assertEqual(report["trades"][0]["net_return"], 0.025)
        self.assertFalse(report["auto_trade_authorized"])
        full_report = restarted.calibration_report(min_samples=1)
        self.assertEqual(full_report["coverage"]["completed_trade_count"], 1)
        self.assertEqual(full_report["calibration"]["buckets"][0]["sample_count"], 1)
        self.assertEqual(full_report["calibration"]["status"], "RESEARCH_ONLY")
        self.assertFalse(full_report["auto_trade_authorized"])

    def test_duplicate_events_and_outcomes_are_rejected(self):
        self.store.record_score_event(self.event)
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.store.record_score_event(self.event)
        self.store.record_realized_trade(self.trade)
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.store.record_realized_trade(self.trade)

    def test_trade_without_event_and_mismatched_direction_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "no frozen score event"):
            self.store.record_realized_trade(self.trade)
        self.store.record_score_event(self.event)
        with self.assertRaisesRegex(ValueError, "symbol/side mismatch"):
            self.store.record_realized_trade(dict(self.trade, side="LONG"))

    def test_uncompleted_signals_remain_visible(self):
        self.store.record_score_event(self.event)
        report = self.store.calibration_rows()
        self.assertEqual(report["uncompleted_score_event_count"], 1)
        self.assertEqual(report["trades"], [])
        full_report = self.store.calibration_report()
        self.assertEqual(full_report["coverage"]["uncompleted_score_event_count"], 1)
        self.assertEqual(full_report["calibration"]["buckets"], [])

    def test_invalid_outcome_is_rejected_without_partial_write(self):
        self.store.record_score_event(self.event)
        with self.assertRaisesRegex(ValueError, "exit_ts precedes"):
            self.store.record_realized_trade(dict(self.trade, exit_ts=1))
        report = self.store.calibration_rows()
        self.assertEqual(report["completed_trade_count"], 0)
        self.assertEqual(report["uncompleted_score_event_count"], 1)


if __name__ == "__main__":
    unittest.main()
