"""Debug migration — check why tables don't appear."""
import os
os.environ["DATABASE_URL"] = "postgresql://postgres:123@localhost:5432/aiinvest"

import importlib
import app.infrastructure.database.pg_pool
importlib.reload(app.infrastructure.database.pg_pool)

print("DB_URL:", app.infrastructure.database.pg_pool.DB_URL)

import psycopg2
conn = psycopg2.connect("dbname=aiinvest user=postgres password=123 host=localhost port=5432")
cur = conn.cursor()

cur.execute("SELECT current_database()")
print("DB:", cur.fetchone())

cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")
tables = [r[0] for r in cur.fetchall()]
print("Tables before:", tables)
conn.close()

app.infrastructure.database.pg_pool.migrate()

conn2 = psycopg2.connect("dbname=aiinvest user=postgres password=123 host=localhost port=5432")
cur2 = conn2.cursor()
cur2.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")
tables_after = [r[0] for r in cur2.fetchall()]
print("Tables after:", tables_after)
conn2.close()
