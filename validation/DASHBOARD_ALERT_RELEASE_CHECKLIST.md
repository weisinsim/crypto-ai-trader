# Dashboard Alert Release Checklist

Scope: basic entry-signal cards inside the dashboard only. This is a notification feature, not an order-execution feature.

## Safety contract

- [ ] Live dashboard health is explicitly `LIVE` and its heartbeat is no older than 120 seconds.
- [ ] Each displayed candidate has a canonical USDT symbol, `LONG` or `SHORT` side, finite positive entry/stop/TP1/TP2, and correct directional price geometry.
- [ ] Signal candle time is numeric, not in the future, and no older than 90 minutes.
- [ ] Each candidate has `notification_only: true` and `auto_trade_authorized: false`.
- [ ] Models not explicitly released as production are displayed as research-only / not live-approved.
- [ ] API error, malformed JSON, stale heartbeat, or invalid payload results in no displayed candidates.
- [ ] No dashboard action calls an exchange order endpoint. Live order execution remains disabled.

## Automated evidence

The CI suite should pass before deployment:
- Python validation workflow
- Validation audit tests workflow
- Existing server-side `/api/chatgpt/alerts` tests
- Dashboard static contract tests for price geometry, timestamp freshness and fail-closed labels

A passing CI run checks the code contract; it does not prove the deployed dashboard is running this commit or that market data is currently healthy.

## Manual smoke test after deployment

1. Open the deployed dashboard and verify the displayed build corresponds to the reviewed commit.
2. With market status not `LIVE`, confirm the panel shows no candidates.
3. With no fresh candidate, confirm the panel says there are no valid alerts.
4. If a valid candidate exists, verify symbol, side, entry, SL, TP1, TP2, signal candle time, and research-only status.
5. Temporarily make the alert endpoint unavailable in a non-production environment; confirm the UI fails closed and displays no stale card.
6. Refresh the page and simulate a WebSocket reconnect; confirm the panel does not retain an old candidate as current.
7. Verify no order is submitted and no exchange trading credentials are needed for this dashboard-only feature.

## Release decision

Do not treat a passing workflow as deployment approval. A person with deployment access must publish the reviewed branch/commit and complete the smoke test above. Do not merge or deploy automatically as part of this checklist.
