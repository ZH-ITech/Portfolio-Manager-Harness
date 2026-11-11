"""Technical indicators (PMH-15). Pure pandas, no I/O, so the same module runs
on the server and is copied into the sandbox as `ta.py`.

All functions take a pandas Series/DataFrame indexed by date and return
Series aligned to the input. Standard definitions:
- RSI and ATR use Wilder smoothing (alpha = 1/period).
- MACD = EMA(fast) - EMA(slow); signal = EMA(MACD, signal).
- Bollinger Bands use the population standard deviation (ddof=0).
"""
import pandas as pd


def sma(close: pd.Series, period: int) -> pd.Series:
    return close.rolling(period, min_periods=period).mean()


def ema(close: pd.Series, period: int) -> pd.Series:
    return close.ewm(span=period, adjust=False, min_periods=period).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss
    out = 100 - 100 / (1 + rs)
    out[(avg_loss == 0) & avg_gain.notna()] = 100.0
    return out


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    line = close.ewm(span=fast, adjust=False).mean() - close.ewm(span=slow, adjust=False).mean()
    sig = line.ewm(span=signal, adjust=False).mean()
    return pd.DataFrame({"macd": line, "signal": sig, "hist": line - sig})


def bollinger(close: pd.Series, period: int = 20, k: float = 2.0) -> pd.DataFrame:
    mid = sma(close, period)
    sd = close.rolling(period, min_periods=period).std(ddof=0)
    return pd.DataFrame({"middle": mid, "upper": mid + k * sd, "lower": mid - k * sd})


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev = df["close"].shift()
    tr = pd.concat([df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def summary(df: pd.DataFrame) -> dict:
    """Latest readings and plain trend signals for a price DataFrame with
    columns open/high/low/close/volume. Missing values mean not enough history."""
    c = df["close"]
    s50, s200 = sma(c, 50), sma(c, 200)
    r, m, bb = rsi(c), macd(c), bollinger(c)
    vol_avg = sma(df["volume"].astype(float), 20)

    def last(x):
        v = x.iloc[-1] if len(x) else None
        return None if v is None or pd.isna(v) else round(float(v), 4)

    out = {
        "last_close": last(c), "sma_20": last(sma(c, 20)), "sma_50": last(s50), "sma_200": last(s200),
        "rsi_14": last(r), "macd": last(m["macd"]), "macd_signal": last(m["signal"]),
        "bb_upper": last(bb["upper"]), "bb_lower": last(bb["lower"]),
        "volume_last": last(df["volume"].astype(float)), "volume_avg_20": last(vol_avg),
        "bars": len(df), "start": str(df.index[0].date()) if len(df) else None,
        "end": str(df.index[-1].date()) if len(df) else None,
    }
    signals = []
    if out["sma_200"] is not None:
        signals.append("price above 200-day average" if out["last_close"] > out["sma_200"] else "price below 200-day average")
    if out["sma_50"] is not None and out["sma_200"] is not None:
        both = pd.concat([s50, s200], axis=1).dropna()
        if len(both) >= 2:
            diff = both.iloc[:, 0] - both.iloc[:, 1]
            if diff.iloc[-1] > 0 and (diff.iloc[-20:] < 0).any():
                signals.append("golden cross in the last 20 sessions")
            elif diff.iloc[-1] < 0 and (diff.iloc[-20:] > 0).any():
                signals.append("death cross in the last 20 sessions")
    if out["rsi_14"] is not None:
        signals.append("RSI overbought (>70)" if out["rsi_14"] > 70 else "RSI oversold (<30)" if out["rsi_14"] < 30 else "RSI neutral (30-70)")
    if out["macd"] is not None:
        signals.append("MACD above signal line" if out["macd"] > out["macd_signal"] else "MACD below signal line")
    if out["volume_avg_20"]:
        ratio = out["volume_last"] / out["volume_avg_20"]
        if ratio > 1.5:
            signals.append(f"volume {ratio:.1f}x its 20-day average")
    out["signals"] = signals
    return out


def chart_series(df: pd.DataFrame, overlays=("sma_20", "sma_50")) -> dict:
    """Chart-ready payload: candles, volume and requested overlays/panes."""
    def pts(series):
        return [{"time": i.strftime("%Y-%m-%d"), "value": round(float(v), 4)} for i, v in series.dropna().items()]

    c = df["close"]
    out = {
        "candles": [{"time": i.strftime("%Y-%m-%d"), "open": round(float(r.open), 4), "high": round(float(r.high), 4),
                     "low": round(float(r.low), 4), "close": round(float(r.close), 4)} for i, r in df.iterrows()],
        "volume": [{"time": i.strftime("%Y-%m-%d"), "value": float(v)} for i, v in df["volume"].items()],
        "overlays": {}, "panes": {},
    }
    for o in overlays:
        kind, _, n = o.partition("_")
        if kind == "sma" and n.isdigit():
            out["overlays"][o] = pts(sma(c, int(n)))
        elif kind == "ema" and n.isdigit():
            out["overlays"][o] = pts(ema(c, int(n)))
        elif o == "bollinger":
            bb = bollinger(c)
            out["overlays"]["bb_upper"], out["overlays"]["bb_lower"] = pts(bb["upper"]), pts(bb["lower"])
        elif o == "rsi":
            out["panes"]["rsi_14"] = pts(rsi(c))
        elif o == "macd":
            m = macd(c)
            out["panes"]["macd"], out["panes"]["macd_signal"] = pts(m["macd"]), pts(m["signal"])
    return out
