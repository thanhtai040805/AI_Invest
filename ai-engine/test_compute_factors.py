"""Test compute_factors_at to find hang."""
import psycopg2, time, os, math, logging
from collections import defaultdict
from datetime import date, timedelta
import pandas as pd
import numpy as np

os.environ["DATABASE_URL"] = "postgresql://postgres:123@localhost:5432/aiinvest"
logging.basicConfig(level=logging.INFO, format="%(message)s")

from app.brain.quant.factors.vn_ic_tester import VNICTester, VN_CONSTRAINTS

conn = psycopg2.connect("postgresql://postgres:123@localhost:5432/aiinvest")
tester = VNICTester(conn)

# Load universe
cur = conn.cursor()
cur.execute("SELECT symbol FROM stocks WHERE exchange IN ('HOSE','HSX') ORDER BY symbol")
all_symbols = [r[0] for r in cur.fetchall()]
print(f"Universe: {len(all_symbols)}")

# Pre-load static data
t0 = time.time()
tester._preload_all_static(all_symbols, date(2022, 3, 6))
print(f"Preload: {time.time()-t0:.1f}s")

# Load OHLCV
t0 = time.time()
cur.execute(
    "SELECT symbol, time::date as dt, adj_close, close, volume "
    "FROM ohlcv "
    "WHERE time::date >= '2022-03-06' AND time::date <= '2026-06-08' AND symbol = ANY(%s) "
    "ORDER BY symbol, time",
    (all_symbols,),
)
rows = cur.fetchall()
records = defaultdict(list)
for sym, dt, ac, cl, vol in rows:
    c = float(ac) if ac is not None else (float(cl) if cl is not None else None)
    if c is None or not math.isfinite(c):
        continue
    records[sym].append({"date": dt, "close": c, "volume": int(vol) if vol is not None else 0})

ohlcv_all = {}
for sym, rows_list in records.items():
    df = pd.DataFrame(rows_list).set_index("date").sort_index()
    df.index = pd.to_datetime(df.index)
    if len(df) > 60:
        df["value"] = df["close"] * df["volume"]
        ohlcv_all[sym] = df
print(f"OHLCV: {len(ohlcv_all)} symbols in {time.time()-t0:.1f}s")

# Sample date
sample_dt = date(2024, 6, 15)
print(f"\nTesting compute_factors_at at {sample_dt}...")

# Liquidity filter
t0 = time.time()
filtered = tester._liquidity_filter(ohlcv_all, sample_dt)
print(f"Liquidity filter: {len(filtered)} stocks in {time.time()-t0:.1f}s")

# Compute factors
t0 = time.time()
factor_ranks = tester.compute_factors_at(filtered, sample_dt)
t1 = time.time()
print(f"compute_factors_at: {len(factor_ranks)} factors in {t1-t0:.1f}s")

for fid, rank in sorted(factor_ranks.items()):
    n = rank.dropna().shape[0]
    print(f"  {fid:20s}: {n} stocks")

conn.close()
