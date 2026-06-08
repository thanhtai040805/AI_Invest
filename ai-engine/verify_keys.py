"""Verify what keys actually exist for each missed field."""
import sys, os
sys.path.insert(0, ".")
os.environ["DB_SCHEMA"] = os.environ.get("DB_SCHEMA", "vndev")
from app.services.pg_pool import DB_URL
import psycopg2
from datetime import date

conn = psycopg2.connect(DB_URL)
cur = conn.cursor()

# Find the ACTUAL keys for total_assets and current_assets in BS data
# for a representative non-bank symbol
for sym in ["AAA", "HPG", "FPT", "VNM"]:
    print(f"\n=== {sym} ===")
    cur.execute("""SELECT period_end, data FROM financial_statements
WHERE symbol = %s AND statement_type = 'BS'
ORDER BY period_end DESC LIMIT 2""", (sym,))
    rows = cur.fetchall()
    for pe, data in rows:
        # Find keys with large positive values (>1e12 = total assets)
        large_keys = [(k, v) for k, v in data.items() if isinstance(v, (int, float)) and v is not None and abs(v) > 1e12]
        print(f"  {pe}: {len(large_keys)} keys > 1e12")
        for k, v in sorted(large_keys)[:10]:
            print(f"    {k[:55]:55s} = {v:>20,.0f}")

# Find the CFO key values in the latest CF data
for sym in ["AAA", "HPG"]:
    print(f"\n=== {sym} CF (latest) ===")
    cur.execute("""SELECT period_end, data FROM financial_statements
WHERE symbol = %s AND statement_type = 'CF'
ORDER BY period_end DESC LIMIT 2""", (sym,))
    for pe, data in cur.fetchall():
        cfo_key = data.get("lưu_chuyển_tiền_thuần_từ_hoạt_động_kinh_doanh")
        print(f"  {pe}: CFO = {cfo_key}")
        # Also show other potential CFO-like keys
        for k in sorted(data.keys()):
            if "lưu_chuyển" in k and "kinh_doanh" in k:
                print(f"    key: {k[:60]} = {data[k]}")

# Confirm: does current_assets candidate actually exist?
cur.execute("""SELECT data FROM financial_statements
WHERE symbol = 'AAA' AND statement_type = 'BS'
ORDER BY period_end DESC LIMIT 1""")
data = cur.fetchone()[0]
print(f"\n=== AAA current_assets checks ===")
for key in ["a_tài_sản_ngắn_hạn", "A. TÀI SẢN NGẮN HẠN"]:
    print(f"  '{key}': {key in data} -> {data.get(key)}")
for key in ["i_tài_sản_ngắn_hạn", "I. Tài sản ngắn hạn"]:
    print(f"  '{key}': {key in data} -> {data.get(key)}")

conn.close()
