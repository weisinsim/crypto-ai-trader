import unittest

from research.score_event_ledger import link_realized_trades, validate_score_event


class ScoreEventLedgerTests(unittest.TestCase):
    def setUp(self):
        self.event = {
            "signal_id": "BTC-1700000000000-LONG", "symbol": "BTCUSDT",
            "side": "LONG", "signal_ts": 1700000000000, "score": 82,
        }
        self.trade = {
            "signal_id": "BTC-1700000000000-LONG", "symbol": "BTCUSDT",
            "side": "LONG", "exit_ts": 1700003600000, "net_return": 0.012,
        }

    def test_links_only_by_stable_id_and_preserves_entry_score(self):
        result = link_realized_trades([self.event], [self.trade])
        self.assertEqual(result["status"], "RESEARCH_ONLY")
        self.assertFalse(result["auto_trade_authorized"])
        self.assertEqual(result["linked_trade_count"], 1)
        self.assertEqual(result["trades"][0]["score"], 82)
        self.assertEqual(result["trades"][0]["net_return"], 0.012)

    def test_unmatched_score_events_are_disclosed(self):
        result = link_realized_trades([self.event], [])
        self.assertEqual(result["unlinked_score_event_count"], 1)
        self.assertEqual(result["trades"], [])

    def test_duplicate_event_or_trade_ids_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "duplicate signal_id"):
            link_realized_trades([self.event, self.event], [])
        with self.assertRaisesRegex(ValueError, "duplicate signal_id"):
            link_realized_trades([self.event], [self.trade, self.trade])

    def test_missing_event_and_side_symbol_mismatch_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "no frozen score event"):
            link_realized_trades([], [self.trade])
        bad = dict(self.trade, side="SHORT")
        with self.assertRaisesRegex(ValueError, "symbol/side mismatch"):
            link_realized_trades([self.event], [bad])

    def test_invalid_score_and_exit_time_fail_closed(self):
        with self.assertRaises(ValueError):
            validate_score_event(dict(self.event, score=101))
        with self.assertRaisesRegex(ValueError, "invalid exit_ts"):
            link_realized_trades([self.event], [dict(self.trade, exit_ts=1)])


if __name__ == "__main__":
    unittest.main()
