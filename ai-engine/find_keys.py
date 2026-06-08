"""Find actual Vietnamese key names for missed fields (simpler queries)."""
import sys, os, json
sys.path.insert(0, ".")
os.environ["DB_SCHEMA"] = os.environ.get("DB_SCHEMA", "vndev")
from app.services.pg_pool import DB_URL
import psycopg2

conn = psycopg2.connect(DB_URL)
cur = conn.cursor()

# 1) Get ALL keys for AAA BS latest period
cur.execute("""SELECT data FROM financial_statements
WHERE symbol = 'AAA' AND statement_type = 'BS'
ORDER BY period_end DESC LIMIT 1""")
row = cur.fetchone()
if row:
    data = row[0]
    print("AAA BS ALL keys:")
    for k, v in sorted(data.items()):
        print(f"  {k[:70]:70s} = {str(v)[:30]}")

# 2) Get ALL keys for AAA CF latest period
cur.execute("""SELECT data FROM financial_statements
WHERE symbol = 'AAA' AND statement_type = 'CF'
ORDER BY period_end DESC LIMIT 1""")
row = cur.fetchone()
if row:
    data = row[0]
    print("\nAAA CF ALL keys (showing lưu_chuyển related):")
    for k, v in sorted(data.items()):
        if 'tiền' in k.lower() or 'lưu' in k.lower() or 'chuyển' in k.lower() or 'thuần' in k.lower() or 'hđkd' in k.lower() or 'hoạt' in k.lower():
            print(f"  {k[:70]:70s} = {str(v)[:30]}")

# 3) Check VCB BS for total_assets-like fields
cur.execute("""SELECT data FROM financial_statements
WHERE symbol = 'VCB' AND statement_type = 'BS'
ORDER BY period_end DESC LIMIT 1""")
row = cur.fetchone()
if row:
    data = row[0]
    print("\nVCB BS keys with 'TỔNG' (total) or 'tài_sản':")
    for k, v in sorted(data.items()):
        if 'tài_sản' in k.lower() or 'tổng' in k.lower() or 'TÀI SẢN' in k or 'nguồn_vốn' in k.lower():
            print(f"  {k[:70]:70s} = {str(v)[:30]}")
    print("\nVCB BS all keys (last 10):")
    for k, v in sorted(data.items())[-10:]:
        print(f"  {k[:70]:70s} = {str(v)[:30]}")

# 4) VCB IS - net income equivalent
cur.execute("""SELECT data FROM financial_statements
WHERE symbol = 'VCB' AND statement_type = 'IS'
ORDER BY period_end DESC LIMIT 1""")
row = cur.fetchone()
if row:
    data = row[0]
    print("\nVCB IS keys (all):")
    for k, v in sorted(data.items()):
        print(f"  {k[:70]:70s} = {str(v)[:30]}")

conn.close()
