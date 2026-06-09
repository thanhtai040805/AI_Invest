"""Check industry and sector mapping"""
import psycopg2
from sqlalchemy.engine.url import make_url
url = make_url("postgresql://postgres:123@localhost:5432/aiinvest")
conn = psycopg2.connect(host=url.host, port=url.port or 5432, user=url.username, password=url.password, dbname=url.database)
cur = conn.cursor()

# Stocks with NULL/empty industry
cur.execute("SELECT symbol, exchange, market_cap FROM stocks WHERE industry IS NULL OR industry = '' ORDER BY symbol")
rows = cur.fetchall()
print(f"Stocks with NULL/empty industry ({len(rows)}):")
for r in rows:
    print(f"  {r[0]:>8s}  {r[1]:>6s}  mcap={r[2]}")

# Sample industry values that DO exist 
cur.execute("SELECT DISTINCT industry FROM stocks WHERE industry IS NOT NULL AND industry != '' ORDER BY industry")
inds = cur.fetchall()
print(f"\nUnique industry values ({len(inds)}):")
for r in inds:
    print(f"  {r[0]}")

conn.close()
