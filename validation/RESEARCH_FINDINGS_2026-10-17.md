# Research Findings — 2026-10-17

Source workflow: [Historical baseline research run 39025410640](https://github.com/weisinsim/crypto-ai-trader/actions/runs/39025410640)

Commit under test: `f6c3350a48ecbc16f4b30b41c1315d83617d4507`

## Executive decision

**Decision: NO PROMOTION. Keep live order placement disabled.**

The workflow and its per-asset jobs completed successfully. That is an execution/CI status, not a strategy approval. The adaptive research logs explicitly report `FAIL_COST_ROBUSTNESS`; the acceptance rule requires positive holdout return and profit factor above 1 on base, 0.30% round-trip cost stress, and 0.46% severe cost stress.

## Verified adaptive diagnostic highlights

These are research-only outputs from the run logs, not forward/live performance:

| Asset | Holdout cost scenario | Net return | Max drawdown | Profit factor | Interpretation |
|---|---|---:|---:|---:|---|
| BTCUSDT | 0.30% round-trip stress | -18.8186% | 24.3954% | 0.6028 | Fails cost robustness |
| BTCUSDT | 0.46% severe stress | -27.0240% | 31.1893% | 0.4691 | Severe degradation |
| SOLUSDT | 0.30% round-trip stress | -8.2143% | 11.1852% | 0.3825 | Fails cost robustness |
| SOLUSDT | 0.46% severe stress | -9.9075% | 12.4341% | 0.3208 | Severe degradation |
| AVAXUSDT | 0.30% round-trip stress | +4.0646% | 9.5912% | 1.0978 | Positive full holdout, but not stable |
| AVAXUSDT | 0.46% severe stress | -2.5497% | 11.5531% | 0.9413 | Fails severe-cost gate |
| XRPUSDT | 0.30% round-trip stress | -0.2073% | 5.8651% | 0.9716 | Fails cost robustness |
| XRPUSDT | 0.46% severe stress | -1.3594% | 6.2977% | 0.8295 | Fails severe-cost gate |

The ETHUSDT adaptive job also did not satisfy the promotion gate. Consult its run log for the full set of diagnostics rather than infer approval from job success.

## Stability warning

AVAXUSDT is a useful example of why one aggregate number is insufficient: its first holdout half under 0.30% cost stress returned +5.9434% (PF 1.5902), while the second half returned -1.7733% (PF 0.9403). The full holdout became negative under the severe 0.46% cost scenario. That is not robust enough for promotion.

BTCUSDT also shows material regime sensitivity: under the adaptive stress diagnostic, the first holdout half at 0.30% costs returned -3.2066%, while the second half returned -18.2968%.

## Required next research actions

1. Preserve the untouched holdout; do not tune parameters against it.
2. Diagnose data/execution assumptions and the worst-performing time segments first.
3. Use rolling walk-forward evaluation with parameter selection confined to training folds.
4. Require net positive returns and PF > 1 across base, stress, and severe-stress costs, plus acceptable drawdown and enough trades.
5. Re-run the exact frozen candidate on a new, later unseen period and paper-trade before considering any production review.
6. Keep exchange order placement disabled until every mandatory gate passes and order-state/risk controls are independently verified.

## Reporting rules

- A green GitHub Actions workflow means the research pipeline ran successfully.
- `PASS_COVERAGE_ONLY` means required research artifacts are present, not that the strategy is profitable.
- A cost-robustness failure or missing audit means **NO PROMOTION**.
- Do not present any result in this report as a live return forecast or guarantee.
