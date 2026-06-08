"""Check what compute_factors_at actually loads from DB."""
import sys, datetime
sys.path.insert(0, ".")

import psycopg2
from app.services.pg_pool import DB_URL
from app.brain.quant.factors.vn_ic_tester import VNICTester

conn = psycopg2.connect(DB_URL)
t = VNICTester(conn)

dt = datetime.date(2024, 6, 15)
symbols = ["VCB", "BID", "CTG", "ACB", "VPB", "VIC", "VHM", "FPT", "HPG", "VNM"]

# Test each loader
print("=== _load_fundamentals ===")
fin = t._load_fundamentals(dt, symbols)
print(f"  {len(fin)} symbols loaded")
for sym, f in fin.items():
    if f["pe"] or f["roe"]:
        print(f"  {sym}: PE={f['pe']}, ROE={f['roe']}, PB={f['pb']}")
        break

print("\n=== _load_meta ===")
meta = t._load_meta(symbols)
print(f"  {len(meta)} symbols loaded")
for sym, m in list(meta.items())[:2]:
    print(f"  {sym}: mcap={m['mcap']}, ceiling={m['ceiling']}, floor={m['floor']}")

print("\n=== _load_foreign ===")
foreign = t._load_foreign(dt, symbols)
print(f"  {len(foreign)} symbols loaded")
for sym, f in list(foreign.items())[:2]:
    print(f"  {sym}: net_value={f['net_value']}, room_remaining={f['room_remaining']}, room_limit={f['room_limit']}")

print("\n=== _load_insider ===")
insider = t._load_insider(dt, symbols)
print(f"  {len(insider)} symbols with data")
for sym, v in list(insider.items())[:2]:
    print(f"  {sym}: net={v}")

# Now trace what compute_factors_at would produce for these
print("\n=== compute_factors_at ===")
# Need OHLCV data first
ohlcv = t.load_full_ohlcv(symbols, datetime.date(2023,1,1), dt)
filtered = t._liquidity_filter(ohlcv, dt)
print(f"  Liquid: {len(filtered)}")
if len(filtered) >= 5:
    ranks = t.compute_factors_at(filtered, dt)
    print(f"  Factors returned: {sorted(ranks.keys())}")
    # Debug: which factors from VN_FACTORS are MISSING?
    from app.brain.quant.factors.vn_ic_tester import VN_FACTORS
    missing = [f for f in VN_FACTORS if f not in ranks]
    if missing:
        print(f"\n  ❌ FACTORS NOT COMPUTED: {missing}")

conn.close()
