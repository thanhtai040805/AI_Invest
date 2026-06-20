"""Migration Script: ohlcv -> market_data_daily

Populates the new schema table using existing historical data.
"""
import os
import psycopg2
from dotenv import load_dotenv

def migrate():
    load_dotenv()
    db_url = os.getenv('DATABASE_URL', 'postgresql://postgres:123@localhost:5432/aiinvest')
    
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()
    
    print("Migrating data from 'ohlcv' to 'market_data_daily'...")
    
    # Mapping:
    # time -> date
    # symbol -> ticker
    # adj_close -> close_adj
    # open -> open_adj (approx)
    # volume -> volume_total
    
    cur.execute("""
        INSERT INTO market_data_daily (
            ticker, date, open_adj, high_adj, low_adj, close_adj, close_unadj,
            volume_continuous, volume_atc, volume_ato, volume_total, data_source
        )
        SELECT 
            symbol, time, open, high, low, adj_close, close,
            volume, 0, 0, volume, 'legacy_migration'
        FROM ohlcv
        ON CONFLICT (ticker, date) DO NOTHING
    """)
    
    count = cur.rowcount
    conn.commit()
    
    # Update ADTV20_continuous for all tickers (simple average of volume)
    print(f"Migrated {count} rows. Calculating ADTV20...")
    
    cur.execute("""
        UPDATE market_data_daily m
        SET adtv20_continuous = sub.avg_vol
        FROM (
            SELECT ticker, date, 
                   AVG(volume_total) OVER(PARTITION BY ticker ORDER BY date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) as avg_vol
            FROM market_data_daily
        ) sub
        WHERE m.ticker = sub.ticker AND m.date = sub.date
    """)
    
    conn.commit()
    conn.close()
    print("Migration and ADTV calculation complete.")

if __name__ == "__main__":
    migrate()
