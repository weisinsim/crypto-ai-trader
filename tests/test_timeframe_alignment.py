import csv
import tempfile
import unittest
from pathlib import Path

from validation.run_multi_asset_exit_audit import check_timeframe_alignment


class TimeframeAlignmentTests(unittest.TestCase):
    def write_candles(self, path, timestamps):
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=["timestamp"])
            writer.writeheader()
            for timestamp in timestamps:
                writer.writerow({"timestamp": timestamp})

    def test_passes_when_every_four_hour_close_exists_in_hourly_series(self):
        with tempfile.TemporaryDirectory() as tmp:
            hourly = Path(tmp) / "hourly.csv"
            four_hour = Path(tmp) / "four_hour.csv"
            hourly_times = [
                "2025-01-01T00:59:59.999Z",
                "2025-01-01T01:59:59.999Z",
                "2025-01-01T02:59:59.999Z",
                "2025-01-01T03:59:59.999Z",
                "2025-01-01T04:59:59.999Z",
            ]
            self.write_candles(hourly, hourly_times)
            self.write_candles(four_hour, ["2025-01-01T03:59:59.999Z"])
            result = check_timeframe_alignment(hourly, four_hour)
            self.assertTrue(result["pass"])
            self.assertEqual(result["four_hour_closes_missing_from_1h"], 0)

    def test_fails_when_four_hour_close_is_missing_from_hourly_series(self):
        with tempfile.TemporaryDirectory() as tmp:
            hourly = Path(tmp) / "hourly.csv"
            four_hour = Path(tmp) / "four_hour.csv"
            self.write_candles(hourly, ["2025-01-01T00:59:59.999Z"])
            self.write_candles(four_hour, ["2025-01-01T03:59:59.999Z"])
            result = check_timeframe_alignment(hourly, four_hour)
            self.assertFalse(result["pass"])
            self.assertEqual(result["four_hour_closes_missing_from_1h"], 1)

    def test_fails_when_four_hour_series_is_off_utc_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            hourly = Path(tmp) / "hourly.csv"
            four_hour = Path(tmp) / "four_hour.csv"
            self.write_candles(hourly, [
                "2025-01-01T00:59:59.999Z",
                "2025-01-01T01:59:59.999Z",
                "2025-01-01T02:59:59.999Z",
                "2025-01-01T03:59:59.999Z",
                "2025-01-01T04:59:59.999Z",
            ])
            # This timestamp is hourly-aligned but is not a Binance UTC 4h close.
            self.write_candles(four_hour, ["2025-01-01T01:59:59.999Z"])
            result = check_timeframe_alignment(hourly, four_hour)
            self.assertFalse(result["pass"])
            self.assertEqual(result["four_hour_closes_off_utc_boundary"], 1)


if __name__ == "__main__":
    unittest.main()
