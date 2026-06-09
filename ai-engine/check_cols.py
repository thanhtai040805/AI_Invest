"""Quick column checker"""
import psycopg2
from sqlalchemy.engine.url import make_url
url = make_url("postgresql://postgres:123@localhost:5432/aiinvest")
conn = psycopg2.connect(host=url.host, port=url.port or 5432, user=url.username, password=url.password, dbname=url.database)
cur = conn.cursor()
for tbl in ["stocks", "ohlcv", "financial_ratios", "financial_statements", "foreign_flow", "insider_trades", "factor_scores"]:
    print(f"\n=== {tbl} ===")
    cur.execute(f"SELECT column_name, data_type FROM information_schema.columns WHERE table_schema='public' AND table_name='{tbl}' ORDER BY ordinal_position")
    for c in cur.fetchall():
        print(f"  {c[0]:>25s}: {c[1]}")
    cur.execute(f"SELECT COUNT(*) FROM public.{tbl}")
    print(f"  Rows: {cur.fetchone()[0]:,}")
conn.close()
