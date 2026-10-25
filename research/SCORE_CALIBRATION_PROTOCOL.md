# Score calibration audit protocol (research only)

## Current limitation

The existing EMA-cross research backtest records side, entry/exit times, prices,
exit reason, costs, PnL and return, but it does not record a model confidence score
for each trade. Therefore its trade log cannot be used to validate the dashboard's
`confirmed_score` threshold. Do not map EMA parameters, PnL, or an arbitrary indicator
to that score after the fact; that would create a misleading calibration result.

## Required input schema

The helper `research.score_calibration.summarize_score_calibration` expects one
row per completed trade:

- `symbol`: canonical market symbol, e.g. `BTCUSDT`
- `score`: score generated at entry time, frozen before the trade outcome is known,
  in the range 0–100
- `net_return`: decimal return on the trade's defined risk/equity basis, after
  entry and exit fees, slippage and applicable historical funding
- `exit_ts`: exit candle timestamp in milliseconds

The score must be captured at the time the signal is produced, not reconstructed
from future data. A stable signal/trade identifier should be retained in the
upstream event log to join score, execution assumptions and outcome without
duplicating or dropping trades.

## Required evaluation design

1. Freeze the score definition and threshold on training/validation data.
2. Keep a strictly chronological, untouched holdout; do not tune on it.
3. For BTC, ETH, SOL, AVAX and XRP, report score bands 70–79, 80–89 and 90–100
   separately, and report long/short direction separately where sample sizes permit.
4. Include all completed trades and disclose rejected/filtered signals so that
   selective reporting cannot inflate results.
5. Report sample count, win rate, mean net expectancy, profit factor, maximum
   drawdown, fee/slippage/funding assumptions and the exact date range.
6. Mark any bucket with fewer than 30 trades as `INSUFFICIENT_SAMPLE`. Thirty is
   only a minimum reporting gate, not proof of statistical significance.
7. Repeat over multiple walk-forward folds and cost-stress assumptions. Do not
   call the score a probability unless a separate calibration method is fitted on
   prior data and validated on independent data.

## Safety boundary

This helper is a report utility only. It neither retrieves market data nor executes
orders. No result from this report enables live trading. Merging, deployment and
live execution require separate explicit authorization and independent risk gates.
