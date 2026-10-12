"""Evaluate frozen BTC/AVAX candidates on a later, non-overlapping CSV window.

Research-only: this script never searches or mutates parameters and never places orders.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from research.run_backtest import load_csv, load_funding_csv
from research.breakout_retest import run_breakout_retest
from research.mean_reversion import simulate as simulate_mean_reversion

FROZEN_PATH = Path(__file__).resolve().parents[1] / "validation" / "frozen_confirmation_candidates.json"
# Source workflow used data ending at the exclusive UTC boundary below.
SOURCE_DATA_END = datetime(2026, 10, 1, tzinfo=timezone.utc)


def utc_ms(text: str) -> int:
    value = datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(value.timestamp() * 1000)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def summarize(result: dict) -> dict:
    trades = result.get("trade_log", [])
    positive = sum(float(t.get("pnl_equity", 0)) for t in trades if float(t.get("pnl_equity", 0)) > 0)
    gross = sum(float(t.get("pnl_equity", 0)) for t in trades)
    largest = max((float(t.get("pnl_equity", 0)) for t in trades), default=0.0)
    return {
        "trades": result.get("trades", len(trades)),
        "net_return_pct": result.get("net_return_pct"),
        "max_drawdown_pct": result.get("max_drawdown_pct"),
        "win_rate_pct": result.get("win_rate_pct"),
        "profit_factor": result.get("profit_factor"),
        "funding_pnl_equity": round(sum(float(t.get("funding_pnl_equity", 0)) for t in trades), 8),
        "funding_events": sum(int(t.get("funding_events", 0)) for t in trades),
        "positive_pnl_concentration_top_trade": round(largest / positive, 6) if positive > 0 else None,
    }


def confirmation_window_indices(rows: list[dict], start_ms: int, end_ms: int) -> tuple[int, int]:
    """Return exact hourly candle indices for a half-open UTC confirmation window."""
    if not isinstance(start_ms, int) or not isinstance(end_ms, int) or end_ms <= start_ms:
        raise ValueError("invalid confirmation window")
    if not rows:
        raise ValueError("candle data does not cover confirmation start")
    candle_times = [row["t"] for row in rows]
    if len(candle_times) != len(set(candle_times)):
        raise ValueError("duplicate candle timestamps detected")
    if any(right - left != 3_600_000 for left, right in zip(candle_times, candle_times[1:])):
        raise ValueError("candle timestamps contain a missing or misaligned hourly interval")
    start_matches = [i for i, timestamp in enumerate(candle_times) if timestamp >= start_ms]
    end_matches = [i for i, timestamp in enumerate(candle_times) if timestamp >= end_ms]
    if not start_matches:
        raise ValueError("candle data does not cover confirmation start")
    start_idx = start_matches[0]
    end_idx = end_matches[0] if end_matches else len(rows)
    if start_idx >= end_idx or rows[start_idx]["t"] != start_ms:
        raise ValueError("candle data must contain the exact confirmation start hour")
    if end_idx < len(rows) and rows[end_idx]["t"] != end_ms:
        raise ValueError("candle data has no exact exclusive end boundary")
    if rows[end_idx - 1]["t"] != end_ms - 3_600_000:
        raise ValueError("candle data does not contain every closed hour through the confirmation window")
    return start_idx, end_idx

def validate_funding_events(funding: list[dict], symbol: str, start_ms: int, end_ms: int) -> dict:
    """Validate in-window settlement cadence and provenance; return source counts."""
    if not isinstance(start_ms, int) or not isinstance(end_ms, int) or end_ms <= start_ms:
        raise ValueError("funding validation window must satisfy integer start_ms < end_ms")
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("funding validation symbol must be a non-empty string")
    if any(event.get("symbol") != symbol for event in funding):
        raise ValueError("funding symbol does not match requested symbol")
    window = [event for event in funding if start_ms <= int(event["funding_time"]) < end_ms]
    if not window:
        raise ValueError("no funding events inside confirmation window")
    event_times = [int(event["funding_time"]) for event in window]
    if len(event_times) != len(set(event_times)):
        raise ValueError("duplicate funding settlement timestamps detected")
    if any(t % 28_800_000 != 0 for t in event_times):
        raise ValueError("funding settlement timestamp is not aligned to the expected 8-hour UTC schedule")
    sources: dict[str, int] = {}
    for event in window:
        source = event.get("mark_price_source", "unspecified")
        sources[source] = sources.get(source, 0) + 1
    allowed_sources = {
        "mark_kline_open", "mark_price_kline_open",
        "bybit_mark_kline_open", "okx_mark_price_kline_open",
        "binance_fapi_funding_rate_mark_price",
    }
    untrusted_sources = sorted(set(sources) - allowed_sources)
    if untrusted_sources:
        raise ValueError(f"unverified funding mark-price provenance: {untrusted_sources}")
    for event in window:
        source = str(event.get("mark_price_source", "unspecified") or "unspecified")
        venue = str(event.get("venue", "") or "").strip().lower()
        if source == "okx_mark_price_kline_open" and venue != "okx":
            raise ValueError(
                "OKX mark-price provenance requires explicit venue=okx; "
                f"got venue={venue or 'missing'}"
            )
        if source == "binance_fapi_funding_rate_mark_price" and venue != "binance":
            raise ValueError(
                "Binance funding provenance requires explicit venue=binance; "
                f"got venue={venue or 'missing'}"
            )
        if source == "bybit_mark_kline_open" and venue != "bybit":
            raise ValueError(
                "Bybit mark-price provenance requires explicit venue=bybit; "
                f"got venue={venue or 'missing'}"
            )
    return {"records_in_window": len(window), "mark_price_sources": sources}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", required=True, choices=["BTCUSDT", "AVAXUSDT"])
    parser.add_argument("--csv", required=True, help="closed 1h candles, including indicator warm-up bars")
    parser.add_argument("--funding-csv", required=True, help="funding CSV including mark-price provenance")
    parser.add_argument("--start", required=True, help="inclusive UTC date YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="exclusive UTC date YYYY-MM-DD")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    start_ms, end_ms = utc_ms(args.start), utc_ms(args.end)
    if end_ms <= start_ms:
        raise ValueError("--end must be later than --start")
    if datetime.fromtimestamp(start_ms / 1000, timezone.utc) < SOURCE_DATA_END:
        raise ValueError("confirmation start overlaps source data; must start on or after 2026-10-01 UTC")

    frozen = json.loads(FROZEN_PATH.read_text(encoding="utf-8"))
    candidate = frozen["candidates"][args.symbol]
    params = candidate["parameters"]
    params_sha = hashlib.sha256(json.dumps(params, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    frozen_sha = sha256(FROZEN_PATH)

    rows = load_csv(args.csv, 3_600_000)
    candle_times = [row["t"] for row in rows]
    if len(candle_times) != len(set(candle_times)):
        raise ValueError("duplicate candle timestamps detected")
    if any(right - left != 3_600_000 for left, right in zip(candle_times, candle_times[1:])):
        raise ValueError("candle timestamps contain a missing or misaligned hourly interval")
    start_matches = [i for i, timestamp in enumerate(candle_times) if timestamp >= start_ms]
    end_matches = [i for i, timestamp in enumerate(candle_times) if timestamp >= end_ms]
    if not start_matches:
        raise ValueError("candle data does not cover confirmation start")
    start_idx = start_matches[0]
    end_idx = end_matches[0] if end_matches else len(rows)
    if start_idx >= end_idx or rows[start_idx]["t"] != start_ms:
        raise ValueError("candle data must contain the exact confirmation start hour")
    if end_idx < len(rows) and rows[end_idx]["t"] != end_ms:
        raise ValueError("candle data has no exact exclusive end boundary")
    if rows[end_idx - 1]["t"] != end_ms - 3_600_000:
        raise ValueError("candle data does not contain every closed hour through the confirmation window")
    funding = load_funding_csv(args.funding_csv)
    funding_summary = validate_funding_events(funding, args.symbol, start_ms, end_ms)
    funding = [event for event in funding if start_ms <= event["funding_time"] < end_ms]
    sources = funding_summary["mark_price_sources"]

    # Run only the exact frozen parameter set. The same closed-candle window is
    # evaluated at three cost assumptions; no selection occurs on these results.
    results = {}
    for rt_cost_pct in (0.14, 0.30, 0.46):
        # Existing simulators take fee and slippage per side; split the stated
        # round-trip cost evenly between the two.
        per_side_cost = (rt_cost_pct / 100) / 2
        if args.symbol == "BTCUSDT":
            result = run_breakout_retest(
                rows, params, start_idx, end_idx, fee_rate=per_side_cost / 2,
                slippage_rate=per_side_cost / 2, funding_rates=funding, symbol=args.symbol,
            )
            no_funding_result = run_breakout_retest(
                rows, params, start_idx, end_idx, fee_rate=per_side_cost / 2,
                slippage_rate=per_side_cost / 2, funding_rates=[], symbol=args.symbol,
            )
        else:
            result = simulate_mean_reversion(
                rows, params, start_idx, end_idx, fee=per_side_cost / 2,
                slippage=per_side_cost / 2, funding_rates=funding, symbol=args.symbol,
            )
            no_funding_result = simulate_mean_reversion(
                rows, params, start_idx, end_idx, fee=per_side_cost / 2,
                slippage=per_side_cost / 2, funding_rates=[], symbol=args.symbol,
            )
        with_funding = summarize(result)
        without_funding = summarize(no_funding_result)
        with_funding["funding_counterfactual_net_return_pct"] = without_funding["net_return_pct"]
        if isinstance(with_funding["net_return_pct"], (int, float)) and isinstance(without_funding["net_return_pct"], (int, float)):
            with_funding["funding_return_delta_pct_points"] = round(
                with_funding["net_return_pct"] - without_funding["net_return_pct"], 8
            )
        else:
            with_funding["funding_return_delta_pct_points"] = None
        results[f"round_trip_cost_{rt_cost_pct:.2f}pct"] = with_funding

    report = {
        "status": "RESEARCH_ONLY",
        "symbol": args.symbol,
        "strategy": candidate["strategy"],
        "window_utc": {"start_inclusive": args.start, "end_exclusive": args.end},
        "source_data_exclusive_end_utc": SOURCE_DATA_END.date().isoformat(),
        "frozen_parameters": params,
        "frozen_parameters_sha256": params_sha,
        "frozen_config_sha256": frozen_sha,
        "funding": {**funding_summary, "pnl_included": True},
        "results": results,
        "gate": {
            "minimum_trades_30": all(v["trades"] >= 30 for v in results.values()),
            "positive_net_return_base": results["round_trip_cost_0.14pct"]["net_return_pct"] is not None and results["round_trip_cost_0.14pct"]["net_return_pct"] > 0,
            "pf_above_1_15_base": isinstance(results["round_trip_cost_0.14pct"]["profit_factor"], (int, float)) and results["round_trip_cost_0.14pct"]["profit_factor"] > 1.15,
            "nonnegative_net_return_0_30pct": results["round_trip_cost_0.30pct"]["net_return_pct"] is not None and results["round_trip_cost_0.30pct"]["net_return_pct"] >= 0,
            "drawdown_at_most_10pct_base": results["round_trip_cost_0.14pct"]["max_drawdown_pct"] is not None and results["round_trip_cost_0.14pct"]["max_drawdown_pct"] <= 10,
            "production_approval": False,
        },
        "limitations": [
            "Historical confirmation is valid only if the input candles/funding were fetched from the requested window and source provenance is independently verified.",
            "Hourly OHLC does not reveal exact intrabar ordering of funding settlements and exits.",
            "No parameter search is performed; results must not be used to tune this frozen candidate.",
        ],
    }
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(f"status=RESEARCH_ONLY symbol={args.symbol} frozen_config_sha256={frozen_sha}")
    print(f"window={args.start}/{args.end} funding_records={len(funding)}")
    print(f"gate={report['gate']}")
    print(f"output={destination}")


if __name__ == "__main__":
    main()
