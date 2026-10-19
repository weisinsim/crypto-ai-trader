# Five-Coin Research Review — 2026-10-19

## Scope and status

This review records diagnostic outputs from GitHub Actions run 39189619082. It is research-only. These same-holdout frozen-stability figures are not an out-of-sample strategy approval and must not be used to select long/short direction. The adaptive sweep is separately gated on historical funding inputs.

## Frozen-stability diagnostic: BOTH directions

| Symbol | Base net return | Base max drawdown | Trades | Win rate | Profit factor | 0.30% round-trip stress | 0.46% round-trip severe |
|---|---:|---:|---:|---:|---:|---:|---:|
| BTCUSDT | -12.3598% | 21.8511% | 103 | 35.92% | 0.8333 | -25.0286% | -35.8845% |
| ETHUSDT | +6.9336% | 5.5739% | 28 | 46.43% | 1.4000 | +2.1959% | -2.3414% |
| SOLUSDT | -8.4383% | 10.2514% | 33 | 33.33% | 0.6358 | -11.6017% | -14.6596% |
| AVAXUSDT | +17.8405% | 13.1024% | 134 | 48.51% | 1.2229 | +3.3531% | -9.3663% |
| XRPUSDT | -4.9977% | 8.0742% | 19 | 26.32% | 0.6465 | -6.8735% | -8.7147% |

Stress columns are net returns under the respective cost assumptions; do not interpret them as funding-reconciled live performance.

## Adaptive candidate gate

The adaptive sweep considered 72 candidates per symbol. Current outputs:
- BTCUSDT: 0 eligible; no selection.
- ETHUSDT: 13 eligible, but recommendation status is REJECTED_COST_ROBUSTNESS; stress holdout reported -25.5046% net return, 27.195% max drawdown, 72 trades, profit factor 0.5296.
- SOLUSDT: 0 eligible; no selection.
- AVAXUSDT: 0 eligible; no selection.
- XRPUSDT: 0 eligible; no selection.

The job logs explicitly reject comparable adaptive results unless historical funding is included. The frozen-stability diagnostic is not a substitute for funding reconciliation.

## Engineering gate: historical replay optimization PR #188

PR #187 is the superseded, conflict-prone draft. The active candidate is PR #188, rebased onto current `main` at `1924aaaa906c9ae941164910932eb43e4d2d34d5`:
https://github.com/weisinsim/crypto-ai-trader/pull/188

The active branch preserves the closed-4h-candle guard (candle open timestamp + 4h interval <= hourly decision close) and adds prefix EMA/RSI parity, candle-boundary, and bounded-window indicator regression tests. PR #188 remains open and draft, and has not been merged.

The following GitHub Actions checks passed on the current PR head `b6c29b349d3c5106b2a1b5cfe03a2ad7148789e7`:
- Research test suite: https://github.com/weisinsim/crypto-ai-trader/actions/runs/39201532803
- Python validation: https://github.com/weisinsim/crypto-ai-trader/actions/runs/39201532811
- Validation audit tests: https://github.com/weisinsim/crypto-ai-trader/actions/runs/39201532709

These tests establish code-level validation only; they do not satisfy the separate historical-market-data differential replay requirement below.

## Required next steps

1. Run differential replay on representative BTC, ETH, SOL, AVAX, and XRP history; compare timestamp-by-timestamp signals and indicator values between baseline and optimized replay, including closed-candle boundaries.
2. Reconcile funding cashflows against verified exchange settlement/mark-price records, plus fees and slippage. Current proxy alignment alone is not reconciliation.
3. Run independent walk-forward/holdout tests after candidate selection is frozen; report all costs and drawdowns.
4. Have a human review PR #188 and explicitly decide whether to merge. Do not merge automatically.
5. Keep live order execution disabled until all research gates pass and a separate explicit authorization is given.

## Decision

**NO LIVE TRADING APPROVAL.** Current diagnostics do not establish robust, cost-resilient, funding-reconciled out-of-sample profitability. No verified funding settlement cashflow dataset has been reconciled in this update. Strategy selection remains blocked by cost/funding/out-of-sample gates, and live execution remains disabled.
