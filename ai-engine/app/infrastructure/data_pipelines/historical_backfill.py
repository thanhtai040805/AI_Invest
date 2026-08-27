import sys
import os
sys.path.append(r"d:\AIInvest\ai-engine")

import asyncio
import logging
import time
from datetime import date, datetime, timedelta
import psycopg2

from app.infrastructure.data_pipelines.ohlcv_ingestion_service import ohlcv_ingestion_svc
from app.domain.rules.market.macro_service import backfill_historical_macro_10y, refresh_macro
from app.infrastructure.vendors.vn.technical_indicators import refresh_incremental as refresh_ti
from app.infrastructure.data_pipelines.financial_etl_alphastock import refresh_incremental as refresh_fi
from app.infrastructure.vendors.vn.foreign_flow import refresh_incremental as refresh_ff

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("backfill")

def get_db_conn():
    db_url = os.getenv("DATABASE_URL", "postgresql://postgres:123@localhost:5432/aiinvest")
    return psycopg2.connect(db_url)

def get_all_symbols():
    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute("SELECT symbol FROM stocks")
    symbols = [r[0] for r in cur.fetchall()]
    cur.close()
    conn.close()
    return symbols

def backfill_ohlcv(start_date: date, end_date: date):
    symbols = get_all_symbols()
    logger.info(f"Starting OHLCV backfill for {len(symbols)} symbols from {start_date} to {end_date}...")
    
    # Filter out fake symbols or ETF if needed, but the stocks table should be clean already
    total_saved = 0
    count = 0
    
    for sym in symbols:
        count += 1
        logger.info(f"[{count}/{len(symbols)}] Fetching OHLCV for {sym}")
        try:
            data = ohlcv_ingestion_svc.fetch_ohlcv(sym, start_date, end_date)
            if data:
                saved = ohlcv_ingestion_svc.save_market_data(data)
                total_saved += saved
                logger.info(f"  -> Saved {saved} rows")
            # Be nice to DNSE API
            time.sleep(0.1)
        except Exception as e:
            logger.error(f"Error for {sym}: {e}")
            
    logger.info(f"OHLCV Backfill complete. Total rows saved: {total_saved}")

def backfill_macro(deep: bool = True):
    logger.info("Refreshing Macro Indicators...")
    try:
        if deep:
            logger.info("Running 10-year deep backfill for macro...")
            backfill_historical_macro_10y()
        else:
            data = refresh_macro()
            logger.info(f"Macro refresh complete. {len(data)} items updated.")
    except Exception as e:
        logger.error(f"Macro error: {e}")

def backfill_tech():
    logger.info("Refreshing Technical Indicators incrementally...")
    try:
        res = refresh_ti()
        logger.info(f"Technical Indicators refresh complete: {res}")
    except Exception as e:
        logger.error(f"Technical error: {e}")

def backfill_financial():
    logger.info("Refreshing Financial Ratios incrementally...")
    try:
        res = refresh_fi()
        logger.info(f"Financial Ratios refresh complete: {res}")
    except Exception as e:
        logger.error(f"Financial error: {e}")

def backfill_foreign_flow():
    logger.info("Refreshing Foreign Flow incrementally...")
    try:
        res = refresh_ff()
        logger.info(f"Foreign Flow refresh complete: {res}")
    except Exception as e:
        logger.error(f"Foreign Flow error: {e}")

if __name__ == "__main__":
    start = date(2026, 6, 5)
    end = date(2026, 8, 24)
    
    logger.info("=== STARTING HISTORICAL BACKFILL ===")
    
    # 1. Backfill OHLCV (Takes the longest)
    # UNCOMMENT TO RUN
    backfill_ohlcv(start, end)
    
    # 2. Backfill Macro
    backfill_macro()
    
    # 3. Backfill Tech
    backfill_tech()
    
    # 4. Financial & Foreign Flow
    backfill_financial()
    backfill_foreign_flow()
    
    logger.info("=== HISTORICAL BACKFILL COMPLETE ===")
