import unittest

from validation.candle_closure import filter_closed_candles


class ClosedCandleFilterTests(unittest.TestCase):
    def setUp(self):
        self.rows = [
            {"t": 0, "c": 100},
            {"t": 3_600_000, "c": 101},
            {"t": 7_200_000, "c": 102},
        ]

    def test_excludes_in_progress_hourly_candle(self):
        result = filter_closed_candles(self.rows, "1h", 7_200_001)
        self.assertEqual([r["t"] for r in result], [0, 3_600_000])

    def test_includes_candle_once_interval_elapsed(self):
        result = filter_closed_candles(self.rows, "1h", 7_200_000)
        self.assertEqual([r["t"] for r in result], [0, 3_600_000])

    def test_four_hour_filter_uses_four_hour_duration(self):
        rows = [{"t": 0}, {"t": 14_400_000}, {"t": 28_800_000}]
        result = filter_closed_candles(rows, "4h", 28_800_000)
        self.assertEqual([r["t"] for r in result], [0, 14_400_000])

    def test_unknown_interval_rejected(self):
        with self.assertRaises(ValueError):
            filter_closed_candles(self.rows, "5m", 100_000_000)

    def test_rejects_fractional_candle_timestamp_instead_of_truncating(self):
        rows = [{"t": 3_600_000.5}]
        with self.assertRaisesRegex(ValueError, "integer millisecond timestamp"):
            filter_closed_candles(rows, "1h", 7_200_000)

    def test_rejects_boolean_and_negative_timestamps(self):
        for timestamp in (True, -1):
            with self.subTest(timestamp=timestamp):
                with self.assertRaises(ValueError):
                    filter_closed_candles([{"t": timestamp}], "1h", 7_200_000)

    def test_accepts_integer_timestamp_string(self):
        rows = [{"t": "3600000", "c": 101}]
        self.assertEqual(filter_closed_candles(rows, "1h", "7200000"), rows)

    def test_rejects_out_of_order_candle_timestamps(self):
        rows = [{"t": 3_600_000}, {"t": 0}]
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            filter_closed_candles(rows, "1h", 10_000_000)

    def test_rejects_duplicate_candle_timestamps(self):
        rows = [{"t": 0}, {"t": 0}]
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            filter_closed_candles(rows, "1h", 10_000_000)

    def test_rejects_missing_timestamp_row(self):
        with self.assertRaisesRegex(ValueError, "mapping with timestamp"):
            filter_closed_candles([{"c": 100}], "1h", 7_200_000)

    def test_rejects_fractional_now_timestamp(self):
        with self.assertRaisesRegex(ValueError, "integer millisecond timestamp"):
            filter_closed_candles(self.rows, "1h", 7_200_000.5)


    def test_upsert_replaces_same_timestamp(self):
        from validation.candle_closure import upsert_candle
        rows = [{"t": 100, "c": 1}]
        self.assertTrue(upsert_candle(rows, {"t": 100, "c": 2}))
        self.assertEqual(rows, [{"t": 100, "c": 2}])

    def test_upsert_appends_newer_timestamp(self):
        from validation.candle_closure import upsert_candle
        rows = [{"t": 100, "c": 1}]
        self.assertTrue(upsert_candle(rows, {"t": 200, "c": 2}))
        self.assertEqual([r["t"] for r in rows], [100, 200])

    def test_upsert_ignores_stale_timestamp_without_mutating_series(self):
        from validation.candle_closure import upsert_candle
        rows = [{"t": 100, "c": 1}, {"t": 200, "c": 2}]
        before = [dict(row) for row in rows]
        self.assertFalse(upsert_candle(rows, {"t": 150, "c": 99}))
        self.assertEqual(rows, before)

    def test_upsert_rejects_malformed_timestamp(self):
        from validation.candle_closure import upsert_candle
        with self.assertRaises(ValueError):
            upsert_candle([], {"t": 1.5, "c": 1})

    def test_rest_series_requires_strictly_increasing_timestamps(self):
        from validation.candle_closure import validate_candle_series
        with self.assertRaises(ValueError):
            validate_candle_series([{"t": 100}, {"t": 90}])
        with self.assertRaises(ValueError):
            validate_candle_series([{"t": 100}, {"t": 100}])

    def test_rest_series_keeps_last_limit_after_validation(self):
        from validation.candle_closure import validate_candle_series
        rows = [{"t": 100}, {"t": 200}, {"t": 300}]
        self.assertEqual([r["t"] for r in validate_candle_series(rows, limit=2)], [200, 300])

    def test_rest_series_rejects_non_mapping_rows(self):
        from validation.candle_closure import validate_candle_series
        with self.assertRaises(ValueError):
            validate_candle_series([{"t": 100}, None])

if __name__ == "__main__":
    unittest.main()
