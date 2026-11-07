import unittest

from notifications.pionex_recovery_window import (
    RecoveryWindowError, recovery_window,
)


class RecoveryWindowTests(unittest.TestCase):
    def test_short_gap_rechecks_overlap(self):
        window = recovery_window(1000000, 1200000, overlap_ms=100000)
        self.assertEqual(window["start_ms"], 900000)
        self.assertEqual(window["end_ms"], 1200000)
        self.assertFalse(window["more_history_pending"])
        self.assertFalse(window["safe_to_advance"])

    def test_long_gap_requires_multiple_windows(self):
        window = recovery_window(1000000, 9000000,
                                 overlap_ms=100000, max_span_ms=500000)
        self.assertEqual(window["start_ms"], 900000)
        self.assertEqual(window["end_ms"], 1400000)
        self.assertTrue(window["more_history_pending"])

    def test_first_run_requires_explicit_baseline(self):
        with self.assertRaises(RecoveryWindowError):
            recovery_window(None, 1000000)

    def test_future_checkpoint_rejected(self):
        with self.assertRaises(RecoveryWindowError):
            recovery_window(2000000, 1000000)

    def test_invalid_bounds_rejected(self):
        for value in (True, -1, 0, 1.5):
            with self.assertRaises(RecoveryWindowError):
                recovery_window(1000000, 2000000, max_span_ms=value)

    def test_overlap_larger_than_span_cannot_stall_recovery(self):
        with self.assertRaisesRegex(RecoveryWindowError, "cannot advance"):
            recovery_window(1000000, 9000000, overlap_ms=500000,
                            max_span_ms=500000)

    def test_overlap_smaller_than_span_advances(self):
        window = recovery_window(1000000, 9000000, overlap_ms=499999,
                                 max_span_ms=500000)
        self.assertEqual(window["end_ms"], 1000001)
        self.assertTrue(window["more_history_pending"])

    def test_overlap_clamps_at_one(self):
        window = recovery_window(5, 10, overlap_ms=100)
        self.assertEqual(window["start_ms"], 1)


if __name__ == "__main__":
    unittest.main()
