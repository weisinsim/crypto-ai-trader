"""Regression tests for preserving historical funding CSV source order."""
import csv
import tempfile
import unittest
from pathlib import Path

from research.run_backtest import load_funding_csv


FIELDS = [
    "symbol", "funding_time", "funding_rate", "mark_price",
    "mark_price_source", "venue",
]


def write_funding_csv(rows):
    handle = tempfile.NamedTemporaryFile(mode="w", newline="", encoding="utf-8", delete=False)
    path = Path(handle.name)
    try:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    finally:
        handle.close()
    return path


def event(timestamp):
    return {
        "symbol": "BTCUSDT",
        "funding_time": str(timestamp),
        "funding_rate": "0.0001",
        "mark_price": "95000.0",
        "mark_price_source": "binance_mark_price",
        "venue": "binance",
    }


class FundingCsvSourceOrderTests(unittest.TestCase):
    def test_rejects_out_of_order_raw_timestamps_instead_of_sorting(self):
        path = write_funding_csv([
            event(1735718400000),  # 2025-01-01 08:00 UTC
            event(1735689600000),  # 2025-01-01 00:00 UTC
        ])
        try:
            with self.assertRaisesRegex(ValueError, "strictly increasing in file order"):
                load_funding_csv(str(path))
        finally:
            path.unlink(missing_ok=True)

    def test_accepts_strictly_ordered_funding_rows(self):
        path = write_funding_csv([
            event(1735689600000),  # 2025-01-01 00:00 UTC
            event(1735718400000),  # 2025-01-01 08:00 UTC
        ])
        try:
            rows = load_funding_csv(str(path))
            self.assertEqual([row["funding_time_raw"] for row in rows], [
                1735689600000, 1735718400000,
            ])
        finally:
            path.unlink(missing_ok=True)

    def test_rejects_distinct_raw_events_that_normalize_to_same_settlement(self):
        path = write_funding_csv([
            event(1735689600000),
            event(1735689600100),  # 100 ms jitter normalizes to the same 8-hour boundary
        ])
        try:
            with self.assertRaisesRegex(ValueError, "duplicate funding timestamp after normalization"):
                load_funding_csv(str(path))
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
