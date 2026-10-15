import unittest
from validation.promotion_gate import classify_candidate, evaluate_live_order_gate

class PromotionGateTests(unittest.TestCase):
    def test_insufficient_holdout_trades_fails(self):
        self.assertEqual(classify_candidate(5, 6, 29, 1.5), 'FAIL_INSUFFICIENT_HOLDOUT_TRADES')

    def test_development_loss_fails_even_if_holdout_wins(self):
        self.assertEqual(classify_candidate(-1, 6, 50, 1.5), 'FAIL_NONPOSITIVE_DEVELOPMENT_OR_HOLDOUT')

    def test_holdout_loss_fails(self):
        self.assertEqual(classify_candidate(5, -1, 50, 1.5), 'FAIL_NONPOSITIVE_DEVELOPMENT_OR_HOLDOUT')

    def test_profit_factor_not_above_one_fails(self):
        self.assertEqual(classify_candidate(5, 6, 50, 1.0), 'FAIL_HOLDOUT_PROFIT_FACTOR')

    def test_missing_metrics_fail(self):
        self.assertEqual(classify_candidate(float('nan'), 6, 50, 1.5), 'FAIL_MISSING_OR_INVALID_NET_R')

    def test_positive_values_are_only_preliminary_pass(self):
        self.assertEqual(classify_candidate(5, 6, 50, 1.5), 'PASS_PRELIMINARY_SCREEN_ONLY')

    def test_boolean_metrics_are_not_accepted(self):
        self.assertEqual(classify_candidate(True, 6, 50, 1.5), 'FAIL_MISSING_OR_INVALID_NET_R')
        self.assertEqual(classify_candidate(5, 6, True, 1.5), 'FAIL_INSUFFICIENT_HOLDOUT_TRADES')
        self.assertEqual(classify_candidate(5, 6, 50, True), 'FAIL_HOLDOUT_PROFIT_FACTOR')


    def valid_order_inputs(self):
        snapshot = {
            "exchange_healthy": True, "kill_switch_active": False, "candle_closed": True,
            "model_status": "PRODUCTION", "signal": "LONG", "data_age_sec": 2.0,
            "analysis_age_sec": 3.0, "spread_bps": 4.0, "risk_fraction": 0.005,
            "daily_loss_fraction": 0.01, "open_positions": 0, "requested_notional": 100.0,
        }
        limits = {
            "max_data_age_sec": 10.0, "max_analysis_age_sec": 10.0, "max_spread_bps": 8.0,
            "max_risk_fraction": 0.01, "max_daily_loss_fraction": 0.05,
            "max_open_positions": 3, "max_notional": 500.0,
        }
        return snapshot, limits

    def test_valid_pretrade_snapshot_is_approved_not_submitted(self):
        snapshot, limits = self.valid_order_inputs()
        result = evaluate_live_order_gate(snapshot, limits)
        self.assertEqual(result, {"approved": True, "reasons": []})

    def test_non_mapping_inputs_fail_closed(self):
        self.assertEqual(evaluate_live_order_gate(None, {}), {
            "approved": False, "reasons": ["INVALID_SNAPSHOT_TYPE"]
        })
        self.assertEqual(evaluate_live_order_gate({}, None), {
            "approved": False, "reasons": ["INVALID_LIMITS_TYPE"]
        })

    def test_negative_market_metrics_and_daily_loss_are_rejected(self):
        snapshot, limits = self.valid_order_inputs()
        snapshot.update({"spread_bps": -1, "daily_loss_fraction": -0.01})
        result = evaluate_live_order_gate(snapshot, limits)
        self.assertFalse(result["approved"])
        self.assertIn("INVALID_NEGATIVE_MARKET_METRIC", result["reasons"])
        self.assertIn("INVALID_DAILY_LOSS_FRACTION", result["reasons"])

    def test_research_model_is_never_live_approved(self):
        snapshot, limits = self.valid_order_inputs()
        snapshot["model_status"] = "RESEARCH"
        result = evaluate_live_order_gate(snapshot, limits)
        self.assertFalse(result["approved"])
        self.assertIn("MODEL_NOT_PRODUCTION_APPROVED", result["reasons"])

    def test_stale_feed_and_open_kill_switch_fail_closed(self):
        snapshot, limits = self.valid_order_inputs()
        snapshot["data_age_sec"] = 999
        snapshot["kill_switch_active"] = True
        result = evaluate_live_order_gate(snapshot, limits)
        self.assertFalse(result["approved"])
        self.assertIn("STALE_MARKET_DATA", result["reasons"])
        self.assertIn("KILL_SWITCH_ACTIVE", result["reasons"])

    def test_missing_and_non_finite_inputs_fail_closed(self):
        snapshot, limits = self.valid_order_inputs()
        del snapshot["spread_bps"]
        result = evaluate_live_order_gate(snapshot, limits)
        self.assertFalse(result["approved"])
        self.assertIn("MISSING_SPREAD_BPS", result["reasons"])
        snapshot, limits = self.valid_order_inputs()
        snapshot["requested_notional"] = float("nan")
        result = evaluate_live_order_gate(snapshot, limits)
        self.assertFalse(result["approved"])
        self.assertIn("INVALID_REQUESTED_NOTIONAL", result["reasons"])

    def test_risk_loss_spread_position_and_notional_limits(self):
        snapshot, limits = self.valid_order_inputs()
        snapshot.update({"risk_fraction": 0.02, "daily_loss_fraction": 0.05,
                         "spread_bps": 12, "open_positions": 3, "requested_notional": 600})
        result = evaluate_live_order_gate(snapshot, limits)
        self.assertFalse(result["approved"])
        for reason in ("RISK_LIMIT_EXCEEDED", "DAILY_LOSS_LIMIT_REACHED",
                       "SPREAD_LIMIT_EXCEEDED", "OPEN_POSITION_LIMIT_REACHED",
                       "NOTIONAL_LIMIT_EXCEEDED"):
            self.assertIn(reason, result["reasons"])

    def test_forming_candle_and_no_trade_signal_are_rejected(self):
        snapshot, limits = self.valid_order_inputs()
        snapshot.update({"candle_closed": False, "signal": "NO-TRADE"})
        result = evaluate_live_order_gate(snapshot, limits)
        self.assertFalse(result["approved"])
        self.assertIn("CANDLE_NOT_CLOSED", result["reasons"])
        self.assertIn("NO_ACTIONABLE_SIGNAL", result["reasons"])


    def test_oversized_integer_metrics_fail_closed_without_crash(self):
        huge = 10 ** 1000
        self.assertEqual(
            classify_candidate(huge, 6, 50, 1.5),
            "FAIL_MISSING_OR_INVALID_NET_R",
        )
        snapshot, limits = self.valid_order_inputs()
        snapshot["requested_notional"] = huge
        result = evaluate_live_order_gate(snapshot, limits)
        self.assertFalse(result["approved"])
        self.assertIn("INVALID_REQUESTED_NOTIONAL", result["reasons"])


if __name__ == '__main__':
    unittest.main()
