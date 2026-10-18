import unittest
from unittest.mock import patch

from research.binance_archive import download_archive_funding, download_archive_klines

HOUR = 3_600_000


def kline(ts, close="100"):
    return [str(ts), "99", "101", "98", close, "12", str(ts + HOUR - 1),
            "1200", "5", "6", "600", "0"]


class BinanceArchiveTests(unittest.TestCase):
    @patch("research.binance_archive._archive_rows")
    def test_archive_klines_are_normalized_and_coverage_checked(self, archive_rows):
        archive_rows.return_value = [kline(0), kline(HOUR, "101")]
        rows = download_archive_klines("BTCUSDT", "1h", 0, 2 * HOUR, HOUR)
        self.assertEqual([row["t"] for row in rows], [0, HOUR])
        self.assertEqual(rows[1]["c"], 101.0)

    @patch("research.binance_archive._archive_rows")
    def test_archive_funding_labels_mark_price_as_proxy(self, archive_rows):
        funding_ts = 8 * HOUR
        archive_rows.side_effect = [
            [[str(funding_ts), "8", "0.001"]],
            [kline(funding_ts - HOUR, "99.5")],
        ]
        rows = download_archive_funding("BTCUSDT", funding_ts, funding_ts + 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["funding_time"], funding_ts)
        self.assertEqual(rows[0]["funding_rate"], 0.001)
        self.assertEqual(rows[0]["mark_price"], 99.5)
        self.assertIn("proxy_NOT_SETTLEMENT_MARK", rows[0]["mark_price_source"])
        self.assertEqual(rows[0]["venue"], "binance_usdm_archive")

    @patch("research.binance_archive._archive_rows")
    def test_archive_funding_fails_closed_without_prior_mark_candle(self, archive_rows):
        funding_ts = 8 * HOUR
        archive_rows.side_effect = [
            [[str(funding_ts), "8", "0.001"]],
            [],
        ]
        with self.assertRaisesRegex(ValueError, "refusing to fabricate settlement mark"):
            download_archive_funding("BTCUSDT", funding_ts, funding_ts + 1)


if __name__ == "__main__":
    unittest.main()
