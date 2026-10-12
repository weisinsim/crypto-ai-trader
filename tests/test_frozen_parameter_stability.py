from research.frozen_parameter_stability import summarize_monthly_trades


def test_monthly_trade_diagnostics_group_by_utc_entry_month_and_exit_reason():
    trades = [
        {"entry_time": 1_790_000_000_000, "pnl_equity": 0.02, "side": "LONG", "reason": "MEAN_TARGET"},
        {"entry_time": 1_790_000_100_000, "pnl_equity": -0.01, "side": "SHORT", "reason": "STOP"},
        {"entry_time": 1_792_700_000_000, "pnl_equity": 0.03, "side": "SHORT", "reason": "TIME"},
    ]

    result = summarize_monthly_trades(trades)

    assert sum(item["trades"] for item in result.values()) == len(trades)
    for month, summary in result.items():
        assert summary["trades"] >= 1
        assert summary["long_trades"] + summary["short_trades"] == summary["trades"]
        assert sum(summary["exit_reasons"].values()) == summary["trades"]
        assert -100 <= summary["net_pnl_equity_pct"] <= 100


def test_monthly_trade_diagnostics_handles_empty_input():
    assert summarize_monthly_trades([]) == {}
