import unittest

import app as dashboard


class ConfirmedSignalInvalidationTests(unittest.TestCase):
    def setUp(self):
        self.symbol = "BTCUSDT"
        dashboard.confirmed_state[self.symbol] = "LONG"
        dashboard.signal_history[self.symbol][:] = [{
            "symbol": self.symbol,
            "signal": "LONG",
            "status": "ACTIVE",
            "signal_time": 100,
        }]
        dashboard.cache[self.symbol].update({
            "signal": "SHORT",
            "opportunity_tier": "A",
            "confirmed_signal": "LONG",
            "confirmed_status": "ACTIVE",
            "confirmed_entry": 100.0,
            "confirmed_sl": 98.0,
            "confirmed_tp1": 104.0,
            "confirmed_tp2": 106.0,
        })

    def test_blocked_opposite_signal_clears_old_active_confirmation(self):
        # BTC is RESEARCH-only, so a SHORT signal must not become executable.
        changed = dashboard.record_confirmed_signal(self.symbol, candle_close_ms=200)

        self.assertFalse(changed)
        self.assertEqual(dashboard.confirmed_state[self.symbol], "NO-TRADE")
        self.assertEqual(dashboard.cache[self.symbol]["confirmed_signal"], "NO-TRADE")
        self.assertEqual(dashboard.cache[self.symbol]["confirmed_status"], "WAITING_VALIDATION")
        self.assertIsNone(dashboard.cache[self.symbol]["confirmed_entry"])
        self.assertIsNone(dashboard.cache[self.symbol]["confirmed_sl"])
        self.assertIsNone(dashboard.cache[self.symbol]["confirmed_tp1"])
        self.assertIsNone(dashboard.cache[self.symbol]["confirmed_tp2"])
        self.assertEqual(dashboard.signal_history[self.symbol][0]["status"], "INVALIDATED")
        self.assertEqual(dashboard.signal_history[self.symbol][0]["ended_at"], 200)


if __name__ == "__main__":
    unittest.main()
