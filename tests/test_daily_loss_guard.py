import json
import tempfile
import unittest
from pathlib import Path

from validation.daily_loss_guard import DailyLossGuard


class DailyLossGuardTests(unittest.TestCase):
    def test_locks_at_limit_and_stays_locked_after_recovery(self):
        guard = DailyLossGuard(100)
        first = guard.record_realized_pnl("trade-1", -60, "2026-10-14")
        self.assertTrue(first["allow_new_entries"])
        locked = guard.record_realized_pnl("trade-2", -40, "2026-10-14")
        self.assertTrue(locked["locked"])
        self.assertFalse(locked["allow_new_entries"])
        recovered = guard.record_realized_pnl("trade-3", 75, "2026-10-14")
        self.assertEqual(recovered["realized_pnl"], -25)
        self.assertTrue(recovered["locked"])
        self.assertFalse(recovered["allow_new_entries"])

    def test_lock_is_scoped_to_utc_day(self):
        guard = DailyLossGuard(50)
        guard.record_realized_pnl("loss", -50, "2026-10-14")
        next_day = guard.status("2026-10-15")
        self.assertFalse(next_day["locked"])
        self.assertTrue(next_day["allow_new_entries"])

    def test_duplicate_event_is_idempotent_and_conflicts_rejected(self):
        guard = DailyLossGuard(100)
        first = guard.record_realized_pnl("fill-1", -25, "2026-10-14")
        second = guard.record_realized_pnl("fill-1", -25, "2026-10-14")
        self.assertEqual(first, second)
        self.assertEqual(second["realized_pnl"], -25)
        with self.assertRaisesRegex(ValueError, "different pnl"):
            guard.record_realized_pnl("fill-1", -30, "2026-10-14")

    def test_persistent_lock_survives_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "daily-loss.json"
            guard = DailyLossGuard(75, path)
            guard.record_realized_pnl("loss-1", -45, "2026-10-14")
            guard.record_realized_pnl("loss-2", -30, "2026-10-14")
            restarted = DailyLossGuard(75, path)
            status = restarted.status("2026-10-14")
            self.assertTrue(status["locked"])
            self.assertFalse(status["allow_new_entries"])
            self.assertEqual(status["event_count"], 2)

    def test_corrupt_or_unknown_schema_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.json"
            path.write_text("{bad", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unreadable"):
                DailyLossGuard(100, path)
            path.write_text(json.dumps({"schema_version": 99, "days": {}}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "schema"):
                DailyLossGuard(100, path)

    def test_invalid_inputs_are_rejected(self):
        with self.assertRaises(ValueError):
            DailyLossGuard(0)
        guard = DailyLossGuard(100)
        for date in ("14-10-2026", "2026-02-30", ""):
            with self.subTest(date=date), self.assertRaises(ValueError):
                guard.status(date)
        with self.assertRaises(ValueError):
            guard.record_realized_pnl("", -1, "2026-10-14")
        with self.assertRaises(ValueError):
            guard.record_realized_pnl("nan", float("nan"), "2026-10-14")


    def test_oversized_values_and_overflowing_pnl_total_are_rejected(self):
        guard = DailyLossGuard(100)
        with self.assertRaisesRegex(ValueError, "finite"):
            DailyLossGuard(10 ** 1000)
        with self.assertRaisesRegex(ValueError, "finite"):
            guard.record_realized_pnl("huge", 10 ** 1000, "2026-10-14")
        guard.record_realized_pnl("large-1", 1e308, "2026-10-14")
        with self.assertRaisesRegex(ValueError, "total must remain finite"):
            guard.record_realized_pnl("large-2", 1e308, "2026-10-14")
        self.assertEqual(guard.status("2026-10-14")["realized_pnl"], 1e308)


if __name__ == "__main__":
    unittest.main()
