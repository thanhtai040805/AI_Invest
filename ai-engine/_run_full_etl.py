import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
import psycopg2
from app.services.pg_pool import DB_URL

# 1. Delete old data (KBS source, 4 quarters only)
conn = psycopg2.connect(DB_URL)
cur = conn.cursor()
cur.execute("DELETE FROM financial_ratios")
print(f"Deleted {cur.rowcount} financial_ratios")
cur.execute("DELETE FROM financial_statements")
print(f"Deleted {cur.rowcount} financial_statements")
conn.commit()

# 2. Re-fetch with VCI source (9 quarters)
from app.services.financial_etl import refresh_all
result = refresh_all()
print(f"ETL done: {result}")

# 3. Done
cur.close()
conn.close()
