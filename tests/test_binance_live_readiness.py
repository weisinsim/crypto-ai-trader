import unittest
from unittest.mock import Mock

from execution.binance_live_readiness import (
    BinanceLiveReadiness,
    LiveReadinessConfigurationError,
)


class Response:
    def __init__(self, payload):
        self.payload = payload
    def raise_for_status(self):
        return None
    def json(self):
        return self.payload


class BinanceLiveReadinessTests(unittest.TestCase):
    def test_requires_explicit_credentials(self):
        with self.assertRaises(LiveReadinessConfigurationError):
            BinanceLiveReadiness(api_key="", api_secret="", client=Mock())

    def test_account_snapshot_is_read_only_and_summarizes_exposure(self):
        account = {
            "availableBalance": "500",
            "totalWalletBalance": "600",
            "canTrade": True,
            "multiAssetsMargin": False,
            "positions": [{"symbol": "BTCUSDT", "positionAmt": "0.01", "entryPrice": "60000"}],
        }
        positions = [{
            "symbol": "BTCUSDT", "positionAmt": "0.01", "entryPrice": "60000",
            "markPrice": "61000", "unRealizedProfit": "10",
        }]
        client = Mock()
        client.get.side_effect = [Response(account), Response(positions), Response({"dualSidePosition": False})]
        adapter = BinanceLiveReadiness("key", "secret", client)
        result = adapter.account_snapshot()
        self.assertTrue(result["read_only"])
        self.assertFalse(result["order_submission_supported"])
        self.assertEqual(result["open_position_count"], 1)
        self.assertEqual(result["gross_open_notional_usdt"], 610.0)
        self.assertEqual(client.get.call_count, 3)
        self.assertTrue(all(call.args[0].startswith("/fapi/") for call in client.get.call_args_list))
        self.assertFalse(any(call.args[0].endswith("/order") for call in client.get.call_args_list))

    def test_rejects_invalid_position_risk(self):
        account = {"availableBalance": "1", "totalWalletBalance": "1", "positions": []}
        client = Mock()
        client.get.side_effect = [Response(account), Response([{"symbol": "BTCUSDT", "positionAmt": "1", "entryPrice": "2", "markPrice": "0"}])]
        adapter = BinanceLiveReadiness("key", "secret", client)
        with self.assertRaisesRegex(ValueError, "invalid_mark_price"):
            adapter.account_snapshot()

    def test_rejects_nonfinite_account_balances(self):
        for field, value in (("availableBalance", "nan"), ("totalWalletBalance", "inf")):
            with self.subTest(field=field):
                account = {"availableBalance": "1", "totalWalletBalance": "1", "positions": []}
                account[field] = value
                client = Mock()
                client.get.side_effect = [
                    Response(account), Response([]), Response({"dualSidePosition": False})
                ]
                adapter = BinanceLiveReadiness("key", "secret", client)
                with self.assertRaisesRegex(ValueError, "non_finite_account_balance"):
                    adapter.account_snapshot()

    def test_rejects_nonfinite_open_position_metrics(self):
        account = {"availableBalance": "1", "totalWalletBalance": "1", "positions": []}
        for field, value in (("positionAmt", "nan"), ("markPrice", "inf"),
                             ("entryPrice", "nan"), ("unRealizedProfit", "-inf")):
            with self.subTest(field=field):
                client = Mock()
                client.get.side_effect = [
                    Response(account),
                    Response([{"symbol": "BTCUSDT", "positionAmt": "1", "entryPrice": "2",
                               "markPrice": "3", "unRealizedProfit": "0", field: value}]),
                ]
                adapter = BinanceLiveReadiness("key", "secret", client)
                with self.assertRaisesRegex(ValueError, "non_finite_position_risk_item"):
                    adapter.account_snapshot()


if __name__ == "__main__":
    unittest.main()
