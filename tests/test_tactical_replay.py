import unittest

from research.tactical_replay import replay_tactical_setup


class TacticalReplayTests(unittest.TestCase):
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

    def inputs(self):
        one_hour = self.candles(205, 3_600_000)
        four_hour = self.candles(60, 14_400_000)
        now_ms = max(one_hour[-1]["t"] + 3_600_000,
                     four_hour[-1]["t"] + 14_400_000)
        return one_hour, four_hour, now_ms

    def test_replay_is_deterministic_and_research_only(self):
        one_hour, four_hour, now_ms = self.inputs()
        first = replay_tactical_setup(
            symbol="BTCUSDT", candles_1h=one_hour,
            candles_4h=four_hour, now_ms=now_ms,
        )
        second = replay_tactical_setup(
            symbol="BTCUSDT", candles_1h=one_hour,
            candles_4h=four_hour, now_ms=now_ms,
        )
        self.assertEqual(first, second)
        self.assertTrue(first["research_only"])
        self.assertFalse(first["auto_trade_authorized"])
        self.assertEqual(first["closed_1h_candles"], 205)
        self.assertEqual(first["closed_4h_candles"], 60)
        self.assertEqual(first["signal_candle_close_ms"], one_hour[-1]["t"] + 3_600_000)

    def test_open_candles_are_excluded(self):
        one_hour, four_hour, now_ms = self.inputs()
        one_hour.append({**one_hour[-1], "t": one_hour[-1]["t"] + 3_600_000, "c": 999.0, "h": 999.0})
        four_hour.append({**four_hour[-1], "t": four_hour[-1]["t"] + 14_400_000, "c": 999.0, "h": 999.0})
        result = replay_tactical_setup(
            symbol="SOLUSDT", candles_1h=one_hour,
            candles_4h=four_hour, now_ms=now_ms,
        )
        self.assertEqual(result["closed_1h_candles"], 205)
        self.assertEqual(result["closed_4h_candles"], 60)
        self.assertNotEqual(result["indicators"]["price"], 999.0)

    def test_rejects_unsupported_model_and_insufficient_history(self):
        one_hour, four_hour, now_ms = self.inputs()
        with self.assertRaisesRegex(ValueError, "supports"):
            replay_tactical_setup(
                symbol="XRPUSDT", candles_1h=one_hour,
                candles_4h=four_hour, now_ms=now_ms,
            )
        with self.assertRaisesRegex(ValueError, "insufficient closed candles"):
            replay_tactical_setup(
                symbol="BTCUSDT", candles_1h=one_hour[:30],
                candles_4h=four_hour, now_ms=now_ms,
            )


if __name__ == "__main__":
    unittest.main()
