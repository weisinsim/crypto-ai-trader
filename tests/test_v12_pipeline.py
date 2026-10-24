import tempfile
import unittest
from unittest.mock import patch

from research.run_v12_research import run_pipeline


def make_candles(count, interval_ms):
    rows = []
    for i in range(count):
        close = 100.0 + i * 0.1
        rows.append({
            "t": i * interval_ms, "o": close - 0.05, "h": close + 0.2,
            "l": close - 0.2, "c": close, "v": 100.0 + i,
            "q": (100.0 + i) * close,
        })
    return rows


class V12PipelineTests(unittest.TestCase):
    def test_pipeline_writes_data_and_validation_report_without_live_trading(self):
        one_hour = make_candles(300, 3_600_000)
        four_hour = make_candles(80, 14_400_000)
        funding = [{"funding_time": 0, "funding_rate": 0.0001,
                    "mark_price": 100.0, "symbol": "BTCUSDT",
                    "mark_price_source": "test_fixture", "venue": "binance_usdm"}]
        with tempfile.TemporaryDirectory() as tmp:
            with patch("research.run_v12_research.download_klines",
                       side_effect=lambda symbol, interval, start, end: one_hour if interval == "1h" else four_hour), \
                 patch("research.run_v12_research.download_funding_history", return_value=funding):
                result = run_pipeline("BTCUSDT", "2026-01-01", "2026-03-01", tmp, folds=2)
            self.assertEqual(result["status"], "RESEARCH_ONLY")
            self.assertFalse(result["live_trading_enabled"])
            self.assertTrue(result["report"].endswith("_v12_validation.json"))
            with open(result["report"], encoding="utf-8") as handle:
                import json
                report = json.load(handle)
            self.assertEqual(report["data_lineage"]["venue"], "Binance USD-M Futures")
            self.assertGreater(report["data_lineage"]["feature_rows"], 0)

    def test_pipeline_rejects_non_aligned_date_range(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                run_pipeline("BTCUSDT", "2026-01-01", "2026-01-02", tmp)


if __name__ == "__main__":
    unittest.main()
