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


if __name__ == "__main__":
    unittest.main()
