"""Tests for deterministic paper candle replay; no exchange calls or orders."""
import unittest

from execution.candle_replay import replay_paper_trade
from execution.paper_replay import summarize_closed_trades


def event():
    return {
        "event_id": "evt-replay-1",
        "symbol": "BTCUSDT",
        "side": "LONG",
        "eligible": True,
        "notification_only": True,
        "auto_trade_authorized": False,
        "entry_price": 100.0,
        "stop_loss": 95.0,
        "take_profit_1": 105.0,
        "take_profit_2": 110.0,
    }


def candle(timestamp_ms, *, open=100, high=101, low=99, close=100):
    return {
        "timestamp_ms": timestamp_ms,
        "open": open,
        "high": high,
        "low": low,
        "close": close,
    }


class CandleReplayTests(unittest.TestCase):
    def test_replay_closes_trade_and_reports_net_result(self):
        result = replay_paper_trade(
            event(),
            [
                candle(2_000, high=106, low=99, close=104),
                candle(3_000, open=104, high=111, low=103, close=110),
                candle(4_000, low=90, high=112),
            ],
            quantity=2,
            opened_at_ms=1_000,
            fee_rate=0,
        )
        self.assertEqual(
            [item["action"] for item in result["actions"]],
            ["TP1_PARTIAL", "TP2_FINAL"],
        )
        self.assertEqual(result["trade"]["status"], "CLOSED")
        self.assertEqual(result["summary"]["net_pnl"], 15.0)
        self.assertEqual(result["orders_sent"], 0)
        self.assertTrue(result["research_only"])

    def test_replay_stop_first_for_stop_target_collision(self):
        result = replay_paper_trade(
            event(),
            [candle(2_000, high=111, low=94, close=106)],
            quantity=2,
            opened_at_ms=1_000,
            fee_rate=0,
        )
        self.assertEqual(result["actions"][0]["action"], "STOP_LOSS")
        self.assertEqual(result["summary"]["net_pnl"], -10.0)

    def test_rejects_duplicate_or_out_of_order_timestamps_before_replay(self):
        for candles in (
            [candle(2_000), candle(2_000)],
            [candle(3_000), candle(2_000)],
            [candle(1_000)],
        ):
            with self.subTest(candles=candles):
                with self.assertRaisesRegex(
                    ValueError, "candles_must_be_strictly_increasing"
                ):
                    replay_paper_trade(
                        event(), candles, quantity=1, opened_at_ms=1_000
                    )

    def test_open_trade_has_no_summary_without_mark_price(self):
        result = replay_paper_trade(
            event(), [candle(2_000)], quantity=1, opened_at_ms=1_000
        )
        self.assertEqual(result["trade"]["status"], "OPEN")
        self.assertIsNone(result["summary"])

    def test_open_trade_can_report_mark_to_market(self):
        result = replay_paper_trade(
            event(), [candle(2_000)], quantity=1, opened_at_ms=1_000,
            fee_rate=0, mark_price=102,
        )
        self.assertAlmostEqual(result["summary"]["net_pnl"], 2.0)


    def test_closed_candle_replay_feeds_net_metrics_report(self):
        result = replay_paper_trade(
            event(),
            [
                candle(2_000, high=106, low=99, close=104),
                candle(3_000, open=104, high=111, low=103, close=110),
            ],
            quantity=2,
            opened_at_ms=1_000,
            fee_rate=0,
        )
        report = summarize_closed_trades([result["trade"]], starting_equity=100)
        self.assertEqual(report["trade_count"], 1)
        self.assertAlmostEqual(report["net_pnl"], result["summary"]["net_pnl"])
        self.assertAlmostEqual(report["ending_equity"], 115.0)
        self.assertEqual(report["open_trades_included"], 0)
        self.assertEqual(result["orders_sent"], 0)


    def test_short_replay_applies_tp1_once_then_closes_at_tp2(self):
        short_event = {
            **event(),
            "side": "SHORT",
            "stop_loss": 105.0,
            "take_profit_1": 95.0,
            "take_profit_2": 90.0,
        }
        result = replay_paper_trade(
            short_event,
            [
                candle(2_000, open=100, high=101, low=94, close=96),
                candle(3_000, open=96, high=97, low=89, close=90),
                candle(4_000, open=90, high=101, low=88, close=99),
            ],
            quantity=2,
            opened_at_ms=1_000,
            fee_rate=0,
        )
        self.assertEqual(
            [item["action"] for item in result["actions"]],
            ["TP1_PARTIAL", "TP2_FINAL"],
        )
        self.assertEqual(result["trade"]["status"], "CLOSED")
        self.assertAlmostEqual(result["summary"]["net_pnl"], 15.0)
        self.assertEqual(result["orders_sent"], 0)


if __name__ == "__main__":
    unittest.main()
