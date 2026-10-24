# V12 Exit-Management Evaluation — 2025-10-01 to 2026-10-01

**Decision: NOT APPROVED FOR LIVE TRADING.** The exit-management upgrade improved some results, but no coin has sufficiently stable, high-confidence performance for production.

## Tested configuration
- Venue: Binance USD-M Futures.
- Period: 2025-10-01 inclusive to 2026-10-01 exclusive.
- Per symbol: 8,760 1h candles, 2,190 4h candles, 1,095 funding settlements; 8,561 feature rows after warm-up.
- Fee 0.05% per side; slippage 0.02% per side.
- Position risk budget 0.5% of equity; maximum notional leverage 3x.
- Entry at the next 1h candle open after a closed-candle signal.
- Initial stop 1.5 ATR; TP1 at 2R for 50% size; TP2 at 3R for the remainder.
- After TP1, the remaining size uses a 1.5 ATR trailing stop, updated at candle close and active from the next candle.
- Opposite-direction candidate exits at the next candle open.
- Maximum holding time 72 bars (72 hours).
- Funding cash flows are included using historical settlement rate × open quantity × endpoint mark price; drawdown uses candle-close mark-to-market.
- Threshold calibration is training-only. Expanding-window walk-forward tests use the 50%–80% part of the series; the last 20% is shown separately.
- The optional short-side RSI floor of 30 was tested but not selected for any coin.

## Results

| Symbol | Full return | Full PF | WF return | WF PF | WF trades | Holdout return | Holdout PF | Holdout trades |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| BTCUSDT | -2.71% | 0.94 | -0.07% | 0.98 | 12 | +4.35%* | 4.79* | 7* |
| ETHUSDT | +5.61% | 1.14 | -3.47% | 0.70 | 31 | -3.38% | 0.60 | 20 |
| SOLUSDT | +1.91% | 1.04 | +3.11% | 1.25 | 37 | +4.09% | 1.53 | 26 |
| AVAXUSDT | -0.48% | 0.99 | -3.36% | 0.72 | 33 | +1.93% | 1.27 | 21 |

PF = profit factor. Returns are simulated and include modeled fees, slippage, and available funding cash flows. Initial equity is normalized to 1.0.

### Severe cost stress (2× fee and slippage assumptions)

| Symbol | Holdout net return | Holdout PF |
|---|---:|---:|
| BTCUSDT* | +3.91% | 4.12 |
| ETHUSDT | -5.68% | 0.42 |
| SOLUSDT | +2.73% | 1.32 |
| AVAXUSDT | +1.19% | 1.16 |

*BTC stress result is based on only 7 trades and is not decision-grade.

* BTC's positive holdout result uses a training-selected score threshold of 80 but only 7 holdout trades; it is explicitly **insufficient evidence**.

## Assessment
- **BTC:** Exit management materially reduced the full-sample loss, but walk-forward is essentially flat with only 12 trades. The positive calibrated holdout is too small to trust.
- **ETH:** Full-sample remains positive, but both walk-forward and holdout are negative. Do not promote.
- **SOL:** Best research candidate. Walk-forward and holdout are positive, and the holdout remains positive under 2× fee/slippage stress (+2.73%, PF 1.32). However, walk-forward PF is only 1.25 across 37 trades, full-sample PF is 1.04, and market-regime stability is not established. Continue research only.
- **AVAX:** Holdout is positive, but full-sample is near breakeven and walk-forward is negative. Do not promote.
- No symbol clears the intended institutional validation bar across full sample, expanding walk-forward, cost stress, adequate trade count, and paper-trading confirmation.

## Next steps
1. Keep live execution disabled; the dashboard's calibrated research settings are not promoted to live logic.
2. Continue research on SOL, using BTC/ETH/AVAX as controls.
3. Keep the reviewed 2025–2026 period as a consumed development dataset; use future paper trading or a newly reserved dataset for any final promotion test.
4. Resolve the repository-wide test failure caused by missing `execution` modules; the V12-focused unit tests and compile check pass.

## Artifacts
Detailed per-symbol feature CSVs, funding data, trade logs, and validation JSON are available in the GitHub Actions artifacts for the V12 Historical Research Evaluation workflow.
