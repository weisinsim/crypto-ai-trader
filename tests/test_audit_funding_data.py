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
        self.assertIn("never rounded or snapped", report["note"])

    def test_reports_out_of_range_events(self):
        rows = [dict(self.funding[0]), dict(self.funding[-1])]
        rows[0]["funding_time"] = 50
        report = audit_funding_against_candles(self.candles, rows, "BTCUSDT")
        self.assertEqual(report["funding"]["records_outside_candle_range"], 1)
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
        with self.assertRaisesRegex(ValueError, "must be positive"):
            audit_funding_against_candles(self.candles, self.funding, "BTCUSDT", max_gap_hours=0)

    def test_symbol_mismatch_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "do not match"):
            audit_funding_against_candles(self.candles, self.funding, "ETHUSDT")


if __name__ == "__main__":
    unittest.main()
