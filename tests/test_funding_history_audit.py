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

    def test_overflowing_integer_rate_is_rejected_without_internal_error(self):
        rows = [{"symbol": "BTCUSDT", "funding_timestamp_ms": 1000, "funding_rate": 10 ** 10000}]
        with self.assertRaisesRegex(ValueError, "finite"):
            audit_funding_events(rows, symbol="BTCUSDT", start_ts_ms=0, end_ts_ms=2000)

    def test_funding_cost_product_overflow_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "funding cost calculation overflowed"):
            calculate_position_funding(
                events=[{
                    "symbol": "BTCUSDT", "funding_timestamp_ms": 1000,
                    "funding_rate": 1e308,
                }],
                symbol="BTCUSDT", side="LONG",
                entry_timestamp_ms=1000, exit_timestamp_ms=2000,
                entry_notional=1e308,
            )

    def test_overflowing_integer_notional_is_rejected_without_internal_error(self):
        with self.assertRaisesRegex(ValueError, "entry_notional"):
            calculate_position_funding(
                events=[], symbol="BTCUSDT", side="LONG",
                entry_timestamp_ms=1000, exit_timestamp_ms=2000,
                entry_notional=10 ** 10000,
            )



class FundingNotionalPathTests(unittest.TestCase):
    def test_partial_exit_reduces_notional_for_later_settlements(self):
        from research.funding_history_audit import calculate_position_funding_with_notional_path

        events = [
            {"symbol": "BTCUSDT", "funding_timestamp_ms": 1000, "funding_rate": 0.001},
            {"symbol": "BTCUSDT", "funding_timestamp_ms": 2000, "funding_rate": 0.001},
            {"symbol": "BTCUSDT", "funding_timestamp_ms": 3000, "funding_rate": 0.001},
        ]
        result = calculate_position_funding_with_notional_path(
            events=events, symbol="BTCUSDT", side="LONG",
            entry_timestamp_ms=1000, exit_timestamp_ms=4000,
            notional_segments=[
                {"start_ts_ms": 1000, "end_ts_ms": 2500, "notional": 1000},
                {"start_ts_ms": 2500, "end_ts_ms": 4000, "notional": 400},
            ],
        )
        self.assertEqual(result["funding_event_count"], 3)
        self.assertEqual([e["applied_notional"] for e in result["applied_events"]], [1000, 1000, 400])
        self.assertAlmostEqual(result["funding_cost"], 2.4)
        self.assertFalse(result["notional_path_verified"])
        self.assertFalse(result["auto_trade_authorized"])

    def test_funding_at_segment_boundary_uses_reduced_notional(self):
        from research.funding_history_audit import calculate_position_funding_with_notional_path

        result = calculate_position_funding_with_notional_path(
            events=[{"symbol": "BTCUSDT", "funding_timestamp_ms": 2000, "funding_rate": 0.01}],
            symbol="BTCUSDT", side="LONG", entry_timestamp_ms=1000,
            exit_timestamp_ms=3000,
            notional_segments=[
                {"start_ts_ms": 1000, "end_ts_ms": 2000, "notional": 1000},
                {"start_ts_ms": 2000, "end_ts_ms": 3000, "notional": 250},
            ],
        )
        self.assertEqual(result["applied_events"][0]["applied_notional"], 250)
        self.assertAlmostEqual(result["funding_cost"], 2.5)

    def test_notional_path_rejects_gaps_overlap_and_invalid_notional(self):
        from research.funding_history_audit import calculate_position_funding_with_notional_path

        base = dict(
            events=[], symbol="BTCUSDT", side="LONG",
            entry_timestamp_ms=1000, exit_timestamp_ms=3000,
        )
        invalid_paths = [
            [{"start_ts_ms": 1000, "end_ts_ms": 1900, "notional": 1000},
             {"start_ts_ms": 2000, "end_ts_ms": 3000, "notional": 500}],
            [{"start_ts_ms": 1000, "end_ts_ms": 2200, "notional": 1000},
             {"start_ts_ms": 2000, "end_ts_ms": 3000, "notional": 500}],
            [{"start_ts_ms": 1000, "end_ts_ms": 3000, "notional": 0}],
        ]
        for path in invalid_paths:
            with self.subTest(path=path), self.assertRaises(ValueError):
                calculate_position_funding_with_notional_path(**base, notional_segments=path)

    def test_overflowing_notional_segment_is_rejected_cleanly(self):
        from research.funding_history_audit import calculate_position_funding_with_notional_path

        with self.assertRaisesRegex(ValueError, "notional must be finite"):
            calculate_position_funding_with_notional_path(
                events=[], symbol="BTCUSDT", side="LONG",
                entry_timestamp_ms=1000, exit_timestamp_ms=2000,
                notional_segments=[{
                    "start_ts_ms": 1000, "end_ts_ms": 2000,
                    "notional": 10 ** 10000,
                }],
            )

    def test_negative_rate_and_short_side_reverse_cashflow(self):
        from research.funding_history_audit import calculate_position_funding_with_notional_path

        result = calculate_position_funding_with_notional_path(
            events=[{"symbol": "BTCUSDT", "funding_timestamp_ms": 1500, "funding_rate": -0.002}],
            symbol="BTCUSDT", side="SHORT", entry_timestamp_ms=1000,
            exit_timestamp_ms=2000,
            notional_segments=[{"start_ts_ms": 1000, "end_ts_ms": 2000, "notional": 500}],
        )
        self.assertAlmostEqual(result["funding_cost"], 1.0)


if __name__ == "__main__":
    unittest.main()
