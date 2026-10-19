# Five-Coin Historical Research Batch — 2026-10-18

## Run provenance

- Workflow: [Manual Five-Coin Historical Research Batch](https://github.com/weisinsim/crypto-ai-trader/actions/runs/39131263094)
- Commit: `e00529f4f7fe64315757a8aa9be961282f0efc3f`
- Window: 2026-04-01 inclusive to 2026-10-01 exclusive (UTC)
- Interval: 1h
- Result: all five symbols processed; research-only
- Artifact: [Download research artifact](https://github.com/weisinsim/crypto-ai-trader/actions/runs/39131263094/artifacts/12138157390)

## Baseline holdout results

These are the workflow's simple EMA baseline smoke-test results, not the asset-adaptive dashboard model. They are not production performance claims.

| Symbol | Bars | Holdout trades | Net return % | Max drawdown % | Win rate % | Profit factor |
|---|---:|---:|---:|---:|---:|---:|
| BTCUSDT | 4,392 | 32 | -0.6936 | 7.4841 | 43.75 | 0.9600 |
| ETHUSDT | 4,392 | 43 | -13.9537 | 16.1928 | 23.26 | 0.5229 |
| SOLUSDT | 4,392 | 38 | -2.3931 | 11.1241 | 34.21 | 0.8992 |
| AVAXUSDT | 4,392 | 29 | -0.1848 | 5.6489 | 34.48 | 0.9902 |
| XRPUSDT | 4,392 | 42 | -8.3958 | 9.7964 | 30.95 | 0.6866 |

The holdout includes 1,757 bars after a 60% chronological training/warmup segment. The baseline configuration uses EMA(20/50), fee rate 0.05% and slippage 0.02% per the research runner. It records a zero funding rate per bar; therefore these metrics must not be interpreted as fully funding-adjusted strategy performance.

## Funding-data audit

All five symbols have 549 funding records in the window, with 0 duplicate records and no cadence gaps over the configured 12-hour threshold. However, all records use prior-closed mark/regular-price proxies rather than verified settlement mark prices. Source authenticity and event completeness are not independently verified, and account cashflows are not reconciled.

The audit state `BOUNDARY_JITTER_NORMALIZED_ALIGNMENT_PASS_NOT_RECONCILED` means timestamp alignment passed after normalizing small boundary jitter; it does not mean funding PnL is reconciled.

## Decision

**NO PROMOTION. Keep live order placement disabled.**

Reasons:
1. Every symbol's baseline holdout net return is negative.
2. Every symbol's profit factor is below 1.
3. Funding cashflows are not authoritative/reconciled.
4. This batch is a pipeline smoke test, not a backtest of the asset-adaptive dashboard strategy.
5. Full live-vs-replay signal parity, realistic funding settlement, slippage-tail stress, and complete execution modelling remain open gates.

## Next required research

1. Run the asset-adaptive dashboard model—not the EMA smoke-test baseline—against the same verified historical windows.
2. Freeze model versions and parameters before a new chronological holdout; do not tune on the holdout.
3. Add realistic funding settlement events/mark prices and report ambiguous funding-boundary trades separately.
4. Include fee/slippage stress, partial take-profit, trailing-stop, intrabar ordering, liquidation and portfolio-overlap assumptions where supported.
5. Report every asset's trade count, expectancy, profit factor, drawdown, net R and regime/fold results.
6. Require robust positive net performance under base and stress costs plus a new unseen confirmation window and paper trading before any production review.
