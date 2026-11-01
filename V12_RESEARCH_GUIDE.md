# V12 Adaptive Institutional Hybrid — Research Guide

## Current status
V12 is a research-only model. It is not approved for live trading and the simulator does not submit exchange orders.

## One-command research run
For a single symbol and UTC date range, the end-to-end runner downloads Binance USD-M 1h/4h candles and funding events, builds causal features, and writes a holdout/forward-fold/cost-stress report:

`python -m research.run_v12_research --symbol BTCUSDT --start 2025-01-01 --end 2026-01-01 --output-dir reports/v12/BTCUSDT`

Run separately for each symbol. The script is a research workflow, not an approval or live execution command.

## Pipeline
1. Download and validate continuous closed 1h and 4h candle CSVs.
2. Optionally download historical funding settlements from the same venue and symbol.
3. Build causal features:
   `python -m research.v12_features --symbol BTCUSDT --candles-1h data/BTCUSDT_1h.csv --candles-4h data/BTCUSDT_4h.csv --funding-csv data/BTCUSDT_funding.csv --output data/BTCUSDT_v12_features.csv`
4. Run chronological holdout, forward-fold, and cost-stress evaluation (this calls the next-open simulator internally):
   `python -m research.v12_validation --csv data/BTCUSDT_v12_features.csv --funding-csv data/BTCUSDT_funding.csv --symbol BTCUSDT --holdout-fraction 0.2 --folds 4 --fee-rate 0.0005 --slippage-rate 0.0002 --output reports/BTCUSDT_v12_validation.json`

Repeat per symbol. Do not mix symbols or exchanges in one input file. Funding input is optional, but when absent the model does not receive funding information and the report must not be interpreted as a funding-aware result.

## Model components
- Momentum + funding/crowding context
- Trend / MACD histogram + breakout and retest
- Indicator confluence: higher-timeframe trend, RSI, volume ratio, VWAP, ADX and optional CVD
- Coin-specific profile and market-regime discount

## Simulator assumptions
- After TP1, the remaining position uses a 1.5 ATR trailing stop updated only after a candle closes; the new stop becomes active on the next candle.
- If the model produces an opposite-direction candidate at a candle close, the remaining position exits at the next candle open.
- A 72-bar (72-hour) maximum holding time exits at the next candle open if no earlier exit occurs.
- Signals use the current closed 1h candle; entry occurs at the next 1h candle open.
- Initial stop is 1.5 ATR; TP1 is 2R for 50% of size; TP2 is 3R for the remaining size.
- Stop is assumed to execute before targets if both stop and target are touched in one OHLC candle.
- Fee and slippage rates are charged on entry and each exit.
- Position risk is capped at 0.5% of current equity by default and notional leverage at 3x.
- When historical funding events are supplied, cash flows are applied as `-direction × open quantity × settlement mark price × funding rate`. Funding exactly at entry time is excluded as a boundary ambiguity; an event at the exit candle open is applied before OHLC exit handling. Endpoint mark prices have not been reconciled against an account ledger.
- Historical OI change and spread are not currently available in the feature builder and remain unknown/empty, not fabricated. A causal CVD proxy is now available when 1h Binance kline taker-buy base-volume fields are present: 12h/24h normalized aggressor-volume delta. This is not tick-level CVD; inspect taker-flow and CVD feature coverage before interpreting any CVD-ON result.

## Calibration and drawdown accounting
- The validation report includes a component ablation study: it removes one of the four ensemble modules at a time, renormalizes the remaining weights, and compares fixed per-coin default thresholds on the same expanding pre-holdout windows. This is diagnostic only; ablation variants are not separately threshold-tuned.
- A separate per-coin weight-profile experiment selects among `default`, `trend_light`, and `trend_off` using training data only, then evaluates the selected profile on the next chronological walk-forward window. It is research-only and does not alter the live dashboard weights.
- The validation runner tests score thresholds and an optional short-side RSI exhaustion floor (disabled vs 30) on the training partition only. A candidate is selected only when it has at least 20 training trades, PF > 1, positive net return, and max drawdown <= 15%; if no candidate qualifies, default settings are retained.
- The final chronological holdout is never used to choose thresholds. The calibrated holdout is reported separately from the default holdout. Expanding-window walk-forward folds recalibrate on earlier data only and test on the next contiguous window.
- Maximum drawdown is computed from candle-close mark-to-market equity while positions are open, not only from closed-trade balances. A holdout with fewer than 20 trades is explicitly marked insufficient for screening.
- These are initial screening gates, not proof of an edge; 20 trades is not enough for statistical confidence and must be followed by larger sample / paper-trading checks.

## Independent five-coin CVD matrix

Use `research/V12_CVD_INDEPENDENT_VALIDATION_PLAN.md` for the frozen experiment protocol. The matrix runner compares CVD ON/OFF on development walk-forward folds for LONG and SHORT independently, then opens the fixed forward holdout only for the selected symbol/direction/CVD arm:

```bash
python -m research.run_v12_matrix \
  --symbols BTCUSDT ETHUSDT SOLUSDT AVAXUSDT XRPUSDT \
  --start 2025-10-01 --holdout-start 2026-10-01 --end 2026-11-01 \
  --output-dir reports/v12-independent-matrix
```

The reviewed pre-2026-10-01 data is development data, not an untouched final holdout. The fixed forward window may initially be too short to meet the minimum trade count; in that case the result remains insufficient and must be accumulated without tuning the frozen candidate. The validator includes an exact 0.30% round-trip cost scenario (0.05% fee plus 0.10% slippage per side). Funding timestamps/mark-price provenance are audited, but account cashflow reconciliation is still not established by timestamp alignment alone.

The matrix report is research-only. It does not promote models, send exchange orders, or authorize deployment.

## Validation gates
The feature builder is causal but still needs review against independent fixtures. Before model promotion, require:
- Verified source lineage and continuous candle timestamps
- Per-coin chronological holdout and rolling walk-forward results
- Fee/slippage stress and historical funding cash-flow reconciliation
- Long and short results reported separately
- Adequate trade count, parameter stability, and drawdown review
- Paper trading with replay parity
- An explicit, auditable approval record

A successful unit test or a positive backtest on one sample is not proof of future profitability.
