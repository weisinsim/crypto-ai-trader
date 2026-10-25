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

    def test_waiting_signals_keep_directional_watch_badges(self):
        self.assertIn('x.signal==="LONG"?"WATCH-LONG":"WATCH-SHORT"', self.html)
        self.assertEqual(self.html.count('x.signal==="LONG"?"WATCH-LONG":"WATCH-SHORT"'), 2)

    def test_candidates_are_not_misrepresented_as_approved(self):
        self.assertIn("当前无模型获准实盘", self.html)
        self.assertIn("策略晋级门槛", self.html)
        self.assertIn("未批准实盘", self.html)
        self.assertIn("RESEARCH ONLY", self.html)

    def test_dashboard_alert_panel_shows_basic_levels_and_safe_status(self):
        self.assertIn('fetch("/api/chatgpt/alerts"', self.html)
        self.assertIn('a.entry??"—"', self.html)
        self.assertIn('a.stop_loss??a.sl??"—"', self.html)
        self.assertIn('a.take_profit_1??a.tp1??"—"', self.html)
        self.assertIn('a.take_profit_2??a.tp2??"—"', self.html)
        self.assertIn("研究候选：未批准实盘", self.html)
        self.assertIn("自动交易：", self.html)
        self.assertIn("信号K线：", self.html)
        self.assertIn('a.auto_trade_authorized===false', self.html)

    def test_browser_alert_filter_rejects_invalid_prices_geometry_and_stale_candles(self):
        self.assertIn("const nowMs=Date.now()", self.html)
        self.assertIn("const validPrice=v=>typeof v===\"number\"&&Number.isFinite(v)&&v>0", self.html)
        self.assertIn('a.side==="LONG"&&!(stop<entry&&entry<tp1&&tp1<=tp2)', self.html)
        self.assertIn('a.side==="SHORT"&&!(tp2<=tp1&&tp1<entry&&entry<stop)', self.html)
        self.assertIn("nowMs-candleMs>90*60*1000", self.html)
        self.assertIn("candleMs>nowMs", self.html)
        self.assertIn("a.notification_only!==true", self.html)
        self.assertIn("a.auto_trade_authorized!==false", self.html)

    def test_dashboard_header_exposes_deployment_revision_state(self):
        self.assertIn('id="buildInfo"', self.html)
        self.assertIn('async function refreshBuildInfo()', self.html)
        self.assertIn('fetch("/api/build-info"', self.html)
        self.assertIn('部署版本：未知（服务未提供 Git commit）', self.html)
        self.assertIn('部署版本：无法核对（接口不可用或尚未部署）', self.html)
        self.assertIn('setInterval(refreshBuildInfo,60000)', self.html)

    def test_html_document_and_script_are_closed(self):
        self.assertEqual(self.html.count("<script>"), 1)
        self.assertEqual(self.html.count("</script>"), 1)
        self.assertTrue(self.html.strip().endswith("</html>"))


if __name__ == "__main__":
    unittest.main()
