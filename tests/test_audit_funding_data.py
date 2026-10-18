import unittest

from research.audit_funding_data import audit_funding_against_candles


class FundingAuditTests(unittest.TestCase):
    def setUp(self):
        self.candles = [{"t": 100}, {"t": 200}, {"t": 300}, {"t": 400}]
        self.funding = [
            {"funding_time": 100, "funding_rate": 0.001, "mark_price": 100,
             "symbol": "BTCUSDT", "mark_price_source": "reported", "venue": "binance_usdm"},
            {"funding_time": 250, "funding_rate": 0.001, "mark_price": 101,
             "symbol": "BTCUSDT", "mark_price_source": "reported", "venue": "binance_usdm"},
            {"funding_time": 400, "funding_rate": -0.001, "mark_price": 99,
             "symbol": "BTCUSDT", "mark_price_source": "reported", "venue": "binance_usdm"},
        ]

    def test_reports_nonmatching_timestamp_without_snapping(self):
        report = audit_funding_against_candles(self.candles, self.funding, "btcusdt")
        self.assertEqual(report["status"], "REVIEW_REQUIRED")
        self.assertEqual(report["funding"]["exact_candle_open_matches"], 2)
        self.assertEqual(report["funding"]["nonmatching_in_range"], 1)
        self.assertEqual(report["funding"]["records_outside_candle_range"], 0)
        self.assertFalse(report["event_completeness_verified"])
        self.assertIn("without snapping", report["note"])

    def test_audit_reports_raw_jitter_and_normalized_alignment_separately(self):
        rows = [dict(self.funding[0]), dict(self.funding[-1])]
        rows[0]["funding_time"] = 100
        rows[0]["funding_time_raw"] = 101
        rows[1]["funding_time_raw"] = 400
        report = audit_funding_against_candles(self.candles, rows, "BTCUSDT")
        self.assertEqual(report["funding"]["exact_candle_open_matches"], 1)
        self.assertEqual(report["funding"]["normalized_candle_open_matches"], 2)
        self.assertEqual(report["funding"]["boundary_jitter_normalized_records"], 1)
        self.assertEqual(report["funding"]["nonmatching_in_range"], 0)
        self.assertEqual(report["funding"]["first_event_ts"], 101)
        self.assertEqual(report["status"], "BOUNDARY_JITTER_NORMALIZED_ALIGNMENT_PASS_NOT_RECONCILED")

    def test_gap_intervals_are_computed_in_raw_timestamp_order(self):
        rows = [dict(self.funding[0]), dict(self.funding[-1])]
        rows[0]["funding_time"] = 300
        rows[0]["funding_time_raw"] = 300
        rows[1]["funding_time"] = 400
        rows[1]["funding_time_raw"] = 200
        report = audit_funding_against_candles(self.candles, rows, "BTCUSDT")
        self.assertEqual(report["funding"]["first_event_ts"], 200)
        self.assertEqual(report["funding"]["last_event_ts"], 300)
        self.assertEqual(report["funding"]["event_interval_ms"]["min"], 100)
        self.assertEqual(report["funding"]["event_interval_ms"]["max"], 100)

    def test_reports_out_of_range_events(self):
        rows = [dict(self.funding[0]), dict(self.funding[-1])]
        rows[0]["funding_time"] = 50
        report = audit_funding_against_candles(self.candles, rows, "BTCUSDT")
        self.assertEqual(report["funding"]["records_outside_candle_range"], 1)
        self.assertEqual(report["status"], "REVIEW_REQUIRED")

    def test_duplicate_raw_event_timestamps_require_review(self):
        rows = [dict(self.funding[0]), dict(self.funding[-1])]
        rows[0]["funding_time_raw"] = 200
        rows[1]["funding_time_raw"] = 200
        report = audit_funding_against_candles(self.candles, rows, "BTCUSDT")
        self.assertEqual(report["duplicate_event_records"], 1)
        self.assertEqual(report["duplicate_event_timestamps"], [200])
        self.assertEqual(report["funding"]["event_interval_ms"]["min"], 0)
        self.assertEqual(report["status"], "REVIEW_REQUIRED")

    def test_exact_alignment_does_not_claim_reconciliation(self):
        rows = [dict(self.funding[0]), dict(self.funding[-1])]
        report = audit_funding_against_candles(self.candles, rows, "BTCUSDT")
        self.assertEqual(report["status"], "TIMESTAMP_ALIGNMENT_PASS_NOT_RECONCILED")
        self.assertFalse(report["source_authenticity_verified"])
        self.assertFalse(report["account_cashflows_reconciled"])

    def test_optional_gap_threshold_flags_large_intervals_for_review(self):
        rows = [dict(self.funding[0]), dict(self.funding[-1])]
        rows[0]["funding_time"] = 100
        rows[1]["funding_time"] = 400
        report = audit_funding_against_candles(self.candles, rows, "BTCUSDT", max_gap_hours=0.00005)
        self.assertEqual(report["status"], "REVIEW_REQUIRED")
        self.assertEqual(report["intervals_exceeding_gap_threshold"], 1)
        self.assertFalse(report["event_completeness_verified"])

    def test_gap_threshold_must_be_positive(self):
        with self.assertRaisesRegex(ValueError, "finite positive number"):
            audit_funding_against_candles(self.candles, self.funding, "BTCUSDT", max_gap_hours=0)

    def test_gap_threshold_rejects_non_finite_values_and_booleans(self):
        for value in (float("nan"), float("inf"), float("-inf"), True):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "finite positive number"):
                    audit_funding_against_candles(
                        self.candles, self.funding, "BTCUSDT", max_gap_hours=value
                    )

    def test_symbol_mismatch_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "do not match"):
            audit_funding_against_candles(self.candles, self.funding, "ETHUSDT")


if __name__ == "__main__":
    unittest.main()
