import ast
import unittest
from pathlib import Path


class AppClosedCandleGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = Path(__file__).resolve().parents[1] / "app.py"
        cls.tree = ast.parse(source.read_text(encoding="utf-8"))
        cls.functions = {
            node.name: node for node in cls.tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

    def test_analyze_symbol_uses_closed_series_for_both_intervals(self):
        fn = self.functions["analyze_symbol"]
        calls = [
            node for node in ast.walk(fn)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "closed_series_rows"
        ]
        intervals = {
            node.args[1].value for node in calls
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant)
        }
        self.assertEqual(intervals, {"1h", "4h"})

    def test_closed_series_helper_uses_shared_filter(self):
        fn = self.functions["closed_series_rows"]
        calls = [
            node for node in ast.walk(fn)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "filter_closed_candles"
        ]
        self.assertEqual(len(calls), 1)

    def test_runtime_adapters_keep_exchange_open_time_timestamps(self):
        source = Path(__file__).resolve().parents[1] / "app.py"
        text = source.read_text(encoding="utf-8")
        self.assertIn('"t": normalize_timestamp_ms(x[0])', text)
        self.assertIn('"t":int(k["t"])', text)
        self.assertIn('"t":int(k["start"])', text)

    def test_bybit_fallback_validates_both_snapshot_series(self):
        fn = self.functions["seed_symbol"]
        validated_assignments = []
        for node in ast.walk(fn):
            if not isinstance(node, ast.Assign):
                continue
            if not any(isinstance(target, ast.Subscript) for target in node.targets):
                continue
            value = node.value
            if (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Name)
                and value.func.id == "validate_candle_series"
            ):
                validated_assignments.append(value)
        self.assertGreaterEqual(len(validated_assignments), 2)

    def test_binance_series_preserves_taker_flow_and_analyzer_uses_cvd_proxy(self):
        set_series_fn = self.functions["set_series"]
        calls = [
            node for node in ast.walk(set_series_fn)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "kline_to_obj"
        ]
        self.assertTrue(any(
            any(keyword.arg == "include_taker_flow" and isinstance(keyword.value, ast.Constant)
                and keyword.value.value is True for keyword in call.keywords)
            for call in calls
        ))
        analyze = self.functions["analyze_symbol"]
        self.assertTrue(any(
            isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "cvd_proxy_features"
            for node in ast.walk(analyze)
        ))


if __name__ == "__main__":
    unittest.main()
