# V12 Historical Research Findings — Baseline Before Exit-Management Upgrade

**Decision: NO PRODUCTION PROMOTION.** These are research results, not live trading performance or a profitability guarantee.

**Version note:** The metrics below are the baseline before the later ATR trailing-stop, next-open signal-reversal exit, and 72-hour time-stop upgrade. A fresh historical evaluation of that upgraded exit logic is running; do not treat this baseline table as the current model result.

## Method
- Venue: Binance USD-M Futures; one symbol per run.
- Period: 2025-10-01 inclusive to 2026-10-01 exclusive.
- Data per symbol: 8,760 1h candles, 2,190 4h candles, 1,095 funding settlements; 8,561 feature rows after warm-up.
- Signal at closed 1h candle; entry at next 1h open.
- Fees: 0.05% per side; slippage: 0.02% per side.
- Risk: 0.5% equity budget per trade, 3x notional cap; initial stop 1.5 ATR; 50% TP1 at 2R and remaining 50% at 3R.
- Historical funding cash flows are included using settlement rate × position quantity × endpoint mark price. Open-position mark-to-market is included in drawdown.
- Threshold selection uses training data only. Expanding-window walk-forward tests cover 50%–80% of the series; the last 20% is reported separately. Because these results have now been reviewed during iterative development, this holdout is considered consumed for future model tuning and is not a pristine final test set.
- The optional short-side RSI floor of 30 was evaluated during training; it was not selected for any of the four symbols.

## Results

| Symbol | Full-sample return | Full PF | Walk-forward return | Walk-forward PF | WF trades | Calibrated holdout return | Holdout PF | Holdout trades |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| BTCUSDT | -7.41% | 0.84 | -4.01% | 0.51 | 19 | +3.85% | 4.37 | 7 |
| ETHUSDT | +6.79% | 1.18 | +1.51% | 1.15 | 31 | -3.01% | 0.62 | 19 |
| SOLUSDT | +6.29% | 1.13 | +2.97% | 1.25 | 35 | +4.02% | 1.55 | 24 |
| AVAXUSDT | -6.53% | 0.83 | -4.80% | 0.64 | 33 | +2.69% | 1.38 | 21 |

PF = profit factor. Returns are simulated and include modeled fees, slippage, and available funding cash flows. Results use normalized initial equity of 1.0.

## Interpretation
- **BTCUSDT:** Walk-forward performance fails. The positive calibrated holdout is based on only 7 trades and is insufficient evidence.
- **ETHUSDT:** Positive full-sample and walk-forward results do not carry into the final holdout, which is negative.
- **SOLUSDT:** Best candidate for continued research, but 35 walk-forward trades and 24 holdout trades are still too few for a production claim; walk-forward fold results are not uniformly positive.
- **AVAXUSDT:** Positive holdout is contradicted by negative full-sample and walk-forward results; no training threshold candidate passed the selection gates.
- No symbol demonstrates sufficiently stable, high-confidence performance to justify live deployment. None meets the intended institutional validation bar across walk-forward and holdout.

## Next actions
1. Keep live order execution disabled.
2. Continue model research on SOL first, while retaining BTC/ETH/AVAX as controls.
3. Add a larger, genuinely future paper-trading sample and preserve it from further parameter tuning.
4. Expand validation with additional market regimes and independent data checks before any production review.

## Artifacts
The detailed per-symbol CSVs and JSON reports are available in the GitHub Actions artifacts for the V12 Historical Research Evaluation workflow. The dashboard still uses the default research thresholds; calibrated thresholds and the RSI filter have not been promoted into live decision logic.
