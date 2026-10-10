# Live-vs-replay parity gap report

## Finding confirmed in app.py
`analyze_symbol()` currently assigns `p = c1[-1]` and computes indicators over all rows in `series[s]["1h"]` and `series[s]["4h"]`. The ingestion path retains the latest kline, which may still be forming. Elsewhere the dashboard derives `last_closed_candle` from the prior 1h row, indicating the display has a separate closed-candle concept. Unless the feed layer is proven to remove in-progress bars before `analyze_symbol()` runs, this is a look-ahead / timing-parity defect for any claim that historical close-based replay reproduces live alerts.

## Required resolution before strategy-performance claims
1. Confirm the exchange's kline close flag / close time and exclude any bar that is not fully closed before computing decision indicators.
2. Apply the same closed-bar filtering to both 1h and 4h inputs.
3. Ensure `confirmed_signal_time`, entry, stop and target are all derived from the same closed 1h bar.
4. Add regression tests with a deliberately extreme in-progress candle and assert that the signal does not change until that candle closes.
5. Compare replay and live decision outputs on frozen fixtures for every asset family.

## Additional parity observations
- Replay indicator functions are intended to mirror the implementations in app.py (EMA, RSI, ATR, approximate ADX, VWAP, and 48-bar support/resistance), but automated numeric parity fixtures have not yet been added.
- Asset-specific signal rules and ETH suppression are represented in the replay tool, but the replay has not yet been demonstrated to match every live field and state transition.
- The replay intentionally records only LONG/SHORT entries, not WATCH states. The backtest is therefore an entry-candidate replay, not a full UI state replay.

## Gate
Until the closed-candle ingestion behavior is confirmed or fixed and indicator/signal parity tests pass, treat historical results as exploratory. Do not promote any asset model to production based on replay output alone.
