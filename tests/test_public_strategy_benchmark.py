import csv
from pathlib import Path

import pytest

from research.public_strategy_benchmark import benchmark, funding_cost, load_candles, rsi, sma, signals


def rows(n=500):
    out = []
    price = 100.0
    for i in range(n):
        # Deterministic alternating drift gives indicators and crossovers some structure.
        price *= 1.002 if (i // 25) % 2 == 0 else 0.998
        out.append({"t": i*3_600_000, "o": price*0.999, "h": price*1.01,
                    "l": price*0.99, "c": price, "v": 100.0})
    return out


def test_sma_warmup_and_values():
    assert sma([1, 2, 3, 4], 3) == [None, None, 2.0, 3.0]


def test_rsi_warmup_and_bounds():
    values = rsi([float(i+1) for i in range(30)], 14)
    assert all(v is None for v in values[:14])
    assert values[14] == 100
    assert all(v is None or 0 <= v <= 100 for v in values)


@pytest.mark.parametrize("strategy", ["rsi_mean_reversion", "sma200_trend", "donchian_breakout"])
def test_strategy_signals_have_expected_length(strategy):
    enter, exit_ = signals(rows(), strategy)
    assert len(enter) == len(rows())
    assert len(exit_) == len(rows())
    assert all(isinstance(x, bool) for x in enter + exit_)


def test_benchmark_uses_chronological_holdout_and_no_funding_claim():
    report = benchmark(rows(), "BTCUSDT")
    assert report["split"]["holdout_start_index"] == int(500 * 0.8)
    assert report["funding_included"] is False
    assert set(report["results"]) == {"rsi_mean_reversion", "sma200_trend", "donchian_breakout"}


def test_loader_rejects_short_dataset(tmp_path: Path):
    path = tmp_path / "short.csv"
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["t", "o", "h", "l", "c", "v"])
        writer.writeheader()
        writer.writerow({"t": 0, "o": 100, "h": 101, "l": 99, "c": 100, "v": 1})
    with pytest.raises(ValueError, match="250"):
        load_candles(str(path))



def test_funding_cost_includes_settlement_at_exit_and_excludes_entry():
    events = [
        {"t": 100, "rate": 0.01, "mark": 100},
        {"t": 200, "rate": 0.01, "mark": 100},
        {"t": 300, "rate": -0.005, "mark": 100},
    ]
    # Entry timestamp 100 is excluded; exit timestamp 300 is included.
    assert funding_cost(events, 100, 300, 100) == pytest.approx(0.005)
