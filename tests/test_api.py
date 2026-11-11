import numpy as np
from fastapi.testclient import TestClient



def _client(tmp_db, monkeypatch):
    from app import main
    from app.harness import agent
    monkeypatch.setattr(agent, "run_chat", lambda run_id, cid, msg, progress=None: (
        progress("Fetching price history", 0.5),
        {"text": f"echo: {msg}", "charts": [], "tables": [], "sources": [{"source": "test"}], "assumptions": ["a"]})[1])
    main._hits.clear()
    return TestClient(main.app)


def test_chat_flow_saves_results_and_history(tmp_db, monkeypatch):
    c = _client(tmp_db, monkeypatch)
    r = c.post("/api/chat", json={"message": "hello"}).json()
    run = c.get(f"/api/runs/{r['run_id']}").json()
    assert run["status"] == "done" and run["progress"] == 1.0 and run["report"]["text"] == "echo: hello"
    r2 = c.post("/api/chat", json={"message": "follow up", "conversation_id": r["conversation_id"]}).json()
    assert r2["conversation_id"] == r["conversation_id"]
    msgs = c.get(f"/api/conversations/{r['conversation_id']}").json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant"]
    assert c.get("/api/conversations").json()[0]["id"] == r["conversation_id"]


def test_failed_run_is_reported(tmp_db, monkeypatch):
    c = _client(tmp_db, monkeypatch)
    from app.harness import agent

    def boom(*a, **k):
        raise ValueError("bad ticker")
    monkeypatch.setattr(agent, "run_chat", boom)
    r = c.post("/api/chat", json={"message": "x"}).json()
    run = c.get(f"/api/runs/{r['run_id']}").json()
    assert run["status"] == "error" and "bad ticker" in run["error"]


def test_rate_limit(tmp_db, monkeypatch):
    c = _client(tmp_db, monkeypatch)
    from app import main
    monkeypatch.setattr(main, "RATE", 2)
    assert c.post("/api/chat", json={"message": "1"}).status_code == 200
    assert c.post("/api/chat", json={"message": "2"}).status_code == 200
    assert c.post("/api/chat", json={"message": "3"}).status_code == 429


def test_chart_and_compare_endpoints(tmp_db, monkeypatch, ohlcv):
    c = _client(tmp_db, monkeypatch)
    from app.data import market
    df = ohlcv(np.linspace(50, 100, 600), start="2023-01-02")
    monkeypatch.setattr(market.NasdaqProvider, "ohlcv", lambda self, t, period="1y", interval="1d": df)
    ch = c.get("/api/market/chart?ticker=AAPL&period=1y&overlays=sma_50,rsi").json()
    assert ch["candles"] and "sma_50" in ch["overlays"] and "rsi_14" in ch["panes"]
    assert ch["candles"][0]["time"] == ch["overlays"]["sma_50"][0]["time"]   # long averages defined for whole window
    cmp_ = c.get("/api/market/compare?tickers=AAPL,MSFT&period=1y").json()
    assert set(cmp_["series"]) == {"AAPL", "MSFT"} and cmp_["series"]["AAPL"][0]["value"] == 100.0
    assert c.get("/api/market/compare?tickers=AAPL").status_code == 400


def test_data_source_outage_returns_503(tmp_db, monkeypatch):
    c = _client(tmp_db, monkeypatch)
    from app.data import market

    def down(self, ticker, period="1y", interval="1d"):
        raise ConnectionError("dns")
    monkeypatch.setattr(market.NasdaqProvider, "_t", lambda self, t: (_ for _ in ()).throw(ConnectionError("dns")))
    market._cache.clear()
    r = c.get("/api/market/chart?ticker=ZZZZ&period=1y")
    assert r.status_code == 503 and "unavailable" in r.json()["detail"]


def test_quote_endpoint(tmp_db, monkeypatch):
    c = _client(tmp_db, monkeypatch)
    from app.data import market
    monkeypatch.setattr(market.YahooProvider, "quote", lambda self, t: {"ticker": t.upper(), "price": 10.0,
                        "market_state": "REGULAR", "delay_minutes": 0, "price_time": "2026-11-11T15:00:00+00:00"})
    q = c.get("/api/market/quote?ticker=btc-usd").json()
    assert q["ticker"] == "BTC-USD" and q["price"] == 10.0

    def missing(self, t):
        raise market.TickerNotFound("No quote")
    monkeypatch.setattr(market.YahooProvider, "quote", missing)
    assert c.get("/api/market/quote?ticker=ZZZZ").status_code == 404
