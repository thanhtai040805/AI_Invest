"""Check table schemas in PostgreSQL."""
import os
os.environ["DATABASE_URL"] = "postgresql://postgres:123@localhost:5432/aiinvest"

import psycopg2
conn = psycopg2.connect("dbname=aiinvest user=postgres password=123 host=localhost port=5432")
cur = conn.cursor()

tables_to_check = ["corporate_actions", "ohlcv", "stocks"]
for tbl in tables_to_check:
    cur.execute(
        "SELECT column_name, data_type, is_nullable "
        "FROM information_schema.columns "
        "WHERE table_name=%s ORDER BY ordinal_position",
        (tbl,)
    )
    print(f"=== {tbl} ===")
    for row in cur.fetchall():
        print(f"  {row[0]:30s} {row[1]:20s} nullable={row[2]}")
    print()

conn.close()
