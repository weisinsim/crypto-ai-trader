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
   `python -m research.v12_validation --csv data/BTCUSDT_v12_features.csv --symbol BTCUSDT --holdout-fraction 0.2 --folds 4 --fee-rate 0.0005 --slippage-rate 0.0002 --output reports/BTCUSDT_v12_validation.json`

Repeat per symbol. Do not mix symbols or exchanges in one input file. Funding input is optional, but when absent the model does not receive funding information and the report must not be interpreted as a funding-aware result.

## Model components
- Momentum + funding/crowding context
- Trend / MACD histogram + breakout and retest
- Indicator confluence: higher-timeframe trend, RSI, volume ratio, VWAP, ADX and optional CVD
- Coin-specific profile and market-regime discount

## Simulator assumptions
- Signals use the current closed 1h candle; entry occurs at the next 1h candle open.
- Initial stop is 1.5 ATR; TP1 is 2R for 50% of size; TP2 is 3R for the remaining size.
- Stop is assumed to execute before targets if both stop and target are touched in one OHLC candle.
- Fee and slippage rates are charged on entry and each exit.
- Position risk is capped at 0.5% of current equity by default and notional leverage at 3x.
- Funding cash flows are not yet applied to simulator PnL. Historical funding is only passed as a signal feature; this limitation must be resolved before calling the backtest a complete net-return result.
- Historical OI change, CVD, and spread are not currently available in the feature builder and are explicitly left unknown/empty, not fabricated.

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
