import asyncio, json, time
from collections import defaultdict
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import httpx
import websockets

SYMBOLS = ["BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","XRPUSDT","DOGEUSDT","ADAUSDT","AVAXUSDT","LINKUSDT","SUIUSDT"]
BASE = "https://fapi.binance.com"
WS = "wss://fstream.binance.com/stream"
cache = {s: {"symbol":s,"price":None,"change":None,"volume":None,"ts":0} for s in SYMBOLS}
clients=set()

app=FastAPI(title="Crypto AI Trader V6")
app.mount("/static", StaticFiles(directory="/mnt/data/crypto_dashboard_v6/static"), name="static")

@app.get("/")
async def index(): return FileResponse("/mnt/data/crypto_dashboard_v6/static/index.html")

@app.get("/api/health")
async def health():
    now=time.time(); ages=[now-v["ts"] for v in cache.values() if v["ts"]]
    return {"ok": bool(ages), "age_sec": min(ages) if ages else None, "symbols_ready": sum(v["price"] is not None for v in cache.values())}

@app.get("/api/snapshot")
async def snapshot():
    return {"server_ts":time.time(),"data":list(cache.values())}

async def seed_rest():
    async with httpx.AsyncClient(timeout=4) as c:
        async def one(s):
            try:
                r=await c.get(BASE+"/fapi/v1/ticker/24hr",params={"symbol":s}); d=r.json()
                cache[s].update(price=float(d["lastPrice"]),change=float(d["priceChangePercent"]),volume=float(d["quoteVolume"]),ts=time.time())
            except Exception: pass
        await asyncio.gather(*(one(s) for s in SYMBOLS))

async def broadcast(obj):
    dead=[]
    msg=json.dumps(obj)
    for ws in list(clients):
        try: await ws.send_text(msg)
        except Exception: dead.append(ws)
    for ws in dead: clients.discard(ws)

async def ws_loop():
    streams=[]
    for s in SYMBOLS:
        x=s.lower(); streams += [f"{x}@miniTicker"]
    url=WS+"?streams="+"/".join(streams)
    while True:
        try:
            async with websockets.connect(url, ping_interval=20, ping_timeout=20, close_timeout=5, max_size=2**20) as sock:
                await broadcast({"type":"status","status":"LIVE"})
                async for raw in sock:
                    m=json.loads(raw); d=m.get("data",{})
                    s=d.get("s")
                    if s in cache:
                        cache[s].update(price=float(d.get("c")), change=float(d.get("P",0)), volume=float(d.get("q",0)), ts=time.time())
                        await broadcast({"type":"ticker","data":cache[s]})
        except Exception as e:
            await broadcast({"type":"status","status":"FALLBACK","error":str(e)[:120]})
            await seed_rest()
            await asyncio.sleep(3)

@app.on_event("startup")
async def startup():
    await seed_rest()
    asyncio.create_task(ws_loop())

@app.websocket("/ws")
async def client_ws(ws:WebSocket):
    await ws.accept(); clients.add(ws)
    try:
        await ws.send_text(json.dumps({"type":"snapshot","data":list(cache.values()),"server_ts":time.time()}))
        while True: await ws.receive_text()
    except WebSocketDisconnect: clients.discard(ws)
    except Exception: clients.discard(ws)
