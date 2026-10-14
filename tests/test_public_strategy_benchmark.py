import csv
from pathlib import Path

import pytest

from research.public_strategy_benchmark import benchmark, funding_cost, load_candles, load_funding as module_load_funding, rsi, sma, signals


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


@pytest.mark.parametrize(
    "bad_row",
    [
        {"t": 0, "o": 100, "h": 99, "l": 98, "c": 100, "v": 1},
        {"t": 0, "o": 100, "h": 102, "l": 101, "c": 100, "v": 1},
        {"t": 0, "o": 100, "h": 102, "l": 98, "c": 100, "v": -1},
        {"t": -1, "o": 100, "h": 102, "l": 98, "c": 100, "v": 1},
    ],
)
def test_loader_rejects_structurally_invalid_candles(tmp_path: Path, bad_row):
    path = tmp_path / "bad.csv"
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["t", "o", "h", "l", "c", "v"])
        writer.writeheader()
        writer.writerows([bad_row] * 250)
    with pytest.raises(ValueError):
        load_candles(str(path))


def test_loader_rejects_short_dataset(tmp_path: Path):
    path = tmp_path / "short.csv"
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["t", "o", "h", "l", "c", "v"])
        writer.writeheader()
        writer.writerow({"t": 0, "o": 100, "h": 101, "l": 99, "c": 100, "v": 1})
    with pytest.raises(ValueError, match="250"):
        load_candles(str(path))


def test_loader_rejects_missing_hourly_candles(tmp_path: Path):
    path = tmp_path / "gap.csv"
    data = rows(250)
    for row in data[125:]:
        row["t"] += 3_600_000
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["t", "o", "h", "l", "c", "v"])
        writer.writeheader()
        writer.writerows(data)
    with pytest.raises(ValueError, match="contiguous"):
        load_candles(str(path))



@pytest.mark.parametrize(
    "funding_row",
    [
        {"funding_time": "-1", "funding_rate": "0.001", "mark_price": "100"},
        {"funding_time": "0", "funding_rate": "nan", "mark_price": "100"},
        {"funding_time": "0", "funding_rate": "0.001", "mark_price": "0"},
        {"funding_time": "not-a-time", "funding_rate": "0.001", "mark_price": "100"},
    ],
)
def test_funding_loader_rejects_invalid_records(tmp_path: Path, funding_row):
    path = tmp_path / "funding.csv"
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["funding_time", "funding_rate", "mark_price"])
        writer.writeheader()
        writer.writerow(funding_row)
    with pytest.raises(ValueError):
        module_load_funding(str(path))


def test_funding_cost_includes_settlement_at_exit_and_excludes_entry():
    events = [
        {"t": 100, "rate": 0.01, "mark": 100},
        {"t": 200, "rate": 0.01, "mark": 100},
        {"t": 300, "rate": -0.005, "mark": 100},
    ]
    # Entry timestamp 100 is excluded; exit timestamp 300 is included.
    assert funding_cost(events, 100, 300, 100) == pytest.approx(0.005)



def test_holdout_only_profit_does_not_pass_full_research_gate(monkeypatch):
    import research.public_strategy_benchmark as module

    def fake_simulate(rows, strategy, start, end, round_trip_cost, funding_events=None):
        # The final holdout passes every cost, while earlier periods fail.
        if start == int(len(rows) * 0.8):
            return {"trades": 10, "net_return_pct": 5.0, "max_drawdown_pct": 2.0,
                    "win_rate_pct": 60.0, "profit_factor": 1.5, "positive_net": True}
        return {"trades": 10, "net_return_pct": -2.0, "max_drawdown_pct": 5.0,
                "win_rate_pct": 40.0, "profit_factor": 0.8, "positive_net": False}

    monkeypatch.setattr(module, "simulate", fake_simulate)
    report = module.benchmark(rows(), "XRPUSDT")
    for strategy in module.STRATEGIES:
        assert report["results"][strategy]["gate"]["status"] == "FAIL_RESEARCH_GATE"
        assert report["results"][strategy]["gate"]["holdout_only_status"] == "PASS_HOLDOUT_DIAGNOSTIC"
        assert report["results"][strategy]["gate"]["production_approved"] is False


def test_gate_requires_all_costs_and_all_chronological_splits():
    report = benchmark(rows(), "BTCUSDT")
    for strategy in report["results"]:
        gate = report["results"][strategy]["gate"]
        assert gate["status"] in {"PASS_RESEARCH_GATE", "FAIL_RESEARCH_GATE"}
        assert gate["holdout_only_status"] in {"PASS_HOLDOUT_DIAGNOSTIC", "FAIL_HOLDOUT_DIAGNOSTIC"}
        assert gate["production_approved"] is False



def test_simulation_drawdown_includes_adverse_intrabar_low(monkeypatch):
    import research.public_strategy_benchmark as module

    data = rows(210)
    # Force a long entry at index 201 (filled at index 202 open) and later exit.
    data[202].update({"o": 100.0, "h": 112.0, "l": 80.0, "c": 108.0})
    data[203].update({"o": 108.0, "h": 112.0, "l": 105.0, "c": 110.0})
    data[204].update({"o": 110.0, "h": 112.0, "l": 108.0, "c": 111.0})
    enter = [False] * len(data)
    exit_ = [False] * len(data)
    enter[201] = True
    exit_[203] = True
    monkeypatch.setattr(module, "signals", lambda _rows, _strategy: (enter, exit_))

    result = module.simulate(data, "sma200_trend", 201, 210, 0.0)
    assert result["trades"] == 1
    assert result["net_return_pct"] > 0
    assert result["max_drawdown_pct"] >= 19.9
