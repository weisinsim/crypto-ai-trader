import unittest

from research.v12_features import _ema, build_v12_features


def candles(count, interval_ms, start=100.0):
    rows = []
    for i in range(count):
        close = start + i * 0.2
        rows.append({
            "t": i * interval_ms, "o": close - 0.1, "h": close + 0.3,
            "l": close - 0.3, "c": close, "v": 100.0 + i,
            "q": (100.0 + i) * close,
        })
    return rows


class V12FeatureBuilderTests(unittest.TestCase):
    def test_features_use_only_4h_candles_closed_by_signal_close(self):
        one_hour = candles(300, 3_600_000)
        four_hour = candles(80, 14_400_000)
        features = build_v12_features(one_hour, four_hour)
        self.assertTrue(features)
        first = features[0]
        # At the first feature row, no 4h candle whose close is after the 1h
        # signal close may be included. 4h bar 49 closes at hour 200.
        signal_close = first["t"] + 3_600_000
        eligible_4h = [row for row in four_hour if row["t"] + 14_400_000 <= signal_close]
        self.assertGreaterEqual(len(eligible_4h), 50)
        expected = _ema([row["c"] for row in eligible_4h], 20)
        self.assertAlmostEqual(first["ema20_4h"], expected, places=6)
        self.assertTrue(first["candle_closed"])
        self.assertTrue(first["data_fresh"])

    def test_funding_is_joined_as_of_signal_close_not_future(self):
        one_hour = candles(300, 3_600_000)
        four_hour = candles(80, 14_400_000)
        events = [
            {"funding_time": 0, "funding_rate": 0.0001},
            {"funding_time": 250 * 3_600_000, "funding_rate": -0.0007},
        ]
        features = build_v12_features(one_hour, four_hour, events)
        before_future_event = [row for row in features if row["t"] + 3_600_000 < 250 * 3_600_000]
        after_future_event = [row for row in features if row["t"] + 3_600_000 >= 250 * 3_600_000]
        self.assertTrue(before_future_event)
        self.assertTrue(after_future_event)
        self.assertEqual(before_future_event[-1]["funding_rate"], 0.0001)
        self.assertEqual(after_future_event[0]["funding_rate"], -0.0007)

    def test_feature_builder_does_not_fabricate_oi_or_cvd(self):
        features = build_v12_features(candles(300, 3_600_000), candles(80, 14_400_000))
        self.assertTrue(features)
        self.assertTrue(all(row["oi_change_pct"] is None for row in features))
        self.assertTrue(all(row["cvd_direction"] == "UNKNOWN" for row in features))


if __name__ == "__main__":
    unittest.main()
