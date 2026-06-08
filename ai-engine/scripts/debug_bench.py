"""Debug: why is IC bench returning empty?"""
import sys, math
from datetime import date, timedelta
import numpy as np
import pandas as pd
import psycopg2

sys.path.insert(0, '.')
from app.services.pg_pool import DB_URL

# Test the liquidity filter on one date
conn = psycopg2.connect(DB_URL)
cur = conn.cursor()

dt = date(2025, 3, 15)

# First, how many HOSE symbols total?
cur.execute("SELECT COUNT(*) FROM stocks WHERE exchange IN ('HOSE','HSX')")
print(f"Total HOSE symbols: {cur.fetchone()[0]}")

# How many have OHLCV data on this date?
cur.execute("SELECT COUNT(DISTINCT symbol) FROM ohlcv WHERE time::date = %s AND volume > 0", (dt,))
print(f"Symbols with OHLCV on {dt}: {cur.fetchone()[0]}")

# Load OHLCV for testing
from collections import defaultdict
cur.execute(
    """SELECT symbol, time::date as dt, adj_close, close, volume
       FROM ohlcv
       WHERE time::date >= %s AND time::date <= %s
       ORDER BY symbol, time""",
    (dt - timedelta(days=100), dt),
)
records = defaultdict(list)
for sym, dt2, ac, cl, vol in cur.fetchall():
    c = float(ac or cl or 0)
    records[sym].append({"date": dt2, "close": c, "volume": int(vol or 0)})

print(f"\nSymbols loaded: {len(records)}")
print(f"Symbols with >= 20 days data:")

# Check liquidity
fil_syms = []
for sym, rows in records.items():
    df = pd.DataFrame(rows).set_index("date").sort_index()
    df.index = pd.to_datetime(df.index)
    if len(df) < 20:
        continue
    df["value"] = df["close"] * df["volume"]
    recent = df.tail(20)
    avg_val = recent["value"].mean()
    min_val = 5e9
    if avg_val >= min_val:
        fil_syms.append((sym, avg_val / 1e9))

print(f"  Total with >=20d: {len(records)}")
print(f"  Passing 5B filter: {len(fil_syms)} ({len(fil_syms)/len(records)*100:.0f}%)")
fil_syms.sort(key=lambda x: -x[1])
print(f"  Top 5 by avg value: {[(s, round(v)) for s,v in fil_syms[:5]]}")
print(f"  Bottom 5: {[(s, round(v,2)) for s,v in fil_syms[-5:]]}")

# Check financial_ratios for these symbols
symbols = [s[0] for s in fil_syms[:50]]
cur.execute(
    """SELECT DISTINCT ON (symbol) symbol, pe, pb, roe, gross_margin, net_margin
       FROM financial_ratios
       WHERE symbol = ANY(%s) AND ratio_date <= %s
       ORDER BY symbol, ratio_date DESC""",
    (symbols, dt),
)
fin_count = 0
for r in cur.fetchall():
    if r[1] is not None:  # has PE
        fin_count += 1
print(f"\nSymbols with PE from financial_ratios: {fin_count}/{len(symbols)}")

# Check stocks table data
cur.execute("SELECT symbol, market_cap FROM stocks WHERE symbol = ANY(%s)", (symbols,))
mcap_count = sum(1 for r in cur.fetchall() if r[1] is not None)
print(f"Symbols with market_cap: {mcap_count}/{len(symbols)}")

cur.close()
conn.close()
