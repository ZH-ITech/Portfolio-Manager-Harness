import numpy as np
import pandas as pd
import pytest

from app.analysis import fundamentals as fa
from app.analysis import performance as perf


def _series(vals, start="2023-01-02"):
    return pd.Series(vals, index=pd.date_range(start, periods=len(vals), freq="D"), dtype=float)


def test_returns_and_drawdown():
    c = _series([100, 120, 90, 110])
    assert perf.total_return(c) == pytest.approx(0.10)
    assert perf.max_drawdown(c) == pytest.approx(90 / 120 - 1)


def test_cagr_doubling_in_one_year():
    c = pd.Series([100.0, 200.0], index=pd.to_datetime(["2023-01-01", "2024-01-01"]))
    assert perf.cagr(c) == pytest.approx(1.0, rel=0.01)


def test_what_if_invested():
    c = _series([50, 55, 100])
    r = perf.what_if_invested(c, 1000)
    assert r["value_now"] == pytest.approx(2000.0) and r["return"] == pytest.approx(1.0)
    assert r["assumptions"]


def test_projection_is_a_range():
    c = _series(100 * np.cumprod(1 + np.random.default_rng(1).normal(0.0005, 0.01, 500)))
    p = perf.project_range(c, 1000, 1)
    assert p["p5"] < p["median"] < p["p95"] and p["assumptions"]


def test_compare_rows():
    rows = perf.compare({"A": _series([1, 2, 3, 4]), "B": _series([4, 3, 2, 1])})
    assert [r["ticker"] for r in rows] == ["A", "B"] and rows[0]["total_return"] == 3.0


def test_fundamental_ratios():
    inc = {"Total Revenue": 1000.0, "Gross Profit": 600.0, "Operating Income": 300.0, "Net Income": 200.0}
    bal = {"Stockholders Equity": 800.0, "Total Debt": 400.0, "Total Assets": 2000.0,
           "Current Assets": 500.0, "Current Liabilities": 250.0}
    r = fa.ratios(inc, bal, {"Free Cash Flow": 150.0}, price=10.0, shares=100.0)
    assert r["gross_margin"] == 0.6 and r["net_margin"] == 0.2 and r["roe"] == 0.25
    assert r["debt_to_equity"] == 0.5 and r["current_ratio"] == 2.0 and r["fcf_margin"] == 0.15
    assert r["pe"] == 5.0 and r["pb"] == 1.25


def test_growth_and_missing_values():
    assert fa.growth([100, 110, 99]) == [None, 0.1, -0.1]
    assert fa.ratios({}, {})["net_margin"] is None
