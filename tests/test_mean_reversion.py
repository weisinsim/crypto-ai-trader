import math

from research.mean_reversion import GRID, cost_robustness_gate, ema_series, funding_cashflow, indicators, simulate


def make_rows(n=240):
    rows = []
    for i in range(n):
        close = 100.0 + 4.0 * math.sin(i / 5.0)
        rows.append({
            "t": i * 3_600_000,
            "o": close - 0.2 * math.cos(i / 3.0),
            "h": close + 0.8,
            "l": close - 0.8,
            "c": close,
            "v": 1.0,
            "q": close,
        })
    return rows


def test_rolling_mean_uses_only_prior_closes():
    rows = make_rows(10)
    means, stds, _ = indicators(rows, 3)
    assert means[3] == sum(row["c"] for row in rows[:3]) / 3
    assert stds[3] is not None and stds[3] >= 0


def test_mean_reversion_simulation_returns_valid_metrics():
    rows = make_rows()
    result = simulate(
        rows,
        {"window": 24, "entry_z": 1.5, "stop_atr": 2.0, "rsi_filter": 0},
        100,
        len(rows),
    )
    assert result["trades"] >= 0
    assert math.isfinite(result["net_return_pct"])
    assert 0 <= result["max_drawdown_pct"] <= 100
    assert result["long_trades"] + result["short_trades"] == result["trades"]



def test_adverse_funding_increases_intratrade_drawdown():
    rows = make_rows()
    params = {"window": 24, "entry_z": 1.5, "stop_atr": 2.0, "rsi_filter": 0}
    base = simulate(rows, params, 100, len(rows))
    held = next((trade for trade in base["trade_log"]
                 if trade["exit_time"] - trade["entry_time"] >= 3_600_000), None)
    assert held is not None
    event_ts = held["entry_time"] + 3_600_000
    adverse_rate = 0.05 if held["side"] == "LONG" else -0.05
    event = {"funding_time": event_ts, "funding_rate": adverse_rate,
             "mark_price": 100.0, "symbol": "BTCUSDT"}
    funded = simulate(rows, params, 100, len(rows),
                      funding_rates=[event], symbol="BTCUSDT")
    assert funded["max_drawdown_pct"] > base["max_drawdown_pct"]




def test_mean_reversion_rejects_duplicate_funding_timestamps():
    rows = make_rows()
    event = {"funding_time": rows[150]["t"], "funding_rate": 0.001,
             "mark_price": 100.0, "symbol": "BTCUSDT"}
    try:
        simulate(
            rows,
            {"window": 24, "entry_z": 1.5, "stop_atr": 2.0, "rsi_filter": 0},
            100, len(rows), funding_rates=[event, dict(event)], symbol="BTCUSDT",
        )
    except ValueError as exc:
        assert "duplicate funding timestamp" in str(exc)
    else:
        raise AssertionError("duplicate funding timestamp should raise ValueError")


def test_mean_reversion_rejects_negative_funding_timestamp():
    rows = make_rows()
    event = {"funding_time": -1, "funding_rate": 0.001,
             "mark_price": 100.0, "symbol": "BTCUSDT"}
    try:
        simulate(
            rows,
            {"window": 24, "entry_z": 1.5, "stop_atr": 2.0, "rsi_filter": 0},
            100, len(rows), funding_rates=[event], symbol="BTCUSDT",
        )
    except ValueError as exc:
        assert "funding timestamps must be non-negative" in str(exc)
    else:
        raise AssertionError("negative funding timestamp should raise ValueError")


def test_mean_reversion_rejects_invalid_range():
    rows = make_rows()
    try:
        simulate(rows, {"window": 24, "entry_z": 1.5, "stop_atr": 2.0, "rsi_filter": 0}, 10, 10)
    except ValueError as exc:
        assert "evaluation range" in str(exc)
    else:
        raise AssertionError("invalid evaluation range should raise ValueError")



def test_funding_cashflow_long_pays_and_short_receives_positive_funding():
    events = [
        {"funding_time": 200, "funding_rate": 0.001, "mark_price": 100.0, "symbol": "BTCUSDT"},
    ]
    long_pnl, long_count = funding_cashflow(1, 2.0, 100, 300, events)
    short_pnl, short_count = funding_cashflow(-1, 2.0, 100, 300, events)
    assert long_pnl == -0.2
    assert short_pnl == 0.2
    assert long_count == short_count == 1


def test_funding_cashflow_excludes_entry_timestamp_and_after_exit():
    events = [
        {"funding_time": 100, "funding_rate": 0.01, "mark_price": 100.0},
        {"funding_time": 200, "funding_rate": 0.01, "mark_price": 100.0},
        {"funding_time": 301, "funding_rate": 0.01, "mark_price": 100.0},
    ]
    pnl, count = funding_cashflow(1, 1.0, 100, 300, events)
    assert pnl == -1.0
    assert count == 1


def test_funding_cashflow_rejects_invalid_mark_price():
    events = [{"funding_time": 200, "funding_rate": 0.01, "mark_price": 0.0}]
    try:
        funding_cashflow(1, 1.0, 100, 300, events)
    except ValueError as exc:
        assert "invalid mark price" in str(exc)
    else:
        raise AssertionError("invalid mark price should raise ValueError")


def test_cost_robustness_gate_rejects_candidate_failing_severe_costs():
    holdout = {"net_return_pct": 14.7, "profit_factor": 1.37}
    stress = {"net_return_pct": 5.5, "profit_factor": 1.12}
    severe = {"net_return_pct": -2.89, "profit_factor": 0.94}
    assert cost_robustness_gate(holdout, stress, severe)["status"] == "FAIL_COST_ROBUSTNESS"


def test_cost_robustness_gate_requires_all_cost_levels_to_pass():
    metrics = {"net_return_pct": 1.0, "profit_factor": 1.1}
    assert cost_robustness_gate(metrics, metrics, metrics)["status"] == "PASS_PRELIMINARY_ONLY"
    assert cost_robustness_gate(metrics, None, metrics)["status"] == "NOT_EVALUATED"


def test_cost_robustness_gate_rejects_non_finite_metrics():
    import math
    metrics = {"net_return_pct": math.nan, "profit_factor": 1.1}
    good = {"net_return_pct": 1.0, "profit_factor": 1.1}
    assert cost_robustness_gate(metrics, good, good)["status"] == "FAIL_COST_ROBUSTNESS"



def test_mean_reversion_grid_compares_mean_and_fixed_r_targets():
    assert len(GRID) == 864
    modes = {item["target_mode"] for item in GRID}
    assert modes == {"MEAN", "R_MULTIPLE"}
    assert {item["trend_filter"] for item in GRID} == {0, 100, 200}
    assert {item["target_r"] for item in GRID if item["target_mode"] == "R_MULTIPLE"} == {1.0, 1.5, 2.0}


def test_mean_reversion_accepts_fixed_r_target():
    rows = make_rows()
    result = simulate(
        rows,
        {"window": 24, "entry_z": 1.5, "stop_atr": 2.0, "rsi_filter": 0,
         "direction": "BOTH", "target_mode": "R_MULTIPLE", "target_r": 1.5},
        100,
        len(rows),
    )
    assert result["trades"] >= 0
    assert math.isfinite(result["net_return_pct"])


def test_mean_reversion_rejects_invalid_fixed_r_target():
    rows = make_rows()
    try:
        simulate(
            rows,
            {"window": 24, "entry_z": 1.5, "stop_atr": 2.0, "rsi_filter": 0,
             "target_mode": "R_MULTIPLE", "target_r": 0},
            100,
            len(rows),
        )
    except ValueError as exc:
        assert "positive finite target_r" in str(exc)
    else:
        raise AssertionError("invalid fixed-R target should raise ValueError")



def test_ema_series_is_causal_and_respects_warmup():
    rows = make_rows(220)
    ema = ema_series(rows, 100)
    assert ema[98] is None
    assert ema[99] is not None
    changed_future = [dict(row) for row in rows]
    changed_future[150]["c"] += 1000
    ema_changed = ema_series(changed_future, 100)
    assert ema_changed[149] == ema[149]


def test_mean_reversion_rejects_invalid_trend_filter():
    rows = make_rows()
    try:
        simulate(
            rows,
            {"window": 24, "entry_z": 1.5, "stop_atr": 2.0, "rsi_filter": 0,
             "trend_filter": 50},
            100,
            len(rows),
        )
    except ValueError as exc:
        assert "trend_filter" in str(exc)
    else:
        raise AssertionError("invalid trend filter should raise ValueError")
