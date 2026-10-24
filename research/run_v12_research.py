"""End-to-end V12 research run: download, build causal features, and validate one symbol."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from research.binance_data import download_klines, save_csv as save_candles, utc_ms
from research.binance_funding_data import download_funding_history, save_csv as save_funding
from research.v12_features import build_v12_features, write_feature_csv
from research.v12_validation import run_v12_validation


def run_pipeline(symbol, start_date, end_date, output_dir, *, holdout_fraction=0.2,
                 folds=4, fee_rate=0.0005, slippage_rate=0.0002):
    symbol = str(symbol).strip().upper()
    start_ms, end_ms = utc_ms(start_date), utc_ms(end_date)
    if start_ms % 14_400_000 or end_ms % 14_400_000 or end_ms <= start_ms:
        raise ValueError("date range must be positive and aligned to UTC 4h boundaries")
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    prefix = f"{symbol}_{start_date}_{end_date}"

    one_hour = download_klines(symbol, "1h", start_ms, end_ms)
    four_hour = download_klines(symbol, "4h", start_ms, end_ms)
    funding = download_funding_history(symbol, start_ms, end_ms)

    candles_1h_path = root / f"{prefix}_1h.csv"
    candles_4h_path = root / f"{prefix}_4h.csv"
    funding_path = root / f"{prefix}_funding.csv"
    features_path = root / f"{prefix}_v12_features.csv"
    report_path = root / f"{prefix}_v12_validation.json"

    save_candles(one_hour, candles_1h_path)
    save_candles(four_hour, candles_4h_path)
    save_funding(funding, funding_path)
    features = build_v12_features(one_hour, four_hour, funding, symbol=symbol)
    write_feature_csv(features_path, features)

    report = run_v12_validation(
        features, symbol, holdout_fraction=holdout_fraction, folds=folds,
        fee_rate=fee_rate, slippage_rate=slippage_rate,
        funding_events=funding,
    )
    report["data_lineage"] = {
        "venue": "Binance USD-M Futures",
        "symbol": symbol, "start_date_utc_inclusive": start_date,
        "end_date_utc_exclusive": end_date,
        "candle_1h_rows": len(one_hour), "candle_4h_rows": len(four_hour),
        "funding_events": len(funding), "feature_rows": len(features),
        "files": {
            "candles_1h": str(candles_1h_path),
            "candles_4h": str(candles_4h_path),
            "funding": str(funding_path),
            "features": str(features_path),
        },
        "funding_quality_note": (
            "Funding rate is used as an as-of signal feature and funding cash flows use "
            "historical settlement rate * position quantity * endpoint mark price; "
            "account-ledger reconciliation has not been performed."
        ),
    }
    report_path.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return {
        "status": "RESEARCH_ONLY", "symbol": symbol,
        "candles_1h": len(one_hour), "candles_4h": len(four_hour),
        "funding_events": len(funding), "feature_rows": len(features),
        "report": str(report_path), "net_return_pct_holdout": report["holdout"]["net_return_pct"],
        "holdout_trades": report["holdout"]["trades"],
        "profitability_claim_eligible": False, "live_trading_enabled": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--start", required=True, help="UTC date YYYY-MM-DD, inclusive")
    parser.add_argument("--end", required=True, help="UTC date YYYY-MM-DD, exclusive")
    parser.add_argument("--output-dir", default="reports/v12")
    parser.add_argument("--holdout-fraction", type=float, default=0.2)
    parser.add_argument("--folds", type=int, default=4)
    parser.add_argument("--fee-rate", type=float, default=0.0005)
    parser.add_argument("--slippage-rate", type=float, default=0.0002)
    args = parser.parse_args()
    result = run_pipeline(
        args.symbol, args.start, args.end, args.output_dir,
        holdout_fraction=args.holdout_fraction, folds=args.folds,
        fee_rate=args.fee_rate, slippage_rate=args.slippage_rate,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
