import time
import unittest

from execution.binance_testnet import BinanceFuturesTestnet, TESTNET_BASE_URL


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class ProtectiveExitSnapshotRegressionTests(unittest.TestCase):
    def make_adapter(self):
        class FakeClient:
            def __init__(self):
                self.base_url = TESTNET_BASE_URL
                self.headers = {}
                self.calls = []
                self.get_calls = []

            def get(self, path, params):
                self.get_calls.append((path, params))
                if path == "/fapi/v2/account":
                    return FakeResponse({
                        "canTrade": True,
                        "positions": [
                            {"symbol": "BTCUSDT", "positionAmt": "0.5", "entryPrice": "100"}
                        ],
                    })
                if path == "/fapi/v1/openOrders":
                    return FakeResponse([])
                if path == "/fapi/v1/premiumIndex":
                    return FakeResponse({
                        "symbol": "BTCUSDT",
                        "markPrice": "100",
                        "time": int(time.time() * 1000),
                    })
                if path == "/fapi/v1/exchangeInfo":
                    return FakeResponse({"symbols": [{
                        "symbol": "BTCUSDT",
                        "status": "TRADING",
                        "filters": [
                            {"filterType": "MARKET_LOT_SIZE", "stepSize": "0.001", "minQty": "0.001", "maxQty": "100"},
                            {"filterType": "PRICE_FILTER", "tickSize": "0.1", "minPrice": "0.1", "maxPrice": "1000000"},
                        ],
                    }]})
                raise AssertionError(f"unexpected GET path: {path}")

            def post(self, path, params):
                self.calls.append((path, params))
                raise AssertionError("protective exit POST must not occur in these regression cases")

            def close(self):
                pass

        client = FakeClient()
        adapter = BinanceFuturesTestnet(
            api_key="test-key", api_secret="test-secret", client=client
        )
        return adapter, client

    def submit_with_final_snapshot(self, final_snapshot):
        adapter, client = self.make_adapter()
        original_snapshot = adapter.testnet_position_snapshot
        reads = {"count": 0}

        def snapshot_with_final_override(symbol):
            reads["count"] += 1
            if reads["count"] == 1:
                return original_snapshot(symbol)
            return final_snapshot

        adapter.testnet_position_snapshot = snapshot_with_final_override
        result = adapter.submit_testnet_protective_exit(
            "BTCUSDT", "stop_loss", "0.5", "99.8", "final-snapshot-regression"
        )
        self.assertEqual(reads["count"], 2)
        self.assertFalse(any(path == "/fapi/v1/order" for path, _ in client.calls))
        return result

    def test_invalid_final_snapshot_fails_closed_without_order_post(self):
        result = self.submit_with_final_snapshot({
            "ok": False,
            "reason": "invalid_testnet_open_orders_response",
        })
        self.assertFalse(result["accepted"])
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual(
            result["reason"],
            "protective_exit_final_snapshot_invalid_requires_reconciliation",
        )

    def test_stale_final_snapshot_fails_closed_without_order_post(self):
        stale_ms = int(time.time() * 1000) - 10_000
        result = self.submit_with_final_snapshot({
            "ok": True,
            "symbol": "BTCUSDT",
            "snapshot_started_at_ms": stale_ms,
            "snapshot_completed_at_ms": stale_ms,
            "snapshot_duration_ms": 0,
            "position_amount": "0.5",
            "entry_price": "100",
            "open_orders": [],
        })
        self.assertFalse(result["accepted"])
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual(
            result["reason"],
            "protective_exit_position_snapshot_stale_before_submit_requires_reconciliation",
        )

    def test_inconsistent_final_snapshot_window_fails_closed_without_order_post(self):
        now_ms = int(time.time() * 1000)
        result = self.submit_with_final_snapshot({
            "ok": True,
            "symbol": "BTCUSDT",
            "snapshot_started_at_ms": now_ms - 10,
            "snapshot_completed_at_ms": now_ms,
            "snapshot_duration_ms": 999,
            "position_amount": "0.5",
            "entry_price": "100",
            "open_orders": [],
        })
        self.assertFalse(result["accepted"])
        self.assertTrue(result["requires_reconciliation"])
        self.assertEqual(
            result["reason"],
            "protective_exit_final_snapshot_invalid_requires_reconciliation",
        )


if __name__ == "__main__":
    unittest.main()
