import sys
from pathlib import Path
from datetime import date
import pandas as pd

# Add paths
sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.services.portfolio_service import PortfolioOptimizer
from app.services.factor_service import FactorService

def verify_portfolio_construction():
    print("--- Verification: Portfolio Construction (Allocation Optimizer) ---")
    
    # 1. Setup
    optimizer = PortfolioOptimizer()
    
    # 2. Fetch Alpha data (using date from factor engine)
    import psycopg2
    from app.services.pg_pool import DB_URL
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    
    target_date = date(2026, 6, 4) # Our verified date
    
    # We need: symbol, score, volatility, price, adv
    query = """
    SELECT 
        f.symbol, 
        f.composite_score, 
        (t.indicators->>'volatility_20d')::float as volatility,
        o.close::float as price,
        (t.indicators->>'volume_ma20')::float as adv,
        (t.indicators->>'atr_14')::float as atr
    FROM factor_scores f
    JOIN technical_indicators t ON f.symbol = t.symbol AND f.score_date = t.calc_date
    JOIN ohlcv o ON f.symbol = o.symbol AND f.score_date = o.time::date
    WHERE f.score_date = %s
    ORDER BY f.composite_score DESC
    LIMIT 10
    """
    cur.execute(query, (target_date,))
    rows = cur.fetchall()
    conn.close()
    
    if not rows:
        print("No alpha scores found for this date.")
        return

    alpha_data = []
    for r in rows:
        alpha_data.append({
            "symbol": r[0],
            "composite_score": r[1],
            "volatility_20d": r[2],
            "price": r[3],
            "adv_20d": r[4],
            "atr_14": r[5]
        })

    # 3. Optimize for 1 Billion VND Portfolio
    initial_capital = 1_000_000_000
    allocation = optimizer.optimize_allocation(alpha_data, initial_capital, target_date)
    
    print(f"\nFinal Portfolio Allocation (Date: {target_date}):")
    print(f"{'Symbol':<8} | {'Weight':<8} | {'Quantity':<10} | {'Method'}")
    print("-" * 60)
    
    total_w = 0
    for a in allocation:
        print(f"{a['symbol']:<8} | {a['suggested_weight']*100:>7.1f}% | {a['quantity']:>10,d} | {a['sizing_method']}")
        total_w += a['suggested_weight']
    
    print("-" * 60)
    print(f"TOTAL EXPOSURE: {total_w*100:.1f}% ({'REDUCED BY MACD SHIELD' if total_w < 0.9 else 'FULL EXPOSURE'})")
    
    assert len(allocation) > 0, "Should generate at least some allocations"
    print("\nPortfolio Construction (Tầng 5) implemented successfully.")

if __name__ == "__main__":
    verify_portfolio_construction()
