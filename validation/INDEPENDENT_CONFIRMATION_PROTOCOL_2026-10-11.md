# Next Research Gate: Independent Confirmation Protocol

Date: 2026-10-11  
Repository: `weisinsim/crypto-ai-trader`  
Status: **PROTOCOL ONLY — no new performance claim**

## Current evidence

- Historical baseline research #91 completed across five symbols: https://github.com/weisinsim/crypto-ai-trader/actions/runs/38127055306
- Python validation #198 and audit tests #263 passed on the review commit: https://github.com/weisinsim/crypto-ai-trader/actions/runs/38127711642 and https://github.com/weisinsim/crypto-ai-trader/actions/runs/38127711656
- The adaptive EMA grid selected no candidate for BTC, SOL, XRP, or AVAX. ETH selected a candidate but lost 25.5046% in the approximate 0.30% round-trip stress holdout, with PF 0.5296 and 27.195% maximum drawdown.
- The separate BTC breakout/retest report had 24 short trades and +7.6080% base holdout return, but only +0.0048% at 0.30% round-trip stress and -7.0851% at 0.46%.
- The separate AVAX mean-reversion report had +11.1196% base holdout return, +4.0647% at 0.30% cost, and -2.5496% at 0.46%.
- These strategy reports share historical data and have already been inspected. Their holdout results are consumed and must not be reused to tune parameters.

## Confirmation rules

1. Freeze BTC breakout/retest and AVAX mean-reversion parameters exactly as reported. No further selection based on their inspected holdout outcomes.
2. Source a new, later, untouched chronological window with sufficient 1H bars, or start prospective paper trading. Keep contract, fee tier, slippage assumptions, funding series and candle interval fixed and versioned.
3. Before computing results, reconcile every funding event to authoritative settlement timestamp/rate/mark-price data. Quantify proxy mark-price rows and reject unexplained timestamps.
4. Use conservative execution assumptions: stop is assumed first if stop and target are both hit in one OHLC bar; document settlement/exit ordering; avoid treating hourly candles as proof of intrabar sequence.
5. Predeclare gates before opening confirmation results:
   - at least 30 closed trades for initial statistical screening (prefer 50+ for more informative inference);
   - net return positive after base costs and historical funding;
   - profit factor > 1.15 after base costs;
   - non-negative net return at 0.30% round-trip stress;
   - maximum drawdown no greater than 10% for initial screening;
   - no single fold contributes more than 50% of total positive PnL;
   - results remain directionally stable across at least two chronological subperiods.
6. If any gate fails, retain Research/Reject and do not optimize against the confirmation window. A failed candidate requires a new hypothesis and a future untouched confirmation segment.

## Operational decision

Do not deploy or automate live orders. The immediate next step is to acquire/prepare an untouched confirmation data window or run a prospective paper-trading observation period, not to expand the parameter grid. Green CI is a software correctness signal, not a profitability signal.

## Addendum — run #160 and latest CI (2026-10-12)

Latest five-asset research run: https://github.com/weisinsim/crypto-ai-trader/actions/runs/38222947432  
Latest Python validation: https://github.com/weisinsim/crypto-ai-trader/actions/runs/38222947406  
Latest research test suite: https://github.com/weisinsim/crypto-ai-trader/actions/runs/38222947658  
Follow-up documentation commit: `9c711ffe5b28d0c4287f64d17bb1384e7a4d1434`.

All five research matrix jobs completed and their funding audit gates passed. All five assets nevertheless failed the predeclared strategy cost-robustness gate for the selected breakout/retest candidate; mean-reversion candidates also failed the severe 0.46% round-trip cost gate. BTC breakout/retest returned +7.6080% at base cost, approximately flat at 0.30% (+0.0048%), and -7.0851% at 0.46%. AVAX mean reversion returned +11.1196% at base cost, +4.0647% at 0.30%, and -2.5496% at 0.46%; its second holdout half was negative under both stress levels.

The inspected run #160 holdout is now consumed evidence and must not be used to select, tune, or rank parameters. Do not carry forward the earlier candidate wording as a promotion recommendation. Any follow-up version is a new hypothesis and requires a new, untouched chronological confirmation period.

### Immediate decision

- **No strategy is approved for live trading.**
- Keep all five symbols and strategy families at RESEARCH ONLY / NOT APPROVED.
- The recent green CI runs establish software/test completion only; they do not change this decision.
- Prefer prospective paper trading with frozen signals and logged no-trade decisions. The recent completed history window ends at the last fully closed UTC month; a few days of newer candles alone are not enough to establish a statistically informative confirmation sample.
- Do not expand parameter grids until a new confirmation window and its pass/fail thresholds are fixed in advance.

### Addendum — funding-source gate (2026-10-12)

**Status: BLOCKED pending source-level verification.** The latest CI checks validate repository code and audit tests, but do not prove that the required historical funding files can be downloaded and reconciled from the execution environment. The historical REST endpoint has been inaccessible in some CI regions, and archive availability/content must be checked directly before the independent confirmation run.

Required release checks before using funding data:

1. Record the exact requested symbol and UTC interval, the official URL, HTTP status, retrieval timestamp, and file checksum for every monthly/daily archive.
2. Validate the downloaded payload is a ZIP containing a parseable CSV with the expected schema; an HTTP 200 response alone is not sufficient.
3. Reconcile funding timestamps and rates against the official settlement records, including duplicate, missing, and unexpected settlement intervals.
4. Join settlement mark prices by exact timestamp where available. Label any 8h mark-price-kline proxy explicitly; do not present it as the actual settlement mark price.
5. Fail closed if an archive is missing, malformed, or incomplete. Do not synthesize funding records, silently skip unavailable dates, or treat an unsupported endpoint as a valid fallback.
6. Save an auditable manifest and pass/fail report before running frozen BTC/AVAX confirmation.

Until these checks are evidenced by a successful retrieval and reconciliation artifact, BTC/AVAX independent confirmation remains **NOT RUN / NOT APPROVED**. A green CI run does not clear this data gate.
