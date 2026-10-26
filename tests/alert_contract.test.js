const test = require("node:test");
const assert = require("node:assert/strict");
const { validateAlertPayload } = require("../static/alert_contract.js");

const NOW_MS = 1_800_000_000_000;
const NOW_SECONDS = NOW_MS / 1000;

function validAlert(overrides = {}) {
  return {
    event_id: "0123456789abcdef01234567",
    symbol: "BTCUSDT",
    side: "LONG",
    signal_candle_ms: NOW_MS - 60_000,
    analysis_ts: NOW_SECONDS - 30,
    entry: 100,
    stop_loss: 98,
    take_profit_1: 103,
    take_profit_2: 106,
    score: 70,
    score_is_probability: false,
    minimum_candidate_score: 70,
    entry_candidate: true,
    model_status: "RESEARCH",
    research_only: true,
    notification_only: true,
    auto_trade_authorized: false,
    ...overrides,
  };
}

function validPayload(alerts = [validAlert()], overrides = {}) {
  return {
    generated_at: NOW_SECONDS - 1,
    status: "LIVE",
    alerts,
    notification_only: true,
    auto_trade_authorized: false,
    ...overrides,
  };
}

test("accepts a fresh, valid notification-only candidate at the score threshold", () => {
  const result = validateAlertPayload(validPayload(), NOW_MS);
  assert.equal(result.safe, true);
  assert.equal(result.alerts.length, 1);
  assert.equal(result.alerts[0].score, 70);
});

test("fails closed for stale envelopes or any execution-authorized envelope", () => {
  assert.deepEqual(
    validateAlertPayload(validPayload([], { generated_at: NOW_SECONDS - 121 }), NOW_MS),
    { safe: false, alerts: [] },
  );
  assert.deepEqual(
    validateAlertPayload(validPayload([], { auto_trade_authorized: true }), NOW_MS),
    { safe: false, alerts: [] },
  );
});

test("rejects low scores and score metadata that contradicts the contract", () => {
  const candidates = [
    validAlert({ score: 69.99 }),
    validAlert({ score_is_probability: true }),
    validAlert({ entry_candidate: false }),
    validAlert({ minimum_candidate_score: 60 }),
  ];
  assert.equal(validateAlertPayload(validPayload(candidates), NOW_MS).alerts.length, 0);
});

test("rejects invalid stop/target geometry and stale/future timestamps", () => {
  const candidates = [
    validAlert({ stop_loss: 101 }),
    validAlert({ signal_candle_ms: NOW_MS + 1 }),
    validAlert({ analysis_ts: NOW_SECONDS + 1 }),
    validAlert({ signal_candle_ms: NOW_MS - 91 * 60 * 1000 }),
  ];
  assert.equal(validateAlertPayload(validPayload(candidates), NOW_MS).alerts.length, 0);
});

test("deduplicates repeated event IDs in one payload", () => {
  const result = validateAlertPayload(
    validPayload([validAlert(), validAlert({ symbol: "ETHUSDT" })]),
    NOW_MS,
  );
  assert.equal(result.alerts.length, 1);
});

test("rejects malformed IDs, symbols, non-finite values, and invalid status flags", () => {
  const candidates = [
    validAlert({ event_id: "not-a-valid-id" }),
    validAlert({ symbol: "btc/usdt" }),
    validAlert({ score: Number.NaN }),
    validAlert({ model_status: "UNKNOWN" }),
    validAlert({ research_only: false }),
  ];
  assert.equal(validateAlertPayload(validPayload(candidates), NOW_MS).alerts.length, 0);
});
