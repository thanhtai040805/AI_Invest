"""Run full VN-core factor IC benchmark with comprehensive debug output.

Optimized: pre-loads all static data once, filters in memory per date.
"""
import sys, os, json, datetime, logging
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["DB_SCHEMA"] = os.environ.get("DB_SCHEMA", "vndev")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

import psycopg2
from app.brain.quant.factors.vn_ic_tester import VNICTester, VN_FACTORS, DB_URL

REPORT_DIR = os.path.expanduser("~/.vibe-trading/reports")
os.makedirs(REPORT_DIR, exist_ok=True)
DATE_STR = datetime.date.today().isoformat()

print("=" * 80)
print(f"VN-CORE FACTOR IC BENCHMARK — ALL 31 FACTORS")
print(f"  Report dir: {REPORT_DIR}")
print(f"  Date: {DATE_STR}")
db_disp = DB_URL.split("@")[-1] if "@" in DB_URL else DB_URL
print(f"  DB: {db_disp}")
print(f"  Factors: {len(VN_FACTORS)}")
print("=" * 80)

conn = psycopg2.connect(DB_URL)
try:
    tester = VNICTester(conn)
    results, categories = tester.run(years=3)
finally:
    conn.close()

# Save results JSON
report = {
    "generated_at": DATE_STR,
    "n_eval_dates": len(results.get("raw", [])),
    "raw_ic": results["raw"],
    "sector_neutral_ic": results["sector_neutral"],
    "categories": {k: [r["factor"] + "_" + r["hold"] for r in v] for k, v in categories.items()},
}
results_path = os.path.join(REPORT_DIR, f"vn_ic_results_{DATE_STR}.json")
with open(results_path, "w", encoding="utf-8") as f:
    json.dump(report, f, indent=2, ensure_ascii=False, default=str)
print(f"\nResults saved: {results_path}")

# Save null debug
if hasattr(tester, "null_debug") and tester.null_debug:
    null_path = os.path.join(REPORT_DIR, f"debug_null_{DATE_STR}.json")
    with open(null_path, "w", encoding="utf-8") as f:
        json.dump(tester.null_debug, f, indent=2, ensure_ascii=False, default=str)
    n_skipped = sum(1 for v in tester.null_debug.values() if "__skip__" in v)
    n_with_reasons = sum(1 for v in tester.null_debug.values()
                         if any(not k.startswith("__") for k in v))
    print(f"Null debug saved: {null_path}")
    print(f"  {len(tester.null_debug)} symbols ({n_skipped} skipped, {n_with_reasons} with factor reasons)")

# Verdict summary
print("\n" + "=" * 80)
print("VERDICT SUMMARY")
print("=" * 80)
verdicts = {}
for r in results.get("raw", []):
    v = r.get("verdict", "?")
    verdicts.setdefault(v, []).append(r)

for v in ["ALIVE", "REVERSED", "MARGINAL", "WEAK", "DEAD"]:
    rows = verdicts.get(v, [])
    if rows:
        print(f"\n{v} ({len(rows)}):")
        for r in sorted(rows, key=lambda x: -abs(x["ic"])):
            print(f"  {r['factor']:>18s} [{r['hold']}]  IC={r['ic']:+.4f}  IR={r['ir']:+.2f}  p={r['pval_adj']:.4f}  cov={r['avg_coverage']:.0f}")

print(f"\nDone.")
