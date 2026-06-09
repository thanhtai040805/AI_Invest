"""Check bank financial statement keys"""
import psycopg2
from sqlalchemy.engine.url import make_url
url = make_url("postgresql://postgres:123@localhost:5432/aiinvest")
conn = psycopg2.connect(host=url.host, port=url.port or 5432, user=url.username, password=url.password, dbname=url.database)
cur = conn.cursor()

# Get bank symbols
cur.execute("SELECT symbol FROM stocks WHERE industry='Ngân hàng' ORDER BY symbol")
banks = [r[0] for r in cur.fetchall()]
print(f"Banks ({len(banks)}): {banks}")

# Check BS keys for bank symbols at latest period
cur.execute("""
    SELECT DISTINCT jsonb_object_keys(data) 
    FROM financial_statements 
    WHERE statement_type='BS' AND symbol = ANY(%s)
    ORDER BY 1
""", (banks,))
print(f"\nBS keys across all banks ({len(cur.fetchall())}):")
cur.execute("""
    SELECT DISTINCT jsonb_object_keys(data) 
    FROM financial_statements 
    WHERE statement_type='BS' AND symbol = ANY(%s)
    ORDER BY 1
""", (banks,))
for r in cur.fetchall():
    print(f"  {r[0]}")

# Check IS keys
cur.execute("""
    SELECT DISTINCT jsonb_object_keys(data) 
    FROM financial_statements 
    WHERE statement_type='IS' AND symbol = ANY(%s)
    ORDER BY 1
""", (banks,))
print(f"\nIS keys across all banks ({len(cur.fetchall())}):")
cur.execute("""
    SELECT DISTINCT jsonb_object_keys(data) 
    FROM financial_statements 
    WHERE statement_type='IS' AND symbol = ANY(%s)
    ORDER BY 1
""", (banks,))
for r in cur.fetchall():
    print(f"  {r[0]}")

# Check CF keys
cur.execute("""
    SELECT DISTINCT jsonb_object_keys(data) 
    FROM financial_statements 
    WHERE statement_type='CF' AND symbol = ANY(%s)
    ORDER BY 1
""", (banks,))
print(f"\nCF keys across all banks ({len(cur.fetchall())}):")
cur.execute("""
    SELECT DISTINCT jsonb_object_keys(data) 
    FROM financial_statements 
    WHERE statement_type='CF' AND symbol = ANY(%s)
    ORDER BY 1
""", (banks,))
for r in cur.fetchall():
    print(f"  {r[0]}")

# Check which symbols have rows at latest period
cur.execute("SELECT MAX(period_end) FROM financial_statements")
mx = cur.fetchone()[0]
print(f"\nLatest period: {mx}")

# Count banks with BS data at latest
cur.execute("SELECT COUNT(DISTINCT symbol) FROM financial_statements WHERE statement_type='BS' AND symbol = ANY(%s) AND period_end=%s", (banks, mx))
print(f"Banks with BS data at {mx}: {cur.fetchone()[0]}/{len(banks)}")

conn.close()
