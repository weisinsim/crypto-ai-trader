import unittest

from research.score_event_replay_audit import (
    build_frozen_score_event,
    compare_replayed_score_events,
    audit_replay_series,
)


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

    def test_replay_comparison_reports_identical_events(self):
        first = self.build()
        second = self.build()
        report = compare_replayed_score_events(first, second)
        self.assertEqual(report["status"], "DETERMINISTIC")
        self.assertEqual(report["differences"], {})
        self.assertTrue(report["research_only"])
        self.assertFalse(report["auto_trade_authorized"])

    def test_replay_comparison_reports_field_level_mismatches(self):
        first = self.build()
        second = self.build(score=79.0)
        report = compare_replayed_score_events(first, second)
        self.assertEqual(report["status"], "MISMATCH")
        self.assertIn("score", report["differences"])
        self.assertIn("signal_id", report["differences"])
        self.assertEqual(report["differences"]["score"]["first"], 78.5)
        self.assertEqual(report["differences"]["score"]["second"], 79.0)

    def test_replay_comparison_rejects_non_mapping_inputs(self):
        with self.assertRaisesRegex(ValueError, "mappings"):
            compare_replayed_score_events(self.build(), None)

    def test_replay_series_passes_when_events_match(self):
        expected = [self.build(), self.build(score=81.0, candle_close_ts=1_800_000_003_600)]
        replayed = [self.build(score=81.0, candle_close_ts=1_800_000_003_600), self.build()]
        report = audit_replay_series(expected, replayed)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["matched_event_count"], 2)
        self.assertEqual(report["missing_signal_ids"], [])
        self.assertEqual(report["unexpected_signal_ids"], [])
        self.assertTrue(report["research_only"])
        self.assertFalse(report["auto_trade_authorized"])

    def test_replay_series_reports_missing_unexpected_and_changed_events(self):
        expected = [self.build(), self.build(score=81.0, candle_close_ts=1_800_000_003_600)]
        replayed = [self.build(score=82.0, candle_close_ts=1_800_000_003_600),
                    self.build(candle_close_ts=1_800_000_007_200)]
        report = audit_replay_series(expected, replayed)
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(len(report["missing_signal_ids"]), 2)
        self.assertEqual(len(report["unexpected_signal_ids"]), 2)

    def test_replay_series_rejects_duplicate_ids_and_invalid_rows(self):
        event = self.build()
        report = audit_replay_series([event, event], [event, None])
        self.assertEqual(report["status"], "FAIL")
        self.assertTrue(report["expected_input_errors"])
        self.assertTrue(report["replayed_input_errors"])

    def test_replay_series_requires_sequences(self):
        with self.assertRaisesRegex(ValueError, "sequences"):
            audit_replay_series(None, [])

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
