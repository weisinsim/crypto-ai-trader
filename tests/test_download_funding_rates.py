import unittest
from unittest.mock import patch

from validation.download_funding_rates import download, utc_ms


class FundingDownloaderTests(unittest.TestCase):
    def test_utc_ms_treats_naive_dates_as_utc(self):
        self.assertEqual(utc_ms("2025-01-01"), 1735689600000)
        self.assertEqual(utc_ms("2025-01-01T00:00:00Z"), 1735689600000)

    @patch("validation.download_funding_rates.get_json")
    def test_download_paginates_without_repeating_boundary_events(self, get_json):
        start = 1_735_689_600_000
        end = start + 20_000_000
        first = [
            {"fundingTime": start + 1_000, "fundingRate": "0.0001"},
            {"fundingTime": start + 2_000, "fundingRate": "0.0002"},
        ]
        second = [
            {"fundingTime": start + 2_000, "fundingRate": "0.0002"},
            {"fundingTime": start + 3_000, "fundingRate": "0.0003"},
        ]
        get_json.side_effect = [first, second, []]
        rows = download("BTCUSDT", start, end)
        self.assertEqual(len(rows), 3)
        self.assertEqual(
            [row["funding_rate"] for row in rows],
            ["0.0001", "0.0002", "0.0003"],
        )
        self.assertEqual(get_json.call_count, 3)

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
