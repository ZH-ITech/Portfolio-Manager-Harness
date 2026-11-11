"""Parse an uploaded portfolio spreadsheet (xlsx or csv).
Expected columns (case-insensitive): ticker, shares, and optionally avg_cost."""
import io

import pandas as pd

ALIASES = {"ticker": {"ticker", "symbol", "stock"},
           "shares": {"shares", "quantity", "qty", "units"},
           "avg_cost": {"avg_cost", "average_cost", "cost", "price", "buy_price"}}


def parse_portfolio(content: bytes, filename: str) -> list[dict]:
    if filename.lower().endswith(".csv"):
        df = pd.read_csv(io.BytesIO(content))
    else:
        df = pd.read_excel(io.BytesIO(content))
    cols = {}
    for c in df.columns:
        key = str(c).strip().lower().replace(" ", "_")
        for canon, names in ALIASES.items():
            if key in names and canon not in cols:
                cols[canon] = c
    if "ticker" not in cols or "shares" not in cols:
        raise ValueError("Spreadsheet needs at least 'ticker' and 'shares' columns")
    out = []
    for _, r in df.iterrows():
        if pd.isna(r[cols["ticker"]]):
            continue
        row = {"ticker": str(r[cols["ticker"]]).strip().upper(), "shares": float(r[cols["shares"]])}
        if "avg_cost" in cols and not pd.isna(r[cols["avg_cost"]]):
            row["avg_cost"] = float(r[cols["avg_cost"]])
        out.append(row)
    return out
