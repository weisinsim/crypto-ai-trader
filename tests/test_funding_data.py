import unittest

from research.funding_data import download_funding_rates, parse_funding_rows


def record(ts, rate="0.0001"):
    return {"symbol": "BTCUSDT", "fundingTime": ts, "fundingRate": rate, "markPrice": "100"}


class FundingDataTests(unittest.TestCase):
    def test_parse_filters_range_and_sorts(self):
        rows = parse_funding_rows([record(200), record(100), record(300)], "btcusdt", 100, 300)
        self.assertEqual([r["funding_time"] for r in rows], [100, 200])

    def test_download_paginates_without_timestamp_duplicates(self):
        def fake_fetch(params):
            cursor = params["startTime"]
            if cursor <= 100:
                return [record(100), record(200)]
            if cursor <= 200:
                return [record(200), record(300)]
            return []
        rows = download_funding_rates("BTCUSDT", 100, 301, fetch_page=fake_fetch, limit=2, pause_seconds=0)
        self.assertEqual([r["funding_time"] for r in rows], [100, 200, 300])

    def test_rejects_invalid_symbol_and_pause(self):
        with self.assertRaisesRegex(ValueError, "symbol"):
            download_funding_rates("BTC/USDT", 0, 100, fetch_page=lambda _: [])
        with self.assertRaisesRegex(ValueError, "pause_seconds"):
            download_funding_rates("BTCUSDT", 0, 100, fetch_page=lambda _: [], pause_seconds=-1)

    def test_conflicting_duplicate_fails(self):
        def fake_fetch(params):
            if params["startTime"] <= 100:
                return [record(100)]
            return [record(100, "0.0002")]
        with self.assertRaisesRegex(ValueError, "conflicting"):
            download_funding_rates("BTCUSDT", 100, 200, fetch_page=fake_fetch, limit=1, pause_seconds=0)


if __name__ == "__main__":
    unittest.main()
