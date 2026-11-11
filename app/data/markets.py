"""Regional market boards (PMH-14): major indices per region in one batched request.

Prices come from Yahoo Finance daily bars. During trading hours the latest bar is the
current session, so `price` is the latest available value; most non-US exchanges are
delayed about 15-20 minutes on free data. Symbols that Yahoo does not return are
reported as unavailable instead of failing the whole board."""
import math

import pandas as pd

from app.data.market import DataSourceError, _now, cached

REGIONS: dict[str, list[tuple[str, str, str]]] = {
    "asia": [
        ("^N225", "Nikkei 225", "Japan"),
        ("^HSI", "Hang Seng", "Hong Kong"),
        ("000001.SS", "SSE Composite", "China (Shanghai)"),
        ("399001.SZ", "SZSE Component", "China (Shenzhen)"),
        ("^NSEI", "Nifty 50", "India"),
        ("^BSESN", "BSE Sensex", "India"),
        ("^KS11", "KOSPI", "South Korea"),
        ("^TWII", "TAIEX", "Taiwan"),
        ("^STI", "Straits Times", "Singapore"),
        ("^KLSE", "FTSE Bursa Malaysia KLCI", "Malaysia"),
        ("^JKSE", "Jakarta Composite", "Indonesia"),
        ("^SET.BK", "SET Index", "Thailand"),
        ("^AXJO", "S&P/ASX 200", "Australia"),
        ("^NZ50", "NZX 50", "New Zealand"),
        ("^TA125.TA", "TA-125", "Israel"),
        ("^TASI.SR", "Tadawul All Share", "Saudi Arabia"),
    ],
    "us": [
        ("^GSPC", "S&P 500", "United States"),
        ("^DJI", "Dow Jones", "United States"),
        ("^IXIC", "Nasdaq Composite", "United States"),
        ("^RUT", "Russell 2000", "United States"),
        ("^VIX", "VIX (volatility)", "United States"),
    ],
    "europe": [
        ("^FTSE", "FTSE 100", "United Kingdom"),
        ("^GDAXI", "DAX", "Germany"),
        ("^FCHI", "CAC 40", "France"),
        ("^STOXX50E", "Euro Stoxx 50", "Eurozone"),
        ("^AEX", "AEX", "Netherlands"),
        ("^IBEX", "IBEX 35", "Spain"),
        ("^SSMI", "SMI", "Switzerland"),
    ],
    "crypto": [
        ("BTC-USD", "Bitcoin", "Crypto (24/7)"),
        ("ETH-USD", "Ethereum", "Crypto (24/7)"),
        ("SOL-USD", "Solana", "Crypto (24/7)"),
    ],
}
REGIONS["global"] = [REGIONS["us"][0], REGIONS["us"][2], REGIONS["europe"][0], REGIONS["europe"][1],
                     REGIONS["asia"][0], REGIONS["asia"][1], REGIONS["asia"][2], REGIONS["asia"][4],
                     REGIONS["crypto"][0]]

NOTE = ("Latest daily values from Yahoo Finance. Most non-US exchanges are delayed about 15-20 minutes; "
        "crypto trades 24/7. Not suitable for trading decisions that need real-time data.")


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def _closes(df: pd.DataFrame, symbol: str) -> pd.Series:
    """Close prices for one symbol from a yf.download frame (handles both column layouts)."""
    if isinstance(df.columns, pd.MultiIndex):
        for key in ((symbol, "Close"), ("Close", symbol)):
            if key in df.columns:
                return df[key].dropna()
        return pd.Series(dtype=float)
    return df["Close"].dropna() if "Close" in df.columns else pd.Series(dtype=float)


class _Boards:
    name = "Yahoo Finance via yfinance (delayed)"

    @cached(ttl=120)
    def overview(self, region: str = "asia") -> dict:
        key = (region or "asia").lower()
        if key not in REGIONS:
            raise ValueError(f"Unknown region '{region}'. Use one of: {', '.join(sorted(REGIONS))}.")
        rows = REGIONS[key]
        import yfinance as yf
        df = yf.download([s for s, _, _ in rows], period="7d", interval="1d", group_by="ticker",
                         auto_adjust=False, progress=False, threads=True)
        if df is None or df.empty:
            raise DataSourceError("Market data source returned no data. Try again shortly.")
        out = []
        for sym, name, country in rows:
            c = _closes(df, sym)
            if len(c) == 0:
                out.append({"symbol": sym, "name": name, "country": country, "status": "unavailable"})
                continue
            last = _num(c.iloc[-1])
            prev = _num(c.iloc[-2]) if len(c) > 1 else None
            out.append({"symbol": sym, "name": name, "country": country, "price": round(last, 2) if last else None,
                        "change_pct": round((last / prev - 1) * 100, 2) if last and prev else None,
                        "as_of": str(c.index[-1].date()), "status": "ok"})
        return {"region": key, "indices": out, "note": NOTE, "source": self.name, "fetched_at": _now()}


boards = _Boards()
