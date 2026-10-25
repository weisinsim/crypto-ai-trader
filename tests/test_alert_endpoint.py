import importlib.util
import unittest
from unittest.mock import patch

# The lightweight research CI job installs pytest/httpx only. Run this
# integration test when the application's runtime dependencies are present.
if importlib.util.find_spec("fastapi") is None or importlib.util.find_spec("websockets") is None:
    raise unittest.SkipTest("FastAPI/websockets runtime dependencies are not installed in this test job")

from fastapi.testclient import TestClient
import app as app_module


class ChatGPTAlertEndpointTests(unittest.TestCase):
    def test_http_endpoint_preserves_read_only_contract(self):
        expected = {
            "generated_at": 1_800_000_000.0,
            "status": "DELAYED",
            "alerts": [],
            "notification_only": True,
            "auto_trade_authorized": False,
        }
        with patch.object(app_module.time, "time", return_value=1_800_000_000.0), patch.object(
            app_module, "build_alert_payload", return_value=dict(expected)
        ) as builder:
            # Do not enter TestClient as a context manager: startup handlers
            # launch market-data tasks, which are outside this endpoint test.
            response = TestClient(app_module.app).get("/api/chatgpt/alerts")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        builder.assert_called_once_with(
            app_module.market_status,
            app_module.cache,
            app_module.SYMBOLS,
            1_800_000_000.0,
        )
        self.assertEqual(payload["model_version"], app_module.MODEL_VERSION)
        self.assertEqual(payload["status"], "DELAYED")
        self.assertEqual(payload["alerts"], [])
        self.assertIs(payload["notification_only"], True)
        self.assertIs(payload["auto_trade_authorized"], False)

    def test_http_endpoint_fails_closed_with_invalid_market_status(self):
        with patch.object(app_module.time, "time", return_value=1_800_000_000.0), patch.object(
            app_module, "market_status", "UNKNOWN"
        ), patch.object(app_module, "cache", {}):
            # Exercise the real validator through the HTTP route, not a mocked builder.
            response = TestClient(app_module.app).get("/api/chatgpt/alerts")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "DELAYED")
        self.assertEqual(payload["alerts"], [])
        self.assertIs(payload["notification_only"], True)
        self.assertIs(payload["auto_trade_authorized"], False)
        self.assertEqual(payload["model_version"], app_module.MODEL_VERSION)


    def test_http_endpoint_fails_closed_with_malformed_market_status_types(self):
        # JSON-like malformed values must not raise a TypeError in the live route.
        for market_status in ([], {}):
            with self.subTest(market_status=market_status):
                with patch.object(
                    app_module.time, "time", return_value=1_800_000_000.0
                ), patch.object(app_module, "market_status", market_status), patch.object(
                    app_module, "cache", {"BTCUSDT": {}}
                ):
                    response = TestClient(app_module.app).get("/api/chatgpt/alerts")

                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertEqual(payload["status"], "DELAYED")
                self.assertEqual(payload["alerts"], [])
                self.assertIs(payload["notification_only"], True)
                self.assertIs(payload["auto_trade_authorized"], False)


if __name__ == "__main__":
    unittest.main()
