import sys; sys.path.insert(0,".")
from collections import defaultdict
from datetime import date, timedelta
import pandas as pd
import psycopg2
from app.services.pg_pool import DB_URL

start = date.today() - timedelta(days=int(3*365.25+400+60))
end = date.today()

conn = psycopg2.connect(DB_URL)
cur = conn.cursor()
cur.execute("SELECT symbol, time::date as dt, open, high, low, close, volume "
            "FROM ohlcv WHERE time::date >= %s AND time::date <= %s ORDER BY symbol, time",
            (start, end))
records = defaultdict(list)
for sym, dt, op, hi, lo, cl, vol in cur.fetchall():
    records[sym].append({"date": dt, "open": float(op or 0), "high": float(hi or 0),
                         "low": float(lo or 0), "close": float(cl or 0), "volume": float(vol or 0)})
cur.close(); conn.close()

print(f"Total symbols in DB: {len(records)}")

cols = {"open": {}, "high": {}, "low": {}, "close": {}, "volume": {}}
for sym, rows in records.items():
    df = pd.DataFrame(rows).set_index("date").sort_index()
    df.index = pd.to_datetime(df.index)
    if len(df) < 60:
        continue
    for c in cols:
        cols[c][sym] = df[c]

print(f"Symbols with >=60d: {len(cols['close'])}")

if cols["close"]:
    # Use sorted union not intersection to get all dates
    all_dates = sorted(set.union(*[set(s.index) for s in cols["close"].values()]))
    print(f"Union dates: {len(all_dates)}  [{all_dates[0]} .. {all_dates[-1]}]")
    
    # Try with common dates (stricter)
    common_dates = sorted(set.intersection(*[set(s.index) for s in cols["close"].values()]))
    print(f"Intersection dates: {len(common_dates)}  [{common_dates[0]} .. {common_dates[-1]}]")
    
    # Build wide DataFrame
    panel_close = pd.DataFrame(cols["close"], index=common_dates)
    print(f"Panel close shape: {panel_close.shape}")
else:
    print("NO SYMBOLS - debugging individual:")
    for sym, rows in list(records.items())[:3]:
        print(f"  {sym}: {len(rows)} rows")
