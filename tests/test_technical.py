import numpy as np
import pandas as pd
import pytest

from app.analysis import technical as ta


def test_sma_known_values():
    s = pd.Series([1, 2, 3, 4, 5], dtype=float)
    assert ta.sma(s, 3).tolist()[2:] == [2.0, 3.0, 4.0]
    assert ta.sma(s, 3).isna().sum() == 2


def test_ema_matches_recursive_definition():
    s = pd.Series([10, 11, 12, 11, 13, 14], dtype=float)
    alpha = 2 / (3 + 1)
    e = [s[0]]
    for x in s[1:]:
        e.append(alpha * x + (1 - alpha) * e[-1])
    assert ta.ema(s, 3).iloc[-1] == pytest.approx(e[-1])


def test_rsi_matches_manual_wilder():
    rng = np.random.default_rng(0)
    s = pd.Series(100 + rng.normal(0, 1, 60).cumsum())
    d = s.diff()
    g, lo = d.clip(lower=0), -d.clip(upper=0)
    ag, al = [], []
    a, b = 0.0, 0.0
    for i in range(1, len(s)):  # pandas ewm(adjust=False) seeds with first value
        if i == 1:
            a, b = g[i], lo[i]
        else:
            a = a + (g[i] - a) / 14
            b = b + (lo[i] - b) / 14
        ag.append(a), al.append(b)
    expected = 100 - 100 / (1 + ag[-1] / al[-1])
    assert ta.rsi(s, 14).iloc[-1] == pytest.approx(expected, rel=1e-9)


def test_rsi_extremes():
    up = pd.Series(np.arange(1, 40, dtype=float))
    assert ta.rsi(up).iloc[-1] == 100.0
    down = pd.Series(np.arange(40, 1, -1, dtype=float))
    assert ta.rsi(down).iloc[-1] == pytest.approx(0.0)


def test_macd_is_ema_difference():
    s = pd.Series(np.linspace(10, 50, 80))
    m = ta.macd(s)
    exp = s.ewm(span=12, adjust=False).mean() - s.ewm(span=26, adjust=False).mean()
    assert m["macd"].iloc[-1] == pytest.approx(exp.iloc[-1])
    assert m["hist"].iloc[-1] == pytest.approx(m["macd"].iloc[-1] - m["signal"].iloc[-1])


def test_bollinger_flat_series_has_no_width():
    s = pd.Series([5.0] * 30)
    bb = ta.bollinger(s)
    assert bb["upper"].iloc[-1] == bb["lower"].iloc[-1] == 5.0


def test_summary_detects_uptrend_and_golden_cross(ohlcv):
    close = np.concatenate([np.linspace(100, 60, 200), np.linspace(60, 140, 60)])[:250]  # cross ~14 sessions ago
    s = ta.summary(ohlcv(close))
    assert "price above 200-day average" in s["signals"]
    assert any("cross" in x for x in s["signals"])
    assert s["bars"] == 250 and s["rsi_14"] > 50
    assert "golden cross in the last 20 sessions" in s["signals"]


def test_summary_with_short_history(ohlcv):
    s = ta.summary(ohlcv(np.linspace(10, 12, 30)))
    assert s["sma_200"] is None and s["sma_20"] is not None


def test_chart_series_shapes(ohlcv):
    df = ohlcv(np.linspace(10, 20, 60))
    out = ta.chart_series(df, ["sma_20", "bollinger", "rsi", "macd"])
    assert len(out["candles"]) == 60 and len(out["volume"]) == 60
    assert len(out["overlays"]["sma_20"]) == 41
    assert {"bb_upper", "bb_lower"} <= set(out["overlays"]) and {"rsi_14", "macd", "macd_signal"} <= set(out["panes"])
