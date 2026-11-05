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

    def test_latest_report_coverage_gates_are_documented(self):
        self.assertIn('aria-label="最新报告覆盖与资金费率审计门槛"', self.html)
        self.assertIn("PR #21", self.html)
        self.assertIn("PR #22", self.html)
        self.assertIn("JSON 格式错误", self.html)
        self.assertIn("资金费率审计失败", self.html)
        self.assertIn("PASS_COVERAGE_ONLY", self.html)
        self.assertIn("不声称最新五币种研究产物已经完整", self.html)

    def test_unvalidated_directional_signals_keep_watch_badges(self):
        self.assertIn('function signalBadge(x)', self.html)
        self.assertIn('x.confirmed_status==="ACTIVE" && validated ? s', self.html)
        self.assertIn('x.signal==="LONG"?"WATCH-LONG":"WATCH-SHORT"', self.html)

    def test_waiting_validation_is_never_presented_as_confirmed_manual_review(self):
        self.assertIn('st==="WAITING_VALIDATION" || !validated', self.html)
        self.assertIn('等待独立验证', self.html)
        self.assertIn('验证通过 · 仅供人工评估', self.html)
        self.assertNotIn('st!=="WAITING") return {cls:"review"', self.html)

    def test_candidates_are_not_misrepresented_as_approved(self):
        self.assertIn("当前无模型获准实盘", self.html)
        self.assertIn("策略晋级门槛", self.html)
        self.assertIn("未批准实盘", self.html)
        self.assertIn("RESEARCH ONLY", self.html)


    def test_confirmed_directional_signals_are_manual_review_only(self):
        self.assertIn('text:s+" · 仅供人工评估"', self.html)
        self.assertIn('信号状态（非执行）', self.html)
        self.assertIn('不代表获准交易或可提交订单', self.html)
        self.assertIn('模型标记PRODUCTION · 仍需人工评估', self.html)
        self.assertNotIn('text:s+" · 可执行"', self.html)
        self.assertNotIn('只有「交易执行状态」显示可执行时才考虑入场', self.html)

    def test_html_document_and_script_are_closed(self):
        opening_scripts = self.html.count("<script>") + self.html.count("<script ")
        self.assertEqual(opening_scripts, self.html.count("</script>"))
        self.assertIn('<script src="/static/alert_contract.js"></script>', self.html)
        self.assertTrue(self.html.strip().endswith("</html>"))


if __name__ == "__main__":
    unittest.main()
