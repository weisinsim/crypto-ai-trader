import unittest

from research.funding_history_audit import (
    audit_funding_events,
    calculate_position_funding,
)


EVENTS = [
    {"symbol": "BTCUSDT", "funding_timestamp_ms": 1000, "funding_rate": 0.001},
    {"symbol": "BTCUSDT", "funding_timestamp_ms": 2000, "funding_rate": -0.0005},
    {"symbol": "BTCUSDT", "funding_timestamp_ms": 3000, "funding_rate": 0.00025},
]


class FundingHistoryAuditTests(unittest.TestCase):
    def test_open_inclusive_close_exclusive_boundaries(self):
        result = calculate_position_funding(
            events=EVENTS, symbol="BTCUSDT", side="LONG",
            entry_timestamp_ms=1000, exit_timestamp_ms=3000,
            entry_notional=1000,
        )
        self.assertEqual(result["funding_event_count"], 2)
        self.assertEqual([e["funding_timestamp_ms"] for e in result["applied_events"]], [1000, 2000])
        self.assertAlmostEqual(result["funding_cost"], 0.5)
        self.assertFalse(result["funding_history_verified"])

    def test_short_reverses_funding_cashflow_sign(self):
        result = calculate_position_funding(
            events=EVENTS, symbol="BTCUSDT", side="SHORT",
            entry_timestamp_ms=1000, exit_timestamp_ms=3000,
            entry_notional=1000,
        )
        self.assertAlmostEqual(result["funding_cost"], -0.5)

    def test_event_at_exit_timestamp_is_not_charged(self):
        result = calculate_position_funding(
            events=EVENTS, symbol="BTCUSDT", side="LONG",
            entry_timestamp_ms=1000, exit_timestamp_ms=2000,
            entry_notional=1000,
        )
        self.assertEqual(result["funding_event_count"], 1)
        self.assertAlmostEqual(result["funding_cost"], 1.0)

    def test_audit_marks_history_unverified_even_when_rows_are_valid(self):
        result = audit_funding_events(
            EVENTS, symbol="BTCUSDT", start_ts_ms=1000, end_ts_ms=4000,
        )
        self.assertEqual(result["in_window_event_count"], 3)
        self.assertFalse(result["coverage_verified"])
        self.assertFalse(result["source_verified"])

    def test_duplicate_timestamps_rejected(self):
        rows = [EVENTS[0], dict(EVENTS[0])]
        with self.assertRaisesRegex(ValueError, "duplicate funding timestamp"):
            audit_funding_events(rows, symbol="BTCUSDT", start_ts_ms=0, end_ts_ms=4000)

    def test_unsorted_timestamps_rejected(self):
        with self.assertRaisesRegex(ValueError, "sorted"):
            audit_funding_events([EVENTS[1], EVENTS[0]], symbol="BTCUSDT",
                                 start_ts_ms=0, end_ts_ms=4000)

    def test_symbol_mismatch_rejected(self):
        with self.assertRaisesRegex(ValueError, "symbol does not match"):
            audit_funding_events(EVENTS, symbol="ETHUSDT", start_ts_ms=0, end_ts_ms=4000)

    def test_non_finite_rate_rejected(self):
        rows = [{"symbol": "BTCUSDT", "funding_timestamp_ms": 1000, "funding_rate": float("nan")}]
        with self.assertRaisesRegex(ValueError, "finite"):
            audit_funding_events(rows, symbol="BTCUSDT", start_ts_ms=0, end_ts_ms=2000)


if __name__ == "__main__":
    unittest.main()
