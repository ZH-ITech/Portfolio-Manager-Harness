"""Fundamental metrics from financial statements (PMH-22). Works on plain dicts
so it is easy to test and to use inside the sandbox as `fa.py`."""


def _get(d: dict, *names):
    for n in names:
        if d.get(n) is not None:
            return d[n]
    return None


def _div(a, b):
    return None if a is None or b in (None, 0) else round(a / b, 4)


def growth(series: list[float]) -> list:
    """Period-over-period growth; series is oldest -> newest."""
    return [None] + [_div(b - a, abs(a)) if a else None for a, b in zip(series, series[1:])]


def ratios(income: dict, balance: dict, cashflow: dict | None = None, price: float | None = None,
           shares: float | None = None) -> dict:
    """One period's statements (as returned by the MCP tool) -> key ratios."""
    revenue = _get(income, "Total Revenue", "Operating Revenue")
    gross = _get(income, "Gross Profit")
    op = _get(income, "Operating Income")
    net = _get(income, "Net Income", "Net Income Common Stockholders")
    equity = _get(balance, "Stockholders Equity", "Common Stock Equity")
    debt = _get(balance, "Total Debt")
    assets = _get(balance, "Total Assets")
    cur_a, cur_l = _get(balance, "Current Assets"), _get(balance, "Current Liabilities")
    fcf = _get(cashflow or {}, "Free Cash Flow")
    out = {
        "gross_margin": _div(gross, revenue), "operating_margin": _div(op, revenue),
        "net_margin": _div(net, revenue), "roe": _div(net, equity), "roa": _div(net, assets),
        "debt_to_equity": _div(debt, equity), "current_ratio": _div(cur_a, cur_l),
        "fcf_margin": _div(fcf, revenue),
    }
    if price and shares:
        mcap = price * shares
        out["market_cap"] = round(mcap, 0)
        out["pe"] = _div(mcap, net)
        out["ps"] = _div(mcap, revenue)
        out["pb"] = _div(mcap, equity)
    return out
