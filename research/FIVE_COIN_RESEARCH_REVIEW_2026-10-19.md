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

The replay input contract now matches the downloader: CSV timestamps are Binance candle CLOSE timestamps (inclusive close millisecond, typically :59.999 UTC). At each hourly decision, only 4h rows with close timestamp <= the decision timestamp are available; the replay no longer adds an extra interval to a close timestamp. Regression tests cover exact close and one-millisecond-before-close, and the incremental 4h cursor is checked against the reference filter. The timestamp-contract fix is in `4ea9afb5ebe778b08d0d9eb9d340680e71cd20ad`; its initial test run exposed stale open-time fixtures, which were updated in `614be19630090ad21f7b804ae6f9a13620ccff5a`. All three PR workflows passed on `614be19630090ad21f7b804ae6f9a13620ccff5a`:
- Research test suite: https://github.com/weisinsim/crypto-ai-trader/actions/runs/39213999417
- Python validation: https://github.com/weisinsim/crypto-ai-trader/actions/runs/39213999564
- Validation audit tests: https://github.com/weisinsim/crypto-ai-trader/actions/runs/39213999500

All three GitHub Actions checks passed on that latest code-bearing commit:
- Research test suite: https://github.com/weisinsim/crypto-ai-trader/actions/runs/39202148990
- Python validation: https://github.com/weisinsim/crypto-ai-trader/actions/runs/39202148997
- Validation audit tests: https://github.com/weisinsim/crypto-ai-trader/actions/runs/39202149009

The report-only follow-up commit `d1086d4db6ba508fbf871fb2836c2e23f0aa8b31` also passed all three pull-request workflows:
- Python validation: https://github.com/weisinsim/crypto-ai-trader/actions/runs/39209635073
- Validation audit tests: https://github.com/weisinsim/crypto-ai-trader/actions/runs/39209635096
- Research test suite: https://github.com/weisinsim/crypto-ai-trader/actions/runs/39209635107

These checks establish code-level validation only; they do not satisfy the separate historical-market-data differential replay requirement. PR #188 remains open and draft, and has not been merged.

## Required next steps

1. Run differential replay on representative BTC, ETH, SOL, AVAX, and XRP history; compare timestamp-by-timestamp signals and indicator values between baseline and optimized replay, including closed-candle boundaries.
2. Reconcile funding cashflows against verified exchange settlement/mark-price records, plus fees and slippage. Current proxy alignment alone is not reconciliation.
3. Run independent walk-forward/holdout tests after candidate selection is frozen; report all costs and drawdowns.
4. Have a human review PR #188 and explicitly decide whether to merge. Do not merge automatically.
5. Keep live order execution disabled until all research gates pass and a separate explicit authorization is given.

## Decision

**NO LIVE TRADING APPROVAL.** Current diagnostics do not establish robust, cost-resilient, funding-reconciled out-of-sample profitability. No verified funding settlement cashflow dataset has been reconciled in this update. Strategy selection remains blocked by cost/funding/out-of-sample gates, and live execution remains disabled.


## Historical data workflow #15 audit (2026-10-19)

A separate manual run completed successfully on `main` commit `1924aaaa906c9ae941164910932eb43e4d2d34d5` (workflow run #15, run ID `39215150224`). It generated artifact `historical-research-2025-10-01-to-2026-10-01` (artifact ID `12169084233`, SHA-256 `77d7af64bdffcbe50bbdc9f9f926c2a864fd775efc8a6cd8ba8337828f479bc1`). This run is on `main`, not PR #188's candidate branch; its results are useful diagnostics but do not validate the PR branch.

The artifact's replay manifest reports 8,760 1h candles and 2,190 4h candles for each requested symbol. Signal counts were BTC 177, ETH 0, SOL 57, AVAX 72, XRP 150. The signal audit reports zero timestamp/price/stop/target mismatches for the nonempty signal files, but explicitly states that this is not live-strategy parity or profitability validation. ETH is marked `NO_SIGNALS`; inspection of the `main` replay source shows an explicit `reason="NO_PRODUCTION"` branch for ETH, so zero signals are an implementation/configuration block, not evidence that ETH has no market opportunities. Do not loosen thresholds or infer an ETH strategy until an independently specified model is reviewed and tested.

The summary classifies BTC results as `RESEARCH_ONLY`; AVAX, SOL, and XRP are largely `INSUFFICIENT_SAMPLE`; ETH is `BLOCKED:NO_SIGNALS`. For example, BTC single-position 3.0R at 5 bps shows 40 holdout trades, PF 1.2646 and +7.7948R, but at 30 bps shows PF 0.6691 and -13.8916R. This is strongly cost-sensitive and is not a deployable edge. Funding integrity reports 1,095 ordered records per symbol and no basic duplicate/cadence errors, but the artifact itself cautions that this is a coarse integrity diagnostic; it does not reconcile funding cashflows to verified exchange settlement records.

### Actions required from this audit

1. Treat workflow #15 as baseline `main` research only; rerun the equivalent bounded historical workflow against the PR #188 branch after confirming that workflow supports the branch and preserves artifact provenance.
2. Keep ETH blocked until a documented ETH signal model exists; add a regression test that catches a hard-coded `NO_PRODUCTION` path being mistaken for an empirical no-signal result.
3. Add a parity audit that compares the replay with the intended production logic, not only self-consistency of emitted signal rows.
4. Retain the minimum holdout trade gate and cost tiers. Do not select the 5 bps BTC result while the 10–30 bps sensitivity is materially negative.
5. Reconcile actual funding cashflows and execution costs before any deployment decision.

This audit does not change the decision: **NO LIVE TRADING APPROVAL**.