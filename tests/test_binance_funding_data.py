import csv
import tempfile
import unittest
from pathlib import Path

from research.binance_funding_data import (
    download_funding_history,
    normalize_funding_rows,
    save_csv,
)


class BinanceFundingDataTests(unittest.TestCase):
    def test_download_paginates_and_preserves_source_labels(self):
        pages = {
            100: [
                {"fundingTime": 200, "fundingRate": "0.001", "markPrice": "101"},
                {"fundingTime": 100, "fundingRate": "-0.001", "markPrice": "99"},
            ],
            201: [
                {"fundingTime": 300, "fundingRate": "0.002", "markPrice": "102"},
            ],
        }
        calls = []

        def fetch_page(params):
            calls.append(params.copy())
            return pages.get(params["startTime"], [])

        rows = download_funding_history(
            "BTCUSDT", 100, 400, fetch_page=fetch_page, limit=2, pause_seconds=0,
        )
        self.assertEqual([row["funding_time"] for row in rows], [100, 200, 300])
        self.assertEqual(len(calls), 2)
        self.assertEqual(rows[0]["mark_price_source"], "binance_funding_rate_endpoint_markPrice")
        self.assertEqual(rows[0]["venue"], "binance_usdm")
        self.assertEqual(rows[0]["symbol"], "BTCUSDT")

    def test_empty_range_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "no funding events"):
            download_funding_history(
                "BTCUSDT", 100, 200, fetch_page=lambda _: [], pause_seconds=0,
            )

    def test_duplicate_timestamps_are_rejected(self):
        raw = [
            {"fundingTime": 100, "fundingRate": "0.001", "markPrice": "99"},
            {"fundingTime": 100, "fundingRate": "0.002", "markPrice": "100"},
        ]
        with self.assertRaisesRegex(ValueError, "duplicate funding timestamp"):
            normalize_funding_rows(raw, "BTCUSDT")

    def test_invalid_rate_or_mark_price_is_rejected(self):
        for rate, mark in (("nan", "100"), ("0.001", "0"), ("0.001", "inf")):
            with self.subTest(rate=rate, mark=mark), self.assertRaises(ValueError):
                normalize_funding_rows(
                    [{"fundingTime": 100, "fundingRate": rate, "markPrice": mark}],
                    "BTCUSDT",
                )

    def test_csv_output_is_compatible_with_backtest_loader(self):
        rows = normalize_funding_rows(
            [{"fundingTime": 100, "fundingRate": "0.001", "markPrice": "99"}],
            "BTCUSDT",
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "funding.csv"
            save_csv(rows, path)
            with path.open(newline="", encoding="utf-8") as handle:
                saved = list(csv.DictReader(handle))
        self.assertEqual(saved[0]["funding_time"], "100")
        self.assertEqual(saved[0]["symbol"], "BTCUSDT")
        self.assertEqual(saved[0]["venue"], "binance_usdm")


if __name__ == "__main__":
    unittest.main()
