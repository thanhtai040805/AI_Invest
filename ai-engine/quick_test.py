"""Quick test — 1-year benchmark to verify key mapping fixes."""
import sys, os, json, datetime, logging
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["DB_SCHEMA"] = os.environ.get("DB_SCHEMA", "vndev")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

import psycopg2
from app.brain.quant.factors.vn_ic_tester import VNICTester, VN_FACTORS, DB_URL

conn = psycopg2.connect(DB_URL)
tester = VNICTester(conn)
results, categories = tester.run(years=1)
conn.close()

# Show coverage for missing factors
for r in results.get("raw", []):
    if r["avg_coverage"] > 0 and r["factor"] in ("ACCRUAL", "ALTMAN_Z"):
        print(f"\n{r['factor']} [{r['hold']}]: IC={r['ic']:.4f}  cov={r['avg_coverage']:.0f}  [{r['verdict']}]")

print("\nDone.")
