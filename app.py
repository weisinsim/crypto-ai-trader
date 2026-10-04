import asyncio
import json
import time
from pathlib import Path

import httpx
import websockets
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"

SYMBOLS = ["BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","XRPUSDT","DOGEUSDT","ADAUSDT","AVAXUSDT","LINKUSDT","SUIUSDT"]
FUTURES_BASES = [
    "https://fapi.binance.com",
    "https://fapi1.binance.com",
    "https://fapi2.binance.com",
    "https://fapi3.binance.com",
    "https://fapi4.binance.com",
]
SPOT_BASE = "https://api.binance.com"
WS_BASES = [
    "wss://fstream.binance.com/stream",
    "wss://fstream1.binance.com/stream",
    "wss://fstream2.binance.com/stream",
    "wss://fstream3.binance.com/stream",
    "wss://fstream4.binance.com/stream",
]


cache = {
    s: {
        "symbol": s, "price": None, "change": None, "volume": None, "ts": 0,
        "ema20_1h": None, "ema50_1h": None, "ema200_1h": None,
        "ema20_4h": None, "ema50_4h": None, "ema200_4h": None,
        "rsi_1h": None, "atr_1h": None, "vol_ratio": None,
        "trend": "LOADING", "signal": "NO-TRADE",
        "entry": None, "sl": None, "tp1": None, "tp2": None, "rr": None,
        "funding": None, "oi": None, "analysis_ts": 0
    } for s in SYMBOLS
}
clients = set()
market_status = {"status": "STARTING", "updated": 0, "error": None}
diagnostics = {
    "rest_source": None,
    "ws_source": None,
    "last_errors": [],
    "last_success": 0,
}

def log_error(stage, detail):
    msg = f"{stage}: {str(detail)[:240]}"
    diagnostics["last_errors"] = (diagnostics["last_errors"] + [msg])[-12:]
    market_status["error"] = msg


app = FastAPI(title="Crypto AI Trader V6")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def index():
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/api/health")
async def health():
    now = time.time()
    ages = [now-v["ts"] for v in cache.values() if v["ts"]]
    ready = sum(v["price"] is not None for v in cache.values())
    return {
        "ok": ready > 0,
        "status": market_status["status"],
        "age_sec": min(ages) if ages else None,
        "symbols_ready": ready,
        "analysis_ready": sum(v["analysis_ts"] > 0 for v in cache.values())
    }


@app.get("/api/debug")
async def debug():
    now = time.time()
    return {
        "status": market_status,
        "diagnostics": diagnostics,
        "symbols_ready": sum(v["price"] is not None for v in cache.values()),
        "analysis_ready": sum(v["analysis_ts"] > 0 for v in cache.values()),
        "age_sec": {s: round(now-v["ts"], 1) if v["ts"] else None for s, v in cache.items()},
    }


@app.get("/api/snapshot")
async def snapshot():
    return {
        "server_ts": time.time(),
        "status": market_status,
        "data": list(cache.values())
    }


def ema(values, period):
    if len(values) < period:
        return None
    k = 2 / (period + 1)
    e = sum(values[:period]) / period
    for v in values[period:]:
        e = v * k + e * (1-k)
    return e


def rsi(values, period=14):
    if len(values) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(values)):
        d = values[i] - values[i-1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    ag = sum(gains[:period]) / period
    al = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        ag = (ag * (period-1) + gains[i]) / period
        al = (al * (period-1) + losses[i]) / period
    if al == 0:
        return 100.0
    return 100 - (100 / (1 + ag/al))


def atr(highs, lows, closes, period=14):
    if len(closes) < period + 1:
        return None
    tr = []
    for i in range(1, len(closes)):
        tr.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i] - closes[i-1])
        ))
    return sum(tr[-period:]) / period


def round_price(x):
    if x is None:
        return None
    if x >= 1000: return round(x, 2)
    if x >= 1: return round(x, 4)
    return round(x, 6)


async def get_json(client, path, params, allow_spot=False):
    last_error = None
    bases = FUTURES_BASES + ([SPOT_BASE] if allow_spot else [])
    for base in bases:
        try:
            r = await client.get(base + path, params=params)
            if r.status_code == 200:
                if base != SPOT_BASE:
                    diagnostics["rest_source"] = base
                return r.json()
            last_error = f"{base}{path} -> HTTP {r.status_code}: {r.text[:160]}"
        except Exception as e:
            last_error = f"{base}{path} -> {type(e).__name__}: {e}"
    log_error("REST", last_error or "unknown error")
    raise RuntimeError(last_error or "REST request failed")


async def get_market_json(client, futures_path, params, spot_path=None):
    try:
        return await get_json(client, futures_path, params, allow_spot=False)
    except Exception:
        if spot_path:
            return await get_json(client, spot_path, params, allow_spot=True)
        raise



async def analyze_symbol(client, s):
    try:
        async with asyncio.TaskGroup() as tg:
            t1 = tg.create_task(get_market_json(
                client, "/fapi/v1/klines",
                {"symbol": s, "interval": "1h", "limit": 240},
                "/api/v3/klines"
            ))
            t4 = tg.create_task(get_market_json(
                client, "/fapi/v1/klines",
                {"symbol": s, "interval": "4h", "limit": 240},
                "/api/v3/klines"
            ))
        k1, k4 = t1.result(), t4.result()

        # Funding/OI are optional: if Futures endpoints are blocked,
        # technical analysis still works from market candles.
        fr = {}
        oi = {}
        try:
            fr = await get_json(client, "/fapi/v1/premiumIndex", {"symbol": s})
        except Exception as e:
            log_error(f"FUNDING {s}", e)
        try:
            oi = await get_json(client, "/fapi/v1/openInterest", {"symbol": s})
        except Exception as e:
            log_error(f"OI {s}", e)

        c1 = [float(x[4]) for x in k1]
        h1 = [float(x[2]) for x in k1]
        l1 = [float(x[3]) for x in k1]
        q1 = [float(x[7]) for x in k1]
        c4 = [float(x[4]) for x in k4]

        p = c1[-1]
        e20 = ema(c1, 20); e50 = ema(c1, 50); e200 = ema(c1, 200)
        e20_4 = ema(c4, 20); e50_4 = ema(c4, 50); e200_4 = ema(c4, 200)
        r = rsi(c1); a = atr(h1, l1, c1)
        vr = q1[-1] / (sum(q1[-21:-1]) / 20) if len(q1) >= 21 else None

        trend4 = "BULL" if p > e20_4 > e50_4 else ("BEAR" if p < e20_4 < e50_4 else "RANGE")
        score = 0
        score += 2 if p > e20_4 else -2
        score += 2 if e20_4 > e50_4 else -2
        score += 1 if p > e20 else -1
        score += 1 if e20 > e50 else -1
        score += 1 if r is not None and r >= 50 else -1
        score += 1 if vr is not None and vr >= 1.2 else 0

        signal = "NO-TRADE"
        if trend4 == "BULL" and score >= 4 and r is not None and r < 72:
            signal = "LONG"
        elif trend4 == "BULL" and score >= 2:
            signal = "WATCH-LONG"
        elif trend4 == "BEAR" and score <= -4 and r is not None and r > 28:
            signal = "SHORT"
        elif trend4 == "BEAR" and score <= -2:
            signal = "WATCH-SHORT"

        stop_dist = (a * 1.5) if a else p * 0.01
        if signal in ("LONG", "WATCH-LONG"):
            entry = p
            sl = p - stop_dist
            tp1 = p + stop_dist * 2
            tp2 = p + stop_dist * 3
        elif signal in ("SHORT", "WATCH-SHORT"):
            entry = p
            sl = p + stop_dist
            tp1 = p - stop_dist * 2
            tp2 = p - stop_dist * 3
        else:
            entry = sl = tp1 = tp2 = None

        cache[s].update({
            "price": p,
            "ema20_1h": round_price(e20), "ema50_1h": round_price(e50), "ema200_1h": round_price(e200),
            "ema20_4h": round_price(e20_4), "ema50_4h": round_price(e50_4), "ema200_4h": round_price(e200_4),
            "rsi_1h": round(r, 1) if r is not None else None,
            "atr_1h": round_price(a),
            "vol_ratio": round(vr, 2) if vr is not None else None,
            "trend": trend4, "signal": signal,
            "entry": round_price(entry), "sl": round_price(sl),
            "tp1": round_price(tp1), "tp2": round_price(tp2),
            "rr": 2.0 if signal != "NO-TRADE" else None,
            "funding": float(fr.get("lastFundingRate", 0)) if fr else None,
            "oi": float(oi.get("openInterest", 0)) if oi else None,
            "analysis_ts": time.time(),
            "ts": time.time()
        })
    except Exception as e:
        cache[s]["error"] = str(e)[:120]
        log_error(f"ANALYSIS {s}", e)


async def seed_rest():
    async with httpx.AsyncClient(timeout=8) as c:
        async def one(s):
            try:
                d = await get_market_json(c, "/fapi/v1/ticker/24hr", {"symbol": s}, "/api/v3/ticker/24hr")
                cache[s].update(
                    price=float(d["lastPrice"]),
                    change=float(d["priceChangePercent"]),
                    volume=float(d["quoteVolume"]),
                    ts=time.time()
                )
            except Exception as e:
                cache[s]["error"] = str(e)[:120]
                log_error(f"SEED {s}", e)
        await asyncio.gather(*(one(s) for s in SYMBOLS))
        ready = sum(v["price"] is not None for v in cache.values())
        if ready:
            diagnostics["last_success"] = time.time()
            market_status.update(status="LIVE", updated=time.time(), error=None)
        else:
            market_status.update(status="FALLBACK", updated=time.time())


async def price_poll_loop():
    while True:
        try:
            async with httpx.AsyncClient(timeout=8) as c:
                await asyncio.gather(*(poll_price(c, s) for s in SYMBOLS))
        except Exception as e:
            log_error("PRICE POLL", e)
        await asyncio.sleep(5)


async def poll_price(client, s):
    try:
        d = await get_market_json(client, "/fapi/v1/ticker/24hr", {"symbol": s}, "/api/v3/ticker/24hr")
        cache[s].update(
            price=float(d["lastPrice"]),
            change=float(d["priceChangePercent"]),
            volume=float(d.get("quoteVolume", 0)),
            ts=time.time(),
        )
        diagnostics["last_success"] = time.time()
        await broadcast({"type": "ticker", "data": cache[s]})
    except Exception as e:
        cache[s]["error"] = str(e)[:120]
        log_error(f"PRICE {s}", e)


async def analysis_loop():
    while True:
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                await asyncio.gather(*(analyze_symbol(c, s) for s in SYMBOLS))
            market_status.update(status="LIVE" if market_status["status"] != "OFFLINE" else "LIVE",
                                 updated=time.time(), error=None)
            await broadcast({"type": "snapshot", "data": list(cache.values()), "server_ts": time.time()})
        except Exception as e:
            market_status.update(status="DELAYED", updated=time.time(), error=str(e)[:120])
            log_error("ANALYSIS LOOP", e)
        await asyncio.sleep(60)


async def broadcast(obj):
    dead = []
    msg = json.dumps(obj)
    for ws in list(clients):
        try:
            await ws.send_text(msg)
        except Exception:
            dead.append(ws)
    for ws in dead:
        clients.discard(ws)


async def ws_loop():
    streams = [f"{s.lower()}@miniTicker" for s in SYMBOLS]
    while True:
        connected = False
        for ws_base in WS_BASES:
            url = ws_base + "?streams=" + "/".join(streams)
            try:
                async with websockets.connect(
                    url,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=5,
                    max_size=2**20,
                ) as sock:
                    connected = True
                    diagnostics["ws_source"] = ws_base
                    market_status.update(status="LIVE", updated=time.time(), error=None)
                    await broadcast({"type": "status", "status": "LIVE"})
                    async for raw in sock:
                        m = json.loads(raw)
                        d = m.get("data", {})
                        s = d.get("s")
                        if s in cache:
                            cache[s].update(
                                price=float(d.get("c")),
                                change=float(d.get("P", 0)),
                                volume=float(d.get("q", 0)),
                                ts=time.time(),
                            )
                            diagnostics["last_success"] = time.time()
                            await broadcast({"type": "ticker", "data": cache[s]})
                    break
            except Exception as e:
                log_error("BINANCE WS", e)
                continue
        if not connected:
            market_status.update(status="FALLBACK", updated=time.time())
            await broadcast({"type": "status", "status": "FALLBACK"})
            await seed_rest()
        await asyncio.sleep(3)


@app.on_event("startup")
async def startup():
    await seed_rest()
    asyncio.create_task(ws_loop())
    asyncio.create_task(price_poll_loop())
    asyncio.create_task(analysis_loop())


@app.websocket("/ws")
async def client_ws(ws: WebSocket):
    await ws.accept()
    clients.add(ws)
    try:
        await ws.send_text(json.dumps({
            "type": "snapshot", "data": list(cache.values()),
            "server_ts": time.time(), "status": market_status
        }))
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        clients.discard(ws)
    except Exception:
        clients.discard(ws)
