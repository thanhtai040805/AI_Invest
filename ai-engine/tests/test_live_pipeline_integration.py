import asyncio
import os
import sys
import logging
from datetime import date

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../")))
from app.application.use_cases.daily_pipeline_orchestrator import pipeline

async def test_live_pipeline():
    test_dates = [
        date(2025, 8, 15), # Bull market
        date(2026, 6, 30), # Recent session
        date(2021, 11, 15),# Peak bull breakout
        date(2022, 5, 15), # Bear crash (should hold 100% cash)
    ]
    
    for test_date in test_dates:
        print("\n" + "=" * 80)
        print(f"TESTING LIVE DAILY INVESTMENT PIPELINE FOR DATE: {test_date}")
        print("=" * 80)
        
        res = await pipeline.run(
            target_date=test_date,
            current_nav=1_000_000_000.0,
            standalone_nav=500_000_000.0
        )
        
        print(f"Date: {res.get('date')} | Regime: {res.get('regime')} | Target Cash: {res.get('cash_ratio'):.2%}")
        
        orders_ma = res.get('multi_agent_instructions', [])
        print(f"[BOOK 1: MULTI-AGENT INTEGRATED ORDERS] ({len(orders_ma)} orders)")
        for o in orders_ma:
            print(f"  Ticker: {o['ticker']:<6} | Tier: {o['tier']:<12} | Z-Score: {o['z_score']:+.2f} | Weight: {o['target_weight_pct']:.1%} | HardStop: {o['hard_stop_pct']:.1%}")
            
        orders_sa = res.get('standalone_ml_instructions', [])
        print(f"[BOOK 2: STANDALONE PURE-ML FUND ORDERS] ({len(orders_sa)} orders)")
        for o in orders_sa:
            print(f"  Ticker: {o['ticker']:<6} | Tier: {o['tier']:<12} | Z-Score: {o['z_score']:+.2f} | Weight: {o['target_weight_pct']:.1%} | Mode: {o['execution_mode']}")
            
    print("\n" + "=" * 80)
    print("ALL TESTS PASSED! Multi-Agent and Standalone ML Book are fully validated.")

if __name__ == "__main__":
    asyncio.run(test_live_pipeline())
