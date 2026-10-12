# Five-Asset Baseline Review — 2026-10-11

Run: https://github.com/weisinsim/crypto-ai-trader/actions/runs/38111199169
Commit: 69cad2a6bd92d32a33190c6e51968c23e6c1abec
Status: RESEARCH ONLY; no production approval.

## Frozen-parameter holdout cost stress

| Asset | Trades | Base net return | Base PF | Base max DD | 0.30% RT net return | 0.30% PF | 0.46% RT net return | 0.46% PF |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| ETHUSDT | 57 | +13.3404% | 1.3280 | 9.8933% | +3.8669% | 1.0866 | -4.8296% | 0.9001 |
| BTCUSDT | 47 | +2.1901% | 1.0560 | 16.9561% | -6.5814% | 0.8476 | -14.6176% | 0.6877 |
| AVAXUSDT | 52 | +3.7582% | 1.0937 | 18.0789% | -1.2503% | 0.9706 | -6.0218% | 0.8652 |
| XRPUSDT | 40 | +2.7544% | 1.0887 | 11.5552% | -2.2167% | 0.9341 | -6.9541% | 0.8071 |
| SOLUSDT | 57 | -14.4239% | 0.6860 | 16.5329% | -19.9782% | 0.5940 | -25.1789% | 0.5181 |

All assets fail the predeclared cost-robustness gate, which requires positive net return and PF > 1 at base, 0.30% and 0.46% round-trip cost scenarios.

## ETH mean-reversion walk-forward diagnostic

The run's ETH mean-reversion walk-forward folds were:
- Fold 1: 16 trades, -5.7362% net return, PF 0.5314
- Fold 2: 17 trades, +3.9587% net return, PF 1.4169
- Fold 3: 18 trades, -5.6181% net return, PF 0.5177
- Fold 4: 25 trades, -8.8123% net return, PF 0.5279

Despite positive inspected holdout results under base and 0.30% costs, these walk-forward diagnostics are unstable and mostly negative. ETH remains a research lead only; it is not robustly validated. The inspected holdout must not be used for further parameter selection.

## Funding data audit caveat

Each symbol reported 8,760 hourly candles and 1,095 funding records, with no gaps greater than 8 hours. Each symbol also reported 3 prior-trade-candle-close proxy price rows, with remaining rows from mark-price kline sources. The audit itself states that gap checks do not prove every funding event is present and proxy prices are not exchange mark-price observations. Obtain and reconcile authoritative funding event timestamps and mark prices before claiming exchange-level funding reconciliation.

## Required next steps

1. Freeze and archive this run and its inputs.
2. Do not tune against the already-inspected holdout.
3. Verify funding events against authoritative exchange data and report source counts and unresolved gaps.
4. Any new candidate must be selected only within training/walk-forward windows and evaluated on a genuinely new chronological confirmation window or prospective paper trading.
5. Require all predeclared cost gates, trade-count, drawdown, data-integrity, and stability checks to pass before any promotion.

A successful GitHub Actions run indicates the research pipeline executed; it does not imply the strategies are profitable.

## Re-run update — historical research run #160 (2026-10-12)

Workflow: https://github.com/weisinsim/crypto-ai-trader/actions/runs/38222947432  
Code revision: `2fb39c97726167db26342b908a6c5d6782244317`  
Outcome: all five 1H matrix jobs completed successfully; funding coverage/provenance audit gates passed. The workflow's successful execution is not a profitability certification.

### Fresh 60/40 chronological baseline holdout

| Asset | Trades | Net return | Max drawdown | Win rate | Profit factor |
|---|---:|---:|---:|---:|---:|
| BTCUSDT | 64 | +1.2806% | 7.4841% | 43.75% | 1.0372 |
| ETHUSDT | 76 | -15.2345% | 18.3986% | 28.95% | 0.6954 |
| SOLUSDT | 66 | -8.3235% | 14.1128% | 31.82% | 0.8065 |
| XRPUSDT | 71 | -1.4910% | 10.5219% | 36.62% | 0.9674 |
| AVAXUSDT | 69 | -5.1559% | 13.4518% | 33.33% | 0.8904 |

This baseline is a smoke-test archetype, not a selected or approved production strategy. Only BTC is slightly positive on this holdout, with a weak PF of 1.0372 and low expectancy (+0.02% of equity per trade); that is not a robust edge.

### Train-selected breakout/retest and mean-reversion follow-up

The training-only breakout/retest search selected candidates for diagnostics, but **all five symbols failed the cost-robustness gate**. Selected holdout results (base / 0.30% round-trip / 0.46% round-trip) were:

| Asset | Trades | Base return / PF | 0.30% return / PF | 0.46% return / PF |
|---|---:|---:|---:|---:|
| BTCUSDT (short-only) | 24 | +7.6080% / 1.4383 | +0.0048% / 1.0002 | -7.0851% / 0.7052 |
| ETHUSDT (long-only) | 17 | -3.1172% / 0.7824 | -6.5364% / 0.6087 | -9.8445% / 0.4822 |
| SOLUSDT (short-only) | 16 | -3.9403% / 0.6656 | -7.2913% / 0.4670 | -10.5345% / 0.3202 |
| XRPUSDT (long-only) | 14 | +1.7323% / 1.2235 | -0.1705% / 0.9795 | -2.0411% / 0.7707 |
| AVAXUSDT (short-only) | 26 | -9.2510% / 0.5818 | -13.7102% / 0.4530 | -17.9611% / 0.3548 |

Mean-reversion diagnostics also failed the cost-robustness gate for every symbol. AVAX is the strongest base-cost mean-reversion result (+11.1196%, PF 1.2818), but its 0.46% stress result is -2.5496% with PF 0.9414, and the second half of holdout is negative at both stressed costs. Do not use the holdout results to select a different parameter neighborhood.

### Next action

1. Keep all five assets and all tested strategy families in RESEARCH ONLY / NOT APPROVED.
2. Do not run more parameter selection against this same 60/40 holdout.
3. Freeze the strategy rules selected from training only, then evaluate them on a later, untouched chronological window or prospective paper trading.
4. Before considering promotion, require positive net return and PF > 1 at base, 0.30%, and 0.46% round-trip cost, adequate trade count, acceptable drawdown, stable predeclared subperiods, and independent funding/execution reconciliation.
5. Treat win rate as a secondary metric; expectancy, profit factor, costs, drawdown, and robustness are the governing gates.

