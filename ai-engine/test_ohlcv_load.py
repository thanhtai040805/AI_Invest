"""Test OHLCV loading speed."""
import psycopg2, time, os
from collections import defaultdict
import pandas as pd
import math

DB_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:123@localhost:5432/aiinvest")
conn = psycopg2.connect(DB_URL)
cur = conn.cursor()

cur.execute("SELECT symbol FROM stocks WHERE exchange IN ('HOSE','HSX') ORDER BY symbol")
symbols = [r[0] for r in cur.fetchall()]
print(f"Symbols: {len(symbols)}")

# Test query
t0 = time.time()
cur.execute(
    "SELECT symbol, time::date as dt, adj_close, close, volume "
    "FROM ohlcv "
    "WHERE time::date >= '2022-03-06' AND time::date <= '2026-06-08' AND symbol = ANY(%s) "
    "ORDER BY symbol, time",
    (symbols,),
)
rows = cur.fetchall()
t1 = time.time()
print(f"Query: {len(rows)} rows in {t1-t0:.1f}s")

# Test iteration
records = defaultdict(list)
skipped = 0
for sym, dt, ac, cl, vol in rows:
    c = float(ac) if ac is not None else (float(cl) if cl is not None else None)
    if c is None or not math.isfinite(c):
        skipped += 1
        continue
    records[sym].append({"date": dt, "close": c, "volume": int(vol) if vol is not None else 0})
t2 = time.time()
print(f"Iteration: {len(records)} symbols in {t2-t1:.1f}s (skipped {skipped})")

# Test dataframe build
result = {}
for sym, rows_list in records.items():
    df = pd.DataFrame(rows_list).set_index("date").sort_index()
    df.index = pd.to_datetime(df.index)
    if len(df) > 60:
        df["value"] = df["close"] * df["volume"]
        result[sym] = df
t3 = time.time()
print(f"DataFrame build: {len(result)} symbols in {t3-t2:.1f}s")
print(f"Total: {t3-t0:.1f}s")

conn.close()
