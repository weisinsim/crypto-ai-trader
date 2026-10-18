import csv
import hashlib
import tempfile
import unittest
from pathlib import Path

from validation.audit_historical_data import audit


class HistoricalDataAuditTests(unittest.TestCase):
    def write_csv(self, rows, include_volume=True):
        tmp = tempfile.NamedTemporaryFile(mode="w", newline="", suffix=".csv", delete=False, encoding="utf-8")
        with tmp:
            fields = ["timestamp", "open", "high", "low", "close"]
            if include_volume:
                fields.append("volume")
            writer = csv.DictWriter(tmp, fieldnames=fields)
            writer.writeheader()
            writer.writerows(
                ({**row, **({"volume": row.get("volume", "5")} if include_volume else {})} for row in rows)
            )
        self.addCleanup(lambda: Path(tmp.name).unlink(missing_ok=True))
        return Path(tmp.name)

    def test_valid_contiguous_candles_pass(self):
        rows = [
            {"timestamp": "2025-01-01T00:59:59.999Z", "open": "100", "high": "102", "low": "99", "close": "101"},
            {"timestamp": "2025-01-01T01:59:59.999Z", "open": "101", "high": "103", "low": "100", "close": "102"},
        ]
        path = self.write_csv(rows)
        result = audit(path, "1h")
        self.assertTrue(result["pass_basic_integrity"])
        self.assertEqual(result["gap_count"], 0)
        source_bytes = path.read_bytes()
        self.assertEqual(result["sha256"], hashlib.sha256(source_bytes).hexdigest())
        self.assertEqual(result["size_bytes"], len(source_bytes))

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

    def test_negative_volume_fails_integrity_when_volume_column_exists(self):
        tmp = tempfile.NamedTemporaryFile(mode="w", newline="", suffix=".csv", delete=False, encoding="utf-8")
        with tmp:
            writer = csv.DictWriter(tmp, fieldnames=["timestamp", "open", "high", "low", "close", "volume"])
            writer.writeheader()
            writer.writerow({"timestamp": "2025-01-01T00:59:59.999Z", "open": "100", "high": "102",
                             "low": "99", "close": "101", "volume": "-1"})
        path = Path(tmp.name)
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        result = audit(path, "1h")
        self.assertFalse(result["pass_basic_integrity"])
        self.assertEqual(result["invalid_volume_rows"], 1)

    def test_non_finite_quote_volume_fails_integrity(self):
        tmp = tempfile.NamedTemporaryFile(mode="w", newline="", suffix=".csv", delete=False, encoding="utf-8")
        with tmp:
            writer = csv.DictWriter(tmp, fieldnames=["timestamp", "open", "high", "low", "close", "quote_volume"])
            writer.writeheader()
            writer.writerow({"timestamp": "2025-01-01T00:59:59.999Z", "open": "100", "high": "102",
                             "low": "99", "close": "101", "quote_volume": "nan"})
        path = Path(tmp.name)
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        result = audit(path, "1h")
        self.assertFalse(result["pass_basic_integrity"])
        self.assertEqual(result["invalid_volume_rows"], 1)

    def test_normalized_archive_schema_with_epoch_ms_and_short_ohlc_names_passes(self):
        tmp = tempfile.NamedTemporaryFile(mode="w", newline="", suffix=".csv", delete=False, encoding="utf-8")
        with tmp:
            writer = csv.DictWriter(tmp, fieldnames=["t", "o", "h", "l", "c", "v", "q"])
            writer.writeheader()
            writer.writerow({"t": 1_735_689_600_000, "o": "100", "h": "102", "l": "99",
                             "c": "101", "v": "5", "q": "505"})
            writer.writerow({"t": 1_735_693_200_000, "o": "101", "h": "103", "l": "100",
                             "c": "102", "v": "6", "q": "612"})
        path = Path(tmp.name)
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        result = audit(path, "1h")
        self.assertTrue(result["pass_basic_integrity"])
        self.assertEqual(result["rows"], 2)
        self.assertEqual(result["gap_count"], 0)


    def test_missing_volume_column_fails_integrity(self):
        rows = [
            {"timestamp": "2025-01-01T00:59:59.999Z", "open": "100", "high": "102", "low": "99", "close": "101"},
            {"timestamp": "2025-01-01T01:59:59.999Z", "open": "101", "high": "103", "low": "100", "close": "102"},
        ]
        result = audit(self.write_csv(rows, include_volume=False), "1h")
        self.assertFalse(result["pass_basic_integrity"])
        self.assertTrue(result["missing_volume_column"])

    def test_unsupported_interval_is_rejected(self):
        row = {"timestamp": "2025-01-01T00:59:59.999Z", "open": "100", "high": "102", "low": "99", "close": "101"}
        with self.assertRaises(ValueError):
            audit(self.write_csv([row]), "3h")


if __name__ == "__main__":
    unittest.main()
