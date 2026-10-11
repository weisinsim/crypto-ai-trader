# Adaptive and Breakout/Retest Candidate Review — 2026-10-11

Workflow: https://github.com/weisinsim/crypto-ai-trader/actions/runs/38127055306  
Commit under review: `63fc86677d0d5ff1ac33af2e26de21b65ed0816d`  
Status: **RESEARCH ONLY — NO PRODUCTION APPROVAL**

## Candidate results

The workflow completed successfully for BTC, ETH, SOL, AVAX, and XRP. It ran breakout/retest, adaptive parameter sweep, and mean-reversion research. These are different strategies; their results must not be mixed as if they were one model. The values below are the selected strategy reports printed in job logs. The workflow success indicates execution completed, not that the strategy is profitable.

| Asset | Strategy | Holdout trades | Base net return | Max drawdown | PF | ~0.30% round-trip stress return | ~0.46% severe stress return |
|---|---|---:|---:|---:|---:|---:|---:|
| BTCUSDT | Breakout/retest | 24 | +7.6080% | 5.0761% | 1.4383 | +0.0048% | -7.0851% |
| ETHUSDT | Breakout/retest | 17 | -3.1172% | 7.6992% | 0.7824 | -6.5364% | -9.8445% |
| SOLUSDT | Breakout/retest | 16 | -3.9403% | 7.5583% | 0.6656 | -7.2913% | -10.5345% |
| AVAXUSDT | Breakout/retest | 26 | -9.2510% | 15.4064% | 0.5818 | -13.7102% | -17.9611% |
| XRPUSDT | Breakout/retest | 14 | +1.7323% | 3.1158% | 1.2235 | -0.1705% | -2.0411% |

Additional mean-reversion results:
- BTC: -9.7122%, 61 trades, PF 0.7779; at stress costs -18.8191%.
- ETH: -3.2706%, 16 trades, PF 0.7327; at stress costs -5.7487%.
- SOL: -6.4828%, 15 trades, PF 0.4594; at stress costs -8.2055%.
- AVAX: +11.1196%, 66 trades, PF 1.2818; at 0.30% cost +4.0647%, PF 1.0978; at 0.46% cost -2.5496%, PF 0.9414.
- XRP: +0.9532%, 10 trades, PF 1.1437; at 0.30% cost -0.2107%, PF 0.9712.

## Decision

1. **No production candidate.** All selected candidates fail at least one robustness criterion; several fail even at base modeled costs.
2. **BTC breakout/retest is a research candidate only.** Its +7.61% base holdout return falls to approximately breakeven under the 0.30% round-trip stress and becomes negative under severe cost stress. It requires independent confirmation and funding/cost audit.
3. **AVAX mean-reversion is also research-only.** Its return is positive at base and 0.30% stress, but negative at 0.46%; funding mark-price provenance and second-half sensitivity remain unresolved.
4. Do not retune against the already inspected holdout. Freeze current candidate parameters and use a new untouched confirmation window or prospective paper-trading period.
5. Audit the exact report schema/logs to verify funding records and settlement cashflows were applied to each selected strategy. The presence of a funding CSV argument alone is not enough to establish this.

The results are exploratory historical results, not a promise of future returns. Workflow green means the code ran to completion, not that a strategy passed production validation.
