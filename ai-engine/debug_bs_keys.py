import psycopg2
from app.services.pg_pool import DB_URL
conn = psycopg2.connect(DB_URL)
cur = conn.cursor()

# Find common keys across BS statements (keys that appear in most rows)
cur.execute("""
    SELECT key, COUNT(*) as cnt
    FROM financial_statements, jsonb_object_keys(data) as key
    WHERE statement_type = 'BS'
    GROUP BY key
    ORDER BY cnt DESC
    LIMIT 40
""")
print("BS keys (most common):")
for r in cur.fetchall():
    print(f"  {r[0]}: {r[1]} rows")

cur.execute("""
    SELECT key, COUNT(*) as cnt
    FROM financial_statements, jsonb_object_keys(data) as key
    WHERE statement_type = 'IS'
    GROUP BY key
    ORDER BY cnt DESC
    LIMIT 40
""")
print("\nIS keys (most common):")
for r in cur.fetchall():
    print(f"  {r[0]}: {r[1]} rows")

cur.execute("""
    SELECT key, COUNT(*) as cnt
    FROM financial_statements, jsonb_object_keys(data) as key
    WHERE statement_type = 'CF'
    GROUP BY key
    ORDER BY cnt DESC
    LIMIT 20
""")
print("\nCF keys (most common):")
for r in cur.fetchall():
    print(f"  {r[0]}: {r[1]} rows")

# Check what keys could be used for ACCRUAL
# ACCRUAL = (ΔCA - ΔCash - ΔCL + ΔSTD) / NI
# Need: current_assets, cash, current_liabilities, short_term_debt, net_income
print("\n--- Checking for ACCRUAL-relevant keys ---")
for q in ["tài_sản_ngắn_hạn", "tiền", "nợ_ngắn_hạn", "vay_ngắn_hạn", "lợi_nhuận_sau_thuế"]:
    cur.execute("SELECT COUNT(*) FROM financial_statements WHERE statement_type='BS' AND data ? %s", (q,))
    bs = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM financial_statements WHERE statement_type='IS' AND data ? %s", (q,))
    is_ = cur.fetchone()[0]
    print(f"  '{q}': BS={bs}, IS={is_}")

# Check specific VCB BS data
cur.execute("SELECT data FROM financial_statements WHERE symbol='VCB' AND statement_type='BS' ORDER BY period_end DESC LIMIT 2")
print("\nVCB BS latest keys:")
for r in cur.fetchall():
    print(f"  {list(r[0].keys())[:15]}...")

cur.close()
conn.close()
