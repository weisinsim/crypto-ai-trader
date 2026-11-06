"""Offline safety tests for the Pionex GET-only futures transport."""
import asyncio
import hashlib
import hmac
import unittest

import httpx

from notifications.pionex_readonly import PionexFuturesReader, PionexReadError


class PionexReaderTests(unittest.TestCase):
    def test_disabled_by_default(self):
        with self.assertRaises(PionexReadError):
            PionexFuturesReader.from_env({"PIONEX_READ_API_KEY": "x", "PIONEX_READ_API_SECRET": "y"})

    def test_no_mutation_methods_or_paths(self):
        self.assertFalse(hasattr(PionexFuturesReader, "place_order"))
        self.assertFalse(hasattr(PionexFuturesReader, "cancel_order"))
        self.assertTrue(all(path.startswith("/uapi/v1/") for path in PionexFuturesReader.ALLOWED_PATHS))
        self.assertTrue(all("/account/" in path or "/trade/" in path for path in PionexFuturesReader.ALLOWED_PATHS))

    def test_authentication_and_position_query(self):
        captured = []

        def handler(request):
            captured.append(request)
            return httpx.Response(200, json={"result": True, "data": {"positions": []}})

        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                reader = PionexFuturesReader("read-key", "read-secret", client=client, clock_ms=lambda: 1700000000000)
                return await reader.positions("BTC_USDT_PERP")

        self.assertEqual(asyncio.run(run()), {"positions": []})
        request = captured[0]
        self.assertEqual(request.method, "GET")
        self.assertEqual(request.url.path, "/uapi/v1/account/positions")
        self.assertEqual(request.url.params["symbol"], "BTC_USDT_PERP")
        self.assertEqual(request.headers["PIONEX-KEY"], "read-key")
        signed = "GET/uapi/v1/account/positions?symbol=BTC_USDT_PERP&timestamp=1700000000000"
        expected = hmac.new(b"read-secret", signed.encode(), hashlib.sha256).hexdigest()
        self.assertEqual(request.headers["PIONEX-SIGNATURE"], expected)
        self.assertNotIn("read-secret", str(request.url))

    def test_rejects_write_paths_and_spot_symbols(self):
        reader = PionexFuturesReader("k", "s")
        with self.assertRaises(PionexReadError):
            asyncio.run(reader.get("/uapi/v1/trade/order", symbol="BTC_USDT_PERP", side="BUY"))
        with self.assertRaises(PionexReadError):
            asyncio.run(reader.fills("BTC_USDT"))

    def test_fails_closed_on_exchange_rejection(self):
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"result": False, "data": {}})
            )) as client:
                reader = PionexFuturesReader("k", "s", client=client)
                return await reader.positions()

        with self.assertRaises(PionexReadError):
            asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
