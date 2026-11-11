"""Returns, risk, comparisons and what-if scenarios (PMH-11). Pure pandas/numpy;
copied into the sandbox as `perf.py`."""
import numpy as np
import pandas as pd

TRADING_DAYS = 252


def returns(close: pd.Series) -> pd.Series:
    return close.pct_change().dropna()


def total_return(close: pd.Series) -> float:
    return float(close.iloc[-1] / close.iloc[0] - 1)


def cagr(close: pd.Series) -> float:
    years = (close.index[-1] - close.index[0]).days / 365.25
    return float((close.iloc[-1] / close.iloc[0]) ** (1 / years) - 1) if years > 0 else float("nan")


def volatility(close: pd.Series) -> float:
    """Annualised standard deviation of daily returns."""
    return float(returns(close).std(ddof=1) * np.sqrt(TRADING_DAYS))


def max_drawdown(close: pd.Series) -> float:
    return float((close / close.cummax() - 1).min())


def sharpe(close: pd.Series, risk_free: float = 0.0) -> float:
    r = returns(close)
    excess = r - risk_free / TRADING_DAYS
    sd = excess.std(ddof=1)
    return float(excess.mean() / sd * np.sqrt(TRADING_DAYS)) if sd else float("nan")


def compare(closes: dict[str, pd.Series]) -> list[dict]:
    rows = []
    for t, c in closes.items():
        c = c.dropna()
        rows.append({"ticker": t, "total_return": round(total_return(c), 4), "cagr": round(cagr(c), 4),
                     "volatility": round(volatility(c), 4), "max_drawdown": round(max_drawdown(c), 4),
                     "sharpe": round(sharpe(c), 2)})
    return rows


def correlation(closes: dict[str, pd.Series]) -> dict:
    df = pd.DataFrame({t: returns(c) for t, c in closes.items()}).dropna()
    return df.corr().round(3).to_dict()


def what_if_invested(close: pd.Series, amount: float, start=None) -> dict:
    """Value today of `amount` invested at the first close on/after `start`
    (no dividends, fees or taxes)."""
    c = close if start is None else close[close.index >= pd.Timestamp(start, tz=close.index.tz)]
    shares = amount / c.iloc[0]
    return {"amount": amount, "buy_date": str(c.index[0].date()), "buy_price": round(float(c.iloc[0]), 4),
            "value_now": round(float(shares * c.iloc[-1]), 2), "return": round(total_return(c), 4),
            "assumptions": ["price return only (no dividends)", "no fees or taxes", "bought at the close"]}


def project_range(close: pd.Series, amount: float, years: float, sims: int = 2000, seed: int = 7) -> dict:
    """Monte Carlo projection using bootstrapped historical daily returns.
    Returns percentile outcomes, never a single prediction."""
    r = returns(close).values
    n = int(round(years * TRADING_DAYS))
    rng = np.random.default_rng(seed)
    paths = np.prod(1 + rng.choice(r, size=(sims, n), replace=True), axis=1)
    pct = np.percentile(paths * amount, [5, 25, 50, 75, 95])
    return {"amount": amount, "years": years, "simulations": sims,
            "p5": round(pct[0], 2), "p25": round(pct[1], 2), "median": round(pct[2], 2),
            "p75": round(pct[3], 2), "p95": round(pct[4], 2),
            "method": "bootstrap of historical daily returns",
            "assumptions": ["future returns resemble the sampled history", "no dividends, fees or taxes",
                            "not a forecast; a range of plausible outcomes"]}
