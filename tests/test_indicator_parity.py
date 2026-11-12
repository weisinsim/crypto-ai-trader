import ast
import unittest
from pathlib import Path

from validation import replay_dashboard_signals as replay


def load_indicator_functions():
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    tree = ast.parse(app_path.read_text(encoding="utf-8"))
    names = {"ema", "rsi", "atr", "adx", "vwap", "sr_levels"}
    nodes = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    module = ast.Module(body=nodes, type_ignores=[])
    namespace = {}
    exec(compile(ast.fix_missing_locations(module), str(app_path), "exec"), namespace)
    return namespace


class IndicatorParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.live = load_indicator_functions()

    def setUp(self):
        close = [100 + i * 0.15 + ((i % 7) - 3) * 0.4 for i in range(260)]
        self.close = close
        self.high = [x + 0.7 + (i % 3) * 0.1 for i, x in enumerate(close)]
        self.low = [x - 0.6 - (i % 4) * 0.1 for i, x in enumerate(close)]
        self.volume = [1000 + i * 3 + (i % 11) * 17 for i in range(260)]

    def assert_close_or_both_none(self, left, right, places=10):
        if left is None or right is None:
            self.assertIsNone(left)
            self.assertIsNone(right)
        else:
            self.assertAlmostEqual(left, right, places=places)

    def test_ema_matches_live(self):
        self.assert_close_or_both_none(replay.ema(self.close, 20), self.live["ema"](self.close, 20))

    def test_rsi_matches_live(self):
        self.assert_close_or_both_none(replay.rsi(self.close), self.live["rsi"](self.close))

    def test_atr_matches_live(self):
        self.assert_close_or_both_none(replay.atr(self.high, self.low, self.close), self.live["atr"](self.high, self.low, self.close))

    def test_adx_matches_live(self):
        self.assert_close_or_both_none(replay.adx(self.high, self.low, self.close), self.live["adx"](self.high, self.low, self.close))

    def test_vwap_matches_live(self):
        self.assert_close_or_both_none(replay.vwap(self.high, self.low, self.close, self.volume), self.live["vwap"](self.high, self.low, self.close, self.volume))

    def test_support_resistance_matches_live(self):
        replay_support, replay_resistance = replay.levels(self.high, self.low, self.close)
        live_support, live_resistance = self.live["sr_levels"](self.high, self.low, self.close)
        self.assert_close_or_both_none(replay_support, live_support)
        self.assert_close_or_both_none(replay_resistance, live_resistance)

    def test_precomputed_ema_and_rsi_match_each_prefix(self):
        ema_values = replay.ema_series(self.close, 20)
        rsi_values = replay.rsi_series(self.close)
        for end in range(1, len(self.close) + 1):
            expected_ema = replay.ema(self.close[:end], 20)
            self.assert_close_or_both_none(ema_values[end - 1], expected_ema)
            expected_rsi = replay.rsi(self.close[:end])
            self.assert_close_or_both_none(rsi_values[end - 1], expected_rsi)

    def test_four_hour_candle_must_be_closed_at_hourly_decision(self):
        # Input timestamps are Binance candle close times (close is inclusive at :59.999).
        # At 11:00, 4h candles closing at 04:00 and 08:00 are usable; the 12:00 close is not.
        decision_close = 11 * 3_600_000
        four = [
            {"ts": 4 * 3_600_000 - 1},
            {"ts": 8 * 3_600_000 - 1},
            {"ts": 12 * 3_600_000 - 1},
        ]
        count = replay.closed_four_count_at(four, decision_close)
        expected = replay.closed_rows_at(four, decision_close)
        self.assertEqual(four[:count], expected)
        self.assertEqual([row["ts"] for row in expected], [4 * 3_600_000 - 1, 8 * 3_600_000 - 1])

    def test_incremental_four_hour_cursor_matches_filter_at_boundaries(self):
        # Exercise just-before, exact-close, and just-after boundaries for a
        # monotonically advancing cursor, comparing it with the simple filter.
        four = [{"ts": i * 14_400_000} for i in range(12)]
        decision_times = [
            14_400_000 - 1,
            14_400_000,
            14_400_000 + 1,
            4 * 14_400_000 - 1,
            4 * 14_400_000,
            7 * 14_400_000,
            12 * 14_400_000,
        ]
        cursor = 0
        for decision_close in decision_times:
            cursor = replay.closed_four_count_at(four, decision_close, cursor)
            expected = replay.closed_rows_at(four, decision_close, interval_ms=14_400_000)
            self.assertEqual(four[:cursor], expected)

    def test_replay_vwap_uses_quote_volume_series(self):
        replay_path = Path(__file__).resolve().parents[1] / "validation" / "replay_dashboard_signals.py"
        tree = ast.parse(replay_path.read_text(encoding="utf-8"))
        replay_fn = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "replay")
        calls = [node for node in ast.walk(replay_fn) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Name) and node.func.id == "vwap"]
        self.assertEqual(len(calls), 1)
        self.assertEqual([arg.id for arg in calls[0].args if isinstance(arg, ast.Name)], ["h48", "l48", "c48", "q"])


if __name__ == "__main__":
    unittest.main()
