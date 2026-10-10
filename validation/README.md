# Historical signal replay and exit-parameter validation

## Status

Research tooling only. The code replay is not yet parity-validated against live signal outputs, and no profitable model has been established. Do not promote a model or trade from these outputs alone.

## 1. Download hourly and four-hour candles

Use a fixed research window and the same venue (Binance USD-M Futures). End date is exclusive and interpreted as UTC when no timezone is supplied.

```bash
python validation/download_futures_klines.py --symbols BTCUSDT ETHUSDT SOLUSDT AVAXUSDT XRPUSDT --interval 1h --start 2024-10-01 --end 2026-10-01 --out-dir data/historical
python validation/download_futures_klines.py --symbols BTCUSDT ETHUSDT SOLUSDT AVAXUSDT XRPUSDT --interval 4h --start 2024-10-01 --end 2026-10-01 --out-dir data/historical
```

The downloader uses public Binance USD-M Futures endpoints; no API key is needed. Check that the symbols and requested history exist for the chosen venue. Keep the raw data and download manifests under versioned research artifacts when practical.

## 2. Audit data integrity

```bash
python validation/audit_historical_data.py --data-dir data/historical
```

Review every file for missing files, duplicate timestamps, invalid OHLC values and candle gaps. A basic integrity pass does not guarantee complete history; expected row counts and the chosen date boundaries must also be checked.

## 3. Replay asset-specific signals

```bash
python validation/replay_dashboard_signals.py --data-dir data/historical --out-dir data/replay
```

This creates one signal CSV per coin and a manifest. It is a first-pass translation of dashboard rules into a causal historical replay, not a claim that the same signals were emitted in production. Before interpreting performance, test timestamp alignment, indicator parity, 4h close availability and signal-by-signal parity with the live implementation. The current dashboard includes approximations and refreshes that can use the latest in-progress bar; replay must not.

## 4. Compare 2.5R vs 3R exits

```bash
python validation/exit_parameter_audit.py --candles data/historical/BTCUSDT_1h.csv --signals data/replay/BTCUSDT_signals.csv --cost-bps 10 --max-hold 72 --out data/replay/BTCUSDT_exit_audit.json
```

Repeat for each coin. The signal file's `stop` column is used when present; otherwise the fallback ATR stop applies. Target distance is measured from entry using the actual entry-to-stop risk. The cost is a simplified round-trip bps assumption. Run cost sensitivity (for example 5, 10, 20, 30 bps) and do not choose a winner from a single cost assumption. The audit now fails closed if any adjacent candle timestamps do not match the expected interval cadence; for 4h data, pass `--interval 4h` explicitly.

## Input and execution assumptions

- Timestamps are ISO-8601 UTC close timestamps from the downloader. The signal entry is assumed at the close of that signal candle and exits are evaluated from the next candle onward.
- If stop and target are both touched in one OHLC candle, stop-first is assumed.
- Time exits use the final close only when the full configured holding window is present. Signals too close to a split/data boundary to complete that window without a stop/target hit are excluded from the trade metrics and counted as signals without a trade result; they are not assigned an artificially short time exit.
- The current harness does not model funding payments, partial exits, trailing stops, market impact, liquidation, order-book fills, overlapping positions or portfolio-wide exposure.
- Missing signal/candle timestamp matches are skipped; always inspect the audit output's matched signal count.
- A chronological split is only a first guardrail. Preserve a final untouched holdout and run walk-forward folds before making claims.

## Promotion gate

No model should be described as profitable unless data continuity is reviewed, the replay passes automated indicator and signal parity tests, realistic costs/funding are included, a sufficiently large sample exists, and the result survives untouched holdout, walk-forward, regime, and stress tests. Report trades, win rate, net R, expectancy, profit factor, max drawdown, fee sensitivity and fold-by-fold results. Win rate alone is not a decision metric.


## 5. Batch multi-asset cost sensitivity

After all candle files pass the integrity audit and signal replay has been reviewed, run the batch comparison:

```bash
python validation/run_multi_asset_exit_audit.py --data-dir data/historical --signals-dir data/replay --out-dir data/exit_audits --min-trades 30
```

This runs 2.5R and 3R exit comparisons at 5, 10, 20 and 30 bps round-trip cost assumptions for each available coin. It writes per-coin JSON audits and a batch manifest. A result is labeled `INSUFFICIENT_SAMPLE` unless every exit target and both signal-level and single-position modes meet the configured minimum holdout trade count; this label is not a pass. The batch runner does not repair missing inputs and does not validate signal parity or funding/slippage realism.
