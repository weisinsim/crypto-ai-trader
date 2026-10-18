import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from research.binance_data import download_klines, parse_klines, save_csv


def raw_kline(ts, close="100"):
    return [ts, "99", "101", "98", close, "12", ts + 3_600_000 - 1, "1200",
            5, "6", "600", "0"]


class BinanceDataTests(unittest.TestCase):
    def test_parse_excludes_not_yet_closed_candle(self):
        rows = parse_klines([raw_kline(0), raw_kline(3_600_000)], 3_600_000, 3_600_000)
        self.assertEqual([row["t"] for row in rows], [0])

    def test_rejects_short_exchange_row(self):
        with self.assertRaises(ValueError):
            parse_klines([[0, "1"]], 3_600_000, 7_200_000)

    def test_download_paginates_and_checks_coverage(self):
        def fake_fetch(params):
            start = params["startTime"]
            end = params["endTime"]
            return [raw_kline(start)] if start <= end else []
        rows = download_klines("btcusdt", "1h", 0, 3 * 3_600_000,
                               fetch_page=fake_fetch, limit=1, pause_seconds=0)
        self.assertEqual([row["t"] for row in rows], [0, 3_600_000, 7_200_000])

    def test_missing_bar_fails_closed(self):
        def fake_fetch(params):
            start = params["startTime"]
            if start == 0:
                return [raw_kline(0)]
            return [raw_kline(start + 3_600_000)]
        with self.assertRaisesRegex(ValueError, "incomplete coverage"):
            download_klines("BTCUSDT", "1h", 0, 3 * 3_600_000,
                            fetch_page=fake_fetch, limit=1, pause_seconds=0)

    def test_rejects_invalid_symbol(self):
        with self.assertRaisesRegex(ValueError, "symbol"):
            download_klines("BTC/USDT", "1h", 0, 3_600_000,
                            fetch_page=lambda _: [], pause_seconds=0)

    def test_rejects_negative_pause(self):
        with self.assertRaisesRegex(ValueError, "pause_seconds"):
            download_klines("BTCUSDT", "1h", 0, 3_600_000,
                            fetch_page=lambda _: [], pause_seconds=-1)

    def test_requires_aligned_boundaries(self):
        with self.assertRaises(ValueError):
            download_klines("BTCUSDT", "1h", 1, 3_600_000,
                            fetch_page=lambda _: [], pause_seconds=0)

    def test_parse_rejects_noncanonical_timestamp_strings(self):
        for timestamp in (" 0", "0 ", "+0", "0.0", "٠", "²"):
            raw = raw_kline(timestamp)
            with self.subTest(timestamp=timestamp), self.assertRaisesRegex(ValueError, "invalid kline timestamp"):
                parse_klines([raw], 3_600_000, 3_600_000)

    def test_parse_accepts_canonical_ascii_timestamp_string(self):
        rows = parse_klines([raw_kline("0")], 3_600_000, 3_600_000)
        self.assertEqual(rows[0]["t"], 0)

    def test_parse_rejects_malformed_row_shapes_and_timestamps(self):
        for raw in (None, {"t": 0}, [True, "99", "101", "98", "100", "12", 0, "1200"],
                    [0.5, "99", "101", "98", "100", "12", 0, "1200"]):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_klines([raw], 3_600_000, 3_600_000)

    def test_parse_rejects_non_finite_or_non_numeric_market_values(self):
        for field_index, value in ((1, "nan"), (2, "inf"), (5, "-1"), (7, "bad")):
            raw = raw_kline(0)
            raw[field_index] = value
            with self.subTest(field_index=field_index, value=value), self.assertRaises(ValueError):
                parse_klines([raw], 3_600_000, 3_600_000)

    def test_save_csv_replaces_output_atomically(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "candles.csv"
            save_csv([{"t": 0, "o": 99, "h": 101, "l": 98, "c": 100, "v": 12, "q": 1200}], path)
            self.assertEqual(path.read_text(encoding="utf-8").splitlines()[0], "t,o,h,l,c,v,q")
            self.assertEqual([item.name for item in path.parent.iterdir()], ["candles.csv"])

    def test_save_csv_preserves_previous_file_on_write_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "candles.csv"
            original = "previous,csv\\n"
            path.write_text(original, encoding="utf-8")
            rows = [{"t": 0, "o": 99, "h": 101, "l": 98, "c": 100, "v": 12, "q": 1200}]
            with patch("research.binance_data.csv.DictWriter.writerows", side_effect=OSError("simulated disk error")):
                with self.assertRaisesRegex(OSError, "simulated disk error"):
                    save_csv(rows, path)
            self.assertEqual(path.read_text(encoding="utf-8"), original)
            self.assertEqual([item.name for item in Path(tmp).iterdir()], ["candles.csv"])

    def test_parse_rejects_inconsistent_ohlc(self):
        raw = raw_kline(0)
        raw[2] = "99.5"  # high below open/close
        with self.assertRaisesRegex(ValueError, "inconsistent OHLC"):
            parse_klines([raw], 3_600_000, 3_600_000)

    def test_parse_rejects_non_list_response_and_invalid_interval(self):
        with self.assertRaisesRegex(ValueError, "response must be a list"):
            parse_klines({"bad": "payload"}, 3_600_000, 3_600_000)
        with self.assertRaisesRegex(ValueError, "interval_ms"):
            parse_klines([], True, 3_600_000)

    def test_rejects_boolean_and_non_finite_pause(self):
        for pause in (True, float("nan"), float("inf")):
            with self.subTest(pause=pause), self.assertRaisesRegex(ValueError, "pause_seconds"):
                download_klines("BTCUSDT", "1h", 0, 3_600_000,
                                fetch_page=lambda _: [], pause_seconds=pause)


if __name__ == "__main__":
    unittest.main()
