# Notification Delivery Contract

Status: interface contract only; no provider integration or end-to-end delivery is implied.

## Event lifecycle

- `PENDING`: candidate persisted; no delivery has been proven.
- `SENDING`: a delivery attempt has begun, if the adapter implements an attempt ledger.
- `DELIVERED`: only after the provider's documented success response or receipt has been validated.
- `RETRYABLE_FAILURE`: transient timeout, rate limit, or server error; retry under bounded policy.
- `PERMANENT_FAILURE`: authentication/authorization failure or invalid destination; do not spin-retry.
- `UNKNOWN`: request outcome cannot be determined. Query provider status if supported; otherwise avoid unbounded blind retries and disclose duplicate-delivery risk.

## Required adapter interface

The adapter should accept an immutable event ID and payload, and return a structured result containing:
- status from the lifecycle above;
- provider name and provider message/receipt ID when supplied;
- attempt timestamp and bounded, sanitized error category;
- retryable boolean;
- idempotency key used, if supported by provider.

Never log credentials, authorization headers, signed URLs, or full sensitive provider responses.

## Safety invariants

1. The scanner and outbox must not import or invoke exchange order-submission functions.
2. An outbox record is not a delivered notification.
3. Marking delivered must follow a validated provider acknowledgement, not merely a successful local function return.
4. Retries must be bounded and respect provider rate limits.
5. Use provider-supported idempotency keys where available; if unavailable, document at-least-once duplicate risk after ambiguous timeouts.
6. Invalid destination or credential failure must be visible as an operational alert and must not be reported as healthy delivery.
7. Test the adapter with a fake provider before a real test notification; record a redacted receipt for the end-to-end test.
8. Do not use real exchange credentials or enable production trading as part of notification tests.

## Acceptance evidence

- Unit tests for success, timeout, rate limit, authentication failure, malformed acknowledgement, and duplicate retry.
- Durable persistence of event ID, attempts, final state, and provider receipt ID.
- A manual test notification with timestamp and redacted provider receipt.
- CI success on the exact commit.
- Separate evidence that mobile/device delivery was actually received; provider acceptance alone may not prove handset display.
