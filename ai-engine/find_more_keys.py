import sys, os
sys.path.insert(0, ".")
os.environ["DB_SCHEMA"] = os.environ.get("DB_SCHEMA", "vndev")
from app.services.pg_pool import DB_URL
import psycopg2

conn = psycopg2.connect(DB_URL)
cur = conn.cursor()

# VCB BS cash keys
cur.execute("""SELECT data FROM financial_statements
WHERE symbol = 'VCB' AND statement_type = 'BS'
ORDER BY period_end DESC LIMIT 1""")
data = cur.fetchone()[0]
print("VCB BS cash-related keys:")
for k in sorted(data.keys()):
    if "tiền" in k.lower() or "cash" in k.lower():
        print(f"  {k[:70]} = {data[k]}")

# VCB CF CFO keys
cur.execute("""SELECT data FROM financial_statements
WHERE symbol = 'VCB' AND statement_type = 'CF'
ORDER BY period_end DESC LIMIT 1""")
data = cur.fetchone()[0]
print("\nVCB CF CFO-related keys:")
for k in sorted(data.keys()):
    if "lưu_chuyển" in k and "hoạt_động_kinh_doanh" in k:
        print(f"  {k[:80]} = {data[k]}")

# HPG short_term_debt candidates
cur.execute("""SELECT data FROM financial_statements
WHERE symbol = 'HPG' AND statement_type = 'BS'
ORDER BY period_end DESC LIMIT 1""")
data = cur.fetchone()[0]
print("\nHPG short_term_debt candidates:")
for k in sorted(data.keys()):
    if ("nợ" in k.lower() and ("ngắn" in k.lower() or "vay" in k.lower())) or "debt" in k.lower():
        v = data[k]
        if v is not None and (not isinstance(v, (int, float)) or abs(v) > 1e10):
            print(f"  {k[:65]} = {v}")

# HPG retained_earnings
print("\nHPG retained_earnings candidates:")
for k in sorted(data.keys()):
    if "phân_phối" in k.lower() or "chưa" in k.lower() or "retain" in k.lower():
        v = data[k]
        if v is not None and isinstance(v, (int, float)) and abs(v) > 1e10:
            print(f"  {k[:65]} = {v:,.0f}")

# HPG depreciation
print("\nHPG depreciation candidates:")
for k in sorted(data.keys()):
    if "khấu" in k.lower() or "hao" in k.lower() or "khau" in k.lower():
        print(f"  {k[:65]} = {data[k]}")

conn.close()
