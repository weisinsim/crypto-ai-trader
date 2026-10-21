"""Tests for independent multi-symbol candle replay orchestration."""
import unittest

from execution.multi_candle_replay import replay_multiple_events


def make_event(event_id, symbol, side="LONG"):
    return {
        "event_id": event_id,
        "symbol": symbol,
        "side": side,
        "eligible": True,
        "notification_only": True,
        "auto_trade_authorized": False,
        "entry_price": 100.0,
        "stop_loss": 95.0 if side == "LONG" else 105.0,
        "take_profit_1": 105.0 if side == "LONG" else 95.0,
        "take_profit_2": 110.0 if side == "LONG" else 90.0,
    }


def candle(timestamp_ms, *, open=100, high=111, low=99, close=110):
    return {"timestamp_ms": timestamp_ms, "open": open, "high": high,
            "low": low, "close": close}


class MultiCandleReplayTests(unittest.TestCase):
    def test_aggregates_closed_trades_across_symbols(self):
        scenarios = [
            {"event": make_event("e1", "BTCUSDT"), "candles": [candle(2000)],
             "quantity": 2, "opened_at_ms": 1000, "fee_rate": 0},
            {"event": make_event("e2", "ETHUSDT"), "candles": [
                candle(3000, high=101, low=94, close=96)],
             "quantity": 2, "opened_at_ms": 1000, "fee_rate": 0},
        ]
        result = replay_multiple_events(scenarios, starting_equity=100)
        self.assertEqual(result["scenario_count"], 2)
        self.assertEqual(result["closed_trade_count"], 2)
        self.assertEqual(result["open_trade_count"], 0)
        self.assertEqual(result["closed_trade_metrics"]["net_pnl"], 5.0)
        self.assertEqual(result["orders_sent"], 0)
        self.assertTrue(result["research_only"])
        self.assertFalse(result["portfolio_margin_modeled"])

    def test_keeps_open_positions_out_of_closed_metrics_and_counts_them(self):
        result = replay_multiple_events([
            {"event": make_event("open1", "BTCUSDT"),
             "candles": [candle(2000, high=101, low=99, close=100)],
             "quantity": 1, "opened_at_ms": 1000, "fee_rate": 0,
             "mark_price": 100}
        ])
        self.assertEqual(result["closed_trade_count"], 0)
        self.assertEqual(result["open_trade_count"], 1)
        self.assertEqual(result["closed_trade_metrics"]["trade_count"], 0)

    def test_rejects_duplicate_event_ids(self):
        scenario = {"event": make_event("same", "BTCUSDT"),
                    "candles": [], "quantity": 1, "opened_at_ms": 1000}
        with self.assertRaisesRegex(ValueError, "duplicate_event_id"):
            replay_multiple_events([scenario, scenario])

    def test_rejects_missing_scenario_fields(self):
        with self.assertRaisesRegex(ValueError, "missing_scenario_field"):
            replay_multiple_events([{"event": make_event("e", "BTCUSDT")}])


if __name__ == "__main__":
    unittest.main()
