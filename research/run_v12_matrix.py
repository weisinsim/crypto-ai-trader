"""Run a five-coin V12 research matrix with train-only CVD selection and a fixed forward holdout.

This workflow downloads each symbol once, compares CVD ON/OFF on development walk-forward
folds for LONG and SHORT independently, then evaluates only the selected frozen arm on the
reserved forward period. It is research-only and never submits orders.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from research.audit_funding_data import audit_funding_against_candles
from research.binance_data import download_klines, save_csv as save_candles, utc_ms
from research.binance_funding_data import download_funding_history, save_csv as save_funding
from research.run_backtest import load_funding_csv
from research.v12_features import build_v12_features, write_feature_csv
from research.v12_validation import run_v12_validation

DEFAULT_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "AVAXUSDT", "XRPUSDT")
DIRECTIONS = ("LONG", "SHORT")
CVD_MODES = ("ON", "OFF")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _finite_metric(value) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, TypeError, ValueError):
        return False


def _funding_audit_allows_research(audit: dict) -> bool:
    """Allow only explicitly sourced, gap-free timestamp alignment for research."""
    if not isinstance(audit, dict) or audit.get("status") not in {
        "TIMESTAMP_ALIGNMENT_PASS_NOT_RECONCILED",
        "BOUNDARY_JITTER_NORMALIZED_ALIGNMENT_PASS_NOT_RECONCILED",
    }:
        return False
    funding = audit.get("funding")
    if not isinstance(funding, dict):
        return False
    allowed_sources = {
        "binance_funding_rate_endpoint_markPrice",
        "binance_archive_prior_closed_mark_price_1h_proxy_NOT_SETTLEMENT_MARK",
        "binance_archive_prior_closed_regular_price_1h_proxy_NOT_SETTLEMENT_MARK",
    }
    allowed_venues = {"binance_usdm", "binance_usdm_archive"}
    sources = funding.get("source_labels")
    venues = funding.get("venue_labels")
    return (
        audit.get("candle_timestamps_strictly_increasing") is True
        and audit.get("duplicate_candle_records") == 0
        and audit.get("duplicate_event_records") == 0
        and audit.get("intervals_exceeding_gap_threshold") == 0
        and funding.get("records_outside_candle_range") == 0
        and funding.get("nonmatching_in_range") == 0
        and funding.get("records_missing_or_invalid_source_label") == 0
        and funding.get("records_missing_or_invalid_venue_label") == 0
        and isinstance(sources, list) and bool(sources)
        and all(source in allowed_sources for source in sources)
        and isinstance(venues, list) and bool(venues)
        and all(venue in allowed_venues for venue in venues)
    )


def _development_score(report: dict) -> tuple:
    """Return a train/WF-only selection tuple; never inspect final forward metrics here."""
    folds = report.get("walk_forward_folds", [])
    fold_metrics = [item.get("test_metrics", {}) for item in folds]
    fold_metrics_complete = all(
        isinstance(item, dict) and _finite_metric(item.get("net_return_pct"))
        for item in fold_metrics
    )
    positive = sum(
        _finite_metric(item.get("net_return_pct"))
        and item["net_return_pct"] > 0 for item in fold_metrics
    )
    aggregate = report.get("walk_forward_aggregate", {})
    trades = aggregate.get("trades", 0)
    pf = aggregate.get("profit_factor")
    net_return = aggregate.get("net_return_pct")
    drawdown = aggregate.get("max_drawdown_pct")
    finite_pf = _finite_metric(pf)
    finite_return = _finite_metric(net_return)
    finite_drawdown = _finite_metric(drawdown)
    valid_trades = type(trades) is int and trades >= 20
    eligible = (
        len(folds) >= 3
        and fold_metrics_complete
        and positive >= (len(folds) // 2 + 1)
        and valid_trades
        and finite_pf and pf > 1
        and finite_return and net_return > 0
        and finite_drawdown and 0 <= drawdown <= 15
    )
    # Tuple order is deliberate: only development WF statistics are used.
    score = (
        int(eligible), positive,
        float(net_return) if finite_return else -1e9,
        float(pf) if finite_pf else -1e9,
        -float(drawdown) if finite_drawdown else -1e9,
        trades if type(trades) is int else -1,
    )
    return score


def _run_arm(rows, symbol, direction, cvd_mode, funding, *, folds, fee_rate, slippage_rate):
    return run_v12_validation(
        rows, symbol, holdout_fraction=0.2, folds=folds,
        fee_rate=fee_rate, slippage_rate=slippage_rate,
        funding_events=funding, direction_mode=direction, cvd_mode=cvd_mode,
    )


def run_matrix(*, start_date: str, end_date: str, holdout_start: str,
               output_dir: str, symbols=DEFAULT_SYMBOLS, folds: int = 4,
               fee_rate: float = 0.0005, slippage_rate: float = 0.0002) -> dict:
    start_ms, end_ms, holdout_ms = utc_ms(start_date), utc_ms(end_date), utc_ms(holdout_start)
    if end_ms <= start_ms or holdout_ms <= start_ms or holdout_ms >= end_ms:
        raise ValueError("require start_date < holdout_start < end_date")
    if start_ms % 14_400_000 or end_ms % 14_400_000 or holdout_ms % 3_600_000:
        raise ValueError("start/end must align to 4h UTC boundaries and holdout to 1h")
    if len(set(symbols)) != len(symbols) or not symbols:
        raise ValueError("symbols must be a non-empty unique sequence")

    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    matrix = {
        "status": "RESEARCH_ONLY",
        "profitability_claim_eligible": False,
        "strategy_approval": False,
        "live_trading_enabled": False,
        "methodology": {
            "start_utc_inclusive": start_date,
            "end_utc_exclusive": end_date,
            "forward_holdout_start_utc_inclusive": holdout_start,
            "development_cvd_selection": "CVD mode selected only from pre-holdout walk-forward folds",
            "forward_holdout_policy": "fixed timestamp; never used for CVD/parameter/direction selection",
            "directions": list(DIRECTIONS),
            "cvd_modes": list(CVD_MODES),
            "exact_stress_cost": "0.30% round-trip = 0.05% fee + 0.10% slippage per side",
            "archive_policy": "Current-month daily archives may lag; missing candles fail closed and must not be interpolated or mixed across venues",
        },
        "symbols": {},
    }

    for raw_symbol in symbols:
        symbol = str(raw_symbol).strip().upper()
        symbol_dir = root / symbol
        symbol_dir.mkdir(parents=True, exist_ok=True)
        prefix = f"{symbol}_{start_date}_{end_date}"
        candle_1h = download_klines(symbol, "1h", start_ms, end_ms, include_taker_flow=True)
        candle_4h = download_klines(symbol, "4h", start_ms, end_ms)
        funding = download_funding_history(symbol, start_ms, end_ms)

        p1 = symbol_dir / f"{prefix}_1h.csv"
        p4 = symbol_dir / f"{prefix}_4h.csv"
        pf = symbol_dir / f"{prefix}_funding.csv"
        px = symbol_dir / f"{prefix}_features.csv"
        save_candles(candle_1h, p1)
        save_candles(candle_4h, p4)
        save_funding(funding, pf)
        # Re-load through the canonical funding parser so raw exchange timestamps
        # are preserved for audit while sub-second 8h-boundary jitter is normalized
        # only under the shared, explicit tolerance policy used by backtests.
        funding = load_funding_csv(str(pf))
        features = build_v12_features(candle_1h, candle_4h, funding, symbol=symbol)
        write_feature_csv(px, features)

        flow_rows = sum(1 for row in candle_1h if "tb" in row and "tq" in row)
        cvd_rows = sum(1 for row in features if row.get("cvd_ratio_24") is not None)
        flow_coverage = flow_rows / len(candle_1h) if candle_1h else 0.0
        cvd_coverage = cvd_rows / len(features) if features else 0.0
        funding_audit = audit_funding_against_candles(candle_1h, funding, symbol, max_gap_hours=12)

        dev_rows = [row for row in features if row["t"] < holdout_ms]
        if not dev_rows or not features or features[0]["t"] > holdout_ms:
            raise ValueError(f"{symbol}: insufficient feature coverage around holdout boundary")
        if not any(row["t"] == holdout_ms for row in features):
            raise ValueError(f"{symbol}: holdout_start must exactly match a generated feature timestamp")
        symbol_result = {
            "status": "RESEARCH_ONLY",
            "data_lineage": {
                "venue": "Binance USD-M Futures",
                "data_acquisition": "Binance API with monthly archive and daily archive fallback for incomplete current month",
                "symbol": symbol,
                "candle_1h_rows": len(candle_1h),
                "candle_4h_rows": len(candle_4h),
                "funding_events": len(funding),
                "feature_rows": len(features),
                "taker_flow_1h_coverage_pct": round(flow_coverage * 100, 4),
                "cvd_24h_feature_coverage_pct": round(cvd_coverage * 100, 4),
                "funding_audit": funding_audit,
                "funding_cashflows_reconciled": False,
                "files": {
                    "candles_1h": {"path": str(p1), "sha256": _sha256(p1)},
                    "candles_4h": {"path": str(p4), "sha256": _sha256(p4)},
                    "funding": {"path": str(pf), "sha256": _sha256(pf)},
                    "features": {"path": str(px), "sha256": _sha256(px)},
                },
                "cvd_source": "Binance 1h kline taker-buy base volume; 12h/24h aggressor-delta proxy, not tick-level CVD",
            },
            "development": {},
            "forward_confirmation": {},
        }

        for direction in DIRECTIONS:
            if not _funding_audit_allows_research(funding_audit):
                symbol_result["development"][direction] = {
                    "status": "DATA_QUALITY_BLOCKED",
                    "reason": "Funding timestamp audit is REVIEW_REQUIRED or has an unknown status; no backtest arm may consume these events",
                }
                symbol_result["forward_confirmation"][direction] = {
                    "status": "NOT_RUN",
                    "reason": "Funding data quality must be resolved before development selection or forward confirmation",
                }
                continue
            arms = {}
            for cvd_mode in CVD_MODES:
                if cvd_mode == "ON" and (flow_coverage < 0.999999 or cvd_coverage < 0.999999):
                    arms[cvd_mode] = {
                        "status": "DATA_QUALITY_BLOCKED",
                        "reason": "CVD ON requires complete taker-flow and 24h proxy coverage",
                    }
                    continue
                arms[cvd_mode] = _run_arm(
                    dev_rows, symbol, direction, cvd_mode, funding,
                    folds=folds, fee_rate=fee_rate, slippage_rate=slippage_rate,
                )
            eligible_arms = [
                (mode, report) for mode, report in arms.items()
                if report.get("status") == "RESEARCH_ONLY"
                and _development_score(report)[0] == 1
            ]
            if not eligible_arms:
                symbol_result["development"][direction] = {
                    "arms": arms,
                    "selection": {
                        "status": "NO_DEVELOPMENT_CANDIDATE",
                        "reason": "No CVD mode met the predeclared development walk-forward screen",
                    },
                }
                symbol_result["forward_confirmation"][direction] = {
                    "status": "NOT_RUN",
                    "reason": "Do not inspect the reserved forward holdout without a frozen development candidate",
                }
                continue

            selected_mode, selected_report = max(
                eligible_arms, key=lambda item: _development_score(item[1])
            )
            symbol_result["development"][direction] = {
                "arms": arms,
                "selection": {
                    "status": "FROZEN_CANDIDATE_FOR_FORWARD_CONFIRMATION",
                    "selected_cvd_mode": selected_mode,
                    "selection_basis": "development walk-forward only; forward holdout metrics not read",
                    "development_score": list(_development_score(selected_report)),
                    "development_walk_forward_aggregate": selected_report["walk_forward_aggregate"],
                },
            }

            # The final forward window is opened only for the single preselected
            # symbol/direction/CVD arm; all other arms remain development-only.
            final_report = run_v12_validation(
                features, symbol, holdout_fraction=0.2, folds=folds,
                fee_rate=fee_rate, slippage_rate=slippage_rate,
                funding_events=funding, direction_mode=direction,
                cvd_mode=selected_mode, holdout_start_ts=holdout_ms,
            )
            final_report["forward_holdout_sample_gate"] = (
                "SUFFICIENT_FOR_SCREENING" if final_report["calibrated_holdout"]["trades"] >= 100
                else "INSUFFICIENT_FORWARD_TRADES"
            )
            final_report["promotion_eligible"] = False
            final_report["promotion_note"] = (
                "Research report only. Forward confirmation is not production approval; "
                "parameter-neighborhood, full winner-removal replay, bootstrap, paper-soak, "
                "and funding cashflow reconciliation gates remain separate."
            )
            symbol_result["forward_confirmation"][direction] = final_report

        matrix["symbols"][symbol] = symbol_result

    output_path = root / "V12_CVD_INDEPENDENT_MATRIX.json"
    output_path.write_text(json.dumps(matrix, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return {"status": matrix["status"], "symbols": list(matrix["symbols"]),
            "report": str(output_path), "live_trading_enabled": False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, help="UTC date YYYY-MM-DD inclusive")
    parser.add_argument("--end", required=True, help="UTC date YYYY-MM-DD exclusive")
    parser.add_argument("--holdout-start", required=True, help="frozen forward holdout UTC date YYYY-MM-DD")
    parser.add_argument("--output-dir", default="reports/v12-independent-matrix")
    parser.add_argument("--symbols", nargs="+", default=list(DEFAULT_SYMBOLS))
    parser.add_argument("--folds", type=int, default=4)
    parser.add_argument("--fee-rate", type=float, default=0.0005, help="per-side fee rate")
    parser.add_argument("--slippage-rate", type=float, default=0.0002, help="per-side base slippage rate")
    args = parser.parse_args()
    result = run_matrix(
        start_date=args.start, end_date=args.end, holdout_start=args.holdout_start,
        output_dir=args.output_dir, symbols=args.symbols, folds=args.folds,
        fee_rate=args.fee_rate, slippage_rate=args.slippage_rate,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
