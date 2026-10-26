(function (root) {
  "use strict";

  const SYMBOL_RE = /^[A-Z0-9]{2,20}USDT$/;
  const EVENT_ID_RE = /^[a-f0-9]{24}$/;
  const MIN_ENTRY_CANDIDATE_SCORE = 70;

  function validateAlertPayload(payload, nowMs) {
    const clockMs = Number.isFinite(nowMs) ? nowMs : Date.now();
    const nowSeconds = clockMs / 1000;
    const envelopeFresh = payload
      && Number.isFinite(payload.generated_at)
      && payload.generated_at > 0
      && payload.generated_at <= nowSeconds
      && nowSeconds - payload.generated_at <= 120;
    const safe = payload
      && payload.status === "LIVE"
      && envelopeFresh
      && payload.notification_only === true
      && payload.auto_trade_authorized === false
      && Array.isArray(payload.alerts);

    if (!safe) return { safe: false, alerts: [] };

    const seen = new Set();
    const alerts = [];
    for (const alert of payload.alerts) {
      if (!alert || typeof alert.symbol !== "string" || !SYMBOL_RE.test(alert.symbol)) continue;
      if (!["LONG", "SHORT"].includes(alert.side)) continue;
      if (!["RESEARCH", "PRODUCTION"].includes(alert.model_status)) continue;
      if (alert.notification_only !== true || alert.auto_trade_authorized !== false) continue;
      if (alert.research_only !== (alert.model_status !== "PRODUCTION")) continue;
      if (typeof alert.event_id !== "string" || !EVENT_ID_RE.test(alert.event_id) || seen.has(alert.event_id)) continue;
      if (!Number.isFinite(alert.score) || alert.score < MIN_ENTRY_CANDIDATE_SCORE || alert.score > 100) continue;
      if (alert.score_is_probability !== false || alert.entry_candidate !== true
          || alert.minimum_candidate_score !== MIN_ENTRY_CANDIDATE_SCORE) continue;
      if (!Number.isFinite(alert.signal_candle_ms) || alert.signal_candle_ms <= 0
          || alert.signal_candle_ms > clockMs || clockMs - alert.signal_candle_ms > 90 * 60 * 1000) continue;
      if (!Number.isFinite(alert.analysis_ts) || alert.analysis_ts <= 0
          || alert.analysis_ts > nowSeconds || nowSeconds - alert.analysis_ts > 90 * 60) continue;
      if (![alert.entry, alert.stop_loss, alert.take_profit_1, alert.take_profit_2]
          .every(value => typeof value === "number" && Number.isFinite(value) && value > 0)) continue;

      const geometry = alert.side === "LONG"
        ? alert.stop_loss < alert.entry && alert.entry < alert.take_profit_1 && alert.take_profit_1 <= alert.take_profit_2
        : alert.take_profit_2 <= alert.take_profit_1 && alert.take_profit_1 < alert.entry && alert.entry < alert.stop_loss;
      if (!geometry) continue;

      seen.add(alert.event_id);
      alerts.push(alert);
    }

    return { safe: true, alerts };
  }

  const api = Object.freeze({ validateAlertPayload });
  root.AlertContract = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
