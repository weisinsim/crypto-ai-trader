import unittest

from research.score_event_replay_audit import build_frozen_score_event


class ScoreEventReplayAuditTests(unittest.TestCase):
    def build(self, **overrides):
        args = {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "timeframe": "1h",
            "candle_close_ts": 1_800_000_000_000,
            "model_version": "v26.1",
            "score": 78.5,
        }
        args.update(overrides)
        return build_frozen_score_event(**args)

    def test_same_closed_candle_and_score_produce_same_event(self):
        first = self.build()
        second = self.build()
        self.assertEqual(first, second)
        self.assertTrue(first["signal_id"].startswith("replay_"))
        self.assertTrue(first["research_only"])
        self.assertFalse(first["auto_trade_authorized"])

    def test_identity_changes_when_score_or_model_or_candle_changes(self):
        base = self.build()
        self.assertNotEqual(base["signal_id"], self.build(score=78.6)["signal_id"])
        self.assertNotEqual(base["signal_id"], self.build(model_version="v26.2")["signal_id"])
        self.assertNotEqual(
            base["signal_id"],
            self.build(candle_close_ts=1_800_000_003_600)["signal_id"],
        )
        self.assertNotEqual(base["signal_id"], self.build(side="SHORT")["signal_id"])

    def test_normalizes_symbol_side_and_timeframe(self):
        first = self.build()
        normalized = self.build(symbol=" btcusdt ", side="long", timeframe="1H")
        self.assertEqual(first["signal_id"], normalized["signal_id"])
        self.assertEqual(normalized["symbol"], "BTCUSDT")
        self.assertEqual(normalized["side"], "LONG")
        self.assertEqual(normalized["timeframe"], "1h")

    def test_rejects_invalid_identity_or_score_inputs(self):
        invalid_cases = [
            {"symbol": ""},
            {"side": "BUY"},
            {"timeframe": ""},
            {"candle_close_ts": True},
            {"candle_close_ts": -1},
            {"model_version": ""},
            {"score": True},
            {"score": float("nan")},
            {"score": 101},
        ]
        for overrides in invalid_cases:
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                self.build(**overrides)


if __name__ == "__main__":
    unittest.main()
