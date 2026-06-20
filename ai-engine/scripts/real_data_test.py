"""Real-Data Integration Verification Script.
Tests core components using actual database data.
"""
import os
import sys
import logging
import psycopg2
from datetime import date
from dotenv import load_dotenv

# Setup path to find app module
sys.path.append(os.getcwd())

from app.domain.rules.market.hmm_classifier import hmm_classifier
from app.domain.rules.universe_manager import universe_manager
from app.domain.rules.hard_laws import hard_law_engine, ProposedOrder, PortfolioState

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("RealDataTest")

def run():
    load_dotenv()
    db_url = os.getenv('DATABASE_URL', 'postgresql://postgres:123@localhost:5432/aiinvest')
    
    try:
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        
        # 1. Get latest date
        cur.execute("SELECT MAX(date) FROM market_data_daily")
        latest_date = cur.fetchone()[0]
        if not latest_date:
            logger.error("No data in market_data_daily table.")
            return

        logger.info(f"Using real data from: {latest_date}")

        # 2. Test HMM Regime
        metrics = hmm_classifier.get_market_metrics(latest_date)
        posterior = hmm_classifier.calculate_posterior(*metrics)
        regime = hmm_classifier.classify(posterior)
        logger.info(f"RESULT - Market Regime: {regime.value} (Bear Prob: {posterior.get(hmm_classifier.states[2], 0.0):.2%})")

        # 3. Test Universe Manager
        cur.execute("SELECT ticker FROM market_data_daily WHERE date = %s LIMIT 5", (latest_date,))
        tickers = [r[0] for r in cur.fetchall()]
        uni_res = universe_manager.classify_universe(tickers, latest_date)
        for res in uni_res['results']:
            logger.info(f"RESULT - Ticker {res['ticker']}: {res['universe_group']}")

        # 4. Test Hard Law
        # Proposed order: Buy 1000 shares of the first ticker
        t1 = tickers[0]
        cur.execute("SELECT close_adj, adtv20_continuous FROM market_data_daily WHERE ticker=%s AND date=%s", (t1, latest_date))
        price, adtv = cur.fetchone()
        
        order = ProposedOrder(ticker=t1, side="BUY", quantity=1000, price=price, stop_loss_price=price*0.9, sector="Unknown")
        portfolio = PortfolioState(nav=1_000_000_000)
        
        check = hard_law_engine.check_order(order, portfolio, adtv or 1.0)
        logger.info(f"RESULT - Hard Law Check for {t1}: {'PASSED' if check.passed else 'FAILED'} {check.reason}")

        conn.close()
        logger.info("REAL-DATA TEST COMPLETED SUCCESSFULLY.")

    except Exception as e:
        logger.error(f"Real-data test failed: {e}")

if __name__ == "__main__":
    run()
