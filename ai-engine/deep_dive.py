"""Deep dive on found issues"""
import psycopg2
from sqlalchemy.engine.url import make_url
url = make_url("postgresql://postgres:123@localhost:5432/aiinvest")
conn = psycopg2.connect(host=url.host, port=url.port or 5432, user=url.username, password=url.password, dbname=url.database)
cur = conn.cursor()

print("=" * 80)
print("DEEP DIVE: FINANCIAL STATEMENTS")
print("=" * 80)

# Check what statement_type values exist
cur.execute("SELECT DISTINCT statement_type FROM financial_statements")
print("\nstatement_type values:")
for r in cur.fetchall():
    cur.execute("SELECT COUNT(*) FROM financial_statements WHERE statement_type=%s", (r[0],))
    print(f"  '{r[0]}': {cur.fetchone()[0]} rows")

# Check what frequency values exist
cur.execute("SELECT DISTINCT frequency FROM financial_statements")
print("\nfrequency values:")
for r in cur.fetchall():
    cur.execute("SELECT COUNT(*) FROM financial_statements WHERE frequency=%s", (r[0],))
    print(f"  '{r[0]}': {cur.fetchone()[0]} rows")

# Date range per type
cur.execute("SELECT statement_type, MIN(period_end), MAX(period_end) FROM financial_statements GROUP BY statement_type ORDER BY statement_type")
print("\nDate ranges per statement_type:")
for r in cur.fetchall():
    print(f"  {r[0]:>25s}: {r[1]} to {r[2]}")

# Sample keys for first type found
cur.execute("SELECT DISTINCT statement_type FROM financial_statements LIMIT 1")
first_type = cur.fetchone()[0]
cur.execute("SELECT DISTINCT jsonb_object_keys(data) FROM financial_statements WHERE statement_type=%s LIMIT 10", (first_type,))
print(f"\nSample keys for '{first_type}':")
for r in cur.fetchall():
    print(f"  {r[0]}")

print("\n" + "=" * 80)
print("DEEP DIVE: FOREIGN FLOW ROOM_LIMIT")
print("=" * 80)

cur.execute("SELECT COUNT(*), COUNT(room_limit), COUNT(*) FILTER (WHERE room_limit=0), COUNT(*) FILTER (WHERE room_limit>0), COUNT(*) FILTER (WHERE room_limit IS NULL) FROM foreign_flow")
tot, has, zero, pos, null = cur.fetchone()
print(f"\nTotal rows: {tot}")
print(f"room_limit IS NOT NULL: {has} ({100*has//tot}%)")
print(f"room_limit = 0: {zero}")
print(f"room_limit > 0: {pos}")
print(f"room_limit IS NULL: {null}")

# Sample non-null room_limit values
cur.execute("SELECT DISTINCT room_limit FROM foreign_flow WHERE room_limit IS NOT NULL AND room_limit > 0 ORDER BY room_limit LIMIT 20")
print("\nNon-zero room_limit values present:")
vals = cur.fetchall()
if vals:
    for r in vals:
        print(f"  {r[0]}")
else:
    print("  NONE - all room_limit are 0 or NULL")

# Check the SQL used in vn_ic_tester
print("\n\nLooking at vn_ic_tester _load_foreign_accum SQL:")
print("  SELECT symbol, trade_date as dt,")
print("    MAX(room_remaining) as room_remaining,")
print("    MAX(room_limit) as room_limit,")
print("    room_remaining::float / NULLIF(room_limit, 0) as room_ratio")
print("  FROM foreign_flow WHERE ...")
print("  This would produce NULL for room_ratio if room_limit=0 or NULL")

print("\n" + "=" * 80)
print("DEEP DIVE: MARKET CAP")
print("=" * 80)

# Check if market_cap comes from stocks table or fundamentals
cur.execute("""
    SELECT symbol, market_cap, industry
    FROM stocks
    WHERE market_cap IS NULL OR market_cap = 0
    ORDER BY symbol
    LIMIT 40
""")
rows = cur.fetchall()
print(f"\n{len(rows)} samples of stocks with NULL market_cap:")
for r in rows[:10]:
    print(f"  {r[0]:>8s}  industry={str(r[2] or 'N/A'):>20s}")

# Check if some are ETFs/funds
print("\nSymbols with 'FUC' prefix (likely ETFs/funds):")
cur.execute("SELECT symbol, exchange, market_cap FROM stocks WHERE symbol LIKE 'FUC%'")
for r in cur.fetchall():
    print(f"  {r[0]:>8s}  {r[1]:>6s}  mcap={r[2]}")

conn.close()
