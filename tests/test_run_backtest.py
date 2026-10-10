import csv
import tempfile
import unittest
from pathlib import Path

from research.run_backtest import load_csv, load_funding_csv, max_drawdown_pct


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


if __name__ == "__main__":
    unittest.main()
