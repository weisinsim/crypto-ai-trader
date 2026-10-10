import unittest

from validation.candle_closure import filter_closed_candles


class ClosedCandleFilterTests(unittest.TestCase):
    def setUp(self):
        self.rows = [
            {"t": 0, "c": 100},
            {"t": 3_600_000, "c": 101},
            {"t": 7_200_000, "c": 102},
        ]

    def test_excludes_in_progress_hourly_candle(self):
        result = filter_closed_candles(self.rows, "1h", 7_200_001)
        self.assertEqual([r["t"] for r in result], [0, 3_600_000])

    def test_includes_candle_once_interval_elapsed(self):
        result = filter_closed_candles(self.rows, "1h", 7_200_000)
        self.assertEqual([r["t"] for r in result], [0, 3_600_000])

    def test_four_hour_filter_uses_four_hour_duration(self):
        rows = [{"t": 0}, {"t": 14_400_000}, {"t": 28_800_000}]
        result = filter_closed_candles(rows, "4h", 28_800_000)
        self.assertEqual([r["t"] for r in result], [0, 14_400_000])

    def test_unknown_interval_rejected(self):
        with self.assertRaises(ValueError):
            filter_closed_candles(self.rows, "5m", 100_000_000)


if __name__ == "__main__":
    unittest.main()
