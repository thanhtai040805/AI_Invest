import sys, os, json
sys.path.insert(0, ".")
os.environ["DB_SCHEMA"] = os.environ.get("DB_SCHEMA", "vndev")
from app.services.pg_pool import DB_URL
import psycopg2

conn = psycopg2.connect(DB_URL)
cur = conn.cursor()

# Dump ALL unique keys for a non-bank (AAA) and a bank (VCB)
for sym in ["AAA", "VCB"]:
    for st in ["BS", "IS", "CF"]:
        cur.execute("""SELECT DISTINCT jsonb_object_keys(data) as k
FROM financial_statements
WHERE symbol = %s AND statement_type = %s
ORDER BY k""", (sym, st))
        keys = [r[0] for r in cur.fetchall()]
        print(f"\n{sym} [{st}] ({len(keys)} keys):")
        for k in keys[:30]:
            print(f"  {k}")
        if len(keys) > 30:
            print(f"  ... and {len(keys)-30} more")

# Check what market_cap values look like
cur.execute("SELECT symbol, market_cap FROM stocks WHERE market_cap > 0 LIMIT 10")
print("\nmarket_cap samples:")
for r in cur.fetchall():
    print(f"  {r[0]}: {r[1]:,.0f}")

# Check symbols without market_cap
cur.execute("SELECT symbol FROM stocks WHERE exchange IN ('HOSE','HSX') AND (market_cap IS NULL OR market_cap = 0) LIMIT 10")
print("\nSymbols with no market_cap:")
for r in cur.fetchall():
    print(f"  {r[0]}")

cur.close()
conn.close()
