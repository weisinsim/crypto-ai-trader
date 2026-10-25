import unittest

from research.historical_replay_inputs import normalize_closed_candle_window


class HistoricalReplayInputTests(unittest.TestCase):
    def candle(self, timestamp, **overrides):
        row = {
            "t": timestamp,
            "o": 100.0,
            "h": 105.0,
            "l": 95.0,
            "c": 102.0,
            "v": 10.0,
            "q": 1020.0,
        }
        row.update(overrides)
        return row

    def test_returns_only_closed_candles_with_injected_clock(self):
        rows = [
            self.candle(0),
            self.candle(3_600_000),
            self.candle(7_200_000),
        ]
        result = normalize_closed_candle_window(
            rows, timeframe="1h", now_ms=7_200_000, minimum_rows=2
        )
        self.assertEqual([row["t"] for row in result], [0, 3_600_000])
        self.assertIsInstance(result, tuple)

    def test_detaches_rows_from_mutable_source(self):
        source = [self.candle(0)]
        frozen = normalize_closed_candle_window(
            source, timeframe="1h", now_ms=3_600_000
        )
        source[0]["c"] = 999
        self.assertEqual(frozen[0]["c"], 102.0)

    def test_rejects_unsupported_timeframe_and_insufficient_closed_rows(self):
        with self.assertRaisesRegex(ValueError, "timeframe"):
            normalize_closed_candle_window([], timeframe="5m", now_ms=1)
        with self.assertRaisesRegex(ValueError, "insufficient closed candles"):
            normalize_closed_candle_window(
                [self.candle(0)], timeframe="1h", now_ms=3_599_999
            )

    def test_rejects_missing_fields_and_nonfinite_values(self):
        missing = self.candle(0)
        del missing["q"]
        with self.assertRaisesRegex(ValueError, "missing fields"):
            normalize_closed_candle_window([missing], timeframe="1h", now_ms=4_000_000)
        with self.assertRaisesRegex(ValueError, "finite numeric"):
            normalize_closed_candle_window(
                [self.candle(0, c=float("nan"))],
                timeframe="1h", now_ms=4_000_000,
            )

    def test_rejects_invalid_ohlc_geometry_and_negative_volume(self):
        with self.assertRaisesRegex(ValueError, "high is inconsistent"):
            normalize_closed_candle_window(
                [self.candle(0, h=101.0)], timeframe="1h", now_ms=4_000_000
            )
        with self.assertRaisesRegex(ValueError, "nonnegative"):
            normalize_closed_candle_window(
                [self.candle(0, v=-1.0)], timeframe="1h", now_ms=4_000_000
            )

    def test_rejects_duplicate_or_out_of_order_timestamps(self):
        rows = [self.candle(0), self.candle(0)]
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            normalize_closed_candle_window(rows, timeframe="1h", now_ms=8_000_000)


if __name__ == "__main__":
    unittest.main()
