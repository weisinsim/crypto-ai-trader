import unittest

from research.run_backtest import build_walk_forward_windows


class WalkForwardWindowTests(unittest.TestCase):
    def test_windows_are_contiguous_non_overlapping_and_cover_range(self):
        windows = build_walk_forward_windows(n=1000, start_eval=400, folds=4, min_bars=50)
        self.assertEqual(windows, [(400, 550), (550, 700), (700, 850), (850, 1000)])
        self.assertEqual(windows[0][0], 400)
        self.assertEqual(windows[-1][1], 1000)
        for left, right in zip(windows, windows[1:]):
            self.assertEqual(left[1], right[0])

    def test_rejects_too_short_fold_instead_of_silently_skipping(self):
        with self.assertRaisesRegex(ValueError, "insufficient bars per walk-forward fold"):
            build_walk_forward_windows(n=250, start_eval=200, folds=4, min_bars=20)

    def test_rejects_invalid_fold_count(self):
        with self.assertRaisesRegex(ValueError, "folds must be >= 2"):
            build_walk_forward_windows(n=100, start_eval=40, folds=1, min_bars=10)

    def test_rejects_start_outside_data(self):
        with self.assertRaisesRegex(ValueError, "start_eval must be inside"):
            build_walk_forward_windows(n=100, start_eval=100, folds=2, min_bars=10)


if __name__ == "__main__":
    unittest.main()
