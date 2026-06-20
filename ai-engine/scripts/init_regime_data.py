"""Helper Script: Initialize market_regime data.

Calculates market breadth (pct stocks > MA50) for the last 30 days.
"""
import os
import psycopg2
from datetime import date, timedelta
from dotenv import load_dotenv

def init_regime():
    load_dotenv()
    db_url = os.getenv('DATABASE_URL', 'postgresql://postgres:123@localhost:5432/aiinvest')
    
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()
    
    print("Calculating market breadth for the last 30 days...")
    
    # 1. Get latest date only
    cur.execute("SELECT MAX(date) FROM market_data_daily")
    latest_date = cur.fetchone()[0]
    
    if not latest_date:
        print("No data found.")
        return

    dates = [latest_date]
    
    for d in dates:
        # Calculate breadth: % of tickers where close_adj > MA50
        # Optimizing: only look at tickers active on that date
        cur.execute("""
            WITH latest_active AS (
                SELECT ticker, close_adj
                FROM market_data_daily
                WHERE date = %s
            ),
            ma_calc AS (
                SELECT ticker, AVG(close_adj) as ma50
                FROM market_data_daily
                WHERE date <= %s AND date > %s - INTERVAL '70 days'
                GROUP BY ticker
            )
            SELECT 
                COUNT(*) FILTER (WHERE l.close_adj > m.ma50) * 100.0 / COUNT(*)
            FROM latest_active l
            JOIN ma_calc m ON l.ticker = m.ticker
        """, (d, d, d))
        
        breadth = cur.fetchone()[0] or 50.0
        
        cur.execute("""
            INSERT INTO market_regime (date, breadth_ma50)
            VALUES (%s, %s)
            ON CONFLICT (date) DO UPDATE SET breadth_ma50 = EXCLUDED.breadth_ma50
        """, (d, float(breadth)))
    
    conn.commit()
    conn.close()
    print("Market regime initialization complete.")

if __name__ == "__main__":
    init_regime()
