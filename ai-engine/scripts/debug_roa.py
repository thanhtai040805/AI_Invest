import sys; sys.path.insert(0, ".")
import psycopg2
from app.services.pg_pool import DB_URL

conn = psycopg2.connect(DB_URL)
cur = conn.cursor()

# ROE column
cur.execute("SELECT data_type FROM information_schema.columns WHERE table_name='financial_ratios' AND column_name='roe'")
print("roe col type:", cur.fetchone())

cur.execute("SELECT COUNT(*) FROM financial_ratios WHERE roe IS NOT NULL")
print("roe non-null:", cur.fetchone()[0])

cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='financial_ratios' ORDER BY ordinal_position")
cols = [r[0] for r in cur.fetchall()]
print("Columns:", cols)

cur.execute("SELECT * FROM financial_ratios LIMIT 2")
for r in cur.fetchall():
    print("Row:", dict(zip(cols, r)))

# Market cap
cur.execute("SELECT market_cap FROM stocks WHERE market_cap IS NOT NULL ORDER BY market_cap")
mcs = [r[0] for r in cur.fetchall()]
print(f"mcap range: {mcs[:3]} ... {mcs[-3:]} (n={len(mcs)})")

# Foreign flow coverage
cur.execute("SELECT COUNT(DISTINCT symbol) FROM foreign_flow")
print("foreign_flow distinct symbols:", cur.fetchone()[0])

cur.close(); conn.close()
