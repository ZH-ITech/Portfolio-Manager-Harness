import os

import numpy as np
import pandas as pd
import pytest

os.environ.setdefault("CELERY_EAGER", "true")


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    from app.harness import audit
    monkeypatch.setattr(audit.settings, "db_path", str(tmp_path / "t.db"))
    audit.init_db()
    return audit


def make_ohlcv(close, start="2024-01-01"):
    close = np.asarray(close, dtype=float)
    idx = pd.bdate_range(start, periods=len(close))
    return pd.DataFrame({"open": close, "high": close * 1.01, "low": close * 0.99, "close": close,
                         "volume": np.full(len(close), 1_000_000.0)}, index=idx)


@pytest.fixture
def ohlcv():
    return make_ohlcv
