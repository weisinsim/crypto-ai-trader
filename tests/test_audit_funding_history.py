import hashlib
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
        source_bytes = path.read_bytes()
        self.assertEqual(result["sha256"], hashlib.sha256(source_bytes).hexdigest())
        self.assertEqual(result["size_bytes"], len(source_bytes))

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


    def test_flags_leading_funding_gap_outside_requested_window(self):
        path = self.write_csv([
            ("2025-01-01T12:00:00Z", "0.0001"),
            ("2025-01-01T20:00:00Z", "-0.0001"),
        ])
        start_ms = 1735689600000  # 2025-01-01T00:00:00Z
        result = audit(path, max_gap_hours=8, start_ms=start_ms,
                       end_ms=1735776000000)
        self.assertFalse(result["pass_basic_integrity"])
        self.assertEqual(result["leading_gap_hours"], 12.0)

    def test_accepts_funding_events_within_window_boundaries(self):
        path = self.write_csv([
            ("2025-01-01T00:00:00Z", "0.0001"),
            ("2025-01-01T08:00:00Z", "-0.0001"),
            ("2025-01-01T16:00:00Z", "0.0002"),
        ])
        result = audit(path, max_gap_hours=8, start_ms=1735689600000,
                       end_ms=1735776000000)
        self.assertTrue(result["pass_basic_integrity"])
        self.assertEqual(result["leading_gap_hours"], 0.0)
        self.assertEqual(result["trailing_gap_hours"], 8.0)

    def test_rejects_funding_event_at_exclusive_end_boundary(self):
        path = self.write_csv([
            ("2025-01-01T08:00:00Z", "0.0001"),
            ("2025-01-02T00:00:00Z", "-0.0001"),
        ])
        result = audit(path, max_gap_hours=8, start_ms=1735689600000,
                       end_ms=1735776000000)
        self.assertFalse(result["pass_basic_integrity"])
        self.assertEqual(result["trailing_gap_hours"], 0.0)
        self.assertEqual(result["events_outside_requested_window"], 1)
        self.assertEqual(result["outside_window_timestamp_examples_ms"], [1735776000000])

    def test_flags_trailing_funding_gap_outside_requested_window(self):
        path = self.write_csv([
            ("2025-01-01T00:00:00Z", "0.0001"),
            ("2025-01-01T08:00:00Z", "-0.0001"),
        ])
        result = audit(path, max_gap_hours=8, start_ms=1735689600000,
                       end_ms=1735776000000)
        self.assertFalse(result["pass_basic_integrity"])
        self.assertEqual(result["trailing_gap_hours"], 16.0)

    def test_rejects_invalid_audit_thresholds(self):
        path = self.write_csv([("2025-01-01T00:00:00Z", "0.0001")])
        for kwargs in (
            {"max_gap_hours": 0},
            {"max_gap_hours": float("inf")},
            {"max_gap_hours": True},
            {"max_abs_rate": 0},
            {"max_abs_rate": float("nan")},
            {"max_abs_rate": True},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaisesRegex(ValueError, "must be a finite number > 0"):
                    audit(path, **kwargs)

    def test_rejects_invalid_requested_window_arguments(self):
        path = self.write_csv([("2025-01-01T00:00:00Z", "0.0001")])
        with self.assertRaisesRegex(ValueError, "end_ms must be later"):
            audit(path, start_ms=2000, end_ms=1000)
        with self.assertRaisesRegex(ValueError, "start_ms must be an integer"):
            audit(path, start_ms=True)

    def test_empty_file_fails_closed(self):
        path = self.write_csv([])
        self.assertFalse(audit(path)["pass_basic_integrity"])


if __name__ == "__main__":
    unittest.main()
