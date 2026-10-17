import unittest

from validation.signal_confirmation import invalidate_stale_confirmation


class ConfirmedSignalInvalidationTests(unittest.TestCase):
    def test_blocked_opposite_signal_clears_old_active_confirmation(self):
        cache_entry = {
            "confirmed_signal": "LONG",
            "confirmed_status": "ACTIVE",
            "confirmed_entry": 100.0,
            "confirmed_sl": 98.0,
            "confirmed_tp1": 104.0,
            "confirmed_tp2": 106.0,
        }
        history = [{"signal": "LONG", "status": "ACTIVE", "signal_time": 100}]

        previous = invalidate_stale_confirmation("LONG", "SHORT", cache_entry, history, 200)

        self.assertEqual(previous, "NO-TRADE")
        self.assertEqual(cache_entry["confirmed_signal"], "NO-TRADE")
        self.assertEqual(cache_entry["confirmed_signal_time"], 200)
        self.assertEqual(cache_entry["confirmed_status"], "INVALIDATED")
        for key in ("confirmed_entry", "confirmed_sl", "confirmed_tp1", "confirmed_tp2"):
            self.assertIsNone(cache_entry[key])
        self.assertEqual(history[0]["status"], "INVALIDATED")
        self.assertEqual(history[0]["ended_at"], 200)

    def test_same_direction_does_not_invalidate(self):
        cache_entry = {"confirmed_signal": "LONG", "confirmed_status": "ACTIVE"}
        history = [{"status": "ACTIVE"}]

        previous = invalidate_stale_confirmation("LONG", "LONG", cache_entry, history, 200)

        self.assertEqual(previous, "LONG")
        self.assertEqual(cache_entry["confirmed_status"], "ACTIVE")
        self.assertEqual(history[0]["status"], "ACTIVE")


if __name__ == "__main__":
    unittest.main()
