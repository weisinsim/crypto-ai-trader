import unittest

from execution.testnet_lifecycle import ensure_testnet_protective_exits


class FakeAdapter:
    def __init__(self, position="0.5", entry="100", orders=None, fail_kind=None):
        self.position = position
        self.entry = entry
        self.orders = orders or []
        self.fail_kind = fail_kind
        self.submissions = []

    def testnet_position_snapshot(self, symbol):
        return {
            "ok": True,
            "symbol": symbol,
            "position_amount": self.position,
            "entry_price": self.entry,
            "open_orders": list(self.orders),
        }

    def submit_testnet_protective_exit(self, symbol, kind, quantity, price, client_id):
        self.submissions.append((symbol, kind, str(quantity), str(price), client_id))
        if kind == self.fail_kind:
            return {"accepted": False, "reason": "simulated_failure"}
        order_type = "STOP_MARKET" if kind == "stop_loss" else "TAKE_PROFIT_MARKET"
        self.orders.append({
            "status": "NEW", "reduce_only": True, "type": order_type,
            "stop_price": str(price), "client_order_id": client_id,
        })
        return {"accepted": True, "client_order_id": client_id}


class TestnetLifecycleTests(unittest.TestCase):
    def test_installs_stop_first_then_target(self):
        adapter = FakeAdapter()
        result = ensure_testnet_protective_exits(adapter, "BTCUSDT", "99", "102", "btc-run-01")
        self.assertTrue(result["ok"])
        self.assertEqual([s[1] for s in adapter.submissions], ["stop_loss", "take_profit"])
        self.assertTrue(result["requires_post_submission_reconciliation"])

    def test_target_failure_leaves_stop_and_demands_reconciliation(self):
        adapter = FakeAdapter(fail_kind="take_profit")
        result = ensure_testnet_protective_exits(adapter, "BTCUSDT", "99", "102", "btc-run-02")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "take_profit_submission_failed_stop_remains")
        self.assertEqual([s[1] for s in adapter.submissions], ["stop_loss", "take_profit"])
        self.assertTrue(result["requires_reconciliation"])

    def test_existing_matching_stop_is_reused(self):
        adapter = FakeAdapter(orders=[{
            "status": "NEW", "reduce_only": True, "type": "STOP_MARKET", "stop_price": "99",
        }])
        result = ensure_testnet_protective_exits(adapter, "BTCUSDT", "99", "102", "btc-run-03")
        self.assertTrue(result["ok"])
        self.assertEqual([s[1] for s in adapter.submissions], ["take_profit"])

    def test_mismatched_existing_stop_fails_closed(self):
        adapter = FakeAdapter(orders=[{
            "status": "NEW", "reduce_only": True, "type": "STOP_MARKET", "stop_price": "98",
        }])
        result = ensure_testnet_protective_exits(adapter, "BTCUSDT", "99", "102", "btc-run-04")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "existing_stop_price_mismatch")
        self.assertEqual(adapter.submissions, [])

    def test_flat_position_does_not_submit_orders(self):
        adapter = FakeAdapter(position="0")
        result = ensure_testnet_protective_exits(adapter, "BTCUSDT", "99", "102", "btc-run-05")
        self.assertEqual(result["reason"], "no_open_position")
        self.assertEqual(adapter.submissions, [])

    def test_short_position_requires_target_below_entry_and_stop_above(self):
        adapter = FakeAdapter(position="-0.5", entry="100")
        result = ensure_testnet_protective_exits(adapter, "BTCUSDT", "101", "98", "btc-run-06")
        self.assertTrue(result["ok"])
        self.assertEqual([s[1] for s in adapter.submissions], ["stop_loss", "take_profit"])


if __name__ == "__main__":
    unittest.main()
