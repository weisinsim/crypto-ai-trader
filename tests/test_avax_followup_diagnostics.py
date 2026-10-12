from research.avax_followup_diagnostics import concentration, metrics, trade_attribution


def trade(pnl, *, side="SHORT", reason="STOP", entry=1_790_000_000_000, exit=1_790_007_200_000):
    return {"pnl_equity": pnl, "side": side, "reason": reason, "entry_time": entry,
            "exit_time": exit, "price_pnl_equity": pnl + 0.001,
            "trading_cost_equity": 0.001, "funding_pnl_equity": 0.0}


def test_metrics_handles_empty_and_mixed_trade_sets():
    assert metrics([])["trades"] == 0
    result = metrics([trade(0.1), trade(-0.05), trade(0.02)])
    assert result["trades"] == 3
    assert result["profit_factor"] == 2.4
    assert result["win_rate_pct"] == 66.67


def test_concentration_reports_top_winner_shares():
    result = concentration([trade(0.5), trade(0.3), trade(-0.2), trade(0.2)])
    shares = result["positive_pnl_trade_concentration"]
    assert shares["top_1_share_pct"] == 50.0
    assert shares["top_3_share_pct"] == 100.0
    assert shares["top_5_share_pct"] == 100.0


def test_remove_top_winner_diagnostic_is_explicitly_additive():
    result = concentration([trade(0.5), trade(0.3), trade(-0.2), trade(0.2)])
    after = result["after_removing_top_1_winning_trades_additive_only"]
    assert after["trades"] == 3
    assert after["net_trade_pnl_equity_pct"] == 30.0


def test_trade_attribution_groups_month_side_and_exit_reason():
    trades = [trade(-0.02, side="SHORT", reason="STOP"),
              trade(0.03, side="LONG", reason="MEAN_TARGET")]
    result = trade_attribution(trades)
    assert result["side:SHORT"]["trades"] == 1
    assert result["side:SHORT"]["stop_count"] == 1
    assert result["exit_reason:MEAN_TARGET"]["trades"] == 1
    assert result["side:SHORT"]["mean_hold_hours"] == 2.0
    assert any(key.startswith("month:") for key in result)
    assert result["side:SHORT"]["funding_pnl_equity_pct_additive"] == 0.0


def test_trade_attribution_handles_empty_input():
    assert trade_attribution([]) == {}



def test_trade_attribution_components_reconcile_with_group_pnl():
    trades = [
        trade(0.02, side="LONG", reason="MEAN_TARGET"),
        trade(-0.01, side="SHORT", reason="STOP"),
    ]
    result = trade_attribution(trades)
    side_groups = [value for key, value in result.items() if key.startswith("side:")]
    assert sum(item["trades"] for item in side_groups) == len(trades)
    assert round(sum(item["net_trade_pnl_equity_pct_additive"] for item in side_groups), 4) == 1.0
    assert round(sum(item["price_pnl_equity_pct_additive"] for item in side_groups), 4) == 3.0
    assert round(sum(item["trading_cost_equity_pct_additive"] for item in side_groups), 4) == 0.2


def test_trade_attribution_uses_utc_entry_month():
    # 2026-10-01 00:30 UTC; grouping is explicitly UTC, not runner-local time.
    entry = 1_790_814_600_000
    result = trade_attribution([trade(0.01, entry=entry, exit=entry + 3_600_000)])
    assert result["month:2026-10"]["trades"] == 1
