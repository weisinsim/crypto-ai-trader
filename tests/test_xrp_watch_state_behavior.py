import unittest

from validation.replay_dashboard_signals import replay


class XRPWatchStateBehaviorTests(unittest.TestCase):
    def test_xrp_watch_long_is_preserved_as_state_but_not_entry_signal(self):
        one = []
        four = []
        start = 1_700_000_000_000

        # A gentle, low-volatility uptrend keeps the kill switch off. The
        # current close remains below the prior rolling high, so the XRP
        # breakout entry condition is false while the trend watch condition
        # remains true.
        for i in range(240):
            close = 100.0 + i * 0.005
            one.append({
                "timestamp": "2023-11-14T00:00:00Z",
                "ts": start + i * 3_600_000,
                "o": close - 0.001,
                "h": close + 0.2,
                "l": close - 0.2,
                "c": close,
                "v": 10.0,
                "q": 1000.0,
            })

        for i in range(70):
            close = 100.0 + i * 0.01
            four.append({
                "timestamp": "2023-11-14T00:00:00Z",
                "ts": start + (i + 1) * 14_400_000 - 1,
                "o": close - 0.001,
                "h": close + 0.2,
                "l": close - 0.2,
                "c": close,
                "v": 40.0,
                "q": 4000.0,
            })

        entry_signals = replay("XRPUSDT", one, four)
        self.assertEqual(entry_signals, [])

        states = replay("XRPUSDT", one, four, include_states=True)
        self.assertTrue(states)
        last = states[-1]
        self.assertEqual(last["signal"], "WATCH-LONG")
        self.assertEqual(last["status"], "WATCH")
        self.assertEqual(last["score"], 43)
        self.assertEqual(last["side"], "")
        self.assertIsNotNone(last["target2"])
        self.assertIsNotNone(last["entry"])
        self.assertIsNotNone(last["stop"])
        self.assertIsNotNone(last["target"])


if __name__ == "__main__":
    unittest.main()
