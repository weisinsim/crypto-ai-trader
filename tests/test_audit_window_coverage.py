import csv
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from validation.audit_window_coverage import audit_file, utc_ms


class WindowCoverageTests(unittest.TestCase):
    def write_rows(self, path, timestamps):
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=["timestamp"])
            writer.writeheader()
            writer.writerows({"timestamp": stamp} for stamp in timestamps)

    def stamps_for_indices(self, start, indices):
        return [
            datetime.fromtimestamp((start + (i + 1) * 3_600_000 - 1) / 1000,
                                   tz=timezone.utc).isoformat().replace("+00:00", "Z")
            for i in indices
        ]

    def test_passes_exact_hourly_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "BTCUSDT_1h.csv"
            start = utc_ms("2025-01-01")
            self.write_rows(path, self.stamps_for_indices(start, range(4)))
            result = audit_file(path, "1h", start, start + 4 * 3_600_000)
            self.assertTrue(result["pass_window_coverage"])
            self.assertEqual(result["expected_rows"], 4)

    def test_fails_when_expected_candle_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "BTCUSDT_1h.csv"
            start = utc_ms("2025-01-01")
            self.write_rows(path, self.stamps_for_indices(start, (0, 2, 3)))
            result = audit_file(path, "1h", start, start + 4 * 3_600_000)
            self.assertFalse(result["pass_window_coverage"])
            self.assertEqual(result["missing_expected_rows"], 1)

    def test_fails_when_file_contains_rows_outside_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "BTCUSDT_1h.csv"
            start = utc_ms("2025-01-01")
            stamps = self.stamps_for_indices(start, range(5))
            self.write_rows(path, stamps)
            result = audit_file(path, "1h", start, start + 4 * 3_600_000)
            self.assertFalse(result["pass_window_coverage"])

    def test_fails_when_timestamp_is_duplicated(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "BTCUSDT_1h.csv"
            start = utc_ms("2025-01-01")
            stamps = self.stamps_for_indices(start, (0, 1, 1, 2, 3))
            self.write_rows(path, stamps)
            result = audit_file(path, "1h", start, start + 4 * 3_600_000)
            self.assertFalse(result["pass_window_coverage"])
            self.assertEqual(result["duplicate_timestamps_in_window"], 1)

    def test_fails_when_timestamps_are_out_of_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "BTCUSDT_1h.csv"
            start = utc_ms("2025-01-01")
            self.write_rows(path, self.stamps_for_indices(start, (0, 2, 1, 3)))
            result = audit_file(path, "1h", start, start + 4 * 3_600_000)
            self.assertFalse(result["pass_window_coverage"])
            self.assertEqual(result["out_of_order_or_duplicate_adjacent_pairs"], 1)


if __name__ == "__main__":
    unittest.main()
