import math

from research.mean_reversion import indicators, simulate


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


def test_mean_reversion_rejects_invalid_range():
    rows = make_rows()
    try:
        simulate(rows, {"window": 24, "entry_z": 1.5, "stop_atr": 2.0, "rsi_filter": 0}, 10, 10)
    except ValueError as exc:
        assert "evaluation range" in str(exc)
    else:
        raise AssertionError("invalid evaluation range should raise ValueError")
