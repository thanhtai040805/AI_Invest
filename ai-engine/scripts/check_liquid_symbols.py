"""Check liquid symbols count with different adtv thresholds."""
import psycopg2
from datetime import date, timedelta

conn = psycopg2.connect("dbname=aiinvest user=postgres password=123 host=localhost port=5432")
cur = conn.cursor()

target_date = date(2026, 6, 5)
start_20d = target_date - timedelta(days=30)
start_60d = target_date - timedelta(days=90)

cur.execute("""
    WITH adtv AS (
        SELECT 
            ticker,
            AVG(close_adj * volume_total) as adtv_val
        FROM market_data_daily
        WHERE date >= %s AND date <= %s
        GROUP BY ticker
    ),
    trades AS (
        SELECT 
            ticker,
            COUNT(CASE WHEN volume_total > 0 THEN 1 END) as trading_days
        FROM market_data_daily
        WHERE date >= %s AND date <= %s
        GROUP BY ticker
    )
    SELECT 
        s.symbol,
        a.adtv_val,
        t.trading_days
    FROM stocks s
    JOIN adtv a ON s.symbol = a.ticker
    JOIN trades t ON s.symbol = t.ticker
    WHERE s.exchange IN ('HOSE', 'HSX')
      AND s.trading_status = 'NORMAL'
    ORDER BY a.adtv_val DESC
""", (start_20d, target_date, start_60d, target_date))

rows = cur.fetchall()
print(f"Total HOSE/HSX stocks with data: {len(rows)}")

# Count with threshold = 5,000,000 (5 billion VND) and 45 trading days
threshold_5b = 5_000_000
liquid_5b = [r for r in rows if r[1] >= threshold_5b and r[2] >= 45]
print(f"Liquid stocks (threshold=5B, trading_days>=45): {len(liquid_5b)}")

# Let's print some of them
print("Top 15 liquid:")
for r in liquid_5b[:15]:
    print(f"  {r[0]}: ADTV={r[1] * 1000:,.0f} VND, trading_days={r[2]}")

print("Bottom 15 liquid:")
for r in liquid_5b[-15:]:
    print(f"  {r[0]}: ADTV={r[1] * 1000:,.0f} VND, trading_days={r[2]}")

cur.close()
conn.close()
