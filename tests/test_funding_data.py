import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from research.funding_data import download_archive_funding, download_funding_rates, parse_funding_rows


def record(ts, rate="0.0001"):
    return {"symbol": "BTCUSDT", "fundingTime": ts, "fundingRate": rate, "markPrice": "100"}


class FundingDataTests(unittest.TestCase):
    def test_parse_filters_range_and_sorts(self):
        rows = parse_funding_rows([record(200), record(100), record(300)], "btcusdt", 100, 300)
        self.assertEqual([r["funding_time"] for r in rows], [100, 200])
        self.assertEqual(rows[0]["mark_price"], 100.0)

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

    def test_stale_duplicate_page_aborts_pagination(self):
        def fake_fetch(params):
            if params["startTime"] <= 100:
                return [record(100)]
            return [record(100, "0.0002")]
        with self.assertRaisesRegex(RuntimeError, "did not advance"):
            download_funding_rates("BTCUSDT", 100, 200, fetch_page=fake_fetch, limit=1, pause_seconds=0)


    def test_archive_funding_fails_closed_when_monthly_and_daily_rates_missing(self):
        start = int(datetime(2026, 10, 2, tzinfo=timezone.utc).timestamp() * 1000)
        end = int(datetime(2026, 10, 3, tzinfo=timezone.utc).timestamp() * 1000)

        def response(status_code):
            item = Mock()
            item.status_code = status_code
            item.raise_for_status.side_effect = (
                RuntimeError("unexpected HTTP error") if status_code != 404 else None
            )
            return item

        client = Mock()
        client.get.side_effect = [response(404), response(404)]
        context = Mock()
        context.__enter__ = Mock(return_value=client)
        context.__exit__ = Mock(return_value=False)

        with patch("research.funding_data.httpx.Client", return_value=context):
            with patch("research.funding_data.download_funding_rates",
                       side_effect=RuntimeError("official REST unavailable")) as rest:
                with self.assertRaisesRegex(ValueError, "refusing to synthesize records"):
                    download_archive_funding("BTCUSDT", start, end)

        self.assertEqual(client.get.call_count, 2)
        requested_urls = [call.args[0] for call in client.get.call_args_list]
        self.assertIn("/monthly/fundingRate/", requested_urls[0])
        self.assertIn("/daily/fundingRate/", requested_urls[1])
        rest.assert_called_once_with("BTCUSDT", start, end, base_url="https://fapi.binance.com")

    def test_month_starts_handles_year_boundary(self):
        from research.funding_data import _month_starts

        start = int(datetime(2026, 12, 15, tzinfo=timezone.utc).timestamp() * 1000)
        end = int(datetime(2027, 2, 2, tzinfo=timezone.utc).timestamp() * 1000)
        months = list(_month_starts(start, end))
        self.assertEqual([month.strftime("%Y-%m") for month in months],
                         ["2026-12", "2027-01", "2027-02"])


if __name__ == "__main__":
    unittest.main()
