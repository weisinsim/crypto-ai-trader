from pathlib import Path
import unittest


class DashboardResearchPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (Path(__file__).resolve().parents[1] / "static" / "index.html").read_text(encoding="utf-8")

    def test_research_panel_is_present_and_linked_to_report(self):
        self.assertIn("模型验证面板", self.html)
        self.assertIn("38159007954", self.html)
        self.assertIn("成本 0.30%", self.html)
        self.assertIn("成本 0.46%", self.html)

    def test_all_five_assets_are_represented(self):
        table_start = self.html.index('<tbody><tr><td>BTC</td>')
        table_end = self.html.index("</tbody>", table_start)
        table = self.html[table_start:table_end]
        for symbol in ("BTC", "ETH", "SOL", "AVAX", "XRP"):
            self.assertIn(f"<td>{symbol}</td>", table)

    def test_candidates_are_not_misrepresented_as_approved(self):
        self.assertIn("10/10 个候选组合未通过全部成本稳健性门槛", self.html)
        self.assertIn("不构成实盘授权", self.html)
        self.assertIn("CI 通过不等于策略盈利", self.html)

    def test_html_document_and_script_are_closed(self):
        self.assertEqual(self.html.count("<script>"), 1)
        self.assertEqual(self.html.count("</script>"), 1)
        self.assertTrue(self.html.strip().endswith("</html>"))


if __name__ == "__main__":
    unittest.main()
