import psycopg2
from app.services.pg_pool import DB_URL
conn = psycopg2.connect(DB_URL)
cur = conn.cursor()

# Check financial_statements coverage
cur.execute("SELECT COUNT(DISTINCT symbol) FROM financial_statements")
print(f"financial_statements distinct symbols: {cur.fetchone()[0]}")

cur.execute("SELECT COUNT(DISTINCT symbol) FROM financial_statements WHERE statement_type = 'balance_sheet'")
print(f"  balance_sheet symbols: {cur.fetchone()[0]}")

cur.execute("SELECT COUNT(DISTINCT symbol) FROM financial_statements WHERE statement_type = 'income_statement'")
print(f"  income_statement symbols: {cur.fetchone()[0]}")

cur.execute("SELECT COUNT(DISTINCT symbol) FROM financial_statements WHERE statement_type = 'cash_flow'")
print(f"  cash_flow symbols: {cur.fetchone()[0]}")

# Check financial_ratios columns
cur.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'financial_ratios' ORDER BY ordinal_position")
print(f"\nfinancial_ratios columns:")
for r in cur.fetchall():
    print(f"  {r[0]}: {r[1]}")

# Check financial_statements sample
cur.execute("SELECT symbol, period_end, statement_type, jsonb_object_keys(data) as keys FROM financial_statements WHERE symbol = 'VCB' AND statement_type = 'balance_sheet' LIMIT 1")
print(f"\nfinancial_statements sample keys (VCB balance_sheet):")
for r in cur.fetchall():
    print(f"  {r[0]} keys: {r[3]}")

cur.execute("SELECT symbol, period_end, statement_type, jsonb_object_keys(data) as keys FROM financial_statements WHERE symbol = 'VCB' AND statement_type = 'income_statement' LIMIT 1")
print(f"\nfinancial_statements sample keys (VCB income_statement):")
for r in cur.fetchall():
    print(f"  {r[0]} keys: {r[3]}")

# Check financial_ratios sample
cur.execute("SELECT * FROM financial_ratios WHERE symbol = 'VCB' LIMIT 1")
cols = [desc[0] for desc in cur.description]
print(f"\nfinancial_ratios VCB row columns: {cols}")
row = cur.fetchone()
if row:
    print(f"  values: {row}")

# Count financial_ratios rows per symbol
cur.execute("SELECT COUNT(*) FROM financial_ratios")
print(f"\nTotal financial_ratios rows: {cur.fetchone()[0]}")

cur.execute("SELECT COUNT(DISTINCT symbol) FROM financial_ratios")
print(f"Distinct symbols: {cur.fetchone()[0]}")

cur.close()
conn.close()
