"""Tests for the read-only SQLite outbox alert feed."""
import asyncio
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import app as dashboard
from execution.alert_store import EntryAlertStore
from execution.live_signal_scanner import scan_once


class ChatGPTOutboxFeedTests(unittest.TestCase):
    def setUp(self):
        self.original_status = dict(dashboard.market_status)
        dashboard.market_status.update(status="LIVE", updated=time.time(), error=None)
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "alerts.sqlite3")
        now = time.time()
        self.event = {
            "event_id": "test-event-001", "eligible": True, "symbol": "BTCUSDT",
            "side": "LONG", "signal_candle_ms": int(now * 1000) - 60_000,
            "analysis_ts": now, "entry": 100.0, "stop_loss": 95.0,
            "take_profit_1": 110.0, "take_profit_2": 115.0, "score": 80,
            "model_status": "RESEARCH", "research_only": True,
            "notification_only": True, "auto_trade_authorized": False,
        }

    def tearDown(self):
        dashboard.market_status.clear()
        dashboard.market_status.update(self.original_status)
        self.tmp.cleanup()

    def test_scanner_to_sqlite_to_chatgpt_feed_end_to_end(self):
        now_ms = int(time.time() * 1000)
        candle_boundary = (now_ms // 3_600_000) * 3_600_000

        class Market:
            def last_closed_candle_ms(self, symbol, interval, *, now_ms=None):
                return candle_boundary

        snapshot = {
            "symbol": "BTCUSDT", "confirmed_signal": "LONG",
            "confirmed_signal_time": candle_boundary, "analysis_ts": now_ms / 1000,
            "confirmed_entry": 100.0, "confirmed_sl": 95.0,
            "confirmed_tp1": 110.0, "confirmed_tp2": 115.0,
            "confirmed_score": 80, "model_status": "RESEARCH",
            "model_name": "test-model", "model_version": "test-v1",
        }
        store = EntryAlertStore(self.db)
        result = scan_once(
            symbols=["BTCUSDT"], interval="1h", market_data=Market(),
            snapshot_provider=lambda _symbol: dict(snapshot), store=store, now_ms=now_ms,
        )
        self.assertEqual(result["queued"], 1)
        with patch.dict(os.environ, {"ALERT_OUTBOX_DB": self.db}):
            payload = asyncio.run(dashboard.chatgpt_outbox_alert_feed())
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["alerts"][0]["side"], "LONG")
        self.assertFalse(payload["alerts"][0]["auto_trade_authorized"])
        second = scan_once(
            symbols=["BTCUSDT"], interval="1h", market_data=Market(),
            snapshot_provider=lambda _symbol: dict(snapshot), store=store, now_ms=now_ms,
        )
        self.assertEqual(second["duplicates"], 1)
        self.assertEqual(len(store.pending()), 1)

    def test_reads_valid_pending_event_without_marking_delivered(self):
        store = EntryAlertStore(self.db)
        self.assertTrue(store.enqueue(self.event, now=time.time()))
        with patch.dict(os.environ, {"ALERT_OUTBOX_DB": self.db}):
            payload = asyncio.run(dashboard.chatgpt_outbox_alert_feed())
        self.assertEqual(payload["status"], "LIVE")
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["alerts"][0]["symbol"], "BTCUSDT")
        self.assertTrue(payload["alerts"][0]["notification_only"])
        self.assertFalse(payload["alerts"][0]["auto_trade_authorized"])
        self.assertEqual(len(EntryAlertStore(self.db).pending()), 1)

    def test_fails_closed_on_non_live_or_stale_heartbeat(self):
        EntryAlertStore(self.db).enqueue(self.event, now=time.time())
        with patch.dict(os.environ, {"ALERT_OUTBOX_DB": self.db}):
            dashboard.market_status["status"] = "DELAYED"
            payload = asyncio.run(dashboard.chatgpt_outbox_alert_feed())
            self.assertEqual(payload["alerts"], [])
            dashboard.market_status.update(status="LIVE", updated=time.time() - 121)
            payload = asyncio.run(dashboard.chatgpt_outbox_alert_feed())
        self.assertEqual(payload["alerts"], [])
        self.assertIn("suppressed", payload["note"])

    def test_rejects_mismatched_event_identity_and_trade_authorization(self):
        store = EntryAlertStore(self.db)
        self.event["event_id"] = "payload-id"
        store.enqueue(self.event, now=time.time())
        # Simulate a corrupt/mismatched payload inside the stored row.
        import sqlite3
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                "UPDATE entry_alert_outbox SET payload_json = replace(payload_json, 'payload-id', 'different-id')"
            )
        with patch.dict(os.environ, {"ALERT_OUTBOX_DB": self.db}):
            payload = asyncio.run(dashboard.chatgpt_outbox_alert_feed())
        self.assertEqual(payload["alerts"], [])

    def test_rejects_unsafe_trade_authorization_flag(self):
        self.event["auto_trade_authorized"] = True
        # Outbox itself refuses an unsafe event, so directly assert no row is created.
        store = EntryAlertStore(self.db)
        self.assertFalse(store.enqueue(self.event, now=time.time()))
        with patch.dict(os.environ, {"ALERT_OUTBOX_DB": self.db}):
            payload = asyncio.run(dashboard.chatgpt_outbox_alert_feed())
        self.assertEqual(payload["alerts"], [])

    def test_invalid_geometry_is_suppressed(self):
        self.event["stop_loss"] = 105.0
        EntryAlertStore(self.db).enqueue(self.event, now=time.time())
        with patch.dict(os.environ, {"ALERT_OUTBOX_DB": self.db}):
            payload = asyncio.run(dashboard.chatgpt_outbox_alert_feed())
        self.assertEqual(payload["alerts"], [])
        self.assertEqual(payload["count"], 0)


if __name__ == "__main__":
    unittest.main()
