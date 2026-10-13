import unittest

from research.funding_data import canonical_settlement_timestamp, download_archive_funding, download_funding_rates, parse_funding_rows, save_funding_csv


def record(ts, rate="0.0001"):
    return {"symbol": "BTCUSDT", "fundingTime": ts, "fundingRate": rate, "markPrice": "100"}


class FundingDataTests(unittest.TestCase):
    def test_canonicalizes_only_small_settlement_timestamp_jitter(self):
        boundary = 1783180800000
        self.assertEqual(canonical_settlement_timestamp(boundary + 7), boundary)
        self.assertEqual(canonical_settlement_timestamp(boundary - 500), boundary)
        with self.assertRaisesRegex(ValueError, "off the canonical 8-hour grid"):
            canonical_settlement_timestamp(boundary + 1001)

    def test_canonical_settlement_timestamp_rejects_invalid_values(self):
        for ts in (-1, 1.5, True, False):
            with self.assertRaisesRegex(ValueError, "non-negative integers"):
                canonical_settlement_timestamp(ts)

    def test_canonical_settlement_timestamp_rejects_boolean_tolerance(self):
        for tolerance in (True, False):
            with self.assertRaisesRegex(ValueError, "non-negative integers"):
                canonical_settlement_timestamp(1783180800000, tolerance_ms=tolerance)
    def test_parse_filters_range_and_sorts(self):
        rows = parse_funding_rows([record(200), record(100), record(300)], "btcusdt", 100, 300)
        self.assertEqual([r["funding_time"] for r in rows], [100, 200])
        self.assertEqual(rows[0]["mark_price"], 100.0)
        self.assertEqual(rows[0]["venue"], "binance")

    def test_download_paginates_without_timestamp_duplicates(self):
        def fake_fetch(params):
            cursor = params["startTime"]
            if cursor <= 100:
                return [record(100), record(200)]
            if cursor <= 201:
                return [record(300)]
            return []
        rows = download_funding_rates("BTCUSDT", 100, 301, fetch_page=fake_fetch, limit=2, pause_seconds=0)
        self.assertEqual([r["funding_time"] for r in rows], [100, 200, 300])

    def test_rejects_non_finite_funding_rate(self):
        with self.assertRaisesRegex(ValueError, "invalid funding rate"):
            parse_funding_rows([record(100, "nan")], "BTCUSDT", 0, 200)

    def test_rejects_invalid_symbol_and_pause(self):
        with self.assertRaisesRegex(ValueError, "symbol"):
            download_funding_rates("BTC/USDT", 0, 100, fetch_page=lambda _: [])
        with self.assertRaisesRegex(ValueError, "pause_seconds"):
            download_funding_rates("BTCUSDT", 0, 100, fetch_page=lambda _: [], pause_seconds=-1)

    def test_archive_fallback_uses_official_rest_when_month_and_daily_archives_missing(self):
        from datetime import datetime, timezone
        from unittest.mock import Mock, patch

        start = int(datetime(2026, 10, 2, tzinfo=timezone.utc).timestamp() * 1000)
        end = int(datetime(2026, 10, 3, tzinfo=timezone.utc).timestamp() * 1000)
        def response(status_code):
            item = Mock()
            item.status_code = status_code
            item.raise_for_status.side_effect = RuntimeError("unexpected HTTP error") if status_code != 404 else None
            item.content = b""
            return item
        client = Mock()
        client.get.side_effect = [response(404), response(404), response(404)]
        context = Mock()
        context.__enter__ = Mock(return_value=client)
        context.__exit__ = Mock(return_value=False)
        official_rows = [{
            "funding_time": start + 8 * 60 * 60 * 1000,
            "funding_rate": 0.0001,
            "mark_price": 123.45,
            "symbol": "BTCUSDT",
            "mark_price_source": "binance_fapi_funding_rate_mark_price",
            "venue": "binance",
        }]
        with patch("research.funding_data.httpx.Client", return_value=context):
            with patch("research.funding_data.download_funding_rates", return_value=official_rows) as rest:
                rows = download_archive_funding("BTCUSDT", start, end)
        rest.assert_called_once_with("BTCUSDT", start, end, base_url="https://fapi.binance.com")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["mark_price"], 123.45)
        self.assertEqual(rows[0]["mark_price_source"], "binance_fapi_funding_rate_mark_price")
        self.assertEqual(rows[0]["venue"], "binance")

    def test_save_funding_csv_preserves_venue_metadata(self):
        import csv
        import tempfile
        from pathlib import Path

        rows = [{
            "funding_time": 100, "funding_rate": 0.0001, "mark_price": 100.0,
            "symbol": "BTCUSDT", "mark_price_source": "okx_mark_price_kline_open",
            "venue": "okx",
        }]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "funding.csv"
            save_funding_csv(rows, path)
            with path.open(newline="", encoding="utf-8") as handle:
                saved = next(csv.DictReader(handle))
        self.assertEqual(saved["venue"], "okx")

    def test_stale_duplicate_page_aborts_pagination(self):
        def fake_fetch(params):
            if params["startTime"] <= 100:
                return [record(100)]
            return [record(100, "0.0002")]
        with self.assertRaisesRegex(RuntimeError, "did not advance"):
            download_funding_rates("BTCUSDT", 100, 200, fetch_page=fake_fetch, limit=1, pause_seconds=0)


if __name__ == "__main__":
    unittest.main()
