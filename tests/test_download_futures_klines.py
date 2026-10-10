import unittest
from unittest.mock import patch

from validation.download_futures_klines import download


class FuturesKlineDownloaderTests(unittest.TestCase):
    @patch("validation.download_futures_klines.get_json")
    def test_excludes_current_unclosed_candle(self, get_json):
        start = 1_735_689_600_000
        hour = 3_600_000
        # Candle 1 close is before now; candle 2 closes exactly at now and
        # must be excluded because the close timestamp must be strictly earlier.
        get_json.return_value = [
            [start, "100", "102", "99", "101", "10", start + hour - 1, "1000", 20, "5", "500", "0"],
            [start + hour, "101", "103", "100", "102", "11", start + 2 * hour - 1, "1100", 21, "6", "600", "0"],
        ]
        rows = download("BTCUSDT", "1h", start, start + 3 * hour, now_ms=start + 2 * hour - 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["timestamp"], "2025-01-01T00:59:59.999000Z")

    @patch("validation.download_futures_klines.get_json")
    def test_excludes_candle_at_exclusive_end(self, get_json):
        start = 1_735_689_600_000
        hour = 3_600_000
        get_json.return_value = [
            [start, "100", "102", "99", "101", "10", start + hour - 1, "1000", 20, "5", "500", "0"],
            [start + hour, "101", "103", "100", "102", "11", start + 2 * hour - 1, "1100", 21, "6", "600", "0"],
        ]
        # End is exclusive as a timestamp boundary: a candle closing at end-1ms
        # is still inside the requested interval, so use the next open boundary
        # to prove that the candle whose open equals end is excluded.
        rows = download("BTCUSDT", "1h", start, start + hour, now_ms=start + 3 * hour)
        self.assertEqual(len(rows), 1)


if __name__ == "__main__":
    unittest.main()
