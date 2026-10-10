import csv
import tempfile
import unittest
from pathlib import Path

from validation.audit_historical_data import audit


class HistoricalDataAuditTests(unittest.TestCase):
    def write_csv(self, rows):
        tmp = tempfile.NamedTemporaryFile(mode="w", newline="", suffix=".csv", delete=False, encoding="utf-8")
        with tmp:
            writer = csv.DictWriter(tmp, fieldnames=["timestamp", "open", "high", "low", "close"])
            writer.writeheader()
            writer.writerows(rows)
        self.addCleanup(lambda: Path(tmp.name).unlink(missing_ok=True))
        return Path(tmp.name)

    def test_valid_contiguous_candles_pass(self):
        rows = [
            {"timestamp": "2025-01-01T00:59:59.999Z", "open": "100", "high": "102", "low": "99", "close": "101"},
            {"timestamp": "2025-01-01T01:59:59.999Z", "open": "101", "high": "103", "low": "100", "close": "102"},
        ]
        result = audit(self.write_csv(rows), "1h")
        self.assertTrue(result["pass_basic_integrity"])
        self.assertEqual(result["gap_count"], 0)

    def test_gap_is_reported(self):
        rows = [
            {"timestamp": "2025-01-01T00:59:59.999Z", "open": "100", "high": "102", "low": "99", "close": "101"},
            {"timestamp": "2025-01-01T02:59:59.999Z", "open": "101", "high": "103", "low": "100", "close": "102"},
        ]
        result = audit(self.write_csv(rows), "1h")
        self.assertEqual(result["gap_count"], 1)
        self.assertEqual(result["missing_candles_approx"], 1)

    def test_irregular_cadence_fails(self):
        rows = [
            {"timestamp": "2025-01-01T00:59:59.999Z", "open": "100", "high": "102", "low": "99", "close": "101"},
            {"timestamp": "2025-01-01T01:29:59.999Z", "open": "101", "high": "103", "low": "100", "close": "102"},
        ]
        result = audit(self.write_csv(rows), "1h")
        self.assertFalse(result["pass_basic_integrity"])
        self.assertEqual(result["cadence_anomaly_count"], 1)

    def test_invalid_ohlc_fails(self):
        rows = [{"timestamp": "2025-01-01T00:59:59.999Z", "open": "100", "high": "99", "low": "98", "close": "100"}]
        result = audit(self.write_csv(rows), "1h")
        self.assertFalse(result["pass_basic_integrity"])
        self.assertEqual(result["invalid_ohlc_rows"], 1)

    def test_timezone_naive_timestamp_fails_closed(self):
        rows = [{"timestamp": "2025-01-01T00:59:59.999", "open": "100", "high": "102", "low": "99", "close": "101"}]
        result = audit(self.write_csv(rows), "1h")
        self.assertFalse(result["pass_basic_integrity"])
        self.assertEqual(result["timestamp_errors"], 1)

    def test_non_finite_ohlc_fails(self):
        rows = [{"timestamp": "2025-01-01T00:59:59.999Z", "open": "NaN", "high": "102", "low": "99", "close": "101"}]
        result = audit(self.write_csv(rows), "1h")
        self.assertFalse(result["pass_basic_integrity"])
        self.assertEqual(result["invalid_ohlc_rows"], 1)

    def test_unsupported_interval_is_rejected(self):
        row = {"timestamp": "2025-01-01T00:59:59.999Z", "open": "100", "high": "102", "low": "99", "close": "101"}
        with self.assertRaises(ValueError):
            audit(self.write_csv([row]), "3h")


if __name__ == "__main__":
    unittest.main()
