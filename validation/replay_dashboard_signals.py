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
from datetime import datetime
from pathlib import Path

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "AVAXUSDT", "XRPUSDT"]
XRP = {"kill_slope": 0.0077, "atr_max": 0.0135, "shock_max": 0.025, "sl_atr": 1.7, "tp_atr": 2.8}


def ts_ms(v):
    dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError(f"Timestamp must include timezone: {v!r}")
    return int(dt.timestamp() * 1000)


def read_rows(path):
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            row = {"timestamp": r["timestamp"], "ts": ts_ms(r["timestamp"]),
                   "o": float(r["open"]), "h": float(r["high"]), "l": float(r["low"]),
                   "c": float(r["close"]), "v": float(r.get("volume") or 0),
                   "q": float(r.get("quote_volume") or 0)}
            if row["l"] > min(row["o"], row["c"]) or row["h"] < max(row["o"], row["c"]) or row["l"] > row["h"]:
                raise ValueError(f"Invalid OHLC values at {r['timestamp']} in {path}")
            rows.append(row)
    rows.sort(key=lambda x: x["ts"])
    if len({x["ts"] for x in rows}) != len(rows):
        raise ValueError(f"Duplicate timestamps in {path}")
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
    def avg(a): return sum(a[-n:])/n if len(a)>=n else None
    dxs=[]
    for j in range(n,len(tr)):
        t,p,m=avg(tr[:j+1]),avg(plus[:j+1]),avg(minus[:j+1])
        if t and t>0:
            pdi=100*p/t; mdi=100*m/t
            dxs.append(100*abs(pdi-mdi)/max(pdi+mdi,1e-9))
    return sum(dxs[-n:])/min(n,len(dxs)) if dxs else None


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


def replay(symbol, one, four):
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
        a=atr(hi,lo,c); r=rsi(c); ad=adx(hi,lo,c); vw=vwap(hi,lo,c,v); sup,res=levels(hi,lo,c)
        if None in (e20,e50,e200,e20_4,e50_4,a,r): continue
        side=""; entry=sl=target=None; score=0; reason="NO-TRADE"
        if symbol=="ETHUSDT":
            reason="NO_PRODUCTION"
        elif symbol=="XRPUSDT":
            slope=(e20_4/e50_4-1) if e50_4 else 0
            shock=abs(c[-1]/c[-2]-1) if len(c)>1 else 0
            kill=abs(slope)>XRP["kill_slope"] or a/p>XRP["atr_max"] or shock>XRP["shock_max"]
            dev=(p-e20)/a if a else 0
            mean_rev=r<=35 and dev<=-0.5 and p>c[-2]
            trend=e20_4>e50_4 and e20>e50
            pull=trend and c[-2]<=c[-3] and p>e20 and r<=55
            brk=trend and res is not None and p>res and p>c[-2]
            if not kill and (mean_rev or pull or brk):
                side="LONG"; entry=p; sl=p-XRP["sl_atr"]*a; target=p+XRP["tp_atr"]*a; score=75; reason="XRP_LONG"
            else: reason="XRP_KILL" if kill else "XRP_NO_ENTRY"
        else:
            bull=e20_4>e50_4 and p>e20_4
            bear=e20_4<e50_4 and p<e20_4
            if symbol=="SOLUSDT": rl=(40,52); rs=(48,60); sm=1.0; tm=2.0
            elif symbol=="AVAXUSDT": rl=(40,52); rs=(48,60); sm=1.2; tm=1.2
            else: rl=(45,55); rs=(45,55); sm=1.2; tm=1.5
            long_ok=bull and rl[0]<=r<=rl[1] and p>e20 and p>c[-2]
            short_ok=bear and rs[0]<=r<=rs[1] and p<e20 and p<c[-2]
            direction=1 if long_ok else -1 if short_ok else 0
            if direction:
                entry=p; sl=p-direction*sm*a; target=p+direction*tm*a
                vr=q[-1]/(sum(q[-21:-1])/20) if len(q)>=21 and sum(q[-21:-1]) else None
                trend_pts=30; rsi_pts=20; adx_pts=15 if ad and ad>=20 else 8 if ad and ad>=15 else 0
                vol_pts=15 if vr and vr>=1.1 else 8 if vr and vr>=0.8 else 0
                dist_support=max(0,p-sup) if sup else None
                dist_res=max(0,res-p) if res else None
                optimal=((direction==1 and dist_support is not None and dist_support/a<=2.5) or (direction==-1 and dist_res is not None and dist_res/a<=2.5))
                score=min(100,trend_pts+rsi_pts+adx_pts+vol_pts+(10 if optimal else 5))
                if score>=75: side="LONG" if direction==1 else "SHORT"; reason="ENTRY"
                else: entry=sl=target=None; reason="WATCH_SCORE_BELOW_75"
            elif bull and r<=rl[1]: reason="WATCH_LONG"
            elif bear and r>=rs[0]: reason="WATCH_SHORT"
        if side:
            output.append({"timestamp":one[i]["timestamp"],"side":side,"entry":entry,"atr":a,
                           "stop":sl,"target":target,"score":score,"model":symbol,
                           "model_version":"app.py-replay-v1","rsi":r,"ema20_1h":e20,"ema50_1h":e50,
                           "ema20_4h":e20_4,"ema50_4h":e50_4,"adx":ad,"vwap":vw,
                           "support":sup,"resistance":res,"reason":reason})
    return output


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data-dir",default="data/historical")
    p.add_argument("--symbols",nargs="+",default=SYMBOLS)
    p.add_argument("--out-dir",default="data/replay")
    a=p.parse_args()
    data=Path(a.data_dir); out=Path(a.out_dir); out.mkdir(parents=True,exist_ok=True)
    manifest=[]
    for s in a.symbols:
        one=read_rows(data/f"{s}_1h.csv"); four=read_rows(data/f"{s}_4h.csv")
        trades=replay(s,one,four)
        fields=["timestamp","side","entry","atr","stop","target","score","model","model_version","rsi","ema20_1h","ema50_1h","ema20_4h","ema50_4h","adx","vwap","support","resistance","reason"]
        dest=out/f"{s}_signals.csv"
        with dest.open("w",newline="",encoding="utf-8") as f:
            w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(trades)
        manifest.append({"symbol":s,"hourly_candles":len(one),"four_hour_candles":len(four),"signals":len(trades),"file":str(dest),
                         "notice":"Historical code replay only; verify parity and data gaps before interpreting results."})
        print(json.dumps(manifest[-1]))
    (out/"replay_manifest.json").write_text(json.dumps(manifest,indent=2),encoding="utf-8")


if __name__ == "__main__":
    main()
