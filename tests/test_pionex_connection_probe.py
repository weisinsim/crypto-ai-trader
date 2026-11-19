"""Offline safety tests for the redacted Pionex connection probe."""
import asyncio
import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from notifications.pionex_connection_probe import probe_positions, live_probe_authorized, main


class FakeReader:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = 0

    async def positions(self):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


class ProbeTests(unittest.TestCase):
    def run_probe(self, reader):
        return asyncio.run(probe_positions(reader))

    def test_live_probe_requires_two_exact_opt_ins(self):
        self.assertFalse(live_probe_authorized({}))
        self.assertFalse(live_probe_authorized({"PIONEX_READ_ONLY_ENABLED": "true"}))
        self.assertFalse(live_probe_authorized({"PIONEX_LIVE_PROBE_APPROVED": "true"}))
        self.assertFalse(live_probe_authorized({
            "PIONEX_READ_ONLY_ENABLED": "TRUE",
            "PIONEX_LIVE_PROBE_APPROVED": "true",
        }))
        self.assertTrue(live_probe_authorized({
            "PIONEX_READ_ONLY_ENABLED": "true",
            "PIONEX_LIVE_PROBE_APPROVED": "true",
        }))

    def test_main_refuses_live_requests_without_separate_approval(self):
        with patch.dict("os.environ", {
            "PIONEX_READ_ONLY_ENABLED": "true",
            "PIONEX_READ_API_KEY": "PRIVATE_KEY",
            "PIONEX_READ_API_SECRET": "PRIVATE_SECRET",
        }, clear=True):
            with patch("notifications.pionex_connection_probe.PionexFuturesReader.from_env",
                       side_effect=AssertionError("must not construct live reader")):
                output = io.StringIO()
                with redirect_stdout(output):
                    exit_code = asyncio.run(main())
        self.assertEqual(exit_code, 2)
        self.assertEqual(json.loads(output.getvalue())["reason"], "live_probe_not_approved")
        self.assertNotIn("PRIVATE", output.getvalue())

    def test_approved_main_success_still_does_not_claim_live_reconciliation(self):
        reader = FakeReader({"positions": [{"symbol": "BTC_USDT_PERP", "size": "123"}]})
        env = {"PIONEX_READ_ONLY_ENABLED": "true", "PIONEX_LIVE_PROBE_APPROVED": "true"}
        with patch.dict("os.environ", env, clear=True):
            with patch("notifications.pionex_connection_probe.PionexFuturesReader.from_env", return_value=reader):
                output = io.StringIO()
                with redirect_stdout(output):
                    exit_code = asyncio.run(main())
        report = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertTrue(report["ok"])
        self.assertFalse(report["schema_verified"])
        self.assertFalse(report["fills_verified"])
        self.assertFalse(report["notifications_verified"])
        self.assertNotIn("BTC_USDT_PERP", output.getvalue())
        self.assertEqual(reader.calls, 1)

    def test_approved_main_failure_redacts_exchange_error(self):
        reader = FakeReader(error=RuntimeError("PIONEX_READ_API_SECRET=PRIVATE"))
        env = {"PIONEX_READ_ONLY_ENABLED": "true", "PIONEX_LIVE_PROBE_APPROVED": "true"}
        with patch.dict("os.environ", env, clear=True):
            with patch("notifications.pionex_connection_probe.PionexFuturesReader.from_env", return_value=reader):
                output = io.StringIO()
                with redirect_stdout(output):
                    exit_code = asyncio.run(main())
        report = json.loads(output.getvalue())
        self.assertEqual(exit_code, 1)
        self.assertEqual(report["reason"], "read_or_validation_failed")
        self.assertNotIn("PRIVATE", output.getvalue())
        self.assertEqual(reader.calls, 1)

    def test_dict_success_does_not_leak_positions(self):
        reader = FakeReader({"positions": [{"symbol": "BTC_USDT_PERP", "quantity": "9", "secret": "SENSITIVE"}]})
        result = self.run_probe(reader)
        self.assertTrue(result["ok"])
        self.assertEqual(result["response_shape"], "object")
        self.assertFalse(result["schema_verified"])
        self.assertFalse(result["fills_verified"])
        self.assertFalse(result["notifications_verified"])
        self.assertNotIn("SENSITIVE", str(result))
        self.assertNotIn("BTC_USDT_PERP", str(result))
        self.assertEqual(reader.calls, 1)

    def test_list_success_does_not_leak_data(self):
        result = self.run_probe(FakeReader([{"account": "PRIVATE"}]))
        self.assertTrue(result["ok"])
        self.assertEqual(result["response_shape"], "array")
        self.assertNotIn("PRIVATE", str(result))

    def test_failure_redacts_exception_and_credentials(self):
        result = self.run_probe(FakeReader(error=RuntimeError("API_SECRET=PRIVATE")))
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "read_or_validation_failed")
        self.assertNotIn("PRIVATE", str(result))

    def test_invalid_shape_fails_closed(self):
        for payload in (None, False, "unexpected", 42):
            with self.subTest(payload=payload):
                result = self.run_probe(FakeReader(payload))
                self.assertFalse(result["ok"])
                self.assertEqual(result["reason"], "unexpected_data_shape")


if __name__ == "__main__":
    unittest.main()
