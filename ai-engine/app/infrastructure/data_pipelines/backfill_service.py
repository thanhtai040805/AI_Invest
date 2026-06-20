"""Daily backfill orchestration — fetch today's OHLCV from DNSE REST, save to PG.
Triggers macro_indicators ETL after successful OHLCV backfill.
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta

from app.infrastructure.monitoring.job_state_service import (
    get_job,
    set_running,
    set_completed,
    set_failed,
)
from app.infrastructure.data_pipelines.ohlcv_backfill import run_daily_backfill, sync_stocks

logger = logging.getLogger("ai_engine.backfill")

JOB_NAME = "daily_backfill"


def is_market_closed() -> bool:
    """Check if VN market is closed (after 15:30)."""
    vn_tz = timezone(timedelta(hours=7))
    now_vn = datetime.now(vn_tz)
    total_minutes = now_vn.hour * 60 + now_vn.minute
    return total_minutes >= 15 * 60 + 30


def is_trading_day() -> bool:
    vn_tz = timezone(timedelta(hours=7))
    now_vn = datetime.now(vn_tz)
    if now_vn.weekday() >= 5:
        return False
    holidays = [
        f"{now_vn.year}-01-01",
        f"{now_vn.year}-01-02",
        f"{now_vn.year}-01-03",
        f"{now_vn.year}-04-30",
        f"{now_vn.year}-05-01",
        f"{now_vn.year}-09-02",
        f"{now_vn.year}-09-03",
    ]
    return now_vn.strftime("%Y-%m-%d") not in holidays


async def _run_macro_etl():
    """Run macro_indicators ETL step after backfill."""
    try:
        from app.domain.rules.market.macro_service import refresh_macro, clear_cache
        clear_cache()
        data = refresh_macro()
        logger.info("[Backfill] Macro ETL done: %d indicators", len(data))
        return data
    except Exception as e:
        logger.warning("[Backfill] Macro ETL failed (non-fatal): %s", e)
        return {}


async def auto_run():
    """Run daily backfill if: trading day + market closed + not yet completed today."""
    if not is_trading_day():
        logger.info("[Backfill] Not a trading day, skipping")
        return

    if not is_market_closed():
        logger.info("[Backfill] Market still open, skipping")
        return

    existing = get_job(JOB_NAME)
    if existing and existing["status"] == "running":
        logger.warning("[Backfill] Previous run was interrupted. Resuming...")
    if existing and existing["status"] == "completed":
        logger.info("[Backfill] Already completed today, skipping")
        return

    logger.info("[Backfill] Starting daily backfill (stocks + OHLCV)...")
    set_running(JOB_NAME, {"start_reason": "auto_end_of_day"})

    try:
        stock_count = await asyncio.to_thread(
            sync_stocks,
            exchanges=["STO"],
        )

        ohlcv_result = await asyncio.to_thread(
            run_daily_backfill,
            exchanges=["STO"],
        )

        macro_result = await _run_macro_etl()

        result = {
            "stocks_upserted": stock_count,
            **ohlcv_result,
            "macro_indicators": len(macro_result),
        }
        set_completed(JOB_NAME, result)
        logger.info(f"[Backfill] Done: {result}")
    except Exception as e:
        logger.error(f"[Backfill] Failed: {e}")
        set_failed(JOB_NAME, str(e))


async def trigger_run():
    """Force re-run daily backfill + macro ETL (admin API)."""
    logger.info("[Backfill] Manual trigger...")
    set_running(JOB_NAME, {"start_reason": "manual_trigger"})
    try:
        stock_count = await asyncio.to_thread(
            sync_stocks,
            exchanges=["STO"],
        )
        ohlcv_result = await asyncio.to_thread(
            run_daily_backfill,
            exchanges=["STO"],
        )
        macro_result = await _run_macro_etl()
        result = {
            "stocks_upserted": stock_count,
            **ohlcv_result,
            "macro_indicators": len(macro_result),
        }
        set_completed(JOB_NAME, result)
        logger.info(f"[Backfill] Done: {result}")
        return result
    except Exception as e:
        logger.error(f"[Backfill] Failed: {e}")
        set_failed(JOB_NAME, str(e))
        raise
