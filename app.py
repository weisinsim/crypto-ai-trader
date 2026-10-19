import asyncio
import json
import hashlib
import math
import time
from validation.candle_closure import filter_closed_candles, normalize_timestamp_ms, upsert_candle, validate_candle_series
from validation.dashboard_health import build_health_payload
from validation.signal_confirmation import invalidate_stale_confirmation
from validation.xrp_model import evaluate_xrp_signal
from pathlib import Path

import httpx
import websockets
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"

SYMBOLS = ["BTCUSDT","ETHUSDT","SOLUSDT","AVAXUSDT","XRPUSDT"]

FUTURES_BASES = [
    "https://fapi.binance.com",
    "https://fapi1.binance.com",
    "https://fapi2.binance.com",
    "https://fapi3.binance.com",
    "https://fapi4.binance.com",
]
FUTURES_WS = [
    "wss://fstream.binance.com/stream",
    "wss://fstream.binance.com:443/stream",
]
BYBIT_BASE = "https://api.bybit.com"
BYBIT_WS = "wss://stream.bybit.com/v5/public/linear"

cache = {
    s: {
        "symbol": s, "price": None, "change": None, "volume": None, "ts": 0,
        "ema20_1h": None, "ema50_1h": None, "ema200_1h": None,
        "ema20_4h": None, "ema50_4h": None, "ema200_4h": None,
        "rsi_1h": None, "atr_1h": None, "vol_ratio": None,
        "trend": "LOADING", "signal": "NO-TRADE",
        "entry": None, "sl": None, "tp1": None, "tp2": None, "rr": None, "score": 0, "score_label": "NO-TRADE", "score_breakdown": {}, "position_qty": None, "position_usd": None, "margin_3x": None,
        "funding": None, "oi": None, "oi_change": None, "adx_1h": None, "vwap_1h": None, "support": None, "resistance": None, "atr_pct": None, "btc_filter": "NEUTRAL", "analysis_ts": 0, "source": None,
        "error": None, "live_signal": "NO-TRADE",
        "confirmed_signal": "NO-TRADE", "confirmed_signal_time": None, "confirmed_entry": None, "confirmed_sl": None, "confirmed_tp1": None, "confirmed_tp2": None, "confirmed_score": 0, "confirmed_score_label": "NO-TRADE", "confirmed_status": "WAITING", "regime": "LOADING", "direction_bias": "NEUTRAL", "opportunity_tier": "D", "confirmation_basis": "1H candle close", "last_closed_candle": None,
    } for s in SYMBOLS
}
series = {s: {"1h": [], "4h": []} for s in SYMBOLS}
clients = set()
market_status = {"status": "STARTING", "updated": 0, "error": None}
diagnostics = {
    "rest_source": None, "ws_source": None, "last_success": 0,
    "binance_failures": 0, "fallback_source": None, "last_errors": []
}
last_analysis = {s: 0 for s in SYMBOLS}
oi_history = {s: [] for s in SYMBOLS}
signal_history = {s: [] for s in SYMBOLS}
confirmed_state = {s: "NO-TRADE" for s in SYMBOLS}
http_client = None

MODEL_NAME = "Crypto AI Trader Multi-Asset Adaptive"
MODEL_VERSION = "V28.2-Validated Execution Gate"
XRP_PARAMS = {"kill_slope": 0.0077, "atr_max": 0.0135, "shock_max": 0.025, "sl_atr": 1.7, "tp_atr": 2.8, "direction": "LONG_ONLY"}
MODEL_LIBRARY = {
    "BTCUSDT": {"name":"BTC Trend Tactical", "status":"RESEARCH", "direction":"LONG/SHORT", "horizon":"1–3H tactical", "family":"trend_tactical"},
    "ETHUSDT": {"name":"ETH No-Production Alpha", "status":"NO-PRODUCTION", "direction":"NONE", "horizon":"WAIT", "family":"none"},
    "SOLUSDT": {"name":"SOL Momentum Pullback", "status":"RESEARCH", "direction":"LONG/SHORT", "horizon":"up to 12H", "family":"momentum_pullback"},
    "AVAXUSDT": {"name":"AVAX Trend Pullback", "status":"RESEARCH", "direction":"LONG/SHORT", "horizon":"up to 12H", "family":"trend_pullback"},
    "XRPUSDT": {"name":"XRP V27.2 Reversal", "status":"FROZEN", "direction":"LONG ONLY", "horizon":"1H confirmation / swing", "family":"xrp_v27"}
}
for _s in SYMBOLS:
    cache[_s].update({"model_name":MODEL_LIBRARY[_s]["name"],"model_status":MODEL_LIBRARY[_s]["status"],"direction_mode":MODEL_LIBRARY[_s]["direction"],"horizon":MODEL_LIBRARY[_s]["horizon"]})
app = FastAPI(title=f"Crypto AI Trader {MODEL_NAME}")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

def log_error(stage, detail):
    msg = f"{stage}: {str(detail)[:220]}"
    diagnostics["last_errors"] = (diagnostics["last_errors"] + [msg])[-10:]
    market_status["error"] = msg

async def get_json(base, path, params=None, timeout=3.0):
    r = await http_client.get(base + path, params=params or {}, timeout=timeout)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:120]}")
    return r.json()

async def futures_json(path, params=None, timeout=3.0):
    last = None
    for base in FUTURES_BASES:
        try:
            data = await get_json(base, path, params, timeout)
            diagnostics["rest_source"] = base
            return data
        except Exception as e:
            last = e
            diagnostics["binance_failures"] += 1
    raise RuntimeError(f"Binance Futures unavailable: {last}")

async def bybit_json(path, params=None, timeout=3.0):
    data = await get_json(BYBIT_BASE, path, params, timeout)
    if data.get("retCode", 0) != 0:
        raise RuntimeError(str(data)[:180])
    diagnostics["fallback_source"] = BYBIT_BASE
    return data["result"]

@app.get("/")
async def index():
    return FileResponse(str(STATIC_DIR / "index.html"))

@app.get("/api/health")
async def health():
    # Health must reflect the least healthy tracked market, not just one fresh symbol.
    now = time.time()
    source = diagnostics["rest_source"] or diagnostics["ws_source"] or diagnostics["fallback_source"]
    return build_health_payload(now, cache, SYMBOLS, market_status["status"], source)

@app.get("/api/debug")
async def debug():
    now = time.time()
    return {
        "status": market_status,
        "diagnostics": diagnostics,
        "symbols_ready": sum(v["price"] is not None for v in cache.values()),
        "analysis_ready": sum(v["analysis_ts"] > 0 for v in cache.values()),
        "age_sec": {s: round(now-v["ts"], 1) if v["ts"] else None for s, v in cache.items()},
        "errors": {s: v["error"] for s, v in cache.items() if v["error"]},
    }

@app.get("/api/snapshot")
async def snapshot():
    return {"server_ts": time.time(), "status": market_status, "data": list(cache.values())}

@app.get("/api/models")
async def models():
    return {"model_name": MODEL_NAME, "version": MODEL_VERSION, "models": MODEL_LIBRARY}

@app.get("/api/signals")
async def signals():
    return {"server_ts": time.time(), "history": signal_history, "confirmed": {s: {k: cache[s].get(k) for k in ("confirmed_signal","confirmed_signal_time","confirmed_entry","confirmed_sl","confirmed_tp1","confirmed_tp2","confirmed_score","confirmed_score_label","confirmed_status")} for s in SYMBOLS}}

@app.get("/api/chatgpt/alerts")
async def chatgpt_alert_feed():
    """Read-only feed for a ChatGPT task or other authorized polling client.

    This endpoint reports confirmed candidates only. It never sends a push
    notification and never authorizes a trade. The deployment must expose this
    endpoint over HTTPS for a scheduled task to read it.
    """
    now = time.time()
    max_age_seconds = 90 * 60
    alerts = []
    # Fail closed: status must be LIVE and the health heartbeat must itself be
    # recent. A stale status flag can survive a stalled market-data loop.
    status_updated = market_status.get("updated")
    status_is_fresh = (
        isinstance(status_updated, (int, float))
        and math.isfinite(status_updated)
        and 0 < status_updated <= now
        and now - status_updated <= 120
    )
    if market_status.get("status") != "LIVE" or not status_is_fresh:
        reason = (
            "Market feed is not LIVE; cached candidates are suppressed. No orders are placed."
            if market_status.get("status") != "LIVE"
            else "Market status heartbeat is stale or invalid; cached candidates are suppressed. No orders are placed."
        )
        return {
            "generated_at": now,
            "feed": "crypto-ai-trader-chatgpt-alerts-v1",
            "status": market_status.get("status", "UNKNOWN"),
            "alerts": [],
            "count": 0,
            "note": reason,
        }
    for symbol in SYMBOLS:
        item = cache[symbol]
        side = item.get("confirmed_signal")
        candle_time = item.get("confirmed_signal_time")
        analysis_time = item.get("analysis_ts")
        model_status = item.get("model_status", "UNKNOWN")
        if side not in ("LONG", "SHORT"):
            continue
        if model_status not in ("RESEARCH", "FROZEN", "PRODUCTION"):
            continue
        if not isinstance(candle_time, (int, float)) or not math.isfinite(candle_time) or candle_time <= 0 or candle_time > now * 1000:
            continue
        if now * 1000 - candle_time > max_age_seconds * 1000:
            continue
        if not isinstance(analysis_time, (int, float)) or not math.isfinite(analysis_time) or analysis_time <= 0 or analysis_time > now or now - analysis_time > max_age_seconds:
            continue
        entry = item.get("confirmed_entry")
        stop = item.get("confirmed_sl")
        tp1 = item.get("confirmed_tp1")
        tp2 = item.get("confirmed_tp2")
        if not all(isinstance(v, (int, float)) and v > 0 for v in (entry, stop, tp1, tp2)):
            continue
        if side == "LONG" and not (stop < entry < tp1 <= tp2):
            continue
        if side == "SHORT" and not (tp2 <= tp1 < entry < stop):
            continue
        event_material = f"{symbol}|{side}|{int(candle_time)}|{item.get('model_name', '')}"
        event_id = hashlib.sha256(event_material.encode("utf-8")).hexdigest()[:24]
        alerts.append({
            "event_id": event_id,
            "symbol": symbol,
            "side": side,
            "signal_candle_ms": candle_time,
            "analysis_ts": analysis_time,
            "entry": entry,
            "stop_loss": stop,
            "take_profit_1": tp1,
            "take_profit_2": tp2,
            "score": item.get("confirmed_score"),
            "score_label": item.get("confirmed_score_label"),
            "model_name": item.get("model_name"),
            "model_status": model_status,
            "research_only": model_status != "PRODUCTION",
            "notification_only": True,
            "auto_trade_authorized": False,
        })
    return {
        "generated_at": now,
        "feed": "crypto-ai-trader-chatgpt-alerts-v1",
        "status": market_status.get("status", "UNKNOWN"),
        "alerts": alerts,
        "count": len(alerts),
        "note": "Read-only signal feed; empty alerts means no fresh confirmed candidate. No orders are placed.",
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
        ag = (ag*(period-1) + gains[i]) / period
        al = (al*(period-1) + losses[i]) / period
    if al == 0:
        return 100.0
    return 100 - (100/(1 + ag/al))

def atr(highs, lows, closes, period=14):
    if len(closes) < period + 1:
        return None
    tr = []
    for i in range(1, len(closes)):
        tr.append(max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1])))
    return sum(tr[-period:]) / period


def adx(highs, lows, closes, period=14):
    if len(closes) < period*2+1: return None
    trs=[]; plus=[]; minus=[]
    for i in range(1,len(closes)):
        up=highs[i]-highs[i-1]; dn=lows[i-1]-lows[i]
        trs.append(max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1])))
        plus.append(up if up>dn and up>0 else 0)
        minus.append(dn if dn>up and dn>0 else 0)
    def avg(arr): return sum(arr[-period:])/period if len(arr)>=period else None
    tr=avg(trs); pd=avg(plus); md=avg(minus)
    if not tr or tr==0: return None
    pdi=100*pd/tr; mdi=100*md/tr
    dx=100*abs(pdi-mdi)/max(pdi+mdi,1e-9)
    # Wilder-style rolling DX approximation, stable enough for a dashboard filter.
    dxs=[]
    for j in range(period, len(trs)):
        trj=avg(trs[:j+1]); pj=avg(plus[:j+1]); mj=avg(minus[:j+1])
        if trj and trj>0:
            p=100*pj/trj; m=100*mj/trj
            dxs.append(100*abs(p-m)/max(p+m,1e-9))
    return sum(dxs[-period:])/min(period,len(dxs)) if dxs else dx

def vwap(highs, lows, closes, volumes, period=48):
    n=min(period,len(closes))
    if n==0: return None
    pv=0.0; vv=0.0
    for h,l,c,v in zip(highs[-n:],lows[-n:],closes[-n:],volumes[-n:]):
        typ=(h+l+c)/3
        pv += typ*v; vv += v
    return pv/vv if vv else None

def sr_levels(highs, lows, closes, lookback=48):
    n=min(lookback,len(closes))
    if n<5: return None,None
    p=closes[-1]
    recent_h=highs[-n:]; recent_l=lows[-n:]
    # Exclude the current candle so the level is not simply today's price.
    resistance=max(recent_h[:-1])
    support=min(recent_l[:-1])
    return support,resistance

def round_price(x):
    if x is None: return None
    if x >= 1000: return round(x, 2)
    if x >= 1: return round(x, 4)
    return round(x, 6)

def kline_to_obj(x):
    return {"t": normalize_timestamp_ms(x[0]), "o": float(x[1]), "h": float(x[2]), "l": float(x[3]),
            "c": float(x[4]), "v": float(x[5]), "q": float(x[7])}

def set_series(s, interval, klines):
    rows = [kline_to_obj(x) for x in klines]
    series[s][interval] = validate_candle_series(rows, limit=210)

def closed_series_rows(s, interval, now_ms=None):
    """Return only candles whose full interval has elapsed; kline t is open time."""
    now_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    return filter_closed_candles(series[s][interval], interval, now_ms)

def record_confirmed_signal(s, candle_close_ms=None):
    """Freeze the 1H signal at candle close so intrabar flips do not create fake entries."""
    sig = cache[s].get("signal") or "NO-TRADE"
    prev = confirmed_state[s]
    ts_ms = int(candle_close_ms) if candle_close_ms else int(time.time() * 1000)
    if sig == prev:
        return False

    # A changed signal invalidates any previously confirmed direction before
    # applying the new model's validation gate. Otherwise a blocked opposite
    # signal can leave a stale LONG/SHORT confirmation displayed as ACTIVE.
    prev = invalidate_stale_confirmation(
        prev, sig, cache[s], signal_history[s], ts_ms
    )
    if prev == "NO-TRADE":
        confirmed_state[s] = "NO-TRADE"

    # Only independently validated candidate/frozen models may become executable confirmations.
    cfg = MODEL_LIBRARY.get(s, {})
    # Execution requires an explicit, auditable validation record.
    # FROZEN means parameters are preserved, not that profitability is proven.
    validation = cfg.get("validation", {})
    executable = (
        sig in ("LONG", "SHORT")
        and cache[s].get("opportunity_tier") == "A"
        and cfg.get("status") == "CANDIDATE"
        and validation.get("holdout_passed") is True
        and validation.get("walk_forward_passed") is True
        and validation.get("cost_stress_passed") is True
        and validation.get("report_id")
    )
    if sig in ("LONG", "SHORT") and not executable:
        cache[s]["confirmed_status"] = "WAITING_VALIDATION"
        cache[s]["confirmation_basis"] = "1H candle close + validated model gate"
        return False

    confirmed_state[s] = sig
    cache[s]["confirmed_signal"] = sig
    cache[s]["confirmed_signal_time"] = ts_ms
    cache[s]["confirmed_entry"] = cache[s].get("entry")
    cache[s]["confirmed_sl"] = cache[s].get("sl")
    cache[s]["confirmed_tp1"] = cache[s].get("tp1")
    cache[s]["confirmed_tp2"] = cache[s].get("tp2")
    cache[s]["confirmed_score"] = cache[s].get("score", 0)
    cache[s]["confirmed_score_label"] = cache[s].get("score_label", "NO-TRADE")
    cache[s]["confirmed_status"] = "ACTIVE" if sig in ("LONG", "SHORT") else "NO-TRADE"

    if sig in ("LONG", "SHORT"):
        event = {
            "symbol": s, "signal": sig, "signal_time": ts_ms,
            "entry": cache[s].get("entry"), "sl": cache[s].get("sl"),
            "tp1": cache[s].get("tp1"), "tp2": cache[s].get("tp2"),
            "score": cache[s].get("score", 0), "score_label": cache[s].get("score_label", "NO-TRADE"),
            "entry_location": cache[s].get("entry_location"), "status": "ACTIVE"
        }
        signal_history[s].insert(0, event)
        signal_history[s] = signal_history[s][:20]
    return True

async def seed_symbol(s):
    try:
        k1, k4 = await asyncio.gather(
            futures_json("/fapi/v1/klines", {"symbol": s, "interval": "1h", "limit": 300}, 3.0),
            futures_json("/fapi/v1/klines", {"symbol": s, "interval": "4h", "limit": 300}, 3.0),
        )
        set_series(s, "1h", k1)
        set_series(s, "4h", k4)
        await analyze_symbol(s)
        cache[s]["confirmed_status"] = "WAITING"
        cache[s]["confirmation_basis"] = "Next 1H candle close required"
        cache[s]["source"] = "Binance Futures"
        return True
    except Exception as e:
        log_error(f"SEED {s}", e)
        try:
            b1, b4 = await asyncio.gather(
                bybit_json("/v5/market/kline", {"category":"linear","symbol":s,"interval":"60","limit":300}, 3.0),
                bybit_json("/v5/market/kline", {"category":"linear","symbol":s,"interval":"240","limit":300}, 3.0),
            )
            def bybit_rows(obj):
                rows = obj["list"]
                rows.reverse()
                return [{"t":int(x[0]),"o":float(x[1]),"h":float(x[2]),"l":float(x[3]),"c":float(x[4]),"v":float(x[5]),"q":float(x[6])} for x in rows]
            # Apply the same fail-closed chronology checks to fallback snapshots.
            series[s]["1h"] = validate_candle_series(bybit_rows(b1), limit=210)
            series[s]["4h"] = validate_candle_series(bybit_rows(b4), limit=210)
            cache[s]["source"] = "Bybit fallback"
            await analyze_symbol(s)
            cache[s]["confirmed_status"] = "WAITING"
            cache[s]["confirmation_basis"] = "Next 1H candle close required"
            return True
        except Exception as e2:
            log_error(f"FALLBACK {s}", e2)
            cache[s]["error"] = str(e2)[:120]
            return False

async def analyze_symbol(s):
    # Never calculate decisions from an in-progress 1h or 4h candle.
    k1 = closed_series_rows(s, "1h")
    k4 = closed_series_rows(s, "4h")
    if len(k1) < 200 or len(k4) < 50:
        return False
    try:
        cfg = MODEL_LIBRARY[s]
        c1=[x["c"] for x in k1]; h1=[x["h"] for x in k1]; l1=[x["l"] for x in k1]; q1=[x["q"] for x in k1]
        c4=[x["c"] for x in k4]
        p=c1[-1]
        e20=ema(c1,20); e50=ema(c1,50); e200=ema(c1,200)
        e20_4=ema(c4,20); e50_4=ema(c4,50); e200_4=ema(c4,200)
        r=rsi(c1); a=atr(h1,l1,c1); adx_v=adx(h1,l1,c1,14); vwap_v=vwap(h1,l1,c1,q1,48)
        vr=q1[-1]/(sum(q1[-21:-1])/20) if len(q1)>=21 else None
        support,resistance=sr_levels(h1,l1,c1,48)
        atr_pct=(a/p*100) if a and p else None
        trend4="BULL" if p>e20_4>e50_4 else ("BEAR" if p<e20_4<e50_4 else "RANGE")
        regime=trend4
        direction_bias="LONG" if trend4=="BULL" else ("SHORT" if trend4=="BEAR" else "NEUTRAL")
        signal="NO-TRADE"; entry=sl=tp1=tp2=None; direction=0; entry_location="—"
        score_breakdown={}
        # XRP V27.2 remains frozen; shared rules keep live and replay outputs aligned.
        if s=="XRPUSDT":
            xrp = evaluate_xrp_signal(c1, e20, e50, e20_4, e50_4, r, a, resistance, XRP_PARAMS)
            signal=xrp["signal"]; direction=xrp["direction"]
            entry=xrp["entry"]; sl=xrp["stop"]; tp1=xrp["target1"]; tp2=xrp["target2"]
            score=xrp["score"]
            score_label="A" if score>=75 else "B" if score>=65 else "C" if score>=50 else "NO-TRADE"
            score_breakdown={"model":"V27.2","kill_switch":not xrp["kill"],"rsi":round(r,1) if r is not None else None}
        elif s=="ETHUSDT":
            # ETH has not passed independent validation; deliberately suppress false positives.
            score=0; score_label="NO-PRODUCTION"
        else:
            # Asset-specific trend regime with a deliberately looser tactical entry.
            bull=e20_4>e50_4 and p>e20_4
            bear=e20_4<e50_4 and p<e20_4
            if s=="SOLUSDT":
                rl=(40,52); rs=(48,60); sl_mult=1.0; tp_mult=2.0; max_hold="12H"
            elif s=="AVAXUSDT":
                rl=(40,52); rs=(48,60); sl_mult=1.2; tp_mult=1.2; max_hold="12H"
            else: # BTC research tactical
                rl=(45,55); rs=(45,55); sl_mult=1.2; tp_mult=1.5; max_hold="1–3H"
            long_ok=bull and r is not None and rl[0]<=r<=rl[1] and p>e20 and p>c1[-2]
            short_ok=bear and r is not None and rs[0]<=r<=rs[1] and p<e20 and p<c1[-2]
            # Tactical mode allows earlier entry; ADX is advisory, not a hard blocker.
            if long_ok:
                signal="LONG"; direction=1
            elif short_ok:
                signal="SHORT"; direction=-1
            elif bull and r is not None and r<=rl[1]:
                signal="WATCH-LONG"; direction=1
            elif bear and r is not None and r>=rs[0]:
                signal="WATCH-SHORT"; direction=-1
            if direction:
                entry=p; sl=p-direction*sl_mult*(a or p*.005); tp1=p+direction*tp_mult*(a or p*.005); tp2=p+direction*(tp_mult*1.5)*(a or p*.005)
                dist_support=max(0,p-support) if support else None
                dist_res=max(0,resistance-p) if resistance else None
                entry_location="OPTIMAL" if ((direction==1 and dist_support is not None and a and dist_support/a<=2.5) or (direction==-1 and dist_res is not None and a and dist_res/a<=2.5)) else "TACTICAL"
            trend_pts=30 if (direction==1 and bull) or (direction==-1 and bear) else 0
            rsi_pts=20 if ((direction==1 and rl[0]<=r<=rl[1]) or (direction==-1 and rs[0]<=r<=rs[1])) else 0
            adx_pts=15 if adx_v and adx_v>=20 else 8 if adx_v and adx_v>=15 else 0
            vol_pts=15 if vr and vr>=1.1 else 8 if vr and vr>=0.8 else 0
            loc_pts=10 if entry_location=="OPTIMAL" else 5 if direction else 0
            score=min(100,int(trend_pts+rsi_pts+adx_pts+vol_pts+loc_pts))
            score_label="A" if score>=75 else "B" if score>=65 else "C" if score>=50 else "NO-TRADE"
            score_breakdown={"trend":trend_pts,"rsi":rsi_pts,"adx":adx_pts,"volume":vol_pts,"entry_location":loc_pts}
            # Research models may display WATCH/SETUP, but only candidates can be execution-ready.
            if cfg["status"]=="RESEARCH" and signal in ("LONG","SHORT") and score<75:
                signal="WATCH-LONG" if direction==1 else "WATCH-SHORT"
            if cfg["status"]=="NO-PRODUCTION":
                signal="NO-TRADE"; direction=0; entry=sl=tp1=tp2=None
        if signal in ("LONG","SHORT") and score >= 75:
            opportunity_tier = "A"
        elif signal in ("LONG","SHORT","WATCH-LONG","WATCH-SHORT") and score >= 65:
            opportunity_tier = "B"
        elif signal in ("WATCH-LONG","WATCH-SHORT") or score >= 50:
            opportunity_tier = "C"
        else:
            opportunity_tier = "D"
        if cfg["status"] == "NO-PRODUCTION":
            opportunity_tier = "D"
        # Common risk sizing.
        risk_usd=10.0
        qty=(risk_usd/abs(entry-sl)) if entry is not None and sl is not None and abs(entry-sl)>0 else None
        position_usd=qty*entry if qty is not None else None
        if position_usd is not None and position_usd>3000:
            position_usd=3000.0; qty=position_usd/entry
        margin_3x=position_usd/3 if position_usd is not None else None
        cache[s].update(
            price=p, ema20_1h=round_price(e20), ema50_1h=round_price(e50), ema200_1h=round_price(e200),
            ema20_4h=round_price(e20_4), ema50_4h=round_price(e50_4), ema200_4h=round_price(e200_4),
            rsi_1h=round(r,1) if r is not None else None, atr_1h=round_price(a), vol_ratio=round(vr,2) if vr is not None else None,
            trend=trend4, signal=signal, live_signal=signal, entry=round_price(entry), sl=round_price(sl), tp1=round_price(tp1), tp2=round_price(tp2),
            rr=round((abs(tp1-entry)/abs(entry-sl)),2) if entry is not None and sl is not None and tp1 is not None and abs(entry-sl)>0 else None,
            score=score, score_label=score_label, score_breakdown=score_breakdown,
            adx_1h=round(adx_v,1) if adx_v is not None else None, vwap_1h=round_price(vwap_v),
            support=round_price(support), resistance=round_price(resistance), atr_pct=round(atr_pct,2) if atr_pct is not None else None,
            btc_filter="NEUTRAL", entry_location=entry_location, entry_location_score=score_breakdown.get("entry_location",0),
            position_qty=round(qty,6) if qty is not None else None, position_usd=round(position_usd,2) if position_usd is not None else None,
            margin_3x=round(margin_3x,2) if margin_3x is not None else None,
            model_name=cfg["name"], model_status=cfg["status"], direction_mode=cfg["direction"], horizon=cfg["horizon"],
            regime=regime, direction_bias=direction_bias, opportunity_tier=opportunity_tier,
            confirmation_basis="1H candle close", last_closed_candle=(series[s]["1h"][-2]["t"] + 3600000 if len(series[s]["1h"]) >= 2 else None),
            analysis_ts=time.time(), ts=time.time(), error=None
        )
        return True
    except Exception as e:
        log_error(f"ANALYSIS {s}", e)
        return False

async def seed_all():
    # Retry historical-data seeding so a temporary exchange/API delay does not leave the dashboard stuck at LOADING.
    for _ in range(3):
        await asyncio.gather(*(seed_symbol(s) for s in SYMBOLS), return_exceptions=True)
        analyzed=sum(v["analysis_ts"] > 0 for v in cache.values())
        if analyzed == len(SYMBOLS):
            break
        await asyncio.sleep(2)
    ready=sum(v["price"] is not None for v in cache.values())
    market_status.update(status="LIVE" if ready else "DELAYED", updated=time.time(), error=None if ready else market_status["error"])
    diagnostics["last_success"]=time.time() if ready else diagnostics["last_success"]
    await broadcast({"type":"snapshot","data":list(cache.values()),"server_ts":time.time(),"status":market_status})

async def price_rest_fallback():
    while True:
        try:
            data = await futures_json("/fapi/v1/ticker/24hr", {}, 3.0)
            wanted=set(SYMBOLS)
            for x in data:
                s=x.get("symbol")
                if s in wanted:
                    cache[s].update(price=float(x["lastPrice"]), change=float(x["priceChangePercent"]), volume=float(x.get("quoteVolume",0)), ts=time.time(), source=cache[s]["source"] or "Binance Futures")
            diagnostics["last_success"]=time.time()
            await broadcast({"type":"snapshot","data":list(cache.values()),"server_ts":time.time(),"status":market_status})
        except Exception as e:
            log_error("FUTURES TICKER FALLBACK", e)
        await asyncio.sleep(8)

async def bybit_ticker_fallback():
    while True:
        try:
            result = await bybit_json("/v5/market/tickers", {"category":"linear"}, 3.0)
            wanted=set(SYMBOLS)
            for x in result.get("list", []):
                s=x.get("symbol")
                if s in wanted and x.get("lastPrice"):
                    change=float(x["price24hPcnt"])*100 if x.get("price24hPcnt") is not None else cache[s]["change"]
                    cache[s].update(price=float(x["lastPrice"]), change=change, volume=float(x.get("turnover24h",0)), funding=float(x["fundingRate"]) if x.get("fundingRate") not in (None,"") else cache[s]["funding"], oi=float(x["openInterest"]) if x.get("openInterest") not in (None,"") else cache[s]["oi"], ts=time.time(), source="Bybit Live")
                    try:
                        ov=float(x["openInterest"]); now=time.time(); hist=oi_history[s]; hist.append((now,ov)); del hist[:-180]
                        old=next((v for t,v in reversed(hist) if now-t>=900), hist[0][1] if hist else ov)
                        cache[s]["oi_change"]=((ov/old)-1)*100 if old else None
                    except Exception: pass

            diagnostics["last_success"]=time.time()
            await broadcast({"type":"snapshot","data":list(cache.values()),"server_ts":time.time(),"status":market_status})
        except Exception as e:
            log_error("BYBIT TICKER FALLBACK", e)
        await asyncio.sleep(5)

async def handle_bybit_ws(sock):
    args=[]
    for s in SYMBOLS:
        args += [f"tickers.{s}", f"kline.60.{s}", f"kline.240.{s}"]
    await sock.send(json.dumps({"op":"subscribe","args":args}))
    async for raw in sock:
        m=json.loads(raw)
        topic=m.get("topic","")
        if topic.startswith("tickers."):
            rows=m.get("data") or {}
            if isinstance(rows,list):
                rows=rows[0] if rows else {}
            s=topic.split(".")[-1]
            if s in cache and rows.get("lastPrice"):
                cache[s].update(price=float(rows["lastPrice"]), change=float(rows["price24hPcnt"])*100 if rows.get("price24hPcnt") is not None else cache[s]["change"], volume=float(rows.get("turnover24h",0)), funding=float(rows["fundingRate"]) if rows.get("fundingRate") is not None else cache[s]["funding"], oi=float(rows["openInterest"]) if rows.get("openInterest") is not None else cache[s]["oi"], ts=time.time(), source="Bybit Live")
                try:
                    ov=float(rows["openInterest"]); now=time.time(); hist=oi_history[s]; hist.append((now,ov)); del hist[:-180]
                    old=next((v for t,v in reversed(hist) if now-t>=900), hist[0][1] if hist else ov)
                    cache[s]["oi_change"]=((ov/old)-1)*100 if old else None
                except Exception: pass
                diagnostics["last_success"]=time.time()
                await broadcast({"type":"ticker","data":cache[s]})
        elif topic.startswith("kline."):
            parts=topic.split(".")
            if len(parts)==3:
                interval, s = parts[1], parts[2]
                if s in cache and interval in ("60","240"):
                    rows=m.get("data") or []
                    if rows:
                        k=rows[0]
                        item={"t":int(k["start"]),"o":float(k["open"]),"h":float(k["high"]),"l":float(k["low"]),"c":float(k["close"]),"v":float(k["volume"]),"q":float(k["turnover"])}
                        tf="1h" if interval=="60" else "4h"
                        arr=series[s][tf]
                        is_new_bar = bool(arr and item["t"] > arr[-1]["t"])
                        if not upsert_candle(arr, item):
                            log_error("BYBIT WS STALE CANDLE", f"{s} {tf} timestamp {item['t']} older than {arr[-1]['t']}")
                            continue
                        if interval=="60" and is_new_bar:
                            await analyze_symbol(s)
                            record_confirmed_signal(s, int(arr[-2]["t"]) + 3600000 if len(arr) >= 2 else None)
                        cache[s].update(price=item["c"], ts=time.time(), source="Bybit Live")
                        if time.time()-last_analysis[s] >= 3:
                            last_analysis[s]=time.time()
                            await analyze_symbol(s)
                            await broadcast({"type":"snapshot","data":list(cache.values()),"server_ts":time.time(),"status":market_status})

async def ws_loop():
    streams=[]
    for s in SYMBOLS:
        q=s.lower()
        streams += [f"{q}@miniTicker", f"{q}@kline_1h", f"{q}@kline_4h"]
    query="/".join(streams)
    while True:
        connected=False
        for base in FUTURES_WS:
            try:
                async with websockets.connect(base+"?streams="+query, ping_interval=20, ping_timeout=20, close_timeout=3, max_size=2**20) as sock:
                    connected=True
                    diagnostics["ws_source"]=base
                    market_status.update(status="LIVE", updated=time.time(), error=None)
                    await broadcast({"type":"status","status":"LIVE","source":"Binance Futures WS"})
                    async for raw in sock:
                        m=json.loads(raw); d=m.get("data",{})
                        if d.get("e")=="24hrMiniTicker":
                            s=d.get("s")
                            if s in cache:
                                cache[s].update(price=float(d["c"]), change=float(d["P"]), volume=float(d["q"]), ts=time.time(), source="Binance Futures")
                                diagnostics["last_success"]=time.time()
                                await broadcast({"type":"ticker","data":cache[s]})
                        elif d.get("e")=="kline":
                            s=d.get("s"); k=d.get("k",{})
                            if s in cache:
                                interval=k.get("i")
                                if interval in ("1h","4h"):
                                    item={"t":int(k["t"]),"o":float(k["o"]),"h":float(k["h"]),"l":float(k["l"]),"c":float(k["c"]),"v":float(k["v"]),"q":float(k["q"])}
                                    arr=series[s][interval]
                                    is_new_bar = bool(arr and item["t"] > arr[-1]["t"])
                                    if not upsert_candle(arr, item):
                                        log_error("BINANCE WS STALE CANDLE", f"{s} {interval} timestamp {item['t']} older than {arr[-1]['t']}")
                                        continue
                                    if interval=="1h" and is_new_bar:
                                        await analyze_symbol(s)
                                        record_confirmed_signal(s, int(arr[-2]["t"]) + 3600000 if len(arr) >= 2 else None)
                                    cache[s].update(price=float(k["c"]), ts=time.time(), source="Binance Futures")
                                    if time.time()-last_analysis[s] >= 3:
                                        last_analysis[s]=time.time()
                                        await analyze_symbol(s)
                                        await broadcast({"type":"snapshot","data":list(cache.values()),"server_ts":time.time(),"status":market_status})
                    break
            except Exception as e:
                log_error("BINANCE FUTURES WS", e)
        if not connected:
            try:
                async with websockets.connect(BYBIT_WS, ping_interval=20, ping_timeout=20, close_timeout=3, max_size=2**20) as sock:
                    connected=True
                    diagnostics["ws_source"]=BYBIT_WS
                    market_status.update(status="LIVE", updated=time.time(), error=None)
                    await broadcast({"type":"status","status":"LIVE","source":"Bybit Live WS"})
                    await handle_bybit_ws(sock)
            except Exception as e:
                log_error("BYBIT WS", e)
        if not connected:
            market_status.update(status="DELAYED", updated=time.time())
            await broadcast({"type":"status","status":"DELAYED"})
        await asyncio.sleep(2)

async def metrics_loop():
    while True:
        for s in SYMBOLS:
            try:
                fr = await futures_json("/fapi/v1/premiumIndex", {"symbol":s}, 2.0)
                oi = await futures_json("/fapi/v1/openInterest", {"symbol":s}, 2.0)
                cache[s]["funding"]=float(fr.get("lastFundingRate",0))
                cache[s]["oi"]=float(oi.get("openInterest",0))
            except Exception:
                pass
        await asyncio.sleep(120)

@app.on_event("startup")
async def startup():
    global http_client
    http_client=httpx.AsyncClient(
        timeout=httpx.Timeout(4.0, connect=2.0),
        limits=httpx.Limits(max_connections=40, max_keepalive_connections=20),
    )
    asyncio.create_task(seed_all())
    asyncio.create_task(ws_loop())
    asyncio.create_task(price_rest_fallback())
    asyncio.create_task(bybit_ticker_fallback())
    asyncio.create_task(metrics_loop())

@app.on_event("shutdown")
async def shutdown():
    global http_client
    if http_client:
        await http_client.aclose()

async def broadcast(obj):
    dead=[]
    msg=json.dumps(obj, separators=(",",":"))
    for ws in list(clients):
        try:
            await ws.send_text(msg)
        except Exception:
            dead.append(ws)
    for ws in dead: clients.discard(ws)

@app.websocket("/ws")
async def client_ws(ws: WebSocket):
    await ws.accept()
    clients.add(ws)
    try:
        await ws.send_text(json.dumps({"type":"snapshot","data":list(cache.values()),"server_ts":time.time(),"status":market_status}))
        while True:
            await ws.receive_text()
    except (WebSocketDisconnect, Exception):
        clients.discard(ws)
