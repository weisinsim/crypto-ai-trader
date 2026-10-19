# ChatGPT alert feed

The dashboard exposes a read-only JSON endpoint at `/api/chatgpt/alerts`.

It returns a JSON object with `status`, `count`, and an `alerts` array containing only fresh, confirmed directional candidates with valid entry/stop/target geometry. Each alert includes `event_id`, `symbol`, `side`, `signal_candle_ms`, `analysis_ts`, `entry`, `stop_loss`, `take_profit_1`, `take_profit_2`, score/model metadata, `research_only`, `notification_only: true`, and `auto_trade_authorized: false`. The feed fails closed unless market status is `LIVE` and its heartbeat was updated within the last 120 seconds; stale, future, or malformed heartbeat timestamps suppress all alerts. It does not send push notifications and does not place orders. An empty `alerts` array means no eligible fresh candidate was found or the market-health gate suppressed candidates. The dashboard independently hides alerts unless the feed status is `LIVE` and each item explicitly passes the notification-only/no-trade authorization checks.

## Make it available to a ChatGPT scheduled task

1. Deploy the dashboard so the endpoint is reachable over HTTPS from the environment in which the ChatGPT task runs.
2. Open the endpoint in a browser and verify it returns JSON with `feed: crypto-ai-trader-chatgpt-alerts-v1`.
3. In ChatGPT, create a scheduled task that checks this exact endpoint on the desired cadence, summarizes only new items in the `alerts` array, and says clearly when there are no fresh alerts.
4. Do not expose API keys, exchange credentials, or private dashboard controls in a public endpoint. If the dashboard is not public, use a supported authenticated connector or secure feed proxy; do not disable authentication merely to make polling work.

Suggested task instruction (replace the placeholder with the deployed HTTPS endpoint):

> Check [DEPLOYED_HTTPS_URL]/api/chatgpt/alerts. Report only fresh confirmed alerts in the returned JSON. For each alert include symbol, LONG/SHORT, entry, stop loss, TP1, TP2, score, model status, and whether it is research-only. If the feed is unavailable, stale, malformed, or `status` is not `LIVE`, report that the feed could not safely confirm signals; do not infer a signal. If `alerts` is empty, say there are no fresh confirmed entry alerts. This is notification-only and must never place or recommend an unvalidated automatic order.

## Important limitation

A ChatGPT scheduled task cannot automatically receive events from this repository just because the endpoint exists. The task needs an HTTPS endpoint it can access and must be configured by the user in ChatGPT. The endpoint has not been verified against a live deployment yet.
