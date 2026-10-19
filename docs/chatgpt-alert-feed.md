# ChatGPT alert feed

The dashboard exposes a read-only JSON endpoint at `/api/chatgpt/alerts`.

It returns a JSON object with `status`, `count`, and an `alerts` array containing only fresh, confirmed directional candidates with valid entry/stop/target geometry. Each alert includes `event_id`, `symbol`, `side`, `signal_candle_ms`, `analysis_ts`, `entry`, `stop_loss`, `take_profit_1`, `take_profit_2`, score/model metadata, `research_only`, `notification_only: true`, and `auto_trade_authorized: false`. The feed fails closed unless market status is `LIVE` and its heartbeat was updated within the last 120 seconds; stale, future, or malformed heartbeat timestamps suppress all alerts. It does not send push notifications and does not place orders. An empty `alerts` array means no eligible fresh candidate was found or the market-health gate suppressed candidates. The dashboard independently hides alerts unless the feed status is `LIVE` and each item explicitly passes the notification-only/no-trade authorization checks.



## SQLite outbox projection

The worker's pending SQLite queue is also exposed by the read-only `/api/chatgpt/outbox-alerts` endpoint. It uses the same `ALERT_OUTBOX_DB` path as the worker (default `data/entry-alerts.sqlite3`); configure both processes to share the same durable database file on the same host. The endpoint returns only eligible, fresh events with valid LONG/SHORT price geometry and the explicit notification-only/no-auto-trade flags. It fails closed when market status is not LIVE, its heartbeat is stale, or the database cannot be read. Query parameter `limit` is clamped to 1–100 (default 50).

Polling this endpoint does not mark outbox records delivered. This is deliberate: a GET request alone cannot prove a ChatGPT notification was delivered or acknowledged. Queue retention/acknowledgement and authenticated deployment remain operational concerns; do not expose an unauthenticated private dashboard or SQLite file to the public internet.

## Make it available to a ChatGPT scheduled task

1. Deploy the dashboard so the endpoint is reachable over HTTPS from the environment in which the ChatGPT task runs.
2. Open the endpoint in a browser and verify it returns JSON with `feed: crypto-ai-trader-chatgpt-alerts-v1`.
3. In ChatGPT, create a scheduled task that checks this exact endpoint on the desired cadence, summarizes only new items in the `alerts` array, and says clearly when there are no fresh alerts.
4. Do not expose API keys, exchange credentials, or private dashboard controls in a public endpoint. If the dashboard is not public, use a supported authenticated connector or secure feed proxy; do not disable authentication merely to make polling work.

Suggested task instruction (replace the placeholder with the deployed HTTPS endpoint):

> Check [DEPLOYED_HTTPS_URL]/api/chatgpt/alerts. Report only fresh confirmed alerts in the returned JSON. For each alert include symbol, LONG/SHORT, entry, stop loss, TP1, TP2, score, model status, and whether it is research-only. If the feed is unavailable, stale, malformed, or `status` is not `LIVE`, report that the feed could not safely confirm signals; do not infer a signal. If `alerts` is empty, say there are no fresh confirmed entry alerts. This is notification-only and must never place or recommend an unvalidated automatic order.

## Important limitation

A ChatGPT scheduled task cannot automatically receive events from this repository just because the endpoint exists. The task needs an HTTPS endpoint it can access and must be configured by the user in ChatGPT. The endpoint has not been verified against a live deployment yet.

## Standalone polling worker (notification-only)

The optional worker entry point is `python -m execution.live_signal_worker`. It is separate from the FastAPI event loop and does not place orders or send notifications. Configure these environment variables before starting it:

- `ALERT_DASHBOARD_URL`: deployed dashboard base URL. HTTPS is required except for loopback HTTP used in local testing.
- `ALERT_OUTBOX_DB`: SQLite file path (default `data/entry-alerts.sqlite3`).
- `ALERT_SYMBOLS`: comma-separated symbols (default BTCUSDT,ETHUSDT,SOLUSDT,AVAXUSDT,XRPUSDT).
- `ALERT_POLL_SECONDS`: polling cadence in seconds, minimum 5 (default 30).

Each cycle requires the dashboard snapshot to report `LIVE`, checks each symbol against the latest closed Binance USD-M candle, and writes eligible notification-only candidates to SQLite. It prints a compact JSON health/result line to stdout. Failed cycles report `DEGRADED` and retry on the next cycle. Run it under a process supervisor (for example systemd or a container restart policy) and monitor stdout/stderr. The worker only creates a local outbox: a separate authorized delivery integration and a deployed HTTPS feed are still required before ChatGPT can receive alerts. Do not expose the SQLite database publicly or store exchange trading credentials in this worker.
