import unittest

from research.chronological_holdout import partition_chronological_holdout


class ChronologicalHoldoutTests(unittest.TestCase):
    def test_partition_uses_signal_timestamp_and_includes_boundary_in_holdout(self):
        rows = [
            {"trade_id": "late-exit-dev", "signal_ts": 10, "exit_ts": 120},
            {"trade_id": "at-cutoff", "signal_ts": 100, "exit_ts": 110},
            {"trade_id": "early", "signal_ts": 1, "exit_ts": 2},
        ]
        result = partition_chronological_holdout(rows, cutoff_ts=100)
        self.assertEqual([r["trade_id"] for r in result["development"]], ["early", "late-exit-dev"])
        self.assertEqual([r["trade_id"] for r in result["holdout"]], ["at-cutoff"])
        self.assertTrue(result["cohorts_disjoint"])
        self.assertFalse(result["holdout_unseen_or_untuned_verified"])

    def test_duplicate_ids_rejected(self):
        row = {"trade_id": "x", "signal_ts": 1, "exit_ts": 2}
        with self.assertRaisesRegex(ValueError, "duplicate trade_id"):
            partition_chronological_holdout([row, row], cutoff_ts=10)

    def test_exit_before_signal_rejected(self):
        with self.assertRaisesRegex(ValueError, "exits before"):
            partition_chronological_holdout(
                [{"trade_id": "bad", "signal_ts": 10, "exit_ts": 9}], cutoff_ts=10
            )

    def test_timestamps_must_be_integer_milliseconds(self):
        with self.assertRaisesRegex(ValueError, "signal_ts"):
            partition_chronological_holdout(
                [{"trade_id": "bad", "signal_ts": 1.5, "exit_ts": 2}], cutoff_ts=10
            )

    def test_empty_input_is_safe(self):
        result = partition_chronological_holdout([], cutoff_ts=10)
        self.assertEqual(result["development_count"], 0)
        self.assertEqual(result["holdout_count"], 0)


if __name__ == "__main__":
    unittest.main()
