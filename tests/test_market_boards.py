"""Regional market boards: batched download parsing, missing symbols, regions (mocked; no network)."""
import numpy as np
import pandas as pd
import pytest

from app.data import market, markets


@pytest.fixture(autouse=True)
def clear_cache():
    market._cache.clear()
    yield
    market._cache.clear()


def _frame(symbols, closes):
    idx = pd.to_datetime(["2026-11-10", "2026-11-11"])
    cols = pd.MultiIndex.from_product([symbols, ["Open", "Close"]])
    data = np.column_stack([v for c in closes for v in (c, c)])
    return pd.DataFrame(data, index=idx, columns=cols)


def test_asia_board_parses_prices_changes_and_missing_symbols(monkeypatch):
    import yfinance as yf
    asia = [s for s, _, _ in markets.REGIONS["asia"]]
    present = asia[:2]   # Nikkei, Hang Seng returned; the rest missing
    monkeypatch.setattr(yf, "download", lambda *a, **k: _frame(present, [[100.0, 101.0], [200.0, 198.0]]))
    d = markets.boards.overview("Asia")
    rows = {r["symbol"]: r for r in d["indices"]}
    assert d["region"] == "asia" and len(d["indices"]) == len(asia)
    assert rows["^N225"]["price"] == 101.0 and rows["^N225"]["change_pct"] == 1.0
    assert rows["^HSI"]["change_pct"] == -1.0 and rows["^HSI"]["as_of"] == "2026-11-11"
    assert rows["^NSEI"]["status"] == "unavailable"
    assert "delayed" in d["note"]


def test_asia_board_covers_major_asian_markets():
    countries = " ".join(c for _, _, c in markets.REGIONS["asia"])
    for c in ("Japan", "Hong Kong", "China", "India", "Korea", "Taiwan", "Singapore", "Australia"):
        assert c in countries


def test_unknown_region_and_empty_data(monkeypatch):
    import yfinance as yf
    with pytest.raises(ValueError):
        markets.boards.overview("mars")
    monkeypatch.setattr(yf, "download", lambda *a, **k: pd.DataFrame())
    with pytest.raises(market.DataSourceError):
        markets.boards.overview("us")


def test_overview_endpoint(tmp_db, monkeypatch):
    from fastapi.testclient import TestClient

    from app import main
    monkeypatch.setattr(markets._Boards, "overview", lambda self, region="asia": {"region": region, "indices": [], "note": "n"})
    c = TestClient(main.app)
    assert c.get("/api/market/overview?region=europe").json()["region"] == "europe"


def test_overview_tool_registered():
    from app.harness.mcp_hub import McpHub
    assert "get_market_overview" in McpHub().tools
