from pathlib import Path
import unittest


class DashboardResearchPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (Path(__file__).resolve().parents[1] / "static" / "index.html").read_text(encoding="utf-8")

    def test_research_panel_is_present_and_linked_to_report(self):
        self.assertIn('aria-label="模型验证状态"', self.html)
        self.assertIn("RESEARCH_AUDIT_2026-10-13.md", self.html)
        self.assertIn("0.30% / 0.46% 成本压力测试", self.html)
        self.assertIn("Validation CI", self.html)
        self.assertIn("15 个策略均为 FAIL_RESEARCH_GATE", self.html)
        self.assertIn("Holdout 单独通过仅作为诊断", self.html)
        self.assertIn("38486102719", self.html)

    def test_all_five_assets_are_represented(self):
        table_start = self.html.index("<table class=\"researchTable\"><thead>")
        table_end = self.html.index("</tbody>", table_start)
        table = self.html[table_start:table_end]
        for symbol in ("BTC", "ETH", "SOL", "AVAX", "XRP"):
            self.assertIn(f"<td>{symbol}</td>", table)

    def test_waiting_signals_keep_directional_watch_badges(self):
        self.assertIn('x.signal==="LONG"?"WATCH-LONG":"WATCH-SHORT"', self.html)
        self.assertEqual(self.html.count('x.signal==="LONG"?"WATCH-LONG":"WATCH-SHORT"'), 2)

    def test_candidates_are_not_misrepresented_as_approved(self):
        self.assertIn("目前没有任何币种通过全部成本情景", self.html)
        self.assertIn("当前没有候选通过稳健盈利门槛", self.html)
        self.assertIn("未批准实盘", self.html)
        self.assertIn("CI 通过只代表软件或流水线完成，不代表策略盈利", self.html)

    def test_html_document_and_script_are_closed(self):
        self.assertEqual(self.html.count("<script>"), 1)
        self.assertEqual(self.html.count("</script>"), 1)
        self.assertTrue(self.html.strip().endswith("</html>"))


if __name__ == "__main__":
    unittest.main()
