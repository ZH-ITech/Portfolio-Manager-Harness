"""Market data and company information providers (PMH-14, PMH-22).
NASDAQ/NYSE via yfinance (delayed, non-commercial). PSX is a pluggable stub
until a licensed source is chosen. Every response carries source + fetched_at."""
import math
import threading
import time
from datetime import datetime, timezone

import pandas as pd

CACHE_TTL_S = 300


class TickerNotFound(ValueError):
    pass
_cache: dict = {}
_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DataSourceError(RuntimeError):
    """The upstream data source could not be reached; safe to retry later."""


def cached(fn=None, *, ttl: int = CACHE_TTL_S):
    """Small TTL cache so repeated questions do not hit rate limits. Also turns
    network/upstream failures into DataSourceError with a clear message."""
    def deco(fn):
        def wrapper(self, *args, **kwargs):
            key = (fn.__name__, args, tuple(sorted(kwargs.items())))
            with _lock:
                hit = _cache.get(key)
                if hit and time.time() - hit[0] < ttl:
                    return hit[1]
            try:
                val = fn(self, *args, **kwargs)
            except (TickerNotFound, ValueError, NotImplementedError):
                raise
            except Exception as e:
                raise DataSourceError(f"Market data source unavailable ({type(e).__name__}). Try again shortly.") from e
            with _lock:
                _cache[key] = (time.time(), val)
            return val
        wrapper.__name__ = fn.__name__
        return wrapper
    return deco(fn) if fn is not None else deco


def _clean(v):
    if v is None:
        return None
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    if hasattr(v, "item"):
        v = v.item()
        return None if isinstance(v, float) and math.isnan(v) else v
    return v


class YahooProvider:
    """Global markets via Yahoo Finance symbols: US (AAPL), India (RELIANCE.NS), London (HSBA.L),
    Tokyo (7203.T), Hong Kong (0700.HK), Germany (SAP.DE), indices (^GSPC), crypto (BTC-USD),
    forex (EURUSD=X). Prices are usually delayed (often ~15 min; varies by exchange)."""
    name = "Yahoo Finance via yfinance (global, usually delayed)"

    def _t(self, ticker: str):
        import yfinance as yf
        return yf.Ticker(ticker.upper().strip())

    # ---------- prices ----------
    @cached
    def ohlcv(self, ticker: str, period: str = "1y", interval: str = "1d") -> pd.DataFrame:
        df = self._t(ticker).history(period=period, interval=interval, auto_adjust=True)
        if df is None or df.empty:
            raise TickerNotFound(f"No price data for '{ticker}'. Check the ticker symbol.")
        df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
        return df

    def history(self, ticker: str, period: str = "1y", interval: str = "1d") -> dict:
        df = self.ohlcv(ticker, period, interval)
        bars = [{"time": i.strftime("%Y-%m-%d"), "open": round(r.open, 4), "high": round(r.high, 4),
                 "low": round(r.low, 4), "close": round(r.close, 4), "volume": int(r.volume)}
                for i, r in df.iterrows()]
        return {"ticker": ticker.upper(), "period": period, "interval": interval, "bars": bars,
                "adjusted": True, "source": self.name, "fetched_at": _now()}

    @cached(ttl=60)
    def quote(self, ticker: str) -> dict:
        t = self._t(ticker)
        info = {}
        try:
            info = t.info or {}
        except Exception:
            info = {}
        fi = t.fast_info
        price = _clean(info.get("regularMarketPrice")) or _clean(getattr(fi, "last_price", None))
        if price is None:
            raise TickerNotFound(f"No quote for '{ticker}'. Check the symbol, or use search_symbol.")
        ts = info.get("regularMarketTime")
        as_of = datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds") if isinstance(ts, (int, float)) else None
        prev = _clean(info.get("regularMarketPreviousClose")) or _clean(getattr(fi, "previous_close", None))
        return {"ticker": ticker.upper(), "name": info.get("shortName") or info.get("longName"),
                "price": price, "previous_close": prev,
                "change_pct": round((price / prev - 1) * 100, 2) if prev else None,
                "currency": info.get("currency") or getattr(fi, "currency", None),
                "exchange": info.get("fullExchangeName") or info.get("exchange"),
                "market_state": info.get("marketState"),            # e.g. REGULAR (open), CLOSED, PRE, POST
                "price_time": as_of,                                 # when the exchange printed this price
                "delay_minutes": _clean(info.get("exchangeDataDelayedBy")),
                "source": self.name, "fetched_at": _now()}

    @cached(ttl=3600)
    def search(self, query: str, limit: int = 8) -> dict:
        import yfinance as yf
        quotes = yf.Search(query, max_results=limit, news_count=0).quotes or []
        rows = [{"symbol": q.get("symbol"), "name": q.get("shortname") or q.get("longname"),
                 "exchange": q.get("exchDisp") or q.get("exchange"), "type": q.get("quoteType")}
                for q in quotes if q.get("symbol")]
        return {"query": query, "matches": rows, "source": self.name, "fetched_at": _now()}

    # ---------- company information ----------
    @cached
    def profile(self, ticker: str) -> dict:
        info = self._t(ticker).info or {}
        if not info.get("shortName") and not info.get("longName"):
            raise TickerNotFound(f"No company information for '{ticker}'.")
        keys = ["shortName", "longName", "sector", "industry", "country", "website", "fullTimeEmployees",
                "marketCap", "enterpriseValue", "sharesOutstanding", "trailingPE", "forwardPE", "priceToBook",
                "priceToSalesTrailing12Months", "enterpriseToEbitda", "dividendYield", "beta",
                "fiftyTwoWeekHigh", "fiftyTwoWeekLow", "profitMargins", "revenueGrowth", "earningsGrowth",
                "returnOnEquity", "debtToEquity", "currency"]
        out = {k: _clean(info.get(k)) for k in keys}
        summary = info.get("longBusinessSummary") or ""
        out["business_summary"] = summary[:800]
        return {"ticker": ticker.upper(), **out, "source": self.name, "fetched_at": _now()}

    @cached
    def statements(self, ticker: str, statement: str = "income", freq: str = "annual", periods: int = 4) -> dict:
        t = self._t(ticker)
        attr = {("income", "annual"): "income_stmt", ("income", "quarterly"): "quarterly_income_stmt",
                ("balance", "annual"): "balance_sheet", ("balance", "quarterly"): "quarterly_balance_sheet",
                ("cashflow", "annual"): "cashflow", ("cashflow", "quarterly"): "quarterly_cashflow"}
        key = (statement, freq)
        if key not in attr:
            raise ValueError("statement must be income|balance|cashflow and freq annual|quarterly")
        df = getattr(t, attr[key])
        if df is None or df.empty:
            raise TickerNotFound(f"No {statement} statement for '{ticker}'.")
        df = df.iloc[:, :periods]
        return {"ticker": ticker.upper(), "statement": statement, "freq": freq,
                "periods": [c.strftime("%Y-%m-%d") for c in df.columns],
                "items": {str(idx): [_clean(v) for v in row.values] for idx, row in df.iterrows()},
                "note": "periods are newest first", "source": self.name, "fetched_at": _now()}

    @cached
    def earnings(self, ticker: str, limit: int = 8) -> dict:
        df = self._t(ticker).earnings_dates
        rows = []
        if df is not None and not df.empty:
            for idx, r in df.head(limit).iterrows():
                rows.append({"date": idx.strftime("%Y-%m-%d"),
                             "eps_estimate": _clean(r.get("EPS Estimate")),
                             "eps_reported": _clean(r.get("Reported EPS")),
                             "surprise_pct": _clean(r.get("Surprise(%)"))})
        return {"ticker": ticker.upper(), "earnings": rows,
                "note": "rows without reported EPS are upcoming", "source": self.name, "fetched_at": _now()}

    @cached
    def news(self, ticker: str, limit: int = 8) -> dict:
        items = []
        for n in (self._t(ticker).news or [])[:limit]:
            c = n.get("content", n)
            prov = c.get("provider")
            items.append({"title": c.get("title"),
                          "publisher": prov.get("displayName") if isinstance(prov, dict) else c.get("publisher"),
                          "published": c.get("pubDate") or c.get("providerPublishTime")})
        return {"ticker": ticker.upper(), "headlines": items, "source": self.name, "fetched_at": _now()}


class PsxProvider:
    name = "PSX (not configured)"

    def _na(self, *_a, **_k):
        raise NotImplementedError("PSX data source not configured yet (stretch goal, see Jira PMH-14).")

    ohlcv = history = quote = profile = statements = earnings = news = search = _na


NasdaqProvider = YahooProvider   # backwards-compatible name

_yahoo = YahooProvider()
# Every Yahoo-covered market goes through one provider; the symbol suffix selects the market.
PROVIDERS = {"GLOBAL": _yahoo, "ANY": _yahoo, "NASDAQ": _yahoo, "NYSE": _yahoo, "US": _yahoo, "PSX": PsxProvider()}


def provider(exchange: str = "GLOBAL"):
    try:
        return PROVIDERS[(exchange or "GLOBAL").upper()]
    except KeyError:
        return _yahoo   # unknown names: rely on the Yahoo symbol suffix
