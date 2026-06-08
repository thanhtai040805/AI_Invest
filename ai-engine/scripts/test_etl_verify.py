"""Quick verification: test alphastock ETL for a few symbols.

Tests:
  - HPG (normal company) — should have full data
  - VMD (excluded) — should be skipped
  - SBT (special FY) — should handle period correctly
  - MIG (insurance) — should work
"""
import sys
sys.path.insert(0, r"D:\AIInvest\ai-engine")

from app.services.financial_etl_alphastock import (
    EXCLUDED_SYMBOLS,
    _get_workspace,
    _period_label_to_date,
    _STMT_MAP,
    _PERIOD_TYPE_MAP,
)

print("=== Excluded symbols ===")
print(f"  {sorted(EXCLUDED_SYMBOLS)}")

print("\n=== Testing: HPG (normal company) ===")
data = _get_workspace("HPG")
if data:
    co = data.get("company", {})
    print(f"  Company: {co.get('company_name')}")
    stmts = data.get("statements", {})
    for api_stmt in _STMT_MAP:
        for api_period in _PERIOD_TYPE_MAP:
            section = stmts.get(api_stmt, {}).get(api_period, {})
            rows = section.get("data", [])
            if rows:
                labels = [r.get("period_label", "") for r in rows]
                print(f"  {api_stmt}/{api_period}: {len(rows)} periods ({labels[0]}...{labels[-1]})")
                # Show period_end mapping for first 3
                for r in rows[:3]:
                    pe = _period_label_to_date(r.get("period_label", ""))
                    print(f"    {r['period_label']} → period_end={pe}")
                break

print("\n=== Testing: SBT (special FY) ===")
data = _get_workspace("SBT")
if data:
    co = data.get("company", {})
    print(f"  Company: {co.get('company_name')}")
    stmts = data.get("statements", {})
    for api_stmt in _STMT_MAP:
        for api_period in _PERIOD_TYPE_MAP:
            section = stmts.get(api_stmt, {}).get(api_period, {})
            rows = section.get("data", [])
            if rows:
                labels = [r.get("period_label", "") for r in rows]
                print(f"  {api_stmt}/{api_period}: {len(rows)} periods ({labels[0]}...{labels[-1]})")
                for r in rows[:3]:
                    pe = _period_label_to_date(r.get("period_label", ""))
                    print(f"    {r['period_label']} → period_end={pe}")
                break

print("\n=== Testing: VMD (excluded) ===")
print(f"  VMD in EXCLUDED_SYMBOLS: {'VMD' in EXCLUDED_SYMBOLS}")

print("\nDone.")
