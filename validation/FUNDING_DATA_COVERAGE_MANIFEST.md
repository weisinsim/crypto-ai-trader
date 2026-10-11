# Funding Data Coverage Manifest

This manifest template must be filled for every symbol before its Funding-adjusted backtest is interpreted.

| Field | Required value |
|---|---|
| Exchange and contract | Exact venue and contract type (e.g. Binance USD-M linear perpetual) |
| Symbol | Exact exchange symbol |
| Requested UTC range | Inclusive start, exclusive end, ISO-8601 UTC |
| Candle range | First/last open timestamp and bar count |
| Funding source | Exact REST endpoint or archive URL per month |
| Funding rows | Count per month and total |
| Expected cadence | Derived from actual contract history; 8h is only a diagnostic reference |
| Missing months/pages | List every 404, empty page, failed request, or skipped period; never silently assume complete coverage |
| Mark price source counts | Separate exchange mark-price observations, near-timestamp mark proxies, prior trade-candle close proxies |
| Duplicate/conflicting rows | Count and resolution; unexplained duplicates fail the gate |
| Input hashes | SHA-256 of raw archives and normalized CSVs |
| Audit output | Link to funding audit JSON and code commit |

## Mandatory failure conditions

- A requested month/page was skipped without being recorded.
- Any funding event is outside candle coverage or has invalid timestamp/rate/price.
- Symbol or contract identity cannot be established.
- A proxy mark price is represented as an exact exchange mark price.
- Coverage is claimed solely because the largest gap is below 24 hours.

A maximum-gap check is only a screening diagnostic. It cannot prove completeness because an entire missing settlement can be hidden by adjacent events or variable funding intervals. Reconcile downloaded event timestamps against the authoritative source and publish monthly counts before promoting any backtest result.

Status stays RESEARCH until the manifest is populated and the raw-to-normalized transformation is reproducible.