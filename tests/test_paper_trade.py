"""Tests for fee-aware paper trade simulation; no exchange orders are sent."""
import unittest

from execution.paper_trade import (
    apply_funding,
    open_paper_trade,
    process_candle,
    trade_summary,
    update_trailing_stop,
)


def event(side="LONG"):
    if side == "LONG":
        entry, stop, tp1, tp2 = 100.0, 95.0, 105.0, 110.0
    else:
        entry, stop, tp1, tp2 = 100.0, 105.0, 95.0, 90.0
    return {
        "event_id": "evt-paper-1", "symbol": "BTCUSDT", "side": side,
        "eligible": True, "notification_only": True, "auto_trade_authorized": False,
        "entry_price": entry, "stop_loss": stop,
        "take_profit_1": tp1, "take_profit_2": tp2,
    }


class PaperTradeTests(unittest.TestCase):
    def open(self, side="LONG"):
        return open_paper_trade(event(side), quantity=2.0, fee_rate=0.001,
                                opened_at_ms=1_000)

    def test_tp1_partially_closes_and_tightens_stop_to_break_even(self):
        trade = self.open()
        result = process_candle(trade, {"open": 100, "high": 106, "low": 99, "close": 104},
                                timestamp_ms=2_000)
        self.assertEqual(result["action"], "TP1_PARTIAL")
        self.assertEqual(trade["quantity_remaining"], 1.0)
        self.assertEqual(trade["trailing_stop"], 100.0)
        self.assertAlmostEqual(trade["realized_gross_pnl"], 5.0)

    def test_stop_first_when_stop_and_target_are_both_inside_same_candle(self):
        trade = self.open()
        result = process_candle(trade, {"open": 100, "high": 111, "low": 94, "close": 106},
                                timestamp_ms=2_000)
        self.assertEqual(result["action"], "STOP_LOSS")
        self.assertEqual(trade["status"], "CLOSED")
        self.assertAlmostEqual(trade["realized_gross_pnl"], -10.0)

    def test_tp2_closes_remaining_position(self):
        trade = self.open()
        result = process_candle(trade, {"open": 100, "high": 111, "low": 99, "close": 110},
                                timestamp_ms=2_000)
        self.assertEqual(result["action"], "TP1_PARTIAL+TP2_FINAL")
        self.assertEqual(trade["status"], "CLOSED")
        self.assertAlmostEqual(trade["realized_gross_pnl"], 15.0)
        self.assertEqual(trade["quantity_remaining"], 0)

    def test_short_stop_and_target_geometry(self):
        trade = self.open("SHORT")
        result = process_candle(trade, {"open": 100, "high": 101, "low": 89, "close": 92},
                                timestamp_ms=2_000)
        self.assertEqual(result["action"], "TP1_PARTIAL+TP2_FINAL")
        self.assertAlmostEqual(trade["realized_gross_pnl"], 15.0)

    def test_trailing_stop_only_tightens(self):
        trade = self.open()
        self.assertTrue(update_trailing_stop(trade, candidate_stop=98))
        self.assertFalse(update_trailing_stop(trade, candidate_stop=97))
        self.assertEqual(trade["trailing_stop"], 98)
        self.assertFalse(update_trailing_stop(trade, candidate_stop=96))
        self.assertEqual(trade["trailing_stop"], 98)

    def test_funding_and_fees_are_included_in_net_pnl(self):
        trade = self.open()
        apply_funding(trade, rate=0.001, mark_price=100, timestamp_ms=1_500)
        # Duplicate funding timestamp must be rejected while the trade is still open.
        with self.assertRaisesRegex(ValueError, "invalid_funding_input"):
            apply_funding(trade, rate=0.001, mark_price=100, timestamp_ms=1_500)
        process_candle(trade, {"open": 100, "high": 111, "low": 99, "close": 110},
                       timestamp_ms=2_000)
        summary = trade_summary(trade)
        self.assertAlmostEqual(summary["gross_pnl"], 15.0)
        self.assertGreater(summary["fees_paid"], 0)
        self.assertAlmostEqual(summary["funding_paid"], 0.2)
        self.assertAlmostEqual(summary["net_pnl"], 15.0 - summary["fees_paid"] - 0.2)

    def test_candle_timestamps_must_be_strictly_increasing(self):
        trade = self.open()
        candle = {"open": 100, "high": 104, "low": 99, "close": 102}
        process_candle(trade, candle, timestamp_ms=2_000)
        with self.assertRaisesRegex(ValueError, "invalid_candle"):
            process_candle(trade, candle, timestamp_ms=2_000)
        with self.assertRaisesRegex(ValueError, "invalid_candle"):
            process_candle(trade, candle, timestamp_ms=1_500)

    def test_zero_fee_rate_is_valid_for_controlled_simulation(self):
        trade = open_paper_trade(event(), quantity=1, fee_rate=0, opened_at_ms=1_000)
        self.assertEqual(trade["fees_paid"], 0)

    def test_rejects_non_notification_event(self):
        bad = event()
        bad["auto_trade_authorized"] = True
        with self.assertRaisesRegex(ValueError, "notification_only"):
            open_paper_trade(bad, quantity=1, opened_at_ms=1_000)

    def test_rejects_bad_ohlc_and_nonchronological_candle(self):
        trade = self.open()
        with self.assertRaisesRegex(ValueError, "invalid_candle"):
            process_candle(trade, {"open": 100, "high": 99, "low": 98, "close": 98},
                           timestamp_ms=2_000)
        with self.assertRaisesRegex(ValueError, "invalid_candle"):
            process_candle(trade, {"open": 100, "high": 101, "low": 99, "close": 100},
                           timestamp_ms=1_000)


if __name__ == "__main__":
    unittest.main()
