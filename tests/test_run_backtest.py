import csv
import tempfile
import unittest
from pathlib import Path

from research.run_backtest import (build_walk_forward_windows, load_csv, load_funding_csv,
                                   max_drawdown_pct)


class RunnerInputTests(unittest.TestCase):
    def test_walk_forward_windows_cover_evaluation_range_without_gaps(self):
        windows = build_walk_forward_windows(n=103, start_eval=40, folds=3, min_bars=10)
        self.assertEqual(windows[0][0], 40)
        self.assertEqual(windows[-1][1], 103)
        self.assertTrue(all(left[1] == right[0] for left, right in zip(windows, windows[1:])))
        self.assertEqual(sum(end - start for start, end in windows), 63)

    def test_walk_forward_windows_reject_too_short_folds(self):
        with self.assertRaisesRegex(ValueError, "insufficient bars per walk-forward fold"):
            build_walk_forward_windows(n=100, start_eval=80, folds=4, min_bars=6)

    def test_walk_forward_windows_reject_single_fold(self):
        with self.assertRaisesRegex(ValueError, "folds must be >= 2"):
            build_walk_forward_windows(n=100, start_eval=60, folds=1, min_bars=5)

    def test_max_drawdown_uses_only_supplied_window_points(self):
        earlier_history = [{"t": 0, "equity": 1.0}, {"t": 1, "equity": 0.5}]
        later_window = [{"t": 2, "equity": 1.0}, {"t": 3, "equity": 0.9}]
        self.assertEqual(max_drawdown_pct(later_window), 10.0)
        self.assertEqual(max_drawdown_pct(earlier_history), 50.0)

    def test_max_drawdown_includes_starting_equity_peak(self):
        first_bar_loss = [{"t": 10, "equity": 0.8}, {"t": 11, "equity": 0.9}]
        self.assertEqual(max_drawdown_pct(first_bar_loss, initial_equity=1.0), 20.0)

    def test_max_drawdown_empty_curve_is_zero(self):
        self.assertEqual(max_drawdown_pct([]), 0.0)

    def test_max_drawdown_rejects_non_positive_or_non_finite_starting_equity(self):
        for initial in (0, -1, float("nan"), float("inf")):
            with self.subTest(initial=initial):
                with self.assertRaisesRegex(ValueError, "initial_equity must be finite and positive"):
                    max_drawdown_pct([], initial_equity=initial)

    def test_max_drawdown_rejects_non_finite_curve_equity(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "non-finite values"):
                    max_drawdown_pct([{"t": 1, "equity": value}])


    def _write_csv(self, timestamps):
        handle = tempfile.NamedTemporaryFile(mode="w", newline="", encoding="utf-8", delete=False)
        writer = csv.DictWriter(handle, fieldnames=("t", "o", "h", "l", "c", "v", "q"))
        writer.writeheader()
        for ts in timestamps:
            writer.writerow({"t": ts, "o": 100, "h": 101, "l": 99, "c": 100, "v": 10, "q": 1000})
        handle.close()
        self.addCleanup(Path(handle.name).unlink, missing_ok=True)
        return handle.name

    def test_load_csv_accepts_contiguous_hourly_bars(self):
        path = self._write_csv([0, 3_600_000, 7_200_000])
        self.assertEqual(len(load_csv(path, 3_600_000)), 3)

    def test_load_csv_rejects_missing_hourly_bar(self):
        path = self._write_csv([0, 7_200_000])
        with self.assertRaisesRegex(ValueError, "continuity"):
            load_csv(path, 3_600_000)

    def test_load_csv_rejects_empty_file(self):
        path = self._write_csv([])
        self.assertEqual(load_csv(path, 3_600_000), [])

    def _write_funding_csv(self, records):
        handle = tempfile.NamedTemporaryFile(mode="w", newline="", encoding="utf-8", delete=False)
        writer = csv.DictWriter(handle, fieldnames=("funding_time", "funding_rate", "mark_price", "symbol"))
        writer.writeheader()
        writer.writerows(records)
        handle.close()
        self.addCleanup(Path(handle.name).unlink, missing_ok=True)
        return handle.name

    def test_load_funding_csv_sorts_and_preserves_mark_price(self):
        path = self._write_funding_csv([
            {"funding_time": 200, "funding_rate": "0.001", "mark_price": "101", "symbol": "BTCUSDT"},
            {"funding_time": 100, "funding_rate": "-0.001", "mark_price": "99", "symbol": "BTCUSDT"},
        ])
        rows = load_funding_csv(path)
        self.assertEqual([row["funding_time"] for row in rows], [100, 200])
        self.assertEqual(rows[0]["mark_price"], 99.0)

    def test_load_funding_csv_rejects_duplicate_timestamps(self):
        record = {"funding_time": 100, "funding_rate": "0.001", "mark_price": "100", "symbol": "BTCUSDT"}
        path = self._write_funding_csv([record, record])
        with self.assertRaisesRegex(ValueError, "duplicate funding timestamp"):
            load_funding_csv(path)

    def test_load_funding_csv_rejects_mixed_symbols(self):
        path = self._write_funding_csv([
            {"funding_time": 100, "funding_rate": "0.001", "mark_price": "100", "symbol": "BTCUSDT"},
            {"funding_time": 200, "funding_rate": "0.001", "mark_price": "101", "symbol": "ETHUSDT"},
        ])
        with self.assertRaisesRegex(ValueError, "exactly one"):
            load_funding_csv(path)

    def test_load_funding_csv_rejects_negative_timestamp(self):
        path = self._write_funding_csv([
            {"funding_time": -1, "funding_rate": "0.001", "mark_price": "100", "symbol": "BTCUSDT"},
        ])
        with self.assertRaisesRegex(ValueError, "negative settlement timestamps"):
            load_funding_csv(path)

    def test_load_funding_csv_rejects_non_finite_rate(self):
        path = self._write_funding_csv([
            {"funding_time": 100, "funding_rate": "nan", "mark_price": "100", "symbol": "BTCUSDT"},
        ])
        with self.assertRaisesRegex(ValueError, "non-finite rates"):
            load_funding_csv(path)

    def test_load_funding_csv_rejects_invalid_mark_price(self):
        path = self._write_funding_csv([
            {"funding_time": 100, "funding_rate": "0.001", "mark_price": "0", "symbol": "BTCUSDT"},
        ])
        with self.assertRaisesRegex(ValueError, "invalid mark prices"):
            load_funding_csv(path)

    def test_load_funding_csv_rejects_empty_symbol(self):
        path = self._write_funding_csv([
            {"funding_time": 100, "funding_rate": "0.001", "mark_price": "100", "symbol": ""},
        ])
        with self.assertRaisesRegex(ValueError, "exactly one"):
            load_funding_csv(path)


if __name__ == "__main__":
    unittest.main()
