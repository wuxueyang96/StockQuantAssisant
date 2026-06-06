#!/usr/bin/env python3
"""Force refresh 5min data for all registered stocks (skip trading hour check)."""
import sys
sys.path.insert(0, '/root/.openclaw/workspace/StockQuantAssistant')

from app.models.database import db_manager
from app.services.stock_service import collect_and_store
import pandas as pd

df = db_manager.get_all_stock_codes()
total_rows = 0
errors = 0

for _, row in df.iterrows():
    name = row['name']
    for market_key, code_col in [('a', 'a_code'), ('hk', 'hk_code'), ('us', 'us_code')]:
        code = row.get(code_col)
        if not code or pd.isna(code):
            continue
        try:
            rows = collect_and_store(market_key, code, skip_trading_check=True)
            print(f"  {name:20s} {market_key}:{code:12s} → {rows} rows")
            total_rows += rows
        except Exception as e:
            print(f"  ❌ {name:20s} {market_key}:{code:12s} error: {e}")
            errors += 1

print(f"\nDone. {total_rows} new rows, {errors} errors")
db_manager.close_all()
