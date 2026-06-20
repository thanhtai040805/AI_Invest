"""Test upserting VNINDEX data into market_data_daily."""
from vietfin import vf
import pandas as pd
from datetime import date, timedelta
import psycopg2
from psycopg2.extras import execute_values

DB_URL = "postgresql://postgres:123@localhost:5432/aiinvest"

end_date = date(2026, 6, 19)
start_date = end_date - timedelta(days=730)

print(f"Fetching from {start_date} to {end_date}...")
r = vf.index.price.historical(
    symbol="vnindex",
    start_date=start_date.strftime("%Y-%m-%d"),
    end_date=end_date.strftime("%Y-%m-%d"),
    interval="1d",
    provider="dnse",
)
df = r.to_df()
print("Fetched rows:", len(df))

conn = psycopg2.connect(DB_URL)
cur = conn.cursor()

try:
    # Build rows for execute_values
    # ticker, date, open_adj, high_adj, low_adj, close_adj, close_unadj, volume_total, data_source
    rows = []
    for idx, row in df.iterrows():
        # idx is either Timestamp or string date
        dt = pd.to_datetime(idx).date()
        rows.append((
            'VNINDEX',
            dt,
            float(row['open']),
            float(row['high']),
            float(row['low']),
            float(row['close']),
            float(row['close']),
            int(row['volume']),
            'vietfin'
        ))

    print("Upserting into market_data_daily...")
    execute_values(
        cur,
        """
        INSERT INTO market_data_daily (
            ticker, date, open_adj, high_adj, low_adj, close_adj, close_unadj, volume_total, data_source
        ) VALUES %s
        ON CONFLICT (ticker, date) DO UPDATE SET
            open_adj = EXCLUDED.open_adj,
            high_adj = EXCLUDED.high_adj,
            low_adj = EXCLUDED.low_adj,
            close_adj = EXCLUDED.close_adj,
            close_unadj = EXCLUDED.close_unadj,
            volume_total = EXCLUDED.volume_total,
            data_source = EXCLUDED.data_source
        """,
        rows
    )
    conn.commit()
    print("Successfully upserted!")

    # Verify query
    cur.execute("SELECT COUNT(1) FROM market_data_daily WHERE ticker = 'VNINDEX'")
    print("Verify VNINDEX count in DB:", cur.fetchone()[0])

except Exception as e:
    conn.rollback()
    print("Failed upsert:", e)
finally:
    cur.close()
    conn.close()
