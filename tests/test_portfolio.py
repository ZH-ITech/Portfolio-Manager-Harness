import io

import pandas as pd
import pytest

from app.portfolio import parse_portfolio


def _xlsx(df):
    b = io.BytesIO()
    df.to_excel(b, index=False)
    return b.getvalue()


def test_parses_xlsx_with_aliases():
    df = pd.DataFrame({"Symbol": ["aapl", "msft"], "Quantity": [10, 5], "Buy Price": [150, 300]})
    assert parse_portfolio(_xlsx(df), "p.xlsx") == [
        {"ticker": "AAPL", "shares": 10.0, "avg_cost": 150.0},
        {"ticker": "MSFT", "shares": 5.0, "avg_cost": 300.0},
    ]


def test_rejects_missing_columns():
    with pytest.raises(ValueError):
        parse_portfolio(b"name,value\nx,1\n", "p.csv")
