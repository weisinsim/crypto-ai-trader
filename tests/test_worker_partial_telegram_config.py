"""Regression tests for partial Telegram worker configuration."""
import json
import unittest
from unittest.mock import patch

from execution import live_signal_worker


class StopAfterEmit:
    def is_set(self):
        return False

    def wait(self, seconds):
        return True


class FakeStore:
    _memory_conn = None

    def pending(self):
        return []


class PartialTelegramConfigurationTests(unittest.TestCase):
    def test_partial_credentials_degrade_without_attempting_delivery(self):
        configurations = (
            {"TELEGRAM_BOT_TOKEN": "test-token", "TELEGRAM_CHAT_ID": ""},
            {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": "test-chat"},
        )
        for configured in configurations:
            with self.subTest(configured=tuple(configured)):
                emitted = []
                with patch.dict("os.environ", configured, clear=True):
                    with patch.object(live_signal_worker, "scan_once", return_value={
                        "scanned": 1, "queued": 0, "duplicates": 0,
                        "rejected": 0, "errors": [],
                    }):
                        with patch.object(live_signal_worker, "deliver_pending") as deliver:
                            result = live_signal_worker.run_worker(
                                base_url="https://example.invalid",
                                db_path=":memory:",
                                symbols=["BTCUSDT"],
                                poll_seconds=5,
                                stop_event=StopAfterEmit(),
                                snapshot_fetcher=lambda _url: {
                                    "BTCUSDT": {"symbol": "BTCUSDT"}
                                },
                                market_data=object(),
                                store_factory=lambda _path: FakeStore(),
                                emit=lambda line: emitted.append(json.loads(line)),
                            )
                deliver.assert_not_called()
                self.assertEqual(result["status"], "DEGRADED")
                self.assertEqual(result["reason"], "telegram_delivery_degraded")
                self.assertEqual(result["telegram"]["reason"],
                                 "telegram_credentials_incomplete")
                self.assertEqual(emitted[0]["status"], "DEGRADED")


if __name__ == "__main__":
    unittest.main()
