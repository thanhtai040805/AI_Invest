"""Direct test: does _pick_key find the CFO key?"""
import sys, os, json, math
sys.path.insert(0, ".")
os.environ["DB_SCHEMA"] = os.environ.get("DB_SCHEMA", "vndev")
from app.services.pg_pool import DB_URL
import psycopg2
from datetime import date

conn = psycopg2.connect(DB_URL)
cur = conn.cursor()

# Direct test: check the actual JSON keys in the CF data
cur.execute("""SELECT data FROM financial_statements
WHERE symbol = 'AAA' AND statement_type = 'CF'
ORDER BY period_end DESC LIMIT 1""")
row = cur.fetchone()
if row:
    data = row[0]
    print(f"CF data type: {type(data)}")
    print(f"CF data keys (first 10):")
    for i, k in enumerate(sorted(data.keys())):
        if i < 10:
            print(f"  [{i}] '{k}' -> {data[k]}")
    print(f"\nTotal keys: {len(data)}")

    # Check the EXACT candidate keys
    candidates = (
        "lưu_chuyển_tiền_thuần_từ_hoạt_động_kinh_doanh",
        "Lưu chuyển tiền thuần từ hoạt động kinh doanh",
        "tiền_thuần_từ_hđkd",
        "Tiền thuần từ HĐKD",
    )
    for c in candidates:
        found = c in data
        print(f"\n  Candidate '{c}':")
        print(f"    In data: {found}")
        if found:
            print(f"    Value: {data[c]}")

# Also check the BS total_assets keys
cur.execute("""SELECT data FROM financial_statements
WHERE symbol = 'AAA' AND statement_type = 'BS'
ORDER BY period_end DESC LIMIT 1""")
row = cur.fetchone()
if row:
    data = row[0]
    print("\n\nBS total_assets candidates:")
    candidates = (
        "tài_sản", "TÀI SẢN", "a_tài_sản", "A. TÀI SẢN",
        "tổng_cộng_tài_sản", "TỔNG CỘNG TÀI SẢN",
    )
    for c in candidates:
        found = c in data
        print(f"  '{c[:40]}': in_data={found}")
        if found:
            print(f"    Value: {data[c]}")

# Check total_liabilities
    print("\nBS total_liabilities candidates:")
    candidates = (
        "nợ_phải_trả", "Nợ phải trả",
        "tổng_nợ_phải_trả", "TỔNG NỢ PHẢI TRẢ",
        "c_nợ_phải_trả", "C. NỢ PHẢI TRẢ",
    )
    for c in candidates:
        found = c in data
        print(f"  '{c[:40]}': in_data={found}")
        if found:
            print(f"    Value: {data[c]}")

# Check current_assets
    print("\nBS current_assets candidates:")
    candidates = (
        "tài_sản_ngắn_hạn", "Tài sản ngắn hạn",
        "i_tài_sản_ngắn_hạn", "I. Tài sản ngắn hạn",
        "a_tài_sản_ngắn_hạn", "A. TÀI SẢN NGẮN HẠN",
    )
    for c in candidates:
        found = c in data
        print(f"  '{c[:45]}': in_data={found}")
        if found:
            print(f"    Value: {data[c]}")

conn.close()
