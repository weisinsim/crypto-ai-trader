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

PR #187 is the superseded, conflict-prone draft. The active candidate is PR #188, rebased onto current `main` at `1924aaaa906c9ae941164910932eb43e4d2d34d5`. Keep the closed-4h-candle guard (candle open timestamp + 4h interval <= hourly decision close), and require indicator/signal parity before accepting the optimization. PR #188 is still draft and requires human review before merge.

## Required next steps

1. Resolve PR #187 base conflict on a dedicated branch without overwriting current main fixes.
2. Add/execute differential replay tests on representative BTC, ETH, SOL, AVAX, and XRP history; compare signals and indicator values, not only unit-level calculations.
3. Reconcile funding cashflows against verified exchange settlement/mark-price records, plus fees and slippage. Current proxy alignment alone is not reconciliation.
4. Run independent walk-forward/holdout tests after candidate selection is frozen; report all costs and drawdowns.
5. Keep live order execution disabled until all gates pass and a separate explicit authorization is given.

## Decision

**NO LIVE TRADING APPROVAL.** Current diagnostics do not establish robust, cost-resilient, funding-reconciled out-of-sample profitability.


## Engineering update — 2026-10-19

The original PR #187 conflict was addressed on a separate rebased branch and superseded by draft PR #188:
https://github.com/weisinsim/crypto-ai-trader/pull/188

Current replay-optimization head: `b7957edee2cbee192de0da90b4dc5ba90e7ed222`.
The branch preserves closed-4h-candle filtering and adds prefix EMA/RSI parity, candle-boundary, and bounded-window indicator regression coverage. GitHub reports the PR is open, draft, and mergeable. Automated checks on the current head (`b7957ed...`) passed:
- Research test suite: https://github.com/weisinsim/crypto-ai-trader/actions/runs/39201063964
- Python validation: https://github.com/weisinsim/crypto-ai-trader/actions/runs/39201063986
- Validation audit tests: https://github.com/weisinsim/crypto-ai-trader/actions/runs/39201063969

These tests establish code-level validation only; they do not satisfy the separate historical-market-data differential replay requirement above. No verified funding settlement cashflow dataset has been reconciled in this update. Strategy selection remains blocked by cost/funding/out-of-sample gates, and live execution remains disabled. PR #188 requires human review before merge.
