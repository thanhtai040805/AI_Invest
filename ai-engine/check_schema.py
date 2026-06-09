"""Check schemas"""
import psycopg2
from sqlalchemy.engine.url import make_url
url = make_url("postgresql://postgres:123@localhost:5432/aiinvest")
conn = psycopg2.connect(host=url.host, port=url.port or 5432, user=url.username, password=url.password, dbname=url.database)
cur = conn.cursor()
cur.execute("SELECT schema_name FROM information_schema.schemata ORDER BY schema_name")
schemas = [r[0] for r in cur.fetchall()]
print("Schemas:", schemas)
cur.execute("SELECT table_schema, table_name FROM information_schema.tables WHERE table_type='BASE TABLE' AND table_schema NOT IN ('pg_catalog','information_schema') ORDER BY table_schema, table_name")
tables = cur.fetchall()
if tables:
    for t in tables:
        print(f"  {t[0]}.{t[1]}")
else:
    print("No tables found")
conn.close()
