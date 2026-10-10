import unittest
import tempfile
from pathlib import Path

from validation.replay_dashboard_signals import ema, rsi, atr, replay, ts_ms, read_rows, closed_rows_at


class ReplayDashboardSignalsTests(unittest.TestCase):
    def test_timezone_naive_timestamp_rejected(self):
        with self.assertRaises(ValueError):
            ts_ms("2025-01-01T00:00:00")

    def test_invalid_ohlc_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "bad.csv"
            path.write_text("timestamp,open,high,low,close,volume,quote_volume\n2025-01-01T01:00:00Z,100,99,98,100,1,100\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                read_rows(path)

    def test_missing_volume_columns_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "missing_volume.csv"
            path.write_text("timestamp,open,high,low,close\\n2025-01-01T01:00:00Z,100,101,99,100\\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Missing required columns"):
                read_rows(path)

    def test_malformed_numeric_field_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "bad_number.csv"
            path.write_text("timestamp,open,high,low,close,volume,quote_volume\\n2025-01-01T01:00:00Z,not-a-number,101,99,100,1,100\\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Invalid timestamp or numeric OHLCV field"):
                read_rows(path)

    def test_non_finite_volume_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "bad_volume.csv"
            path.write_text("timestamp,open,high,low,close,volume,quote_volume\n2025-01-01T01:00:00Z,100,101,99,100,NaN,100\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                read_rows(path)

    def test_only_closed_higher_timeframe_bars_are_used(self):
        bars = [{"ts": 100, "c": 1}, {"ts": 200, "c": 2}, {"ts": 300, "c": 3}]
        self.assertEqual(closed_rows_at(bars, 200), bars[:2])

    def test_ema_seed_and_trend(self):
        self.assertEqual(ema([1, 2, 3], 2), 2.5)
        self.assertGreater(ema(list(range(1, 30)), 5), ema(list(range(1, 30)), 10))

    def test_rsi_rising_series_is_100(self):
        self.assertEqual(rsi(list(range(1, 30))), 100.0)

    def test_atr_positive(self):
        close = [100 + i for i in range(20)]
        high = [x + 2 for x in close]
        low = [x - 1 for x in close]
        self.assertGreater(atr(high, low, close), 0)

    def test_eth_is_suppressed(self):
        one = []
        four = []
        for i in range(240):
            t = 1_700_000_000_000 + i * 3_600_000
            c = 100 + i * 0.2
            one.append({"timestamp": "2023-11-14T00:00:00Z", "ts": t, "o": c, "h": c+1, "l": c-1, "c": c, "v": 10, "q": 1000})
        for i in range(70):
            t = 1_700_000_000_000 + (i+1)*14_400_000 - 1
            c = 100 + i * 0.8
            four.append({"timestamp": "2023-11-14T00:00:00Z", "ts": t, "o": c, "h": c+2, "l": c-1, "c": c, "v": 40, "q": 4000})
        self.assertEqual(replay("ETHUSDT", one, four), [])


if __name__ == "__main__":
    unittest.main()
