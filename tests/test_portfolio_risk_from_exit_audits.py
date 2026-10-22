import json
import tempfile
import unittest
from pathlib import Path

from validation.portfolio_risk_from_exit_audits import combine_audit_trades


class PortfolioRiskAdapterTests(unittest.TestCase):
    def write_report(self, root, symbol, dev, holdout):
        path = root / f"{symbol}.json"
        path.write_text(json.dumps({
            "periods": {
                "development": {"target_2.5R": {
                    "signal_level_trades": dev,
                    "single_position_trades": dev[:1],
                }},
                "holdout": {"target_2.5R": {
                    "signal_level_trades": holdout,
                    "single_position_trades": holdout[:1],
                }},
            }
        }), encoding="utf-8")
        return path

    def test_combines_selected_holdout_trades_with_explicit_risk(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            btc = self.write_report(root, "btc", [], [
                {"timestamp": 10, "exit_timestamp": 30, "net_R": 1.0},
            ])
            eth = self.write_report(root, "eth", [], [
                {"timestamp": 20, "exit_timestamp": 40, "net_R": -1.0},
            ])
            trades = combine_audit_trades(
                [("btcusdt", str(btc)), ("ethusdt", str(eth))],
                "holdout", "target_2.5R", "signal_level", 1.1,
            )
            self.assertEqual([t["symbol"] for t in trades], ["BTCUSDT", "ETHUSDT"])
            self.assertEqual([t["risk_pct"] for t in trades], [1.1, 1.1])
            self.assertEqual(trades[0]["timestamp"], 10)
            self.assertEqual(trades[1]["exit_timestamp"], 40)

    def test_rejects_nonpositive_risk_assumption(self):
        with self.assertRaisesRegex(ValueError, "positive"):
            combine_audit_trades([("BTCUSDT", "unused")],
                                 "holdout", "target_2.5R", "signal_level", 0)

    def test_rejects_missing_trade_timestamps(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            report = self.write_report(root, "btc", [], [{"net_R": 1.0}])
            with self.assertRaisesRegex(ValueError, "lacks entry/exit timestamps"):
                combine_audit_trades([("BTCUSDT", str(report))],
                                     "holdout", "target_2.5R", "signal_level", 0.5)

    def test_rejects_invalid_report_selection(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            report = self.write_report(root, "btc", [], [])
            with self.assertRaisesRegex(ValueError, "period must"):
                combine_audit_trades([("BTCUSDT", str(report))],
                                     "all", "target_2.5R", "signal_level", 0.5)


if __name__ == "__main__":
    unittest.main()
