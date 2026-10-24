import unittest

from validation.fetch_binance_futures_data import deduplicate_funding_rows


class LegacyFundingFetcherTests(unittest.TestCase):
    def test_rejects_conflicting_rates_at_same_timestamp(self):
        rows = [
            {"fundingTime": 1000, "fundingRate": "0.0001"},
            {"fundingTime": 1000, "fundingRate": "0.0002"},
        ]
        with self.assertRaisesRegex(RuntimeError, "Conflicting funding rates"):
            deduplicate_funding_rows(rows)

    def test_collapses_identical_rates_and_sorts_events(self):
        rows = [
            {"fundingTime": 2000, "fundingRate": "-0.0001"},
            {"fundingTime": 1000, "fundingRate": "0.0001"},
            {"fundingTime": 1000, "fundingRate": "0.00010"},
        ]
        result = deduplicate_funding_rows(rows)
        self.assertEqual([row["fundingTime"] for row in result], [1000, 2000])

    def test_rejects_nonfinite_rate(self):
        with self.assertRaisesRegex(RuntimeError, "Non-finite funding rate"):
            deduplicate_funding_rows([{"fundingTime": 1000, "fundingRate": "NaN"}])


if __name__ == "__main__":
    unittest.main()
