import psycopg2
from app.services.pg_pool import DB_URL
conn = psycopg2.connect(DB_URL)
cur = conn.cursor()

# Check what statement_types exist
cur.execute("SELECT DISTINCT statement_type FROM financial_statements")
print("statement_type values:")
for r in cur.fetchall():
    cur2 = conn.cursor()
    cur2.execute("SELECT COUNT(DISTINCT symbol) FROM financial_statements WHERE statement_type = %s", (r[0],))
    cnt = cur2.fetchone()[0]
    cur2.execute("SELECT COUNT(*) FROM financial_statements WHERE statement_type = %s", (r[0],))
    rows = cur2.fetchone()[0]
    print(f"  '{r[0]}': {cnt} symbols, {rows} rows")

# Check frequency
cur.execute("SELECT DISTINCT frequency FROM financial_statements")
print("\nfrequency values:")
for r in cur.fetchall():
    print(f"  '{r[0]}'")

# Sample data keys
cur.execute("SELECT symbol, period_end, statement_type, data FROM financial_statements LIMIT 3")
print("\nSample rows:")
for r in cur.fetchall():
    print(f"  {r[0]} {r[1]} {r[2]} keys: {list(r[3].keys())[:10]}...")

# Check foreign_flow has room data
cur.execute("SELECT COUNT(*) FROM foreign_flow WHERE room_remaining IS NOT NULL AND room_limit IS NOT NULL")
print(f"\nforeign_flow with room data: {cur.fetchone()[0]}")

cur.execute("SELECT COUNT(DISTINCT symbol) FROM foreign_flow WHERE room_remaining IS NOT NULL")
print(f"foreign_flow symbols with room: {cur.fetchone()[0]}")

# Check financial_ratios ROE fill rate
cur.execute("SELECT COUNT(*) FROM financial_ratios WHERE roe IS NOT NULL")
roe_filled = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM financial_ratios")
total = cur.fetchone()[0]
print(f"\nfinancial_ratios ROE fill: {roe_filled}/{total} = {roe_filled/total*100:.1f}%")

cur.close()
conn.close()
