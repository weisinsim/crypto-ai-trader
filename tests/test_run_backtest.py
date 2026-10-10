import csv
import tempfile
import unittest
from pathlib import Path

from research.run_backtest import load_csv


class RunnerInputTests(unittest.TestCase):
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
