import unittest

from research.funding_settlement_schedule_audit import audit_funding_settlement_schedule


def event(ts):
    return {"symbol": "BTCUSDT", "funding_timestamp_ms": ts, "funding_rate": 0.0001}


class FundingSettlementScheduleAuditTests(unittest.TestCase):
    def audit(self, events, expected):
        return audit_funding_settlement_schedule(
            events, symbol="BTCUSDT", start_ts_ms=1000, end_ts_ms=5000,
            expected_settlement_ts_ms=expected,
        )

    def test_exact_schedule_passes_without_claiming_exchange_verification(self):
        result = self.audit([event(1000), event(3000)], [1000, 3000])
        self.assertEqual(result["status"], "PASS")
        self.assertTrue(result["schedule_match"])
        self.assertFalse(result["exchange_history_verified"])
        self.assertFalse(result["exchange_schedule_verified"])
        self.assertFalse(result["auto_trade_authorized"])

    def test_missing_settlement_fails(self):
        result = self.audit([event(1000)], [1000, 3000])
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["missing_settlement_ts_ms"], [3000])

    def test_unexpected_settlement_fails(self):
        result = self.audit([event(1000), event(2000)], [1000])
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["unexpected_settlement_ts_ms"], [2000])

    def test_event_outside_requested_window_fails(self):
        result = self.audit([event(1000), event(5000)], [1000])
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["outside_window_ts_ms"], [5000])

    def test_empty_schedule_never_passes_as_complete(self):
        result = self.audit([], [])
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("empty_expected_schedule_not_authoritative", result["errors"])

    def test_duplicate_expected_settlement_rejected(self):
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            self.audit([event(1000)], [1000, 1000])

    def test_unsorted_expected_settlement_rejected(self):
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            self.audit([event(1000)], [3000, 1000])

    def test_end_boundary_is_exclusive(self):
        with self.assertRaisesRegex(ValueError, "within the requested window"):
            self.audit([], [5000])

    def test_duplicate_observed_event_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate funding timestamp"):
            self.audit([event(1000), event(1000)], [1000])


if __name__ == "__main__":
    unittest.main()
