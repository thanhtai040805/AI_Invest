import sys; sys.path.insert(0, ".")
import psycopg2
from app.services.pg_pool import DB_URL

conn = psycopg2.connect(DB_URL)
cur = conn.cursor()

# First, check how many ratios rows exist
cur.execute("SELECT COUNT(*) FROM financial_statements WHERE statement_type='ratios'")
print(f"Ratios rows: {cur.fetchone()[0]}")

# Check data structure of first ratios row
cur.execute("""
    SELECT symbol, period_end, frequency, data
    FROM financial_statements 
    WHERE statement_type='ratios' 
    LIMIT 5
""")
for r in cur.fetchall():
    print(f"\nSymbol={r[0]} period={r[1]} freq={r[2]}")
    data = r[3]
    print(f"  Type: {type(data)}")
    if data:
        print(f"  Keys ({len(data.keys())}): {sorted(data.keys())[:20]}")
        # Try to find anything resembling ROE
        roe_keys = [k for k in data.keys() if 'roe' in k.lower() or 'return' in k.lower() or 'equity' in k.lower() or 'vốn chủ' in k.lower()]
        print(f"  ROE-related keys: {roe_keys}")
        if not roe_keys:
            print(f"  First 10 key-value pairs:")
            for i, (k, v) in enumerate(sorted(data.items())[:10]):
                print(f"    {k}: {v}")

# Now check the actual AlphaStock API has the data
import httpx
r = httpx.get("https://api-ai.alphastock.vn/api/v1/financials/report-workspace", 
              params={"symbol": "FPT", "quarter_limit": 1, "annual_limit": 1},
              headers={"User-Agent": "Mozilla/5.0"})
if r.status_code == 200:
    result = r.json()
    statements = result.get("statements", {})
    ratio_section = statements.get("ratio", {})
    print(f"\nAlphaStock API ratios quarterly data rows: {len(ratio_section.get('quarter', {}).get('data', []))}")
    for row in ratio_section.get("quarter", {}).get("data", []):
        metrics = row.get("metrics_json", {})
        print(f"  period={row.get('period_label')} metrics_keys={sorted(metrics.keys())}")
        roe_vals = {k: v for k, v in metrics.items() if 'roe' in k.lower()}
        print(f"  ROE matches: {roe_vals}")
else:
    print(f"\nAlphaStock API error: {r.status_code}")

conn.close()


conn.close()
