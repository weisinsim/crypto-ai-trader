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

SYMBOLS = ["XRPUSDT"]

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
        "confirmed_signal": "NO-TRADE", "confirmed_signal_time": None, "confirmed_entry": None, "confirmed_sl": None, "confirmed_tp1": None, "confirmed_tp2": None, "confirmed_score": 0, "confirmed_score_label": "NO-TRADE", "confirmed_status": "WAITING",
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

MODEL_NAME = "V27.2 XRP LONG-ONLY Institutional"
MODEL_VERSION = "V27.2"
XRP_PARAMS = {"kill_slope": 0.0077, "atr_max": 0.0135, "shock_max": 0.025, "sl_atr": 1.7, "tp_atr": 2.8, "direction": "LONG_ONLY"}
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
    now = time.time()
    ready = sum(v["price"] is not None for v in cache.values())
    analyzed = sum(v["analysis_ts"] > 0 for v in cache.values())
    ages = [now-v["ts"] for v in cache.values() if v["ts"]]
    return {
        "ok": ready > 0,
        "status": market_status["status"],
        "symbols_ready": ready,
        "analysis_ready": analyzed,
        "age_sec": min(ages) if ages else None,
        "source": diagnostics["rest_source"] or diagnostics["ws_source"] or diagnostics["fallback_source"],
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
        "errors": {s: v["error"] for s, v in cache.items() if v["error"]},
    }

@app.get("/api/snapshot")
async def snapshot():
    return {"server_ts": time.time(), "status": market_status, "data": list(cache.values())}

@app.get("/api/signals")
async def signals():
    return {"server_ts": time.time(), "history": signal_history, "confirmed": {s: {k: cache[s].get(k) for k in ("confirmed_signal","confirmed_signal_time","confirmed_entry","confirmed_sl","confirmed_tp1","confirmed_tp2","confirmed_score","confirmed_score_label","confirmed_status")} for s in SYMBOLS}}

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
    return {"t": int(x[0]), "o": float(x[1]), "h": float(x[2]), "l": float(x[3]),
            "c": float(x[4]), "v": float(x[5]), "q": float(x[7])}

def set_series(s, interval, klines):
    series[s][interval] = [kline_to_obj(x) for x in klines][-210:]

def record_confirmed_signal(s, candle_close_ms=None):
    """Freeze the 1H signal at candle close so intrabar flips do not create fake entries."""
    sig = cache[s].get("signal") or "NO-TRADE"
    prev = confirmed_state[s]
    ts_ms = int(candle_close_ms) if candle_close_ms else int(time.time() * 1000)
    if sig == prev:
        return False

    if prev in ("LONG", "SHORT") and sig not in ("LONG", "SHORT"):
        cache[s]["confirmed_status"] = "INVALIDATED"
        if signal_history[s]:
            signal_history[s][0]["status"] = "INVALIDATED"
            signal_history[s][0]["ended_at"] = ts_ms

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
            futures_json("/fapi/v1/klines", {"symbol": s, "interval": "1h", "limit": 210}, 3.0),
            futures_json("/fapi/v1/klines", {"symbol": s, "interval": "4h", "limit": 210}, 3.0),
        )
        set_series(s, "1h", k1)
        set_series(s, "4h", k4)
        await analyze_symbol(s)
        cache[s]["source"] = "Binance Futures"
        return True
    except Exception as e:
        log_error(f"SEED {s}", e)
        try:
            b1, b4 = await asyncio.gather(
                bybit_json("/v5/market/kline", {"category":"linear","symbol":s,"interval":"60","limit":210}, 3.0),
                bybit_json("/v5/market/kline", {"category":"linear","symbol":s,"interval":"240","limit":210}, 3.0),
            )
            def bybit_rows(obj):
                rows = obj["list"]
                rows.reverse()
                return [{"t":int(x[0]),"o":float(x[1]),"h":float(x[2]),"l":float(x[3]),"c":float(x[4]),"v":float(x[5]),"q":float(x[6])} for x in rows]
            series[s]["1h"] = bybit_rows(b1)[-210:]
            series[s]["4h"] = bybit_rows(b4)[-210:]
            cache[s]["source"] = "Bybit fallback"
            await analyze_symbol(s)
            return True
        except Exception as e2:
            log_error(f"FALLBACK {s}", e2)
            cache[s]["error"] = str(e2)[:120]
            return False

async def analyze_symbol(s):
    k1, k4 = series[s]["1h"], series[s]["4h"]
    if len(k1) < 205 or len(k4) < 50:
        return False
    try:
        c1=[x["c"] for x in k1]; h1=[x["h"] for x in k1]; l1=[x["l"] for x in k1]; q1=[x["q"] for x in k1]
        c4=[x["c"] for x in k4]
        p=c1[-1]
        e20=ema(c1,20); e50=ema(c1,50); e200=ema(c1,200)
        e20_4=ema(c4,20); e50_4=ema(c4,50); e200_4=ema(c4,200)
        r=rsi(c1); a=atr(h1,l1,c1)
        vr=q1[-1]/(sum(q1[-21:-1])/20) if len(q1)>=21 else None
        trend4="BULL" if p>e20_4>e50_4 else ("BEAR" if p<e20_4<e50_4 else "RANGE")
        score=(2 if p>e20_4 else -2)+(2 if e20_4>e50_4 else -2)+(1 if p>e20 else -1)+(1 if e20>e50 else -1)+(1 if r is not None and r>=50 else -1)+(1 if vr is not None and vr>=1.2 else 0)
        signal="NO-TRADE"
        # V27.2 XRP: XRP-only LONG reversal model. Frozen production-candidate logic.
        # Kill switches: excessive trend slope, excessive ATR, or shock candle.
        slope5 = ((e20_4 / e50_4) - 1) if (e20_4 and e50_4) else 0
        atr_pct_raw = (a / p) if (a and p) else 0
        shock_raw = abs(c1[-1] / c1[-2] - 1) if len(c1) > 1 else 0
        xrp_kill = abs(slope5) > XRP_PARAMS["kill_slope"] or atr_pct_raw > XRP_PARAMS["atr_max"] or shock_raw > XRP_PARAMS["shock_max"]
        xrp_dev = ((p - e20) / a) if (a and e20) else 0
        xrp_mean = r is not None and r <= 35 and xrp_dev <= -0.5 and p > c1[-2]
        xrp_trend = e20_4 > e50_4 and e20 > e50
        xrp_pullback = xrp_trend and c1[-2] <= c1[-3] and p > e20 and r is not None and r <= 55
        xrp_res = max(h1[-21:-1]) if len(h1) >= 22 else None
        xrp_breakout = xrp_trend and xrp_res is not None and p > xrp_res and p > c1[-2]
        if not xrp_kill and (xrp_mean or xrp_pullback or xrp_breakout):
            signal="LONG"
        elif not xrp_kill and ((r is not None and r <= 40) or xrp_trend):
            signal="WATCH-LONG"
        dist=(a*1.5 if a else p*0.01)
        if signal in ("LONG","WATCH-LONG"): entry,sl,tp1,tp2=p,p-(XRP_PARAMS["sl_atr"]*(a or p*0.01)),p+(XRP_PARAMS["tp_atr"]*(a or p*0.01)),p+(3.5*(a or p*0.01))
        else: entry=sl=tp1=tp2=None

        # V8 technical model: trend, momentum, volume, volatility, market structure, derivatives and BTC regime.
        direction = 1 if signal in ("LONG","WATCH-LONG") else 0
        adx_v = adx(h1,l1,c1,14)
        vwap_v = vwap(h1,l1,c1,q1,48)
        support,resistance = sr_levels(h1,l1,c1,48)
        atr_pct = (a/p*100) if a and p else None

        # BTC market filter: alt LONG prefers BTC above 4H EMA50/200; SHORT prefers below.
        btc_filter = "NEUTRAL"
        if s != "BTCUSDT" and cache["BTCUSDT"].get("price") is not None:
            bp=cache["BTCUSDT"].get("price"); be50=cache["BTCUSDT"].get("ema50_4h"); be200=cache["BTCUSDT"].get("ema200_4h")
            if be50 and be200:
                if bp>be50>be200: btc_filter="BULL"
                elif bp<be50<be200: btc_filter="BEAR"
                else: btc_filter="NEUTRAL"

        trend_score=0; align_score=0; adx_score=0; rsi_score=0; vol_score=0; sr_score=0; vwap_score=0; atr_score=0; derivatives_score=0; btc_score=0; entry_location_score=0; entry_location="—"
        if direction:
            trend_score = (
                (10 if ((direction==1 and p>e200_4) or (direction==-1 and p<e200_4)) else 0) +
                (5 if ((direction==1 and e20_4>e50_4) or (direction==-1 and e20_4<e50_4)) else 0) +
                (5 if ((direction==1 and p>e20_4) or (direction==-1 and p<e20_4)) else 0)
            )
            align_score = (
                (5 if ((direction==1 and p>e200) or (direction==-1 and p<e200)) else 0) +
                (5 if ((direction==1 and p>e20) or (direction==-1 and p<e20)) else 0) +
                (5 if ((direction==1 and e20>e50) or (direction==-1 and e20<e50)) else 0)
            )
            adx_score = 10 if adx_v is not None and adx_v>=25 else 6 if adx_v is not None and adx_v>=20 else 2 if adx_v is not None and adx_v>=15 else 0
            if r is not None:
                if direction==1: rsi_score=10 if 52<=r<=68 else 6 if 48<=r<=72 else 0
                else: rsi_score=10 if 32<=r<=48 else 6 if 28<=r<=52 else 0
            vol_score = 10 if vr is not None and vr>=1.5 else 7 if vr is not None and vr>=1.2 else 3 if vr is not None and vr>=0.8 else 0
            # Entry-location score: reward entries close to support (LONG) or resistance (SHORT), penalize chasing.
            if a and a>0 and support is not None and resistance is not None:
                if direction==1:
                    dist_support=max(0.0,p-support); atr_units=dist_support/a
                    if p < support: entry_location="BELOW SUPPORT"
                    elif atr_units<=1.5: entry_location="OPTIMAL"
                    elif atr_units<=2.5: entry_location="GOOD"
                    elif atr_units<=3.0: entry_location="FAR"
                    else: entry_location="CHASE RISK"
                    entry_location_score=5 if entry_location=="OPTIMAL" else 4 if entry_location=="GOOD" else 2 if entry_location=="FAR" else 0
                elif direction==-1:
                    dist_res=max(0.0,resistance-p); atr_units=dist_res/a
                    if p > resistance: entry_location="ABOVE RESISTANCE"
                    elif atr_units<=1.5: entry_location="OPTIMAL"
                    elif atr_units<=2.5: entry_location="GOOD"
                    elif atr_units<=3.0: entry_location="FAR"
                    else: entry_location="CHASE RISK"
                    entry_location_score=5 if entry_location=="OPTIMAL" else 4 if entry_location=="GOOD" else 2 if entry_location=="FAR" else 0
            if support is not None and resistance is not None:
                risk_dist=abs(p-sl) if sl is not None else (a*1.5 if a else p*0.01)
                room=(resistance-p) if direction==1 else (p-support)
                if room>0:
                    sr_score=10 if room>=2*risk_dist else 6 if room>=risk_dist else 0
            if vwap_v is not None:
                vwap_score=5 if ((direction==1 and p>vwap_v) or (direction==-1 and p<vwap_v)) else 0
            if atr_pct is not None:
                atr_score=5 if 0.5<=atr_pct<=4 else 2 if atr_pct<=6 else 0
            funding_now=cache[s].get("funding")
            oi_ch=cache[s].get("oi_change")
            if funding_now is not None:
                f=abs(funding_now)*100
                derivatives_score += 5 if f<=0.01 else 3 if f<=0.03 else 0
            if oi_ch is not None:
                # Rising OI with price in the signal direction is confirmation; falling OI is weaker.
                oi_confirm=(oi_ch>0.5 and ((direction==1 and p>e20) or (direction==-1 and p<e20)))
                derivatives_score += 5 if oi_confirm else 2 if oi_ch>-0.5 else 0
            if btc_filter == ("BULL" if direction==1 else "BEAR"):
                btc_score=5
            elif btc_filter == "NEUTRAL":
                btc_score=3

        score=int(min(100,trend_score+align_score+adx_score+rsi_score+vol_score+sr_score+vwap_score+atr_score+derivatives_score+btc_score+entry_location_score))
        score_label="A+" if score>=85 else "A" if score>=75 else "B" if score>=65 else "C" if score>=50 else "NO-TRADE"

        # Hard risk filters: avoid weak trend, poor room to the next level, or a BTC regime conflict.
        hard_no_trade = bool(direction and (
            (adx_v is not None and adx_v<15) or
            (direction==1 and resistance is not None and sl is not None and resistance-p < abs(p-sl)) or
            (direction==-1 and support is not None and sl is not None and p-support < abs(p-sl)) or
            entry_location in ("CHASE RISK","BELOW SUPPORT","ABOVE RESISTANCE")
        ))
        if hard_no_trade:
            score=min(score,49)
            score_label="NO-TRADE"
            # Hard filters are actionable filters: the displayed signal must agree with them.
            signal="NO-TRADE"
            entry=sl=tp1=tp2=None
            direction=0

        # $1,000 account, 1% max loss per trade. Position sizing is risk-based.
        risk_usd = 10.0
        qty = (risk_usd/abs(entry-sl)) if entry is not None and sl is not None and abs(entry-sl)>0 else None
        position_usd = qty*entry if qty is not None else None
        # $1,000 account, max 3x notional = $3,000.
        if position_usd is not None and position_usd > 3000.0:
            position_usd = 3000.0
            qty = position_usd/entry
        margin_3x = position_usd/3 if position_usd is not None else None

        # V27.2 is intentionally LONG-only; SHORT signals are impossible by design.
        if signal in ("SHORT","WATCH-SHORT"):
            signal="NO-TRADE"
            entry=sl=tp1=tp2=None
            direction=0
        cache[s].update(
            price=p, ema20_1h=round_price(e20), ema50_1h=round_price(e50), ema200_1h=round_price(e200),
            ema20_4h=round_price(e20_4), ema50_4h=round_price(e50_4), ema200_4h=round_price(e200_4),
            rsi_1h=round(r,1) if r is not None else None, atr_1h=round_price(a),
            vol_ratio=round(vr,2) if vr is not None else None, trend=trend4, signal=signal, live_signal=signal,
            entry=round_price(entry), sl=round_price(sl), tp1=round_price(tp1), tp2=round_price(tp2),
            rr=2.0 if signal!="NO-TRADE" else None, score=score, score_label=score_label,
            adx_1h=round(adx_v,1) if adx_v is not None else None, vwap_1h=round_price(vwap_v), support=round_price(support), resistance=round_price(resistance), atr_pct=round(atr_pct,2) if atr_pct is not None else None, btc_filter=btc_filter, entry_location=entry_location, entry_location_score=entry_location_score,
            score_breakdown={"trend":trend_score,"alignment":align_score,"adx":adx_score,"rsi":rsi_score,"volume":vol_score,"support_resistance":sr_score,"vwap":vwap_score,"atr":atr_score,"funding_oi":derivatives_score,"btc_filter":btc_score,"entry_location":entry_location_score},
            position_qty=round(qty,6) if qty is not None else None,
            position_usd=round(position_usd,2) if position_usd is not None else None,
            margin_3x=round(margin_3x,2) if margin_3x is not None else None,
            analysis_ts=time.time(), ts=time.time(), error=None
        )
        return True
    except Exception as e:
        log_error(f"ANALYSIS {s}", e)
        return False

async def seed_all():
    await asyncio.gather(*(seed_symbol(s) for s in SYMBOLS), return_exceptions=True)
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
                        is_new_bar = bool(arr and arr[-1]["t"] != item["t"])
                        if interval=="60" and is_new_bar:
                            await analyze_symbol(s)
                            record_confirmed_signal(s, int(arr[-1]["t"]) + 3600000)
                        if arr and arr[-1]["t"]==item["t"]: arr[-1]=item
                        else: arr.append(item); del arr[:-210]
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
                                    is_new_bar = bool(arr and arr[-1]["t"] != item["t"])
                                    if interval=="1h" and is_new_bar:
                                        await analyze_symbol(s)
                                        record_confirmed_signal(s, int(arr[-1]["t"]) + 3600000)
                                    if arr and arr[-1]["t"]==item["t"]: arr[-1]=item
                                    else: arr.append(item); del arr[:-210]
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
