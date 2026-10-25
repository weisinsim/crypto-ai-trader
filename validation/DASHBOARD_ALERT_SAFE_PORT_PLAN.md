# Dashboard Alert Feature: Safe Port Plan

Status: planning artifact only. This branch is based on `main`; it does not add or enable alert endpoints.

## Goal

Port the read-only entry-signal notification panel from the research branch without importing unrelated model, backtest, or execution changes.

## Required server contract

- `GET /api/build-info` reports the exact deployed revision when available; unknown revision remains `null`.
- `auto_trade_enabled` is always `false` and mode remains `research-gated`.
- `GET /api/chatgpt/alerts` is read-only and never submits, queues, or authorizes an exchange order.
- Market status must be explicitly `LIVE`, with a finite, non-boolean heartbeat timestamp in the past no older than 120 seconds.
- Each candidate must have a fresh per-symbol ticker (finite positive price and valid timestamp no older than 120 seconds), a fresh signal candle and analysis timestamp, an allowed model status (`RESEARCH` or `PRODUCTION`), and valid finite positive entry/stop/TP1/TP2 geometry.
- Reject booleans, NaN, infinities, future timestamps, missing fields, stale data, unknown model states, and non-directional signals.

## Required dashboard contract

- Poll the read-only endpoint and render candidates only when the payload is structurally valid, fresh, explicitly `LIVE`, and marked `notification_only: true` and `auto_trade_authorized: false`.
- Render only canonical USDT symbols and `LONG`/`SHORT` sides with valid stop/target geometry.
- Invalid JSON, network errors, unknown states, and stale payloads must fail closed (show no actionable candidates).
- Display `RESEARCH` signals as research-only; do not imply they are approved for live trading.
- No exchange order endpoint or auto-trading switch may be added as part of this port.

## Port sequence

1. Extract the exact route logic and its minimal dependencies from the research branch; do not copy the research branch's entire `app.py`.
2. Add isolated server tests for every fail-closed condition above.
3. Port the dashboard panel and static contract tests as a separate, minimal change.
4. Run Python validation and validation-audit workflows on the isolated branch.
5. Review the diff and confirm no order-execution behavior was introduced.
6. After a separate deployment, verify `/api/build-info` matches the reviewed commit and perform the manual smoke test.

## Release gate

Do not deploy until the route and UI have been ported, CI passes, and a human reviews the minimal diff. Passing CI is not evidence of strategy profitability or of a deployed revision. Real-money execution remains disabled.
