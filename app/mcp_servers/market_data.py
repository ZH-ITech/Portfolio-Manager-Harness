"""MCP server: market data, company information and quick technical summaries.
Run standalone (stdio):  python -m app.mcp_servers.market_data"""
from mcp.server.fastmcp import FastMCP

from app.analysis import technical
from app.data.market import provider

mcp = FastMCP("market-data")


@mcp.tool()
def get_quote(ticker: str, exchange: str = "NASDAQ") -> dict:
    """Latest (delayed) price and previous close."""
    return provider(exchange).quote(ticker)


@mcp.tool()
def get_price_history(ticker: str, period: str = "1y", interval: str = "1d", exchange: str = "NASDAQ") -> dict:
    """Split/dividend-adjusted OHLCV bars. period: 1mo,3mo,6mo,1y,2y,5y,max. interval: 1d,1wk,1mo."""
    return provider(exchange).history(ticker, period, interval)


@mcp.tool()
def get_technical_summary(ticker: str, period: str = "1y", exchange: str = "NASDAQ") -> dict:
    """Latest SMA 20/50/200, RSI 14, MACD, Bollinger Bands, volume vs average, and plain trend signals."""
    p = provider(exchange)
    out = technical.summary(p.ohlcv(ticker, period))
    return {"ticker": ticker.upper(), "period": period, **out, "source": p.name,
            "method": "SMA, Wilder RSI(14), MACD(12,26,9), Bollinger(20,2) on adjusted daily closes"}


@mcp.tool()
def get_company_profile(ticker: str, exchange: str = "NASDAQ") -> dict:
    """Company description, sector, size, valuation multiples and key ratios."""
    return provider(exchange).profile(ticker)


@mcp.tool()
def get_financial_statements(ticker: str, statement: str = "income", freq: str = "annual",
                             periods: int = 4, exchange: str = "NASDAQ") -> dict:
    """Financial statement line items. statement: income|balance|cashflow. freq: annual|quarterly."""
    return provider(exchange).statements(ticker, statement, freq, periods)


@mcp.tool()
def get_earnings(ticker: str, limit: int = 8, exchange: str = "NASDAQ") -> dict:
    """Earnings history (EPS estimate vs reported, surprise %) and upcoming dates."""
    return provider(exchange).earnings(ticker, limit)


@mcp.tool()
def get_news(ticker: str, limit: int = 8, exchange: str = "NASDAQ") -> dict:
    """Recent headlines."""
    return provider(exchange).news(ticker, limit)


if __name__ == "__main__":
    mcp.run()
