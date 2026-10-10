import unittest

from validation.download_funding_rates import parse_archive_row, utc_ms


class FundingArchiveParserTests(unittest.TestCase):
    def test_two_column_archive_uses_second_column_as_rate(self):
        ts = utc_ms("2025-01-01T00:00:00Z")
        row = parse_archive_row([str(ts), "0.0001"], "BTCUSDT", ts, ts + 1)
        self.assertEqual(row["funding_rate"], "0.0001")

    def test_three_column_archive_uses_last_column_not_interval_hours(self):
        ts = utc_ms("2025-01-01T00:00:00Z")
        row = parse_archive_row([str(ts), "8", "0.0001"], "BTCUSDT", ts, ts + 1)
        self.assertEqual(row["funding_rate"], "0.0001")

    def test_archive_parser_filters_exclusive_end(self):
        ts = utc_ms("2025-01-01T00:00:00Z")
        self.assertIsNone(parse_archive_row([str(ts + 1), "8", "0.0001"], "BTCUSDT", ts, ts + 1))

    def test_archive_parser_rejects_non_numeric_rate(self):
        ts = utc_ms("2025-01-01T00:00:00Z")
        with self.assertRaisesRegex(RuntimeError, "No plausible decimal funding rate"):
            parse_archive_row([str(ts), "8", "not-a-rate"], "BTCUSDT", ts, ts + 1)

    def test_three_column_archive_with_interval_last_uses_decimal_rate(self):
        ts = utc_ms("2025-01-01T00:00:00Z")
        row = parse_archive_row([str(ts), "0.0001", "8"], "BTCUSDT", ts, ts + 1)
        self.assertEqual(row["funding_rate"], "0.0001")

    def test_rejects_interval_hours_misread_as_rate(self):
        ts = utc_ms("2025-01-01T00:00:00Z")
        with self.assertRaisesRegex(RuntimeError, "No plausible decimal funding rate"):
            parse_archive_row([str(ts), "8"], "BTCUSDT", ts, ts + 1)

    def test_ignores_interval_hours_after_decimal_rate(self):
        ts = utc_ms("2025-01-01T00:00:00Z")
        row = parse_archive_row([str(ts), "0.0001", "8"], "BTCUSDT", ts, ts + 1)
        self.assertEqual(row["funding_rate"], "0.0001")


if __name__ == "__main__":
    unittest.main()
