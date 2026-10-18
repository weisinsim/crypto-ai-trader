#!/usr/bin/env python3
"""Replay asset-specific dashboard signals using only candles closed at signal time.

Input CSVs are produced by validation/download_futures_klines.py, one 1h and
one 4h file per symbol. Outputs signals suitable for exit_parameter_audit.py.
This is a code-replay research tool, not a claim that these signals were emitted
live and not a profitability guarantee.
"""
import argparse
import csv
import json
import math
from datetime import datetime
from pathlib import Path

from validation.xrp_model import evaluate_xrp_signal

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "AVAXUSDT", "XRPUSDT"]
XRP = {"kill_slope": 0.0077, "atr_max": 0.0135, "shock_max": 0.025, "sl_atr": 1.7, "tp_atr": 2.8}


def ts_ms(v):
    dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError(f"Timestamp must include timezone: {v!r}")
    if dt.microsecond % 1000:
        raise ValueError(f"Timestamp must have millisecond precision: {v!r}")
    return int(dt.timestamp() * 1000)


def read_rows(path):
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        required = {"timestamp", "open", "high", "low", "close", "volume", "quote_volume"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Missing required columns in {path}: {sorted(missing)}")
        for r in reader:
            try:
                row = {"timestamp": r["timestamp"], "ts": ts_ms(r["timestamp"]),
                       "o": float(r["open"]), "h": float(r["high"]), "l": float(r["low"]),
                       "c": float(r["close"]), "v": float(r["volume"]),
                       "q": float(r["quote_volume"])}
            except (TypeError, ValueError, KeyError) as exc:
                raise ValueError(f"Invalid timestamp or numeric OHLCV field in {path}: {r}") from exc
            if (not all(math.isfinite(row[k]) for k in ("o", "h", "l", "c", "v", "q"))
                    or not all(row[k] > 0 for k in ("o", "h", "l", "c"))
                    or row["v"] < 0 or row["q"] < 0
                    or row["l"] > min(row["o"], row["c"])
                    or row["h"] < max(row["o"], row["c"])
                    or row["l"] > row["h"]):
                raise ValueError(f"Invalid OHLCV values at {r['timestamp']} in {path}")
            rows.append(row)
    previous_ts = None
    for index, row in enumerate(rows):
        if previous_ts is not None and row["ts"] <= previous_ts:
            reason = "duplicate" if row["ts"] == previous_ts else "out-of-order"
            raise ValueError(
                f"{reason.capitalize()} candle timestamp at row {index} in {path}; "
                "replay input must be strictly chronological"
            )
        previous_ts = row["ts"]
    return rows


def validate_candle_cadence(rows, interval_ms, source="candle series"):
    """Reject missing intervals instead of treating nonconsecutive bars as adjacent."""
    if not isinstance(interval_ms, int) or isinstance(interval_ms, bool) or interval_ms <= 0:
        raise ValueError("interval_ms must be a positive integer")
    for index in range(1, len(rows)):
        delta = rows[index]["ts"] - rows[index - 1]["ts"]
        if delta != interval_ms:
            raise ValueError(
                f"Unexpected candle spacing in {source} at row {index}: "
                f"expected {interval_ms} ms, got {delta} ms; check for missing or duplicate bars"
            )
    return rows


def ema(v, n):
    if len(v) < n: return None
    k = 2 / (n + 1)
    e = sum(v[:n]) / n
    for x in v[n:]: e = x * k + e * (1-k)
    return e


def rsi(v, n=14):
    if len(v) < n+1: return None
    ds = [v[i]-v[i-1] for i in range(1, len(v))]
    gains = [max(d, 0) for d in ds]; losses = [max(-d, 0) for d in ds]
    ag, al = sum(gains[:n])/n, sum(losses[:n])/n
    for i in range(n, len(ds)):
        ag = (ag*(n-1)+gains[i])/n
        al = (al*(n-1)+losses[i])/n
    return 100.0 if al == 0 else 100 - 100/(1+ag/al)


def atr(h, l, c, n=14):
    if len(c) < n+1: return None
    tr = [max(h[i]-l[i], abs(h[i]-c[i-1]), abs(l[i]-c[i-1])) for i in range(1, len(c))]
    return sum(tr[-n:])/n


def adx(h, l, c, n=14):
    if len(c) < n*2+1: return None
    tr=[]; plus=[]; minus=[]
    for i in range(1,len(c)):
        up=h[i]-h[i-1]; dn=l[i-1]-l[i]
        tr.append(max(h[i]-l[i],abs(h[i]-c[i-1]),abs(l[i]-c[i-1])))
        plus.append(up if up>dn and up>0 else 0)
        minus.append(dn if dn>up and dn>0 else 0)
    # The output only uses the last n valid DX values. Walk backwards instead
    # of recomputing DX for every historical bar on every replay iteration.
    # This preserves the original rolling-window calculation while avoiding
    # needless work over the full history for each hourly signal.
    dxs=[]
    for j in range(len(tr)-1, n-1, -1):
        start=j-n+1
        t=sum(tr[start:j+1])/n
        if t > 0:
            p=sum(plus[start:j+1])/n
            m=sum(minus[start:j+1])/n
            pdi=100*p/t; mdi=100*m/t
            dxs.append(100*abs(pdi-mdi)/max(pdi+mdi,1e-9))
            if len(dxs) == n:
                break
    return sum(dxs)/len(dxs) if dxs else None


def vwap(h,l,c,q,n=48):
    m=min(n,len(c))
    den=sum(q[-m:])
    return sum(((h[i]+l[i]+c[i])/3)*q[i] for i in range(len(c)-m,len(c)))/den if den else None


def levels(h,l,c,n=48):
    m=min(n,len(c))
    if m<5: return None,None
    return min(l[-m:-1]),max(h[-m:-1])


def closed_rows_at(rows, timestamp_ms):
    """Return only bars whose close timestamp is no later than the decision time."""
    return [row for row in rows if row["ts"] <= timestamp_ms]


def risk_levels(symbol, price, direction, atr_value, stop_mult, target_mult):
    """Match the live dashboard's ATR fallback when calculating stop/target levels."""
    fallback = price * (0.01 if symbol == "XRPUSDT" else 0.005)
    distance = atr_value or fallback
    target2_mult = 3.5 if symbol == "XRPUSDT" else target_mult * 1.5
    return (
        price - direction * stop_mult * distance,
        price + direction * target_mult * distance,
        price + direction * target2_mult * distance,
    )


def signal_state_for(side, reason, direction):
    """Map replay decisions to the directional signal labels used for parity review."""
    if side:
        return side
    if reason == "WATCH_LONG" or (reason == "WATCH_SCORE_BELOW_75" and direction == 1):
        return "WATCH-LONG"
    if reason == "WATCH_SHORT" or (reason == "WATCH_SCORE_BELOW_75" and direction == -1):
        return "WATCH-SHORT"
    return "NO-TRADE"


def replay(symbol, one, four, *, include_states=False):
    output=[]
    # For each hourly close, use hourly candles through that close and 4h candles
    # whose close timestamp is no later than this hourly close.
    for i in range(199, len(one)):
        now=one[i]["ts"]
        h1=one[:i+1]
        h4=closed_rows_at(four, now)
        if len(h4)<50: continue
        c=[x["c"] for x in h1]; hi=[x["h"] for x in h1]; lo=[x["l"] for x in h1]; v=[x["v"] for x in h1]; q=[x["q"] for x in h1]
        c4=[x["c"] for x in h4]
        p=c[-1]; e20=ema(c,20); e50=ema(c,50); e200=ema(c,200)
        e20_4=ema(c4,20); e50_4=ema(c4,50)
        a=atr(hi,lo,c); r=rsi(c); ad=adx(hi,lo,c); vw=vwap(hi,lo,c,q); sup,res=levels(hi,lo,c)
        if None in (e20,e50,e200,e20_4,e50_4,a,r): continue
        side=""; entry=sl=target=None; score=0; direction=0; reason="NO-TRADE"
        if symbol=="ETHUSDT":
            reason="NO_PRODUCTION"
        elif symbol=="XRPUSDT":
            xrp = evaluate_xrp_signal(c, e20, e50, e20_4, e50_4, r, a, res, XRP)
            signal = xrp["signal"]
            side = "LONG" if signal == "LONG" else ""
            direction = xrp["direction"]
            entry = xrp["entry"]
            sl = xrp["stop"]
            target = xrp["target1"]
            score = xrp["score"]
            reason = "XRP_LONG" if signal == "LONG" else "WATCH_LONG" if signal == "WATCH-LONG" else "XRP_KILL" if xrp["kill"] else "XRP_NO_ENTRY"
        else:
            bull=e20_4>e50_4 and p>e20_4
            bear=e20_4<e50_4 and p<e20_4
            if symbol=="SOLUSDT": rl=(40,52); rs=(48,60); sm=1.0; tm=2.0
            elif symbol=="AVAXUSDT": rl=(40,52); rs=(48,60); sm=1.2; tm=1.2
            else: rl=(45,55); rs=(45,55); sm=1.2; tm=1.5
            long_ok=bull and r is not None and rl[0]<=r<=rl[1] and p>e20 and p>c[-2]
            short_ok=bear and r is not None and rs[0]<=r<=rs[1] and p<e20 and p<c[-2]
            direction=1 if long_ok else -1 if short_ok else 0
            if not direction and bull and r is not None and r<=rl[1]:
                direction=1; reason="WATCH_LONG"
            elif not direction and bear and r is not None and r>=rs[0]:
                direction=-1; reason="WATCH_SHORT"
            if direction:
                entry=p
                sl,target,_=risk_levels(symbol,p,direction,a,sm,tm)
                vr=q[-1]/(sum(q[-21:-1])/20) if len(q)>=21 and sum(q[-21:-1]) else None
                trend_pts=30 if (direction==1 and bull) or (direction==-1 and bear) else 0
                rsi_pts=20 if ((direction==1 and rl[0]<=r<=rl[1]) or (direction==-1 and rs[0]<=r<=rs[1])) else 0
                adx_pts=15 if ad and ad>=20 else 8 if ad and ad>=15 else 0
                vol_pts=15 if vr and vr>=1.1 else 8 if vr and vr>=0.8 else 0
                dist_support=max(0,p-sup) if sup else None
                dist_res=max(0,res-p) if res else None
                optimal=((direction==1 and dist_support is not None and dist_support/a<=2.5) or (direction==-1 and dist_res is not None and dist_res/a<=2.5))
                score=min(100,int(trend_pts+rsi_pts+adx_pts+vol_pts+(10 if optimal else 5)))
                if long_ok or short_ok:
                    if score>=75: side="LONG" if direction==1 else "SHORT"; reason="ENTRY"
                    else: reason="WATCH_SCORE_BELOW_75"
                elif reason=="NO-TRADE":
                    reason="WATCH_LONG" if direction==1 else "WATCH_SHORT"
        signal_state = signal_state_for(side, reason, direction)
        status = side if side else ("WATCH" if signal_state.startswith("WATCH-") else "NO-TRADE")
        if side or include_states:
            output.append({"timestamp":one[i]["timestamp"],"status":status,"signal":signal_state,"side":side,
                           "entry":entry,"atr":a,"stop":sl,"target":target,
                           "target2":risk_levels(symbol,p,direction,a,(1.2 if symbol=="BTCUSDT" else 1.0 if symbol=="SOLUSDT" else 1.2 if symbol=="AVAXUSDT" else XRP["sl_atr"]), (1.5 if symbol=="BTCUSDT" else 2.0 if symbol=="SOLUSDT" else 1.2 if symbol=="AVAXUSDT" else XRP["tp_atr"]))[2] if direction else None,
                           "score":score,"model":symbol,"model_version":"app.py-replay-v2",
                           "rsi":r,"ema20_1h":e20,"ema50_1h":e50,"ema20_4h":e20_4,
                           "ema50_4h":e50_4,"adx":ad,"vwap":vw,"support":sup,
                           "resistance":res,"reason":reason})
    return output


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data-dir",default="data/historical")
    p.add_argument("--symbols",nargs="+",default=SYMBOLS)
    p.add_argument("--out-dir",default="data/replay")
    p.add_argument("--include-states", action="store_true", help="Also export hourly WATCH/NO-TRADE states for parity review")
    a=p.parse_args()
    data=Path(a.data_dir); out=Path(a.out_dir); out.mkdir(parents=True,exist_ok=True)
    manifest=[]
    for s in a.symbols:
        one_path=data/f"{s}_1h.csv"; four_path=data/f"{s}_4h.csv"
        one=validate_candle_cadence(read_rows(one_path), 3_600_000, str(one_path))
        four=validate_candle_cadence(read_rows(four_path), 14_400_000, str(four_path))
        trades=replay(s,one,four)
        fields=["timestamp","status","signal","side","entry","atr","stop","target","target2","score","model","model_version","rsi","ema20_1h","ema50_1h","ema20_4h","ema50_4h","adx","vwap","support","resistance","reason"]
        if a.include_states:
            states=replay(s,one,four,include_states=True)
            with (out/f"{s}_states.csv").open("w",newline="",encoding="utf-8") as sf:
                sw=csv.DictWriter(sf,fieldnames=fields); sw.writeheader(); sw.writerows(states)
        dest=out/f"{s}_signals.csv"
        with dest.open("w",newline="",encoding="utf-8") as f:
            w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(trades)
        manifest.append({"symbol":s,"hourly_candles":len(one),"four_hour_candles":len(four),"signals":len(trades),"file":str(dest),
                         "notice":"Historical code replay only; verify parity and data gaps before interpreting results."})
        print(json.dumps(manifest[-1]))
    (out/"replay_manifest.json").write_text(json.dumps(manifest,indent=2),encoding="utf-8")


if __name__ == "__main__":
    main()
