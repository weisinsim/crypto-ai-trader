import unittest

from research.binance_data import download_klines, parse_klines


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

    def test_requires_aligned_boundaries(self):
        with self.assertRaises(ValueError):
            download_klines("BTCUSDT", "1h", 1, 3_600_000,
                            fetch_page=lambda _: [], pause_seconds=0)


if __name__ == "__main__":
    unittest.main()
