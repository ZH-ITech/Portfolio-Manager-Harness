import time
from collections import defaultdict, deque

from fastapi import FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.analysis import technical
from app.data.market import DataSourceError, TickerNotFound, provider
from app.harness import audit
from app.harness.security import verify_ptc_token
from app.portfolio import parse_portfolio

app = FastAPI(title="Portfolio Manager Harness: AI Stock Assistant")
audit.init_db()

# ---------- simple per-client rate limit (PMH-12) ----------
RATE = 20          # chat messages
WINDOW = 300       # per 5 minutes
_hits: dict = defaultdict(deque)


def _rate_limit(key: str):
    now = time.time()
    q = _hits[key]
    while q and now - q[0] > WINDOW:
        q.popleft()
    if len(q) >= RATE:
        raise HTTPException(429, "Too many requests. Please wait a few minutes.")
    q.append(now)


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = None


@app.get("/api/health")
def health():
    return {"ok": True}


@app.post("/api/chat")
def chat(body: ChatIn, request: Request):
    from app.worker import run_chat_task
    _rate_limit(request.client.host if request.client else "anon")
    cid = body.conversation_id
    if cid and not audit.conversation_exists(cid):
        raise HTTPException(404, "conversation not found")
    if not cid:
        cid = audit.create_conversation(body.message)
    run_id = audit.create_run({"message": body.message}, conversation_id=cid)
    audit.add_message(cid, "user", body.message, run_id)
    run_chat_task.delay(run_id, cid, body.message)
    return {"conversation_id": cid, "run_id": run_id}


@app.get("/api/runs/{run_id}")
def get_run(run_id: str, events: bool = True):
    run = audit.get_run(run_id, with_events=events)
    if not run:
        raise HTTPException(404, "run not found")
    return run


@app.get("/api/conversations")
def conversations():
    return audit.list_conversations()


@app.get("/api/conversations/{cid}")
def conversation(cid: str):
    if not audit.conversation_exists(cid):
        raise HTTPException(404, "conversation not found")
    return {"id": cid, "messages": audit.get_messages(cid)}


# ---------- chart data for the UI (the model requests charts; data is served here) ----------
PERIODS = {"1mo", "3mo", "6mo", "1y", "2y", "5y"}


@app.get("/api/market/quote")
def quote(ticker: str, exchange: str = "GLOBAL"):
    """Latest price with freshness info (market state, exchange time, delay) for the live badge."""
    try:
        return provider(exchange).quote(ticker)
    except DataSourceError as e:
        raise HTTPException(503, str(e))
    except (TickerNotFound, NotImplementedError, ValueError) as e:
        raise HTTPException(404, str(e))


@app.get("/api/market/chart")
def chart(ticker: str, period: str = "1y", overlays: str = "sma_20,sma_50", exchange: str = "GLOBAL"):
    if period not in PERIODS:
        raise HTTPException(400, "bad period")
    try:
        p = provider(exchange)
        # fetch extra history so long averages are defined across the visible window
        lookback = {"1mo": "6mo", "3mo": "1y", "6mo": "2y", "1y": "2y", "2y": "5y", "5y": "max"}[period]
        df = p.ohlcv(ticker, lookback)
        data = technical.chart_series(df, [o for o in overlays.split(",") if o])
        visible = df.index[df.index >= df.index[-1] - _offset(period)]
        start = visible[0].strftime("%Y-%m-%d")
        trim = lambda pts: [x for x in pts if x["time"] >= start]  # noqa: E731
        data["candles"], data["volume"] = trim(data["candles"]), trim(data["volume"])
        data["overlays"] = {k: trim(v) for k, v in data["overlays"].items()}
        data["panes"] = {k: trim(v) for k, v in data["panes"].items()}
        return {"ticker": ticker.upper(), "period": period, **data, "source": p.name}
    except DataSourceError as e:
        raise HTTPException(503, str(e))
    except (TickerNotFound, NotImplementedError, ValueError) as e:
        raise HTTPException(404, str(e))


@app.get("/api/market/compare")
def compare(tickers: str, period: str = "1y", exchange: str = "GLOBAL"):
    if period not in PERIODS:
        raise HTTPException(400, "bad period")
    names = [t.strip().upper() for t in tickers.split(",") if t.strip()][:5]
    if len(names) < 2:
        raise HTTPException(400, "give 2-5 tickers")
    p = provider(exchange)
    out = {}
    try:
        for t in names:
            c = p.ohlcv(t, period)["close"]
            out[t] = [{"time": i.strftime("%Y-%m-%d"), "value": round(float(v / c.iloc[0] * 100), 3)} for i, v in c.items()]
    except DataSourceError as e:
        raise HTTPException(503, str(e))
    except (TickerNotFound, NotImplementedError) as e:
        raise HTTPException(404, str(e))
    return {"period": period, "normalised_to": 100, "series": out, "source": p.name}


def _offset(period):
    import pandas as pd
    return {"1mo": pd.DateOffset(months=1), "3mo": pd.DateOffset(months=3), "6mo": pd.DateOffset(months=6),
            "1y": pd.DateOffset(years=1), "2y": pd.DateOffset(years=2), "5y": pd.DateOffset(years=5)}[period]


# ---------- optional: portfolio upload (PMH-10) ----------
@app.post("/api/portfolio/upload")
async def upload(file: UploadFile = File(...)):
    try:
        return {"holdings": parse_portfolio(await file.read(), file.filename or "upload.xlsx")}
    except Exception as e:
        raise HTTPException(400, str(e))


# ---------- PTC proxy: sandboxed code -> registered MCP tools (PMH-17) ----------
@app.post("/internal/tools/{name}")
def ptc_proxy(name: str, args: dict, x_ptc_token: str = Header(default="")):
    run_id = verify_ptc_token(x_ptc_token)
    if not run_id:
        raise HTTPException(403, "invalid sandbox token")
    from app.harness.mcp_hub import get_hub
    audit.log(run_id, "sandbox", "ptc_call", {"tool": name, "input": args})
    try:
        result = get_hub().call(name, args)
        audit.log(run_id, "sandbox", "ptc_result", {"tool": name, "result": result})
        return {"ok": True, "result": result}
    except Exception as e:
        audit.log(run_id, "sandbox", "ptc_error", {"tool": name, "error": repr(e)})
        return {"ok": False, "error": repr(e)}


app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def index():
    return FileResponse("static/index.html")
