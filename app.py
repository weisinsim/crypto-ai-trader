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
BASE = "https://fapi.binance.com"
WS = "wss://fstream.binance.com/stream"

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


async def get_json(client, path, params):
    r = await client.get(BASE + path, params=params)
    r.raise_for_status()
    return r.json()


async def analyze_symbol(client, s):
    try:
        k1, k4, fr, oi = await asyncio.gather(
            get_json(client, "/fapi/v1/klines", {"symbol": s, "interval": "1h", "limit": 240}),
            get_json(client, "/fapi/v1/klines", {"symbol": s, "interval": "4h", "limit": 240}),
            get_json(client, "/fapi/v1/premiumIndex", {"symbol": s}),
            get_json(client, "/fapi/v1/openInterest", {"symbol": s})
        )
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
            "funding": float(fr.get("lastFundingRate", 0)),
            "oi": float(oi.get("openInterest", 0)),
            "analysis_ts": time.time(),
            "ts": time.time()
        })
    except Exception as e:
        cache[s]["error"] = str(e)[:120]


async def seed_rest():
    async with httpx.AsyncClient(timeout=8) as c:
        async def one(s):
            try:
                d = await get_json(c, "/fapi/v1/ticker/24hr", {"symbol": s})
                cache[s].update(
                    price=float(d["lastPrice"]),
                    change=float(d["priceChangePercent"]),
                    volume=float(d["quoteVolume"]),
                    ts=time.time()
                )
            except Exception:
                pass
        await asyncio.gather(*(one(s) for s in SYMBOLS))


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
    url = WS + "?streams=" + "/".join(streams)
    while True:
        try:
            async with websockets.connect(url, ping_interval=20, ping_timeout=20, close_timeout=5, max_size=2**20) as sock:
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
                            ts=time.time()
                        )
                        await broadcast({"type": "ticker", "data": cache[s]})
        except Exception as e:
            market_status.update(status="FALLBACK", updated=time.time(), error=str(e)[:120])
            await broadcast({"type": "status", "status": "FALLBACK"})
            await seed_rest()
            await asyncio.sleep(3)


@app.on_event("startup")
async def startup():
    await seed_rest()
    asyncio.create_task(ws_loop())
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
