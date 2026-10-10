# Historical signal replay and exit-parameter validation

## Status

Research tooling only. The code replay is not yet parity-validated against live signal outputs, and no profitable model has been established. Do not promote a model or trade from these outputs alone.

## 1. Download hourly and four-hour candles

Use a fixed research window and the same venue (Binance USD-M Futures). End date is exclusive and interpreted as UTC when no timezone is supplied.

```bash
python validation/download_futures_klines.py --symbols BTCUSDT ETHUSDT SOLUSDT AVAXUSDT XRPUSDT --interval 1h --start 2024-10-01 --end 2026-10-01 --out-dir data/historical
python validation/download_futures_klines.py --symbols BTCUSDT ETHUSDT SOLUSDT AVAXUSDT XRPUSDT --interval 4h --start 2024-10-01 --end 2026-10-01 --out-dir data/historical
```

The candle downloader uses public Binance USD-M Futures endpoints; no API key is needed. Check that the symbols and requested history exist for the chosen venue. Keep the raw data and download manifests under versioned research artifacts when practical.

Download timestamped historical funding events separately:

```bash
python validation/download_funding_rates.py --symbols BTCUSDT ETHUSDT SOLUSDT AVAXUSDT XRPUSDT --start 2024-10-01 --end 2026-10-01 --out-dir data/funding
```

This writes `SYMBOL_funding.csv` with `timestamp,funding_rate`; funding rate is a decimal fraction and timestamp is the UTC funding event time.

Audit downloaded funding files before backtesting:

```bash
python validation/audit_funding_history.py data/funding/BTCUSDT_funding.csv data/funding/ETHUSDT_funding.csv data/funding/SOLUSDT_funding.csv data/funding/AVAXUSDT_funding.csv data/funding/XRPUSDT_funding.csv --max-gap-hours 24
```

The audit checks timezone-aware timestamps, finite rates, ordering, duplicates, and large event gaps. Funding intervals may vary by symbol and over time, so this is a diagnostic threshold rather than proof that every expected event exists. Confirm each file spans the requested research window before interpreting results.

## 2. Audit data integrity

```bash
python validation/audit_historical_data.py --data-dir data/historical
```

Review every file for missing files, duplicate timestamps, invalid OHLC values and candle gaps. A basic integrity pass does not guarantee complete history; expected row counts and the chosen date boundaries must also be checked.

## 3. Replay asset-specific signals

```bash
python validation/replay_dashboard_signals.py --data-dir data/historical --out-dir data/replay
```

This creates one signal CSV per coin and a manifest. It is a first-pass translation of dashboard rules into a causal historical replay, not a claim that the same signals were emitted in production. Before interpreting performance, test timestamp alignment, indicator parity, 4h close availability and signal-by-signal parity with the live implementation. The current dashboard includes approximations and refreshes that can use the latest in-progress bar; replay must not. The automated research workflow now runs `validation/audit_replay_signals.py` before exit audits; it checks signal timestamps against source candles, entry prices against the signal candle close, and stop/target direction. Passing this input audit still does not prove indicator or live-strategy parity.

## 4. Compare 2.5R vs 3R exits

```bash
python validation/exit_parameter_audit.py --candles data/historical/BTCUSDT_1h.csv --signals data/replay/BTCUSDT_signals.csv --funding-file data/funding/BTCUSDT_funding.csv --cost-bps 10 --max-hold 72 --out data/replay/BTCUSDT_exit_audit.json
```

Repeat for each coin. The signal file's `stop` column is used when present; otherwise the fallback ATR stop applies. Target distance is measured from entry using the actual entry-to-stop risk. The cost is a simplified round-trip bps assumption. Run cost sensitivity (for example 5, 10, 20, 30 bps) and do not choose a winner from a single cost assumption. The audit now fails closed if any adjacent candle timestamps do not match the expected interval cadence; for 4h data, pass `--interval 4h` explicitly. Use `--funding-file` to apply timestamped funding events. Positive funding is charged to longs and credited to shorts; negative funding reverses the direction. Events at entry are excluded and events through the exit timestamp are included. If no file is supplied, the optional `--funding-bps-per-8h` constant adverse-cost sensitivity is only a stress-test proxy, not actual historical funding. The audit fails closed if funding coverage exceeds `--funding-max-gap-hours` (default 24 hours, or 24 candle intervals, whichever is larger) at either edge or if an internal funding-event gap exceeds that threshold. This is a coarse completeness gate, not proof that every expected event exists; inspect symbol-specific funding cadence and exchange history before interpreting results. Missing events could otherwise understate costs.

## Input and execution assumptions

- Timestamps are ISO-8601 UTC close timestamps from the downloader. The signal entry is assumed at the close of that signal candle and exits are evaluated from the next candle onward.
- If stop and target are both touched in one OHLC candle, stop-first is assumed. For stop gaps, the fill is modeled at the candle open when it is worse than the stop price; this is a conservative OHLC approximation, not a full execution simulator. Target fills are capped at the target price.
- Time exits use the final close only when the full configured holding window is present. Signals too close to a split/data boundary to complete that window without a stop/target hit are excluded from the trade metrics and counted as signals without a trade result; they are not assigned an artificially short time exit.
- Historical funding events are modeled when `--funding-file` is supplied; without it, only the optional constant-rate stress proxy is used. Partial exits, trailing stops, market impact, liquidation, order-book fills and portfolio-wide exposure are not modeled by this exit audit.
- Missing signal/candle timestamp matches are skipped; always inspect the audit output's matched signal count.
- A chronological split is only a first guardrail. Preserve a final untouched holdout and run walk-forward folds before making claims.

## Promotion gate

No model should be described as profitable unless data continuity is reviewed, the replay passes automated indicator and signal parity tests, realistic costs/funding are included, a sufficiently large sample exists, and the result survives untouched holdout, walk-forward, regime, and stress tests. Report trades, win rate, net R, expectancy, profit factor, max drawdown, fee sensitivity and fold-by-fold results. Win rate alone is not a decision metric.


## 5. Batch multi-asset cost sensitivity

After all candle files pass the integrity audit and signal replay has been reviewed, run the batch comparison:

```bash
python validation/run_multi_asset_exit_audit.py --data-dir data/historical --signals-dir data/replay --funding-dir data/funding --out-dir data/exit_audits --min-trades 30
```

This runs 2.5R and 3R exit comparisons at 5, 10, 20 and 30 bps round-trip cost assumptions for each available coin. It writes per-coin JSON audits and a batch manifest. A result is labeled `INSUFFICIENT_SAMPLE` unless every exit target and both signal-level and single-position modes meet the configured minimum holdout trade count; this label is not a pass. Historical funding is required by default for batch audits; use `--allow-missing-funding` only for explicitly unadjusted research runs. Before running exit comparisons, the batch runner now rejects funding files that fail the basic history audit (timezone-aware timestamps, finite rates, ordering, duplicates, and gaps above the configured 24-hour diagnostic threshold). When a funding file passes that gate, the child audit additionally checks its coverage against the candles and applies timestamped side-aware funding cashflows. Passing these checks is not proof that the requested start/end window is complete; inspect the first/last timestamps and symbol-specific funding cadence. The batch runner does not repair missing inputs and does not validate signal parity or market-impact/slippage realism.


## 6. Automated historical research workflow

The repository includes a manual GitHub Actions workflow at `.github/workflows/historical-research-data.yml`. In GitHub, open **Actions → Historical multi-asset research → Run workflow**, choose the UTC inclusive start date and exclusive end date, and start the run. It downloads the public Binance USD-M Futures candles and funding history, gates replay/backtesting on basic data-integrity audits, then uploads raw inputs and reports as a 30-day artifact.

A green workflow run means the pipeline completed; it does **not** mean a profitable model was found. Inspect the uploaded candle/funding audits and per-symbol batch manifest. Symbols marked `INSUFFICIENT_DATA`, `FUNDING_INTEGRITY_FAILED`, or `INSUFFICIENT_SAMPLE` are not validated candidates. The current replay is explicitly an approximation of dashboard logic; signal parity with production must be established before performance results can be promoted.
