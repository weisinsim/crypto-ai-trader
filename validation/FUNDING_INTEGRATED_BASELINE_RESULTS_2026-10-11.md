# Funding-Integrated Baseline Results — Workflow #88

Date: 2026-10-11  
Commit: `caa0fb291c87dfba6f282f8d59bd36a9a3357c98`  
Workflow: https://github.com/weisinsim/crypto-ai-trader/actions/runs/38126262439  
Python validation: https://github.com/weisinsim/crypto-ai-trader/actions/runs/38126262493

Status: **RESEARCH ONLY — no production approval**.

The five symbol jobs completed successfully. Each report loaded 1,095 funding rows for 8,760 hourly candles. This establishes that the workflow passed the timestamp alignment and runner checks, not that all mark-price records are authoritative or the simulation fully captures exchange execution.

## Holdout results (historical funding included)

| Symbol | Holdout trades | Net return (%) | Max drawdown (%) | Win rate (%) | Profit factor | Walk-forward concern |
|---|---:|---:|---:|---:|---:|---|
| BTCUSDT | 64 | +1.2806 | 7.4841 | 43.75 | 1.0372 | Fold 3: -1.6188%, PF 0.7461 |
| ETHUSDT | 76 | -15.2345 | 18.3986 | 28.95 | 0.6954 | 3 of 4 folds negative |
| SOLUSDT | 66 | -8.3235 | 14.1128 | 31.82 | 0.8065 | 4 of 4 folds negative |
| AVAXUSDT | 69 | -5.1559 | 13.4518 | 33.33 | 0.8904 | 3 of 4 folds negative |
| XRPUSDT | 71 | -1.4910 | 10.5219 | 36.62 | 0.9674 | Fold 3: -8.8219%, PF 0.4035 |

## Decision

- BTC is only marginally positive after the modeled historical funding cashflows and costs; PF 1.0372 is not a robust edge.
- ETH, SOL, AVAX and XRP fail the baseline profitability gate.
- Do not promote the EMA baseline on any of the five symbols.
- These numbers are a diagnostic baseline, not evidence that a tuned model is profitable. Do not use this already inspected holdout to tune parameters.
- Funding data audit reports must still be reviewed for mark-price source provenance. Current archive construction uses mark-price kline opens where available, but some events can use a prior trade-kline close proxy. Proxy-mark events must be quantified and ideally reconciled against authoritative settlement mark prices.
- The simulator charges event funding when a position is open at the settlement candle timestamp. A remaining limitation is intrabar ambiguity: if a position exits within a candle that also carries a settlement event, hourly OHLC alone may not establish exact event/exit ordering. A more conservative event-aware policy or finer-grained execution data is needed before production use.
- Next research should be train-only candidate selection and explicit cost stress tests, followed by a new untouched confirmation period. Keep all current models Research/Reject until those gates pass.
