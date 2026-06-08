import sys, os
sys.path.insert(0, ".")
from app.services.pg_pool import DB_URL
import psycopg2
conn = psycopg2.connect(DB_URL)
cur = conn.cursor()
cur.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name='financial_statements' ORDER BY ordinal_position")
for r in cur.fetchall():
    print(f"  {r[0]:20s} {r[1]}")
cur.close()
conn.close()
