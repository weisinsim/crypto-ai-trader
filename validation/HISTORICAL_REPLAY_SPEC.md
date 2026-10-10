# Historical signal replay specification

## Purpose
Replay the current dashboard's asset-specific closed-candle signal logic against historical Binance USD-M Futures candles. This is a research artifact, not a profitability claim and not an execution system.

## Source rules mirrored from app.py
- Symbols: BTCUSDT, ETHUSDT, SOLUSDT, AVAXUSDT, XRPUSDT.
- Indicators: SMA-seeded EMA(20/50/200), Wilder RSI(14), ATR(14), current dashboard's ADX approximation, 48-bar VWAP and support/resistance.
- Only use 1h candles whose close time is <= the replay timestamp. 4h features may only use 4h candles that have already closed at that timestamp.
- ETH remains NO-TRADE (NO-PRODUCTION).
- XRP uses the frozen V27.2 LONG_ONLY conditions and kill switches.
- BTC/SOL/AVAX use their own family-specific RSI bands and stop/target multipliers from MODEL_LIBRARY / analyze_symbol.
- For each generated signal, record source timestamp, model version, all indicator inputs, entry, stop, target(s), score, direction, and why it fired or did not fire.

## Critical reproducibility caveats
1. Dashboard's current live code evaluates the last series item during periodic refreshes, so an in-progress candle can influence display values. The replay must instead use closed candles only.
2. Historical code replay does not prove historical executable signals were actually emitted in live operation.
3. The current exit audit supports a single fixed target, fixed ATR stop, time exit, and a simplified round-trip cost. It does not model partial take profits, trailing stops, funding, slippage tails, liquidation, or overlapping portfolio positions.
4. Model parameters and this replay spec must be frozen before inspecting the final chronological holdout.
5. No model should be promoted based only on win rate. Report trade count, net R, profit factor, drawdown, expectancy, fee sensitivity, and performance by regime.

## Promotion gate
Do not label a coin/model profitable unless the replay implementation has automated parity tests, signal artifacts are versioned, a chronological holdout is untouched during tuning, costs are included, and results survive sensitivity/stress tests. A small trade count or missing data is a failure to establish evidence, not a pass.
