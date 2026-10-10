import unittest
from unittest.mock import patch

from validation.download_funding_rates import download, utc_ms


class FundingDownloaderTests(unittest.TestCase):
    def test_utc_ms_treats_naive_dates_as_utc(self):
        self.assertEqual(utc_ms("2025-01-01"), 1735689600000)
        self.assertEqual(utc_ms("2025-01-01T00:00:00Z"), 1735689600000)

    @patch("validation.download_funding_rates.get_json")
    def test_download_paginates_when_api_limit_is_reached(self, get_json):
        start = 1_735_689_600_000
        end = start + 20_000_000
        first = [
            {"fundingTime": start + i * 1000, "fundingRate": "0.0001"}
            for i in range(1000)
        ]
        second = [
            {"fundingTime": start + 1_000_000, "fundingRate": "0.0002"},
            {"fundingTime": start + 1_001_000, "fundingRate": "0.0003"},
        ]
        get_json.side_effect = [first, second]
        rows = download("BTCUSDT", start, end)
        self.assertEqual(len(rows), 1002)
        self.assertEqual(get_json.call_count, 2)
        self.assertEqual(rows, sorted(rows, key=lambda row: row["timestamp"]))

    @patch("validation.download_funding_rates.get_json")
    def test_download_filters_exclusive_end(self, get_json):
        start = 1_735_689_600_000
        get_json.return_value = [
            {"fundingTime": start, "fundingRate": "0.0001"},
            {"fundingTime": start + 1000, "fundingRate": "0.0002"},
            {"fundingTime": start + 2000, "fundingRate": "0.0003"},
        ]
        rows = download("BTCUSDT", start, start + 2000)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row["timestamp"].endswith("Z") for row in rows))

    @patch("validation.download_funding_rates.get_json")
    def test_empty_api_response_returns_no_rows(self, get_json):
        get_json.return_value = []
        self.assertEqual(download("BTCUSDT", 1000, 2000), [])

    @patch("validation.download_funding_rates.download_archive")
    @patch("validation.download_funding_rates.get_json")
    def test_region_restriction_falls_back_to_public_archive(self, get_json, archive):
        from urllib.error import HTTPError
        start = 1_735_689_600_000
        end = start + 100_000
        get_json.side_effect = HTTPError(
            "https://fapi.binance.com/fapi/v1/fundingRate", 451,
            "Unavailable For Legal Reasons", {}, None
        )
        archive.return_value = [{"timestamp": "2025-01-01T00:00:00Z", "funding_rate": "0.0001"}]
        rows = download("BTCUSDT", start, end)
        self.assertEqual(rows, archive.return_value)
        archive.assert_called_once_with("BTCUSDT", start, end)

    @patch("validation.download_funding_rates.get_json")
    def test_download_rejects_nonadvancing_pagination(self, get_json):
        start = 1_735_689_600_000
        get_json.return_value = [
            {"fundingTime": start - 1, "fundingRate": "0.0001"},
        ]
        with self.assertRaisesRegex(RuntimeError, "Pagination did not advance"):
            download("BTCUSDT", start, start + 100_000_000)


if __name__ == "__main__":
    unittest.main()
