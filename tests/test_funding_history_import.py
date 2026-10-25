import unittest

from research.funding_history_import import import_funding_csv


class FundingHistoryImportTests(unittest.TestCase):
    def test_imports_iso8601_events_and_creates_digest_manifest(self):
        source = (
            "symbol,fundingTime,fundingRate\n"
            "BTCUSDT,2025-01-01T00:00:00Z,0.001\n"
            "BTCUSDT,2025-01-01T08:00:00Z,-0.0005\n"
        )
        result = import_funding_csv(
            source, symbol="BTCUSDT",
            start_ts_ms=1735689600000, end_ts_ms=1735747200000,
            expected_interval_ms=28_800_000,
        )
        self.assertEqual(len(result["events"]), 2)
        self.assertEqual(result["manifest"]["row_count"], 2)
        self.assertEqual(len(result["manifest"]["sha256"]), 64)
        self.assertTrue(result["manifest"]["cadence_check_passed"])
        self.assertFalse(result["manifest"]["source_verified"])
        self.assertFalse(result["manifest"]["coverage_verified"])
        self.assertFalse(result["auto_trade_authorized"])

    def test_detects_missing_intermediate_settlement(self):
        source = (
            "fundingTime,fundingRate\n"
            "2025-01-01T00:00:00Z,0.001\n"
            "2025-01-01T16:00:00Z,0.001\n"
        )
        result = import_funding_csv(
            source, symbol="BTCUSDT",
            start_ts_ms=1735689600000, end_ts_ms=1735776000000,
            expected_interval_ms=28_800_000,
        )
        self.assertEqual(
            result["manifest"]["missing_expected_timestamps_ms"],
            [1735718400000],
        )
        self.assertFalse(result["manifest"]["cadence_check_passed"])

    def test_empty_window_does_not_pass_expected_cadence(self):
        source = "fundingTime,fundingRate\n"
        result = import_funding_csv(
            source, symbol="BTCUSDT",
            start_ts_ms=1735689600000, end_ts_ms=1735776000000,
            expected_interval_ms=28_800_000,
        )
        self.assertEqual(result["events"], [])
        self.assertFalse(result["manifest"]["cadence_check_passed"])
        self.assertEqual(result["manifest"]["cadence_check_status"], "INSUFFICIENT_EVENTS")
        self.assertFalse(result["manifest"]["cadence_boundary_coverage_verified"])
        self.assertFalse(result["manifest"]["coverage_verified"])

    def test_rejects_ambiguous_numeric_timestamps(self):
        source = "fundingTime,fundingRate\n1735689600000,0.001\n"
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            import_funding_csv(
                source, symbol="BTCUSDT",
                start_ts_ms=0, end_ts_ms=2_000_000_000_000,
            )

    def test_rejects_timezone_naive_timestamps(self):
        source = "fundingTime,fundingRate\n2025-01-01T00:00:00,0.001\n"
        with self.assertRaisesRegex(ValueError, "timezone"):
            import_funding_csv(
                source, symbol="BTCUSDT",
                start_ts_ms=0, end_ts_ms=2_000_000_000_000,
            )

    def test_rejects_mixed_symbols(self):
        source = (
            "symbol,fundingTime,fundingRate\n"
            "ETHUSDT,2025-01-01T00:00:00Z,0.001\n"
        )
        with self.assertRaisesRegex(ValueError, "symbol mismatch"):
            import_funding_csv(
                source, symbol="BTCUSDT",
                start_ts_ms=0, end_ts_ms=2_000_000_000_000,
            )

    def test_rejects_missing_columns(self):
        with self.assertRaisesRegex(ValueError, "requires"):
            import_funding_csv(
                "time,rate\n1,0.001\n", symbol="BTCUSDT",
                start_ts_ms=0, end_ts_ms=10000,
            )


if __name__ == "__main__":
    unittest.main()
