# Notification and Testnet Soak Acceptance Plan

Status: **planned; not evidence of a completed soak**.

This checklist defines the evidence required before promoting the notification-only scanner or testnet execution supervisor. Run against one recorded commit and one recorded configuration. Never enable production order submission as part of these tests.

## 1. Run identity and evidence

Record before each run:
- Git commit SHA and CI run URLs.
- UTC start/end timestamps and operator.
- Symbols, candle interval, poll interval, max signal age, dashboard URL scheme/host (never credentials), and configuration hash.
- Testnet account identifier in redacted form; never store API secrets, signatures, or full account responses.
- Worker stdout/stderr, exception summaries, order/client-order IDs, exchange snapshots, and final reconciliation result.

## 2. Notification candidate scanner

- [ ] Fresh dashboard heartbeat and fresh closed candle: eligible candidate may be queued.
- [ ] Stale dashboard heartbeat: cycle is DEGRADED; no candidate is queued.
- [ ] Future candle timestamp, stale candle, or stale analysis timestamp: candidate is blocked.
- [ ] Invalid direction, model status, symbol, non-finite price, or invalid stop/target geometry: candidate is blocked.
- [ ] Repeated identical event: one pending outbox event only.
- [ ] Same event ID with changed payload: explicit conflict; never silently overwrite.
- [ ] Restart with pending event: event remains pending and is retrievable.
- [ ] Snapshot fetch timeout, malformed response, and per-symbol market-data error: cycle reports DEGRADED, not healthy.
- [ ] Confirm notification-only contract: `notification_only=true` and `auto_trade_authorized=false`; scanner/outbox cannot call order submission.

## 3. Delivery-channel contract

The scanner/outbox is not proof that a human notification was delivered. Before enabling a delivery adapter:
- [ ] Define one provider interface and an explicit receipt/acknowledgement contract.
- [ ] Test provider timeout, authentication rejection, rate limit, malformed response, and retryable server error.
- [ ] Make retries idempotent where the provider supports idempotency; otherwise document possible duplicate delivery.
- [ ] Mark an event DELIVERED only after a provider success/receipt satisfies the contract.
- [ ] Keep delivery credentials in environment/secret storage and redact them from logs.
- [ ] Demonstrate an end-to-end test notification and record its provider receipt. Until then, describe the system as queued/pending, not delivered.

## 4. Testnet order lifecycle (validation account only)

Use a dedicated Binance USD-M TESTNET account with minimal permissions and no production credentials.
- [ ] Preflight fails closed for invalid symbol, unavailable exchange filters, invalid quantity, insufficient minimum notional, stale signal, and risk-limit breach.
- [ ] Accepted entry is reconciled by client order ID before any retry after an ambiguous network response.
- [ ] Partial fill and full fill are reconciled against authoritative exchange position snapshots.
- [ ] Protective stop is confirmed reduce-only and quantity/side/trigger geometry match actual exposure.
- [ ] Take-profit placement failure, stop placement failure, and sibling-order cancellation failure each trigger a safe degraded/halt path.
- [ ] Disconnect/timeout during submit: query by client order ID; never blindly submit a second entry.
- [ ] Restart with open position, pending entry, orphan order, or missing protection: reconcile; if safe recovery cannot be proven, halt new entries and require operator review.
- [ ] Position quantity and open orders reconcile before a run is declared healthy.
- [ ] Repeated supervisor cycles do not duplicate entries or protective exits.
- [ ] Record testnet run duration and every injected failure; no claim of a soak until a real timed run is completed.

## 5. Release gate

Do not enable real-money execution unless all are true:
- [ ] CI passes on the exact candidate commit.
- [ ] Historical replay passes look-ahead, candle-boundary, missing-data, and cost/funding audits.
- [ ] Out-of-sample and walk-forward results include net expectancy, win/loss distribution, drawdown, and trade count by symbol/regime.
- [ ] Notification end-to-end receipt is demonstrated if alerts are part of the release.
- [ ] Testnet lifecycle and recovery evidence is archived and independently reviewed.
- [ ] Risk limits, kill switch, monitoring, credential scope, and rollback procedure are verified.
- [ ] Explicit operator approval is recorded.

A green CI run proves only the checks executed by that workflow. It does not prove profitability, live notification delivery, or live trading readiness.
