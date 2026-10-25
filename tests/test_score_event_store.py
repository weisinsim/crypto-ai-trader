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
        # Primary calibration includes only records with all required cost fields present.
        self.store.record_realized_trade(dict(self.trade, fees=0.001, slippage=0.002, funding=0.0))
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

    def test_cost_field_coverage_is_explicit_and_invalid_values_are_incomplete(self):
        self.store.record_score_event(self.event)
        trade = dict(self.trade, fees=0.001, slippage=0.002, funding=0.0)
        self.store.record_realized_trade(trade)
        report = self.store.calibration_rows()
        self.assertEqual(report["cost_fields_present_count"], 1)
        self.assertEqual(report["cost_fields_incomplete_count"], 0)
        self.assertEqual(report["cost_field_valid_counts"], {"fees": 1, "slippage": 1, "funding": 1})
        self.assertEqual(report["trades"][0]["cost_coverage_status"], "FIELDS_PRESENT_UNVERIFIED")
        self.assertEqual(report["trades"][0]["cost_fields_missing"], [])

        other_event = dict(self.event, signal_id="SOL-1700000000001-SHORT", signal_ts=1700000000001)
        self.store.record_score_event(other_event)
        other_trade = dict(self.trade, signal_id=other_event["signal_id"], exit_ts=1700003600001,
                           fees="unknown", slippage=0.002)
        self.store.record_realized_trade(other_trade)
        report = self.store.calibration_rows()
        self.assertEqual(report["cost_fields_present_count"], 1)
        self.assertEqual(report["cost_fields_incomplete_count"], 1)
        self.assertEqual(report["cost_field_valid_counts"], {"fees": 1, "slippage": 2, "funding": 1})
        self.assertEqual(report["trades"][1]["cost_coverage_status"], "INCOMPLETE_COST_FIELDS")
        self.assertEqual(report["trades"][1]["cost_fields_missing"], ["fees", "funding"])

    def test_primary_calibration_excludes_incomplete_cost_records_but_reports_them(self):
        self.store.record_score_event(self.event)
        self.store.record_realized_trade(dict(self.trade, fees=0.001, slippage=0.002, funding=0.0))
        other_event = dict(self.event, signal_id="SOL-1700000000001-SHORT", signal_ts=1700000000001)
        self.store.record_score_event(other_event)
        self.store.record_realized_trade(dict(
            self.trade, signal_id=other_event["signal_id"], exit_ts=1700003600001,
            fees=0.001, slippage=0.002
        ))
        report = self.store.calibration_report(min_samples=1)
        self.assertEqual(report["coverage"]["completed_trade_count"], 2)
        self.assertEqual(report["coverage"]["cost_fields_present_count"], 1)
        self.assertEqual(report["coverage"]["cost_fields_incomplete_count"], 1)
        self.assertEqual(report["calibration"]["trade_count_in_bands"], 1)
        self.assertEqual(report["calibration"]["cost_eligible_trade_count"], 1)
        self.assertEqual(report["calibration"]["cost_incomplete_excluded_count"], 1)
        self.assertTrue(report["calibration"]["cost_fields_present_means_unverified"])

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

    def test_boolean_and_non_finite_cost_values_are_not_counted_as_coverage(self):
        invalid_values = (True, float("nan"), float("inf"), "0.001")
        for index, value in enumerate(invalid_values, start=1):
            signal_id = f"SOL-170000000000{index}-SHORT"
            event = dict(self.event, signal_id=signal_id, signal_ts=1700000000000 + index)
            trade = dict(
                self.trade, signal_id=signal_id, exit_ts=1700003600000 + index,
                fees=value, slippage=0.002, funding=0.0
            )
            self.store.record_score_event(event)
            self.store.record_realized_trade(trade)

        report = self.store.calibration_rows()
        self.assertEqual(report["cost_fields_present_count"], 0)
        self.assertEqual(report["cost_fields_incomplete_count"], len(invalid_values))
        self.assertTrue(all(
            row["cost_coverage_status"] == "INCOMPLETE_COST_FIELDS"
            for row in report["trades"]
        ))
        calibration = self.store.calibration_report(min_samples=1)
        self.assertEqual(calibration["calibration"]["trade_count_in_bands"], 0)
        self.assertEqual(calibration["calibration"]["cost_incomplete_excluded_count"], len(invalid_values))


if __name__ == "__main__":
    unittest.main()
