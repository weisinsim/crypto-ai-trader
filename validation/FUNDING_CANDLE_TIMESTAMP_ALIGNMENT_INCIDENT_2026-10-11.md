# Funding/Candle Timestamp Alignment Incident — 2026-10-11

Status: **BLOCKED — RESEARCH ONLY**

## Observed failure

- Run #85 failed for all five assets after historical funding was passed into the baseline runner.
- Run #86 failed again after allowing a maximum 1,000 ms offset.
- Example error from the BTC job: `funding timestamp 1781366400002 is 18000002ms from the nearest candle open (>1000ms)`.
- Unit tests #189 and #190 passed, but they only establish behavior on synthetic timestamps; they do not establish real-data timestamp compatibility.

## What this does and does not mean

This is a timestamp/provenance alignment failure, not a strategy performance result. Do not interpret failed runs as negative PnL, and do not use the previous unfunded baseline as a funding-adjusted result.

A 5-hour offset is far beyond a serialization/millisecond tolerance. It must not be repaired by snapping to the nearest hourly candle or applying an arbitrary timezone shift without proving the source convention.

## Required diagnostics before changing the simulator

1. For each asset, print the first/last 10 candle timestamps and first/last 10 funding timestamps as UTC ISO-8601, plus each timestamp modulo 3,600,000.
2. Report candle timestamp range, funding timestamp range, counts, unique deltas to nearest candle open, and count of events outside the candle range.
3. Verify the raw CSV source: Binance Vision kline archive open-time field and fundingRate archive fundingTime field must both be interpreted as UTC milliseconds. Preserve the raw values and source URL/hash.
4. Check that the downloaded candle rows really use the same timestamp convention as the funding rows; do not infer timezone from local runner settings.
5. Only after the root cause is identified, choose a settlement model. If event times are not exactly at candle opens, the OHLC-only simulator must explicitly handle intrabar funding-versus-exit ambiguity or conservatively exclude affected trades. Do not silently shift events.

## Acceptance gate

- Timestamp grids and UTC representations reconcile with source documentation.
- No unexplained multi-hour offsets.
- Event coverage, mark-price provenance, and boundary eligibility are separately audited.
- Synthetic unit tests and the complete five-asset workflow pass.
- Reports clearly identify whether funding is fully reconciled, proxied, or excluded.

Until then, no funding-adjusted profitability claims and no production promotion.
