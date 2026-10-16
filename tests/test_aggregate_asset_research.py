import tempfile
import unittest
from pathlib import Path

from validation.aggregate_asset_research import render


class AggregateAssetResearchTests(unittest.TestCase):
    def test_inventories_reports_without_claiming_profitability(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            report_dir = root / "research-BTCUSDT-1h" / "reports"
            report_dir.mkdir(parents=True)
            (report_dir / "BTCUSDT_1h_frozen_stability.json").write_text(
                '{"status":"NOT_RUN_NO_ELIGIBLE_TRAINING_PARAMETERS","reason":"No selected params"}',
                encoding="utf-8",
            )
            (report_dir / "BTCUSDT_1h_baseline.json").write_text(
                '{"trades": 100}', encoding="utf-8"
            )
            report = render(root)
            self.assertIn("BTCUSDT", report)
            self.assertIn("NOT_RUN_NO_ELIGIBLE_TRAINING_PARAMETERS", report)
            self.assertIn("REPORT_PRESENT_STATUS_UNSPECIFIED", report)
            self.assertIn("not evidence of profitability", report)
            self.assertIn("Expected symbols: 5", report)
            self.assertIn("Missing expected symbols: AVAXUSDT, ETHUSDT, SOLUSDT, XRPUSDT", report)
            self.assertIn("Coverage status: INCOMPLETE_REPORT_COVERAGE", report)
            self.assertIn("Missing required reports: AVAXUSDT_1h_baseline.json, AVAXUSDT_funding_audit.json, BTCUSDT_funding_audit.json, ETHUSDT_1h_baseline.json, ETHUSDT_funding_audit.json, SOLUSDT_1h_baseline.json, SOLUSDT_funding_audit.json, XRPUSDT_1h_baseline.json, XRPUSDT_funding_audit.json", report)
            self.assertIn("JSON reports inventoried: 2", report)

    def test_missing_root_is_explicit(self):
        with tempfile.TemporaryDirectory() as temp:
            report = render(Path(temp) / "not-created")
            self.assertIn("Symbols represented: 0 (none)", report)
            self.assertIn("Missing expected symbols: AVAXUSDT, BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT", report)
            self.assertIn("Coverage status: INCOMPLETE_REPORT_COVERAGE", report)
            self.assertIn("Missing required reports:", report)
            self.assertIn("JSON reports inventoried: 0", report)

    def test_complete_required_coverage_is_not_profitability_approval(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            report_dir = root / "reports"
            report_dir.mkdir()
            for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "AVAXUSDT", "XRPUSDT"):
                (report_dir / f"{symbol}_1h_baseline.json").write_text("{}", encoding="utf-8")
                (report_dir / f"{symbol}_funding_audit.json").write_text('{"status":"PASS"}', encoding="utf-8")
            report = render(root)
            self.assertIn("Coverage status: PASS_COVERAGE_ONLY", report)
            self.assertIn("not evidence of profitability", report)

    def test_invalid_required_json_prevents_coverage_pass(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            report_dir = root / "reports"
            report_dir.mkdir()
            for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "AVAXUSDT", "XRPUSDT"):
                (report_dir / f"{symbol}_1h_baseline.json").write_text("{}", encoding="utf-8")
                (report_dir / f"{symbol}_funding_audit.json").write_text('{"status":"PASS"}', encoding="utf-8")
            (report_dir / "BTCUSDT_funding_audit.json").write_text("{invalid", encoding="utf-8")
            report = render(root)
            self.assertIn("Coverage status: INCOMPLETE_REPORT_COVERAGE", report)
            self.assertIn("Invalid required reports: BTCUSDT_funding_audit.json", report)

    def test_failing_funding_audit_prevents_coverage_pass(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            report_dir = root / "reports"
            report_dir.mkdir()
            for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "AVAXUSDT", "XRPUSDT"):
                (report_dir / f"{symbol}_1h_baseline.json").write_text("{}", encoding="utf-8")
                (report_dir / f"{symbol}_funding_audit.json").write_text('{"status":"PASS"}', encoding="utf-8")
            (report_dir / "AVAXUSDT_funding_audit.json").write_text(
                '{"status":"FAIL","reason":"coverage gaps"}', encoding="utf-8"
            )
            report = render(root)
            self.assertIn("Coverage status: INCOMPLETE_REPORT_COVERAGE", report)
            self.assertIn("Funding audits not PASS: AVAXUSDT_funding_audit.json", report)

    def test_empty_root_is_explicit(self):
        with tempfile.TemporaryDirectory() as temp:
            report = render(Path(temp))
            self.assertIn("Symbols represented: 0 (none)", report)
            self.assertIn("JSON reports inventoried: 0", report)


if __name__ == "__main__":
    unittest.main()
