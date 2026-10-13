# Research Status — 2026-10-13

## Scope and interpretation

This is a research-only status snapshot from GitHub Actions run [38341039831](https://github.com/weisinsim/crypto-ai-trader/actions/runs/38341039831). Historical candle/funding audit and all five baseline jobs completed successfully after the exact one-minute mark-candle source was recognized. A successful workflow means the pipeline completed; it does **not** certify a profitable strategy or approve live trading.

## 1H baseline holdout

| Symbol | Trades | Net return | Max drawdown | Win rate | Profit factor |
|---|---:|---:|---:|---:|---:|
| BTCUSDT | 64 | +1.2806% | 7.4841% | 43.75% | 1.0372 |
| ETHUSDT | 76 | -15.2345% | 18.3986% | 28.95% | 0.6954 |
| SOLUSDT | 66 | -8.3235% | 14.1128% | 31.82% | 0.8065 |
| AVAXUSDT | 69 | -5.1559% | 13.4518% | 33.33% | 0.8904 |
| XRPUSDT | 71 | -1.4910% | 10.5219% | 36.62% | 0.9674 |

These are diagnostic baseline results, not a single optimized model comparison. Only BTC's baseline holdout return was positive, and its profit factor was only slightly above 1. The other four baseline holdouts were negative.

## 2. Frozen confirmation / stress checks

The latest independent frozen-candidate confirmation workflow completed for BTC and AVAX. Its short window is 2026-10-01 through 2026-10-12 and must not be confused with the 365-day baseline.

The broader 1H candidate sweeps remain research-only. Examples from the current reports:
- BTC breakout/retest: holdout +7.6127% at base cost, 24 trades, PF 1.4386; at 0.30% round-trip cost +0.0092%, PF 1.0004; at 0.46% cost -7.0809%, PF 0.7053. Cost robustness gate: FAIL.
- AVAX mean reversion: holdout +11.1195%, 66 trades, PF 1.2819; at 0.30% cost +4.0646%, PF 1.0978; at 0.46% cost -2.5497%, PF 0.9413. Cost robustness gate: FAIL.
- Some adaptive sweeps produced zero eligible candidates; these should remain explicitly unselected rather than loosening thresholds just to obtain a model.

## 3. Current decision

**Status: RESEARCH ONLY — NOT APPROVED FOR LIVE TRADING.**

Reasons:
1. Most 1H baseline holdouts are negative.
2. The best-looking examples do not pass all three cost scenarios.
3. Some candidates have too few holdout trades for confident conclusions.
4. Funding source labels and matching logic still need provenance controls beyond label recognition; a source label alone is not cryptographic or independent proof of authenticity.
5. Intrabar funding/stop/target ordering must continue to be tested at simulator lifecycle level.

## 4. Next work

1. Preserve frozen candidate parameters and avoid repeated selection against the same holdout.
2. Add explicit data-lineage evidence to funding rows/manifests (venue, endpoint/archive path, exact timestamp match, retrieval window, and source file digest); fail closed on approximate matches.
3. Test funding settlement inside open trades through the full simulator, including adverse funding and equity/drawdown marks—not only a helper function.
4. Run a genuinely untouched chronological confirmation window and require minimum trade count, positive net return, PF > 1.15, and passing base/stress/severe cost gates before any promotion.
5. Keep all live execution disabled until all gates pass.

## 5. Latest frozen confirmation: 2026-10-01 to 2026-10-12

Workflow [38356507947](https://github.com/weisinsim/crypto-ai-trader/actions/runs/38356507947) completed successfully as a research pipeline for BTCUSDT and AVAXUSDT. The candidate-level gates failed for both symbols; successful CI is not strategy approval.

| Symbol / frozen setup | Trades | Win rate | Net return @ 0.14% cost | Net return @ 0.30% cost | Net return @ 0.46% cost | PF @ 0.14% / 0.30% / 0.46% |
|---|---:|---:|---:|---:|---:|---:|
| BTCUSDT breakout/retest (SHORT) | 3 | 33.33% | -0.9456% | -2.1851% | -3.4145% | 0.6072 / 0.3103 / 0.1288 |
| AVAXUSDT mean reversion (SHORT) | 6 | 33.33% | -1.3094% | -1.9577% | -2.6024% | 0.6845 / 0.5757 / 0.4873 |

Both reports are explicitly `RESEARCH_ONLY`. Neither reaches the 30-trade minimum, neither has positive net return, neither meets PF > 1.15, and neither passes the 0.30% cost gate. The short confirmation window is not a replacement for the longer baseline or an untouched out-of-sample evaluation. Funding audits passed for the sampled files, but their own limitations state that recognized source labels do not independently authenticate the source files.

## 6. Latest data-audit hardening

Commits [46d8cc86](https://github.com/weisinsim/crypto-ai-trader/commit/46d8cc86cdee060986446cc6f684afee101d5794), [5ed37ecb](https://github.com/weisinsim/crypto-ai-trader/commit/5ed37ecb9df0bb9b2e6c6a92cb303c61288ea2d4), and [6b104c8c](https://github.com/weisinsim/crypto-ai-trader/commit/6b104c8ce499dfa9d8ea8b6b0c1ffbe2fdb84163) tightened funding-audit timestamp parsing and added regression tests for booleans and non-integer values. The latest Python validation, research test suite, historical baseline workflow, and frozen-candidate confirmation workflows all completed successfully for commit `6b104c8c`; this indicates pipeline completion only.
