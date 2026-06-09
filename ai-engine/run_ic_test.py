"""Run VN IC benchmark and save results."""
import psycopg2, logging, time, os, json
from pathlib import Path
from datetime import date
from app.brain.quant.factors.vn_ic_tester import VNICTester

DB_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:123@localhost:5432/aiinvest")
logging.basicConfig(level=logging.INFO, format="%(message)s")

conn = psycopg2.connect(DB_URL)
tester = VNICTester(conn)

t0 = time.time()
results, categories = tester.run()
elapsed = time.time() - t0

out_dir = Path.home() / ".vibe-trading" / "reports"
out_dir.mkdir(parents=True, exist_ok=True)
ts = date.today().isoformat()

# Save results
out_path = out_dir / f"vn_ic_results_{ts}.json"
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(results, f, indent=2, ensure_ascii=False)
print(f"\nResults saved to {out_path}")

# Save null debug (cumulative per-factor null reasons at sample date)
if hasattr(tester, "null_debug") and tester.null_debug:
    null_out = out_dir / f"debug_null_{ts}.json"
    with open(null_out, "w", encoding="utf-8") as f:
        json.dump(tester.null_debug, f, indent=2, ensure_ascii=False)
    n_empty = sum(1 for v in tester.null_debug.values() if not v)
    n_with_issues = sum(1 for v in tester.null_debug.values() if v)
    print(f"Null debug saved to {null_out}")
    print(f"  Symbols with COMPLETE data ({{}}): {n_empty}")
    print(f"  Symbols with NULL issues: {n_with_issues}")

# Factor coverage summary from results
raw = results.get("raw", results)
if isinstance(raw, list):
    print(f"\n{'='*60}")
    print(f"FACTOR COVERAGE REPORT")
    print(f"{'='*60}")
    factors_seen = set()
    for r in raw:
        factors_seen.add(r["factor"])
    print(f"Factors with IC data: {len(factors_seen)}/31")
    missing = [f for f in [
        "MOM_3M","MOM_6M","COND_MOM","AMIHUD","DVOL_TREND",
        "PE_INV","PB_INV","EARN_YLD","FCF_YLD","EVEBITDA_INV","HML_REAL",
        "ACCRUAL","CFO_TO_NI","ROE_NORM","GM","NM","YOY_REV","YOY_EARN","PIOTROSKI_F",
        "EARN_SURP","ALTMAN_Z",
        "FOREIGN_NET_5D","FOREIGN_ACCUM","INSIDER_NET_30D","FOREIGN_ROOM",
        "TET_WINDOW","CEILING_STREAK","FORCED_SELLING",
        "SIZE","VOL_20D","VOL_60D"
    ] if f not in factors_seen]
    if missing:
        print(f"  MISSING: {missing}")
    else:
        print(f"  All 31 factors present!")

print(f"\nTotal time: {elapsed:.0f}s")
conn.close()
