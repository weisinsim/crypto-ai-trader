from research.avax_followup_diagnostics import concentration, metrics


def trade(pnl):
    return {"pnl_equity": pnl}


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
