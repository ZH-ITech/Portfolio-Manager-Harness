"""Global markets via Yahoo symbols, quote freshness fields, symbol search (all mocked; no network)."""
from types import SimpleNamespace as NS

import pytest

from app.data import market


@pytest.fixture(autouse=True)
def clear_cache():
    market._cache.clear()
    yield
    market._cache.clear()


def test_any_exchange_name_routes_to_global_yahoo_except_psx():
    for name in ("GLOBAL", "NASDAQ", "nyse", "LSE", "NSE", None):
        assert isinstance(market.provider(name), market.YahooProvider)
    assert isinstance(market.provider("PSX"), market.PsxProvider)


def test_quote_reports_market_state_time_and_delay(monkeypatch):
    info = {"regularMarketPrice": 2950.5, "regularMarketPreviousClose": 2900.0, "currency": "INR",
            "fullExchangeName": "NSE", "marketState": "CLOSED", "regularMarketTime": 1794390000,
            "exchangeDataDelayedBy": 15, "shortName": "RELIANCE INDUSTRIES"}
    monkeypatch.setattr(market.YahooProvider, "_t", lambda self, t: NS(info=info, fast_info=NS(last_price=None)))
    q = market.provider().quote("RELIANCE.NS")
    assert q["price"] == 2950.5 and q["currency"] == "INR" and q["exchange"] == "NSE"
    assert q["market_state"] == "CLOSED" and q["delay_minutes"] == 15 and q["change_pct"] == 1.74
    assert q["price_time"].startswith("2026-")


def test_quote_falls_back_to_fast_info(monkeypatch):
    class TickerWithBrokenInfo:
        fast_info = NS(last_price=101.0, previous_close=100.0, currency="USD")

        @property
        def info(self):
            raise RuntimeError("info endpoint down")

    monkeypatch.setattr(market.YahooProvider, "_t", lambda self, s: TickerWithBrokenInfo())
    q = market.provider().quote("AAPL")
    assert q["price"] == 101.0 and q["change_pct"] == 1.0 and q["currency"] == "USD"


def test_unknown_symbol_gives_helpful_error(monkeypatch):
    monkeypatch.setattr(market.YahooProvider, "_t", lambda self, t: NS(info={}, fast_info=NS(last_price=None)))
    with pytest.raises(market.TickerNotFound, match="search_symbol"):
        market.provider().quote("NOPE.XX")


def test_symbol_search(monkeypatch):
    import yfinance as yf
    quotes = [{"symbol": "RELIANCE.NS", "shortname": "RELIANCE INDUSTRIES", "exchDisp": "NSE", "quoteType": "EQUITY"},
              {"symbol": "RELIANCE.BO", "longname": "Reliance Industries Ltd", "exchDisp": "Bombay", "quoteType": "EQUITY"}]
    monkeypatch.setattr(yf, "Search", lambda q, max_results=8, news_count=0: NS(quotes=quotes))
    r = market.provider().search("Reliance Industries")
    assert [m["symbol"] for m in r["matches"]] == ["RELIANCE.NS", "RELIANCE.BO"]
    assert r["matches"][1]["name"] == "Reliance Industries Ltd"


def test_psx_is_clearly_not_configured():
    with pytest.raises(NotImplementedError, match="PSX"):
        market.provider("PSX").quote("OGDC")


def test_search_symbol_tool_is_registered():
    from app.harness.mcp_hub import McpHub
    hub = McpHub()
    assert "search_symbol" in hub.tools


def test_fresh_request_fits_free_tier_token_cap():
    """System prompt + all tool definitions + reply budget must stay well under Groq's 8,000 TPM."""
    import json

    from app.harness import agent
    from app.harness.mcp_hub import McpHub
    tools = McpHub().tool_specs() + [agent.RUN_PYTHON, agent.DELEGATE, agent.ANSWER]
    approx_tokens = (len(agent.SYSTEM) + len(json.dumps(tools))) / 3.5   # conservative chars-per-token
    assert approx_tokens + agent.settings.llm_max_tokens < 6000, approx_tokens
