import pytest

from research.mean_reversion import funding_cashflow


def event(ts, rate, mark=100.0):
    return {
        "funding_time": ts,
        "funding_rate": rate,
        "mark_price": mark,
        "symbol": "ETHUSDT",
        "mark_price_source": "mark_kline_open",
    }


def test_long_funding_pnl_excludes_entry_and_includes_exit_settlement():
    events = [event(100, 0.01), event(200, 0.02), event(300, -0.005)]
    pnl, count = funding_cashflow(1, 2.0, 100, 300, events)
    # Long pays positive funding and receives negative funding: -4 + 1 = -3.
    assert pnl == pytest.approx(-3.0)
    assert count == 2


def test_short_funding_pnl_is_opposite_of_long():
    events = [event(200, 0.02), event(300, -0.005)]
    long_pnl, long_count = funding_cashflow(1, 2.0, 100, 300, events)
    short_pnl, short_count = funding_cashflow(-1, 2.0, 100, 300, events)
    assert short_pnl == pytest.approx(-long_pnl)
    assert short_count == long_count == 2


@pytest.mark.parametrize(
    "side,quantity,entry_ts,exit_ts",
    [(0, 1.0, 100, 300), (1, -1.0, 100, 300), (1, 1.0, 300, 100)],
)
def test_funding_cashflow_rejects_invalid_trade_inputs(side, quantity, entry_ts, exit_ts):
    with pytest.raises(ValueError, match="invalid side"):
        funding_cashflow(side, quantity, entry_ts, exit_ts, [event(200, 0.01)])


def test_funding_cashflow_rejects_invalid_settlement_price():
    with pytest.raises(ValueError, match="invalid mark price"):
        funding_cashflow(1, 1.0, 100, 300, [event(200, 0.01, 0)])
