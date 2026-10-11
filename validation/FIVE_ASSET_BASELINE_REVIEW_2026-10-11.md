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