from pathlib import Path
import unittest


class DashboardResearchPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (Path(__file__).resolve().parents[1] / "static" / "index.html").read_text(encoding="utf-8")

    def test_research_panel_is_present_and_linked_to_report(self):
        self.assertIn('aria-label="模型验证状态"', self.html)
        self.assertIn("Historical baseline research #233", self.html)
        self.assertIn("38848049423", self.html)
        self.assertIn("成本压力", self.html)
        self.assertIn("历史数据链路", self.html)
        self.assertIn("五个币种的 walk-forward 候选门槛均未通过", self.html)
        self.assertIn("工作流成功表示计算完成，不表示策略通过盈利门槛", self.html)
        self.assertIn("1,095 条资金费率记录", self.html)

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
        self.assertIn("当前无模型获准实盘", self.html)
        self.assertIn("策略晋级门槛", self.html)
        self.assertIn("未批准实盘", self.html)
        self.assertIn("RESEARCH ONLY", self.html)

    def test_html_document_and_script_are_closed(self):
        self.assertEqual(self.html.count("<script>"), 1)
        self.assertEqual(self.html.count("</script>"), 1)
        self.assertTrue(self.html.strip().endswith("</html>"))


if __name__ == "__main__":
    unittest.main()
