"""Injected into every sandbox run (Programmatic Tool Calling).

    from ptc import call_tool, prices
    q  = call_tool("get_quote", ticker="AAPL")
    df = prices("AAPL", "2y")          # pandas DataFrame: open/high/low/close/volume

Analysis helpers are also available: import ta (indicators), perf (returns,
risk, what-if, projections), fa (fundamental ratios)."""
import json
import os
import urllib.request


def call_tool(name: str, **kwargs):
    req = urllib.request.Request(
        f"{os.environ['PTC_PROXY_URL']}/{name}",
        data=json.dumps(kwargs).encode(),
        headers={"Content-Type": "application/json", "X-PTC-Token": os.environ.get("PTC_TOKEN", "")},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=40) as r:
        body = json.loads(r.read())
    if not body.get("ok"):
        raise RuntimeError(body.get("error", "tool call failed"))
    return body["result"]


def prices(ticker: str, period: str = "1y", interval: str = "1d"):
    import pandas as pd
    h = call_tool("get_price_history", ticker=ticker, period=period, interval=interval)
    df = pd.DataFrame(h["bars"])
    df.index = pd.to_datetime(df.pop("time"))
    return df
