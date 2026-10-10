import tempfile
import unittest
from pathlib import Path

from validation.audit_funding_history import audit


class FundingHistoryAuditTests(unittest.TestCase):
    def write_csv(self, rows):
        tmp = tempfile.TemporaryDirectory()
        path = Path(tmp.name) / "funding.csv"
        path.write_text("timestamp,funding_rate\n" + "".join(
            f"{timestamp},{rate}\n" for timestamp, rate in rows
        ), encoding="utf-8")
        self.addCleanup(tmp.cleanup)
        return path

    def test_accepts_ordered_events_within_gap_threshold(self):
        path = self.write_csv([
            ("2025-01-01T00:00:00Z", "0.0001"),
            ("2025-01-01T08:00:00Z", "-0.0001"),
            ("2025-01-01T16:00:00Z", "0.0002"),
        ])
        result = audit(path, max_gap_hours=24)
        self.assertTrue(result["pass_basic_integrity"])
        self.assertEqual(result["rows"], 3)

    def test_flags_large_internal_gap(self):
        path = self.write_csv([
            ("2025-01-01T00:00:00Z", "0.0001"),
            ("2025-01-03T00:00:00Z", "-0.0001"),
        ])
        result = audit(path, max_gap_hours=24)
        self.assertFalse(result["pass_basic_integrity"])
        self.assertEqual(result["gaps_over_threshold"], 1)

    def test_flags_duplicate_timestamps(self):
        path = self.write_csv([
            ("2025-01-01T00:00:00Z", "0.0001"),
            ("2025-01-01T00:00:00Z", "0.0002"),
        ])
        self.assertFalse(audit(path)["pass_basic_integrity"])

    def test_rejects_timezone_naive_timestamp(self):
        path = self.write_csv([("2025-01-01T00:00:00", "0.0001")])
        with self.assertRaisesRegex(ValueError, "must include timezone"):
            audit(path)

    def test_rejects_nonfinite_rate(self):
        path = self.write_csv([("2025-01-01T00:00:00Z", "NaN")])
        with self.assertRaisesRegex(ValueError, "Non-finite"):
            audit(path)

    def test_rejects_implausible_funding_rate_unit_error(self):
        path = self.write_csv([("2025-01-01T00:00:00Z", "8")])
        with self.assertRaisesRegex(ValueError, "sanity limit"):
            audit(path)

    def test_empty_file_fails_closed(self):
        path = self.write_csv([])
        self.assertFalse(audit(path)["pass_basic_integrity"])


if __name__ == "__main__":
    unittest.main()
