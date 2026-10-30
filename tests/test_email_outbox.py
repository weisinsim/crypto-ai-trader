import tempfile
import unittest
from pathlib import Path

from validation.email_outbox import EmailOutbox


class EmailOutboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "outbox.sqlite3"
        self.outbox = EmailOutbox(self.path, max_attempts=3, lease_seconds=10,
                                  retry_base_seconds=5, retry_max_seconds=20)

    def tearDown(self):
        self.tmp.cleanup()

    def event(self, event_id="BTCUSDT:123:ENTRY"):
        return {"event_id": event_id, "event": "ENTRY_CONFIRMED",
                "symbol": "BTCUSDT", "direction": "LONG", "entry": 65000}

    def test_enqueue_is_idempotent_for_duplicate_event_id(self):
        self.assertTrue(self.outbox.enqueue(self.event(), now=100))
        self.assertFalse(self.outbox.enqueue(self.event(), now=101))
        rows = self.outbox.list_recent()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "PENDING")

    def test_event_id_is_required(self):
        with self.assertRaises(ValueError):
            self.outbox.enqueue({"event": "ENTRY_CONFIRMED"}, now=100)

    def test_claim_increments_attempt_and_sets_lease(self):
        self.outbox.enqueue(self.event(), now=100)
        claimed = self.outbox.claim_due(now=100)
        self.assertEqual(len(claimed), 1)
        self.assertEqual(claimed[0]["attempt"], 1)
        self.assertTrue(claimed[0]["lease_token"])
        self.assertEqual(self.outbox.list_recent()[0]["status"], "SENDING")

    def test_future_retry_is_not_claimed_early(self):
        self.outbox.enqueue(self.event(), now=100)
        claim = self.outbox.claim_due(now=100)[0]
        self.assertTrue(self.outbox.mark_failed(claim["event_id"], claim["lease_token"],
                                                "temporary SMTP error", now=100))
        self.assertEqual(self.outbox.claim_due(now=104), [])
        self.assertEqual(len(self.outbox.claim_due(now=105)), 1)

    def test_retry_backoff_is_bounded_and_terminal_failure_is_recorded(self):
        self.outbox.enqueue(self.event(), now=100)
        for attempt, now in enumerate((100, 105, 115), start=1):
            claim = self.outbox.claim_due(now=now)[0]
            self.assertEqual(claim["attempt"], attempt)
            self.assertTrue(self.outbox.mark_failed(claim["event_id"], claim["lease_token"],
                                                    "SMTP unavailable", now=now))
        row = self.outbox.list_recent()[0]
        self.assertEqual(row["status"], "FAILED")
        self.assertEqual(row["attempts"], 3)
        self.assertEqual(row["last_error"], "SMTP unavailable")
        self.assertEqual(self.outbox.claim_due(now=1000), [])

    def test_expired_lease_can_be_reclaimed_but_old_token_cannot_complete(self):
        self.outbox.enqueue(self.event(), now=100)
        first = self.outbox.claim_due(now=100)[0]
        second = self.outbox.claim_due(now=111)[0]
        self.assertNotEqual(first["lease_token"], second["lease_token"])
        self.assertFalse(self.outbox.mark_sent(first["event_id"], first["lease_token"], now=112))
        self.assertTrue(self.outbox.mark_sent(second["event_id"], second["lease_token"], now=112))
        self.assertEqual(self.outbox.list_recent()[0]["status"], "SENT")

    def test_expired_lease_after_final_attempt_becomes_failed(self):
        self.outbox.enqueue(self.event(), now=100)
        claim = self.outbox.claim_due(now=100)[0]
        self.assertTrue(self.outbox.mark_failed(claim["event_id"], claim["lease_token"],
                                                "retry", now=100))
        claim = self.outbox.claim_due(now=105)[0]
        self.assertTrue(self.outbox.mark_failed(claim["event_id"], claim["lease_token"],
                                                "retry", now=105))
        claim = self.outbox.claim_due(now=115)[0]
        self.assertEqual(claim["attempt"], 3)
        self.assertEqual(self.outbox.claim_due(now=126), [])
        self.assertEqual(self.outbox.list_recent()[0]["status"], "FAILED")

    def test_sent_event_is_not_claimed_again(self):
        self.outbox.enqueue(self.event(), now=100)
        claim = self.outbox.claim_due(now=100)[0]
        self.assertTrue(self.outbox.mark_sent(claim["event_id"], claim["lease_token"], now=101))
        self.assertEqual(self.outbox.claim_due(now=1000), [])

    def test_invalid_event_json_values_are_rejected(self):
        with self.assertRaises(ValueError):
            self.outbox.enqueue({**self.event(), "bad": float("nan")}, now=100)

    def test_history_does_not_expose_event_payload_secrets(self):
        self.outbox.enqueue({**self.event(), "smtp_password": "never-store-this"}, now=100)
        row = self.outbox.list_recent()[0]
        self.assertNotIn("smtp_password", row)
        self.assertEqual(row["event"], "ENTRY_CONFIRMED")


if __name__ == "__main__":
    unittest.main()
