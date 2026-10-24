import unittest

from research.v12_backtest import run_v12_backtest


def feature_row(t, *, o=101.0, h=102.0, l=100.0, c=101.0):
    return {
        "t": t, "o": o, "h": h, "l": l, "c": c,
        "ema20_1h": 100.0, "ema50_1h": 99.0, "ema200_1h": 98.0,
        "ema20_4h": 100.0, "ema50_4h": 98.0, "ema200_4h": 97.0,
        "rsi_1h": 56.0, "atr_1h": 1.0, "atr_pct": 1.0, "adx_1h": 26.0,
        "vol_ratio": 1.5, "vwap_1h": 99.5, "support": 96.0, "resistance": 110.0,
        "funding_rate": -0.0005, "oi_change_pct": 4.0, "momentum_pct": 2.0,
        "macd_hist": 0.2, "breakout_up": True, "breakout_down": False,
        "retest_long": True, "retest_short": False, "cvd_direction": "UP",
        "market_regime": "BULL", "data_fresh": True, "candle_closed": True,
        "spread_bps": 3.0,
    }


class V12BacktestTests(unittest.TestCase):
    def test_entry_is_next_open_and_two_targets_take_profit(self):
        rows = [
            feature_row(0),
            feature_row(3_600_000, o=101.0, h=106.0, l=100.0, c=105.0),
            feature_row(7_200_000, o=105.0, h=106.0, l=104.0, c=105.0),
        ]
        result = run_v12_backtest(rows, "BTCUSDT", fee_rate=0.0001, slippage_rate=0.0001)
        self.assertEqual(result["trades"], 1)
        trade = result["trade_log"][0]
        self.assertEqual(trade["entry_time"], 3_600_000)
        self.assertEqual(trade["exit_kind"], "TP1+TP2")
        self.assertEqual([exit_row["kind"] for exit_row in trade["exits"]], ["TP1", "TP2"])
        self.assertGreater(trade["net_pnl"], 0)
        self.assertFalse(result["live_trading_enabled"])

    def test_stop_wins_when_stop_and_target_touch_same_bar(self):
        rows = [
            feature_row(0),
            feature_row(3_600_000, o=101.0, h=106.0, l=99.0, c=103.0),
            feature_row(7_200_000, o=103.0, h=104.0, l=102.0, c=103.0),
        ]
        result = run_v12_backtest(rows, "BTCUSDT", fee_rate=0.0001, slippage_rate=0.0001)
        trade = result["trade_log"][0]
        self.assertEqual(trade["exit_kind"], "STOP")
        self.assertEqual([exit_row["kind"] for exit_row in trade["exits"]], ["STOP"])
        self.assertLess(trade["net_pnl"], 0)

    def test_costs_reduce_net_pnl(self):
        rows = [
            feature_row(0),
            feature_row(3_600_000, o=101.0, h=106.0, l=100.0, c=105.0),
            feature_row(7_200_000, o=105.0, h=106.0, l=104.0, c=105.0),
        ]
        cheap = run_v12_backtest(rows, "BTCUSDT", fee_rate=0.0, slippage_rate=0.0)
        costly = run_v12_backtest(rows, "BTCUSDT", fee_rate=0.001, slippage_rate=0.001)
        self.assertGreater(cheap["trade_log"][0]["net_pnl"], costly["trade_log"][0]["net_pnl"])

    def test_invalid_risk_configuration_is_rejected(self):
        with self.assertRaises(ValueError):
            run_v12_backtest([feature_row(0), feature_row(1), feature_row(2)],
                             "BTCUSDT", risk_fraction=0.1)

    def test_feature_symbol_mismatch_is_rejected(self):
        rows = [feature_row(0), feature_row(1), feature_row(2)]
        for item in rows:
            item["symbol"] = "ETHUSDT"
        with self.assertRaises(ValueError):
            run_v12_backtest(rows, "BTCUSDT")

    def test_historical_funding_cashflow_is_applied_to_open_position(self):
        rows = [
            feature_row(0),
            feature_row(3_600_000, o=101.0, h=102.0, l=100.0, c=101.0),
            feature_row(7_200_000, o=101.0, h=106.0, l=100.0, c=105.0),
        ]
        baseline = run_v12_backtest(rows, "BTCUSDT", fee_rate=0.0001, slippage_rate=0.0001)
        funding_event = {
            "funding_time": 4_000_000, "funding_rate": 0.001,
            "mark_price": 101.0, "symbol": "BTCUSDT",
        }
        funded = run_v12_backtest(
            rows, "BTCUSDT", fee_rate=0.0001, slippage_rate=0.0001,
            funding_events=[funding_event],
        )
        self.assertLess(funded["trade_log"][0]["funding_pnl"], 0)
        self.assertLess(funded["trade_log"][0]["net_pnl"], baseline["trade_log"][0]["net_pnl"])
        self.assertTrue(funded["assumptions"]["funding_costs_included"])

    def test_funding_at_entry_timestamp_is_excluded_as_boundary_ambiguous(self):
        rows = [
            feature_row(0),
            feature_row(3_600_000, o=101.0, h=102.0, l=100.0, c=101.0),
            feature_row(7_200_000, o=101.0, h=106.0, l=100.0, c=105.0),
        ]
        result = run_v12_backtest(
            rows, "BTCUSDT",
            funding_events=[{"funding_time": 3_600_000, "funding_rate": 0.001,
                             "mark_price": 101.0, "symbol": "BTCUSDT"}],
        )
        self.assertEqual(result["trade_log"][0]["funding_pnl"], 0.0)

    def test_max_drawdown_includes_open_position_adverse_excursion(self):
        rows = [
            feature_row(0),
            feature_row(3_600_000, o=101.0, h=102.0, l=99.6, c=99.8),
            feature_row(7_200_000, o=99.8, h=106.0, l=99.7, c=105.0),
        ]
        result = run_v12_backtest(rows, "BTCUSDT", fee_rate=0.0001, slippage_rate=0.0001)
        self.assertGreater(result["trade_log"][0]["net_pnl"], 0)
        self.assertGreater(result["max_drawdown_pct"], 0)
        self.assertGreater(len(result["equity_curve"]), 2)


if __name__ == "__main__":
    unittest.main()
