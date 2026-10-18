"""Regression tests preventing mixed exchange venues in one funding dataset."""
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


def event(timestamp, venue):
    return {
        "symbol": "BTCUSDT",
        "funding_time": str(timestamp),
        "funding_rate": "0.0001",
        "mark_price": "95000.0",
        "mark_price_source": "binance_mark_price",
        "venue": venue,
    }


class FundingCsvVenueIdentityTests(unittest.TestCase):
    def test_rejects_mixed_explicit_venues(self):
        path = write_funding_csv([
            event(1735689600000, "binance"),
            event(1735718400000, "bybit"),
        ])
        try:
            with self.assertRaisesRegex(ValueError, "must not mix records from different venues"):
                load_funding_csv(str(path))
        finally:
            path.unlink(missing_ok=True)

    def test_accepts_single_explicit_venue(self):
        path = write_funding_csv([
            event(1735689600000, "binance"),
            event(1735718400000, "binance"),
        ])
        try:
            rows = load_funding_csv(str(path))
            self.assertEqual({row["venue"] for row in rows}, {"binance"})
        finally:
            path.unlink(missing_ok=True)

    def test_missing_venue_is_not_misrepresented_as_a_mixed_venue(self):
        path = write_funding_csv([
            event(1735689600000, ""),
            event(1735718400000, "binance"),
        ])
        try:
            rows = load_funding_csv(str(path))
            self.assertEqual({row["venue"] for row in rows}, {"", "binance"})
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
