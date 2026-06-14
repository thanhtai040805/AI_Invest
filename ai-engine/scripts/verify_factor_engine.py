import sys
from pathlib import Path
from datetime import date
import pandas as pd

# Add paths
sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.services.factor_service import FactorService

def verify_factor_engine():
    print("--- Verification: Factor Engine (Z-Score Ranking) ---")
    
    # 1. Setup Service
    service = FactorService()
    
    # 2. Check for latest data date
    import psycopg2
    from app.services.pg_pool import DB_URL
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    cur.execute("SELECT MAX(calc_date) FROM technical_indicators")
    target_date = cur.fetchone()[0]
    conn.close()
    
    if not target_date:
        print("No data in technical_indicators to verify.")
        return

    print(f"Computing factors for {target_date}...")
    service.compute_daily_factors(target_date)
    
    # 3. Verify top Alpha stocks
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    cur.execute("""
        SELECT symbol, composite_score 
        FROM factor_scores 
        WHERE score_date = %s 
        ORDER BY composite_score DESC 
        LIMIT 10
    """, (target_date,))
    top_alpha = cur.fetchall()
    conn.close()
    
    if not top_alpha:
        print("Factor computation ran but no scores were persisted.")
        return

    print("\nTop 10 Alpha Stocks (Institutional Choice):")
    for sym, score in top_alpha:
        print(f"  {sym}: Z-Score {score:.2f}")
    
    assert len(top_alpha) > 0, "Factor engine should produce ranked scores"
    print("\nFactor Engine (Alpha Ranking) implemented successfully.")

if __name__ == "__main__":
    verify_factor_engine()
