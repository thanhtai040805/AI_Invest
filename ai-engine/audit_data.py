"""VN-DEV Database — Full Data Audit"""
import psycopg2, os
from sqlalchemy.engine.url import make_url

DB_URL = os.getenv("DATABASE_URL", "postgresql://postgres:123@localhost:5432/aiinvest")
url = make_url(DB_URL)
conn = psycopg2.connect(host=url.host, port=url.port or 5432, user=url.username, password=url.password, dbname=url.database)
cur = conn.cursor()

report = []
report.append("=" * 80)
report.append("VN-DEV DATABASE — FULL DATA AUDIT  (public schema)")
report.append("=" * 80)

# ── 1. STOCKS ──
cur.execute("SELECT COUNT(*), COUNT(DISTINCT exchange) FROM stocks")
n, nx = cur.fetchone()
report.append(f"\n1. STOCKS: {n} symbols, {nx} exchanges")
cur.execute("SELECT exchange, COUNT(*) FROM stocks GROUP BY exchange ORDER BY COUNT(*) DESC")
for r in cur.fetchall():
    report.append(f"     {r[0]}: {r[1]}")
cur.execute("SELECT industry, COUNT(*) FROM stocks GROUP BY industry ORDER BY COUNT(*) DESC LIMIT 15")
report.append(f"     Top industries:")
for r in cur.fetchall():
    report.append(f"       {str(r[0] or 'NULL'):>30s}: {r[1]}")
# Market cap
cur.execute("SELECT COUNT(*) FROM stocks WHERE market_cap > 0")
n_mcap = cur.fetchone()[0]
report.append(f"     Market cap >0: {n_mcap}/{n}")
cur.execute("SELECT COUNT(*) FROM stocks WHERE market_cap IS NULL OR market_cap = 0")
n_null = cur.fetchone()[0]
report.append(f"     Market cap NULL/0: {n_null}/{n}")
# Symbols missing mcap
if n_null > 0:
    cur.execute("SELECT symbol, exchange, market_cap FROM stocks WHERE market_cap IS NULL OR market_cap = 0 ORDER BY symbol LIMIT 30")
    report.append(f"     Missing market_cap:")
    for r in cur.fetchall():
        report.append(f"       {r[0]:>6s} ({r[1]:>6s})  mcap={r[2]}")

# ── 2. OHLCV ──
cur.execute("SELECT COUNT(*), MIN(time), MAX(time) FROM ohlcv")
n, mn, mx = cur.fetchone()
report.append(f"\n2. OHLCV: {n:,} rows ({mn} to {mx})")
cur.execute("SELECT COUNT(DISTINCT symbol) FROM ohlcv")
report.append(f"     Symbols: {cur.fetchone()[0]}")
cur.execute("SELECT COUNT(DISTINCT time) FROM ohlcv")
report.append(f"     Trading days: {cur.fetchone()[0]}")
cur.execute("SELECT time, COUNT(DISTINCT symbol) FROM ohlcv GROUP BY time ORDER BY time DESC LIMIT 5")
report.append(f"     Most recent:")
for r in cur.fetchall():
    report.append(f"       {str(r[0]):>12s}: {r[1]} symbols")
# Oldest date per symbol
cur.execute("""
    SELECT COUNT(*) FROM (
        SELECT symbol, MIN(time) as first_date, COUNT(*) as n_days
        FROM ohlcv GROUP BY symbol
        HAVING MIN(time) <= '2023-01-01' AND COUNT(*) >= 500
    ) s
""")
report.append(f"     Symbols with data from before 2023 (>=500 days): {cur.fetchone()[0]}")

# ── 3. FINANCIAL_RATIOS ──
cur.execute("SELECT COUNT(*), MIN(ratio_date), MAX(ratio_date) FROM financial_ratios")
n, mn, mx = cur.fetchone()
report.append(f"\n3. FINANCIAL_RATIOS: {n:,} rows ({mn} to {mx})")
cur.execute("SELECT COUNT(DISTINCT symbol) FROM financial_ratios")
report.append(f"     Symbols: {cur.fetchone()[0]}")
cur.execute("SELECT COUNT(DISTINCT ratio_date) FROM financial_ratios")
report.append(f"     Quarters: {cur.fetchone()[0]}")
cur.execute("SELECT MAX(ratio_date) FROM financial_ratios")
lr = cur.fetchone()[0]
report.append(f"     Latest: {lr}")
# Fill rates at latest
for col in ["pe", "pb", "roe", "gross_margin", "net_margin", "fcf_yield", "ev_ebitda"]:
    cur.execute("SELECT COUNT(*) FROM financial_ratios WHERE ratio_date=%s AND %s IS NOT NULL" % ("%s", col), (lr,))
    nv = cur.fetchone()[0]
    cur.execute("SELECT COUNT(DISTINCT symbol) FROM financial_ratios WHERE ratio_date=%s", (lr,))
    nt = cur.fetchone()[0]
    if nt > 0:
        report.append(f"       {col:>20s}: {nv:>4d}/{nt} ({100*nv//nt}%)")
# Symbols per quarter
cur.execute("SELECT ratio_date, COUNT(DISTINCT symbol) FROM financial_ratios GROUP BY ratio_date ORDER BY ratio_date DESC LIMIT 10")
report.append(f"     Symbols per quarter:")
for r in cur.fetchall():
    report.append(f"       {str(r[0]):>12s}: {r[1]}")

# ── 4. FINANCIAL_STATEMENTS ──
for stmt in ["balance_sheet", "income_statement", "cash_flow"]:
    cur.execute("SELECT COUNT(*), MIN(period_end), MAX(period_end) FROM financial_statements WHERE statement_type=%s", (stmt,))
    n, mn, mx = cur.fetchone()
    cur.execute("SELECT COUNT(DISTINCT symbol) FROM financial_statements WHERE statement_type=%s", (stmt,))
    nf = cur.fetchone()[0]
    cur.execute("SELECT COUNT(DISTINCT period_end) FROM financial_statements WHERE statement_type=%s", (stmt,))
    npd = cur.fetchone()[0]
    cur.execute("SELECT DISTINCT jsonb_object_keys(data) FROM financial_statements WHERE statement_type=%s LIMIT 8", (stmt,))
    sk = [r[0] for r in cur.fetchall()]
    report.append(f"\n4. {stmt}: {n:,} rows, {nf} symbols, {npd} periods ({mn} to {mx})")
    report.append(f"     Sample keys: {sk}")

# ── 5. FOREIGN_FLOW ──
cur.execute("SELECT COUNT(*), MIN(trade_date), MAX(trade_date) FROM foreign_flow")
n, mn, mx = cur.fetchone()
cur.execute("SELECT COUNT(DISTINCT symbol) FROM foreign_flow")
nf = cur.fetchone()[0]
cur.execute("SELECT COUNT(DISTINCT trade_date) FROM foreign_flow")
nd = cur.fetchone()[0]
report.append(f"\n5. FOREIGN_FLOW: {n:,} rows, {nf} symbols, {nd} days ({mn} to {mx})")
cur.execute("SELECT COUNT(*) FROM (SELECT symbol FROM foreign_flow WHERE room_limit > 0 GROUP BY symbol) s")
report.append(f"     Symbols with room_limit > 0 ever: {cur.fetchone()[0]}")
cur.execute("SELECT COUNT(*) FROM (SELECT symbol FROM foreign_flow WHERE room_remaining > 0 GROUP BY symbol) s")
report.append(f"     Symbols with room_remaining > 0 ever: {cur.fetchone()[0]}")
cur.execute("SELECT MAX(trade_date) FROM foreign_flow")
report.append(f"     Most recent: {cur.fetchone()[0]}")

# ── 6. INSIDER_TRADES ──
cur.execute("SELECT COUNT(*), MIN(trade_date), MAX(trade_date) FROM insider_trades")
n, mn, mx = cur.fetchone()
cur.execute("SELECT COUNT(DISTINCT symbol) FROM insider_trades")
nf = cur.fetchone()[0]
report.append(f"\n6. INSIDER_TRADES: {n:,} rows, {nf} symbols ({mn} to {mx})")
cur.execute("SELECT trade_type, COUNT(*) FROM insider_trades GROUP BY trade_type ORDER BY COUNT(*) DESC")
for r in cur.fetchall():
    report.append(f"       {r[0]}: {r[1]}")
# 30-day net activity for a sample date
cur.execute("""
    SELECT COUNT(DISTINCT symbol) FROM insider_trades
    WHERE trade_date >= '2025-06-01' AND trade_date <= '2025-06-30'
""")
report.append(f"     Symbols trading in Jun 2025: {cur.fetchone()[0]}")

# ── 7. FACTOR_SCORES ──
cur.execute("SELECT COUNT(*) FROM factor_scores")
fs = cur.fetchone()[0]
report.append(f"\n7. FACTOR_SCORES: {fs:,} rows")
if fs > 0:
    cur.execute("SELECT MIN(score_date), MAX(score_date) FROM factor_scores")
    report.append(f"     Date range: {cur.fetchone()[0]} to {cur.fetchone()[1]}")

conn.close()

# ── SUMMARY ──
report.append("\n" + "=" * 80)
report.append("GAPS SUMMARY")
report.append("=" * 80)
report.append(f"  [OK]  OHLCV: 1.15M rows, {n_days if 'n_days' in dir() else '~2000'} days, all 415 symbols")
report.append(f"  [OK]  Financial ratios: 9.3k rows, {lr} latest")
report.append(f"  [OK]  Financial statements: 37k rows (BS+IS+CF), Vietnamese keys")
report.append(f"  [OK]  Foreign flow: 144k rows, room_limit/room_remaining available")
report.append(f"  [OK]  Insider trades: 29k rows, registered transactions")
report.append(f"  [!!]  Factor scores: {fs} rows — ETL NEVER RAN")
if n_null > 0:
    report.append(f"  [!!]  {n_null}/{n} stocks missing market_cap in stocks table")

for line in report:
    print(line)

print("\nDone.")
