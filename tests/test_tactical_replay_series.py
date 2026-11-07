import unittest

from research.tactical_replay_series import replay_tactical_series


class TacticalReplaySeriesTests(unittest.TestCase):
    def candles(self, count, interval_ms, start=0, drift=0.03):
        rows = []
        price = 100.0
        for i in range(count):
            price += drift + (0.15 if i % 7 < 4 else -0.08)
            rows.append({
                "t": start + i * interval_ms,
                "o": price - 0.1, "h": price + 0.8,
                "l": price - 0.8, "c": price,
                "v": 10.0 + i % 5, "q": (10.0 + i % 5) * price,
            })
        return rows

    def test_replays_each_hour_close_in_chronological_order(self):
        one_hour = self.candles(230, 3_600_000)
        four_hour = self.candles(60, 14_400_000)
        results = replay_tactical_series(
            symbol="BTCUSDT", candles_1h=one_hour, candles_4h=four_hour
        )
        self.assertEqual(len(results), 31)
        close_times = [item["signal_candle_close_ms"] for item in results]
        self.assertEqual(close_times, sorted(close_times))
        self.assertEqual(len(set(close_times)), len(close_times))
        self.assertTrue(all(item["research_only"] for item in results))
        self.assertTrue(all(item["auto_trade_authorized"] is False for item in results))

    def test_future_candle_mutation_does_not_change_earlier_observation(self):
        one_hour = self.candles(230, 3_600_000)
        four_hour = self.candles(60, 14_400_000)
        baseline = replay_tactical_series(
            symbol="SOLUSDT", candles_1h=one_hour, candles_4h=four_hour
        )
        changed = [dict(row) for row in one_hour]
        for row in changed[220:]:
            row["o"] = 500.0
            row["h"] = 501.0
            row["l"] = 499.0
            row["c"] = 500.0
            row["q"] = 5000.0
        replay_changed = replay_tactical_series(
            symbol="SOLUSDT", candles_1h=changed, candles_4h=four_hour
        )
        self.assertEqual(baseline[10], replay_changed[10])

    def test_rejects_invalid_start_index(self):
        with self.assertRaisesRegex(ValueError, "start_index"):
            replay_tactical_series(
                symbol="BTCUSDT", candles_1h=[], candles_4h=[], start_index=-1
            )


if __name__ == "__main__":
    unittest.main()
