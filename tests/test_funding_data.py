import unittest

from research.funding_data import _find_prior_mark_close, _parse_archive_timestamp, _parse_finite_number, canonical_settlement_timestamp, download_archive_funding, download_funding_rates, parse_funding_rows, save_funding_csv


def record(ts, rate="0.0001"):
    return {"symbol": "BTCUSDT", "fundingTime": ts, "fundingRate": rate, "markPrice": "100"}


class FundingDataTests(unittest.TestCase):
    def test_archive_numeric_parser_rejects_non_finite_values_and_invalid_prices(self):
        for value in ("nan", "inf", "-inf", float("nan"), float("inf"), True):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "finite"):
                _parse_finite_number(value, "test value")
        for value in ("0", "-1", "nan", "inf"):
            with self.subTest(price=value), self.assertRaisesRegex(ValueError, "finite and positive"):
                _parse_finite_number(value, "test price", positive=True)
        self.assertEqual(_parse_finite_number("-0.001", "funding rate"), -0.001)
        self.assertEqual(_parse_finite_number("100.5", "mark price", positive=True), 100.5)

    def test_archive_timestamp_parser_rejects_fractional_and_malformed_values(self):
        for value in (100.5, "100.5", "1e3", "nan", "inf", True, False, -1, "-1"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "integer timestamp|cannot be negative"):
                    _parse_archive_timestamp(value)

    def test_archive_timestamp_parser_rejects_unicode_digit_strings(self):
        for value in ("١٧٠٠٠٠٠٠٠٠٠٠٠", "²"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "integer timestamp"):
                _parse_archive_timestamp(value)

    def test_archive_timestamp_parser_accepts_integer_timestamp_strings(self):
        self.assertEqual(_parse_archive_timestamp(" 1700000000000 "), 1700000000000)
        self.assertEqual(_parse_archive_timestamp(1700000000000), 1700000000000)

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
    def test_parse_funding_rows_rejects_invalid_symbol_and_range(self):
        valid = [record(100)]
        for symbol in ("", "BTC/USDT", None):
            with self.subTest(symbol=symbol):
                with self.assertRaisesRegex(ValueError, "symbol"):
                    parse_funding_rows(valid, symbol, 0, 200)
        for start, end in ((True, 200), (0, False), (1.5, 200), (0, 200.5), (-1, 200), (200, 200)):
            with self.subTest(start=start, end=end):
                with self.assertRaisesRegex(ValueError, "integer timestamps"):
                    parse_funding_rows(valid, "BTCUSDT", start, end)

    def test_parse_rejects_response_symbol_mismatch(self):
        mismatched = {**record(100), "symbol": "ETHUSDT"}
        with self.assertRaisesRegex(ValueError, "funding response symbol mismatch"):
            parse_funding_rows([mismatched], "BTCUSDT", 0, 200)

    def test_parse_accepts_matching_response_symbol_case_insensitively(self):
        matching = {**record(100), "symbol": "btcusdt"}
        rows = parse_funding_rows([matching], "BTCUSDT", 0, 200)
        self.assertEqual(rows[0]["symbol"], "BTCUSDT")

    def test_parse_filters_range_and_sorts(self):
        rows = parse_funding_rows([record(200), record(100), record(300)], "btcusdt", 100, 300)
        self.assertEqual([r["funding_time"] for r in rows], [100, 200])
        self.assertEqual(rows[0]["mark_price"], 100.0)
        self.assertEqual(rows[0]["venue"], "binance")

    def test_download_rejects_api_rows_outside_requested_range(self):
        def fake_fetch(params):
            return [record(params["startTime"] - 1)]

        with self.assertRaisesRegex(ValueError, "outside requested page range"):
            download_funding_rates(
                "BTCUSDT", 100, 301, fetch_page=fake_fetch, limit=10, pause_seconds=0
            )
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

    def test_parse_funding_rows_rejects_malformed_timestamps_without_truncation(self):
        for ts in (True, False, 100.5, "100.5", "1e3"):
            with self.subTest(ts=ts):
                with self.assertRaisesRegex(ValueError, "fundingTime must be an integer timestamp"):
                    parse_funding_rows([record(ts)], "BTCUSDT", 0, 1000)

    def test_parse_funding_rows_rejects_noncanonical_timestamp_strings(self):
        for ts in (" 100", "100 ", "+100", "100.0", "١٠٠", "²"):
            with self.subTest(ts=ts), self.assertRaisesRegex(ValueError, "fundingTime must be an integer timestamp"):
                parse_funding_rows([record(ts)], "BTCUSDT", 0, 1000)

    def test_parse_funding_rows_accepts_integer_timestamp_strings(self):
        rows = parse_funding_rows([record("100")], "BTCUSDT", 0, 1000)
        self.assertEqual(rows[0]["funding_time"], 100)

    def test_rejects_non_finite_funding_rate(self):
        with self.assertRaisesRegex(ValueError, "invalid funding rate"):
            parse_funding_rows([record(100, "nan")], "BTCUSDT", 0, 200)

    def test_rejects_boolean_funding_rate_and_mark_price(self):
        for field, value in (("fundingRate", True), ("fundingRate", False),
                             ("markPrice", True), ("markPrice", False)):
            with self.subTest(field=field, value=value):
                malformed = {**record(100), field: value}
                with self.assertRaisesRegex(ValueError, "invalid funding rate or mark price"):
                    parse_funding_rows([malformed], "BTCUSDT", 0, 200)

    def test_rejects_non_numeric_funding_rate_and_mark_price(self):
        for field, value in (("fundingRate", None), ("markPrice", object())):
            with self.subTest(field=field, value=type(value).__name__):
                malformed = {**record(100), field: value}
                with self.assertRaisesRegex(ValueError, "invalid funding rate or mark price"):
                    parse_funding_rows([malformed], "BTCUSDT", 0, 200)

    def test_rejects_invalid_symbol_and_pause(self):
        with self.assertRaisesRegex(ValueError, "symbol"):
            download_funding_rates("BTC/USDT", 0, 100, fetch_page=lambda _: [])
        for pause in (-1, float("nan"), float("inf"), True):
            with self.subTest(pause=pause):
                with self.assertRaisesRegex(ValueError, "pause_seconds"):
                    download_funding_rates("BTCUSDT", 0, 100, fetch_page=lambda _: [], pause_seconds=pause)

    def test_rejects_boolean_and_non_integer_range_or_limit_arguments(self):
        fetch = lambda _: []
        invalid_calls = [
            (True, 100, 1000), (0, True, 1000), (0.5, 100, 1000),
            (0, 100.5, 1000), (0, 100, True), (0, 100, 1.5),
        ]
        for start, end, limit in invalid_calls:
            with self.subTest(start=start, end=end, limit=limit):
                with self.assertRaises(ValueError):
                    download_funding_rates("BTCUSDT", start, end, fetch_page=fetch, limit=limit)

    def test_archive_downloader_rejects_invalid_symbol_and_timestamp_range_before_network(self):
        from unittest.mock import patch

        invalid_calls = (
            (None, 0, 100),
            ("", 0, 100),
            ("BTC/USDT", 0, 100),
            ("BTCUSDT", True, 100),
            ("BTCUSDT", 0, False),
            ("BTCUSDT", 1.5, 100),
            ("BTCUSDT", 0, 100.5),
            ("BTCUSDT", -1, 100),
            ("BTCUSDT", 100, 100),
        )
        with patch("research.funding_data.httpx.Client") as client:
            for symbol, start, end in invalid_calls:
                with self.subTest(symbol=symbol, start=start, end=end):
                    with self.assertRaises(ValueError):
                        download_archive_funding(symbol, start, end)
            client.assert_not_called()
    def test_archive_downloader_rejects_out_of_datetime_range_before_network(self):
        from unittest.mock import patch

        huge = 10 ** 10000
        with patch("research.funding_data.httpx.Client") as client:
            with self.assertRaisesRegex(ValueError, "outside the supported UTC datetime range"):
                download_archive_funding("BTCUSDT", huge, huge + 1)
            client.assert_not_called()
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

    def test_save_funding_csv_preserves_previous_file_on_write_failure(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "funding.csv"
            original = "previous,funding\\n"
            path.write_text(original, encoding="utf-8")
            rows = [{
                "funding_time": 100, "funding_rate": 0.0001, "mark_price": 100.0,
                "symbol": "BTCUSDT", "mark_price_source": "mark_kline_open", "venue": "binance",
            }]
            with patch("research.funding_data.csv.DictWriter.writerows", side_effect=OSError("simulated disk error")):
                with self.assertRaisesRegex(OSError, "simulated disk error"):
                    save_funding_csv(rows, path)
            self.assertEqual(path.read_text(encoding="utf-8"), original)
            self.assertEqual([item.name for item in Path(directory).iterdir()], ["funding.csv"])

    def test_save_funding_csv_rejects_invalid_rows_before_replacing_existing_file(self):
        import tempfile
        from pathlib import Path

        valid = {
            "funding_time": 100, "funding_rate": 0.0001, "mark_price": 100.0,
            "symbol": "BTCUSDT", "mark_price_source": "mark_kline_open", "venue": "binance",
        }
        invalid_rows = [
            [{**valid, "funding_rate": float("nan")}],
            [{**valid, "funding_rate": float("inf")}],
            [{**valid, "mark_price": 0}],
            [{**valid, "mark_price": float("nan")}],
            [valid, {**valid, "funding_time": 100}],
            [{key: value for key, value in valid.items() if key != "venue"}],
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "funding.csv"
            original = "previous,content\\n"
            for rows in invalid_rows:
                path.write_text(original, encoding="utf-8")
                with self.subTest(rows=rows), self.assertRaises(ValueError):
                    save_funding_csv(rows, path)
                self.assertEqual(path.read_text(encoding="utf-8"), original)
                self.assertEqual([item.name for item in Path(directory).iterdir()], ["funding.csv"])

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

    def test_rejects_duplicate_funding_event_across_pages_even_if_identical(self):
        def fake_fetch(params):
            if params["startTime"] <= 100:
                return [record(100), record(200)]
            if params["startTime"] <= 201:
                # Simulate an overlapping pagination response with a duplicate event.
                return [record(200), record(300)]
            return []
        with self.assertRaisesRegex(ValueError, "duplicate funding record at 200"):
            download_funding_rates("BTCUSDT", 100, 301, fetch_page=fake_fetch, limit=2, pause_seconds=0)

    def test_rejects_conflicting_funding_event_across_pages(self):
        def fake_fetch(params):
            if params["startTime"] <= 100:
                return [record(100), record(200)]
            if params["startTime"] <= 201:
                return [record(200, "0.0002"), record(300)]
            return []
        with self.assertRaisesRegex(ValueError, "conflicting duplicate funding record at 200"):
            download_funding_rates("BTCUSDT", 100, 301, fetch_page=fake_fetch, limit=2, pause_seconds=0)

    def test_stale_duplicate_page_aborts_pagination(self):
        def fake_fetch(params):
            if params["startTime"] <= 100:
                return [record(100)]
            return [record(100, "0.0002")]
        with self.assertRaisesRegex(RuntimeError, "did not advance"):
            download_funding_rates("BTCUSDT", 100, 200, fetch_page=fake_fetch, limit=1, pause_seconds=0)


if __name__ == "__main__":
    unittest.main()


class FundingRowShapeValidationTests(unittest.TestCase):
    def test_parse_funding_rows_rejects_non_mapping_rows(self):
        for raw in (None, [], "not-a-row", 123, True):
            with self.subTest(raw=raw), self.assertRaisesRegex(
                ValueError, "funding row 0 must be a mapping"
            ):
                parse_funding_rows([raw], "BTCUSDT", 0, 200)


class FundingRequiredFieldsValidationTests(unittest.TestCase):
    def test_parse_funding_rows_rejects_missing_required_fields(self):
        for field in ("fundingTime", "fundingRate", "markPrice"):
            malformed = record(100)
            del malformed[field]
            with self.subTest(field=field), self.assertRaisesRegex(
                ValueError, f"missing required fields:.*{field}"
            ):
                parse_funding_rows([malformed], "BTCUSDT", 0, 200)

    def test_parse_funding_rows_rejects_non_list_response(self):
        for raw_rows in (None, {}, "not-a-list", 123):
            with self.subTest(raw_rows=raw_rows), self.assertRaisesRegex(
                ValueError, "funding response must be a list of rows"
            ):
                parse_funding_rows(raw_rows, "BTCUSDT", 0, 200)

class PriorMarkCloseLookupTests(unittest.TestCase):
    def test_returns_latest_close_at_or_before_settlement_within_sixty_seconds(self):
        marks = [(100_000, 100.0), (160_000, 101.0), (220_000, 102.0)]
        self.assertEqual(_find_prior_mark_close(marks, 220_000), 102.0)
        self.assertEqual(_find_prior_mark_close(marks, 280_000), 102.0)

    def test_rejects_close_older_than_sixty_seconds(self):
        self.assertIsNone(_find_prior_mark_close([(100_000, 100.0)], 160_001))

    def test_rejects_future_close_and_empty_history(self):
        self.assertIsNone(_find_prior_mark_close([(200_000, 100.0)], 199_999))
        self.assertIsNone(_find_prior_mark_close([], 200_000))

    def test_binary_search_selects_latest_duplicate_timestamp(self):
        marks = [(100_000, 99.0), (100_000, 100.0)]
        self.assertEqual(_find_prior_mark_close(marks, 100_000), 100.0)


