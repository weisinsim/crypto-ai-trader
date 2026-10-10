import csv
import tempfile
import unittest
from pathlib import Path

from research.run_backtest import load_csv, max_drawdown_pct


class RunnerInputTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
