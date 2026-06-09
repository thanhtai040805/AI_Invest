"""Daily ETL Pipeline — post-market-close compute orchestrator.

Runs at 18:00-20:00 VN time, sequentially:
  18:00 → OHLCV backfill (from DNSE, adj_close = close, split-adjusted)
   18:05 → Technical indicators (40+ per symbol)
   18:15 → Insider trades (CafeF API)
   18:20 → Foreign flow (CafeF API)
   18:30 → News events + sentiment (CafeF API)
   18:45 → Financial ratios (AlphaStock API)
   19:00 → Risk flags (10 computed flags from structured DB data)
   19:15 → Factor scores (8 VN-core factors, cross-sectional ranking)
   19:25 → Composite scoring (IC-weighted Z-score + risk gate + portfolio weights)
   19:30 → Buy/Sell signals (composite + risk flags → BUY/HOLD/SELL)
   19:40 → Screener presets cache
   19:45 → Macro indicators (SBV, vi.money, yfinance, VietFin)

Each step is independently runnable via the CLI/API.
adj_close is not computed separately — DNSE returns split-adjusted prices so
adj_close = close and adj_factor = 1.0 for all rows.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.services.job_state_service import (
    get_job,
    set_running,
    set_completed,
    set_failed,
)

logger = logging.getLogger("ai_engine.etl")

TZ_VN = timezone(timedelta(hours=7))

JOB_NAME = "daily_etl"


# ── Market calendar helpers ──────────────────────────────────────────────

def is_trading_day(d: Optional[date] = None) -> bool:
    if d is None:
        d = datetime.now(TZ_VN).date()
    if d.weekday() >= 5:
        return False
    year = d.year
    holidays = {
        f"{year}-01-01", f"{year}-01-02", f"{year}-01-03",
        f"{year}-04-30", f"{year}-05-01",
        f"{year}-09-02", f"{year}-09-03",
    }
    return d.isoformat() not in holidays


def is_market_closed() -> bool:
    now_vn = datetime.now(TZ_VN)
    return now_vn.hour >= 15 or (now_vn.hour == 15 and now_vn.minute >= 30)


# ── Pipeline ─────────────────────────────────────────────────────────────

class DailyETLPipeline:
    """Orchestrates daily compute pipeline. Runs after market close."""

    def __init__(self):
        self.trade_date: Optional[date] = None

    async def run(self, trade_date: Optional[date] = None) -> Dict[str, Any]:
        """Run full pipeline sequentially."""
        self.trade_date = trade_date or datetime.now(TZ_VN).date()
        logger.info("=== DailyETL starting for %s ===", self.trade_date)

        if not is_trading_day(self.trade_date):
            logger.info("Not a trading day, skipping")
            return {"status": "skipped", "reason": "non_trading_day"}

        set_running(JOB_NAME, {"trade_date": str(self.trade_date)})
        results: Dict[str, Any] = {}

        try:
            steps = [
                ("ohlcv_backfill", self.step_ohlcv_backfill()),
                ("technical_indicators", self.step_technical_indicators()),
                ("insider_trades", self.step_insider_trades()),
                ("foreign_flow", self.step_foreign_flow()),
                ("news_events", self.step_news_events()),
                ("financial_ratios", self.step_financial_ratios()),
                ("risk_assessment", self.step_risk_assessment()),
                ("factor_scores", self.step_factor_scores()),
                ("composite_scoring", self.step_composite_scoring()),
                ("signals", self.step_signals()),
                ("screener_cache", self.step_screener_cache()),
                ("macro_indicators", self.step_macro_indicators()),
            ]
            for name, coro in steps:
                try:
                    results[name] = await coro
                except Exception as e:
                    logger.error("ETL step %s failed: %s", name, e)
                    results[name] = {"status": "failed", "error": str(e)}

            set_completed(JOB_NAME, results)
            logger.info("=== DailyETL done ===")
            return results

        except Exception as e:
            logger.error("DailyETL fatal: %s", e)
            set_failed(JOB_NAME, str(e))
            return {"status": "failed", "error": str(e)}

    # ── Step: OHLCV Backfill ──────────────────────────────────────────

    async def step_ohlcv_backfill(self) -> Dict[str, Any]:
        """Fetch today's OHLCV from DNSE REST, upsert to PG."""
        from app.services.ohlcv_backfill import run_daily_backfill, sync_stocks
        stock_count = await asyncio.to_thread(sync_stocks, exchanges=["STO"])
        ohlcv_result = await asyncio.to_thread(run_daily_backfill, exchanges=["STO"])
        return {"stocks_upserted": stock_count, **ohlcv_result}

    # ── Step: Technical Indicators ────────────────────────────────────

    async def step_technical_indicators(self) -> Dict[str, Any]:
        """Pre-compute 40+ technical indicators — incremental update."""
        logger.info("ETL: technical_indicators — incremental update...")
        try:
            from app.brain.dataflows.vendors.vn.technical_indicators import refresh_incremental
            result = await asyncio.to_thread(refresh_incremental)
            logger.info("ETL: technical_indicators — %d rows", result.get("rows", 0))
            return {"status": "success", **result}
        except Exception as e:
            logger.error("ETL: technical_indicators failed: %s", e)
            return {"status": "failed", "error": str(e)}

    # ── Step: Insider Trades ──────────────────────────────────────────

    async def step_insider_trades(self) -> Dict[str, Any]:
        """Fetch insider trades from CafeF API — idempotent upsert."""
        logger.info("ETL: insider_trades — fetching from CafeF API...")
        try:
            from app.brain.dataflows.vendors.vn.insider_trades import refresh_incremental
            result = await asyncio.to_thread(refresh_incremental)
            logger.info("ETL: insider_trades — %d new rows", result.get("new_rows", 0))
            return {"status": "success", **result}
        except Exception as e:
            logger.error("ETL: insider_trades failed: %s", e)
            return {"status": "failed", "error": str(e)}

    # ── Step: Foreign Flow ───────────────────────────────────────────

    async def step_foreign_flow(self) -> Dict[str, Any]:
        """Pre-compute foreign trading flow from CafeF API."""
        logger.info("ETL: foreign_flow — fetching from CafeF API...")
        try:
            from app.brain.dataflows.vendors.vn.foreign_flow import refresh_incremental
            result = await asyncio.to_thread(refresh_incremental)
            logger.info("ETL: foreign_flow — %d rows", result.get("rows", 0))
            return {"status": "success", **result}
        except Exception as e:
            logger.error("ETL: foreign_flow failed: %s", e)
            return {"status": "failed", "error": str(e)}

    # ── Step: News Events ────────────────────────────────────────────

    async def step_news_events(self) -> Dict[str, Any]:
        """Fetch news events from CafeF, compute sentiment, persist to news_events."""
        logger.info("ETL: news_events — fetching from CafeF API...")
        try:
            from app.brain.dataflows.vendors.vn.news_events import refresh_incremental
            result = await asyncio.to_thread(refresh_incremental)
            logger.info("ETL: news_events — %d new rows", result.get("new_rows", 0))
            return {"status": "success", **result}
        except Exception as e:
            logger.error("ETL: news_events failed: %s", e)
            return {"status": "failed", "error": str(e)}

    # ── Step: Financial Ratios ────────────────────────────────────────

    async def step_financial_ratios(self) -> Dict[str, Any]:
        """Fetch latest financial statements from AlphaStock API, upsert to DB."""
        logger.info("ETL: financial_ratios — fetching from AlphaStock...")
        try:
            from app.services.financial_etl_alphastock import refresh_incremental
            result = await asyncio.to_thread(refresh_incremental)
            logger.info("ETL: financial_ratios — %d rows", result.get("rows", 0))
            return {"status": "success", **result}
        except Exception as e:
            logger.error("ETL: financial_ratios failed: %s", e)
            return {"status": "failed", "error": str(e)}

    # ── Step: CRS 7-Tầng Risk Assessment ───────────────────────────────

    async def step_risk_assessment(self) -> Dict[str, Any]:
        """Compute CRS 7-tầng risk scores and upsert to risk_assessments."""
        logger.info("ETL: risk_assessment — computing CRS 7 tầng...")
        try:
            from app.services.risk_assessment_etl import run_assessment
            result = await asyncio.to_thread(run_assessment, self.trade_date)
            logger.info("ETL: risk_assessment — %s assessments", result.get("assessments", 0))
            return {"status": "success", **result}
        except Exception as e:
            logger.error("ETL: risk_assessment failed: %s", e)
            return {"status": "failed", "error": str(e)}

    # ── Step: Factor Scores ───────────────────────────────────────────

    async def step_factor_scores(self) -> Dict[str, Any]:
        """Cross-sectional ranking of VN-core factors for trade_date (25 Tier A factors)."""
        logger.info("ETL: factor_scores — computing cross-sectional ranking...")
        try:
            from app.brain.dataflows.vendors.vn.factor_scores import refresh_all
            result = await asyncio.to_thread(refresh_all, self.trade_date)
            logger.info("ETL: factor_scores — %d rows", result.get("rows", 0))
            return {"status": "success", **result}
        except Exception as e:
            logger.error("ETL: factor_scores failed: %s", e)
            return {"status": "failed", "error": str(e)}

    # ── Step: Composite Scoring ─────────────────────────────────────

    async def step_composite_scoring(self) -> Dict[str, Any]:
        """IC-weighted Z-score composite + risk gate + portfolio construction."""
        logger.info("ETL: composite_scoring — running IC-weighted pipeline...")
        try:
            from app.brain.dataflows.vendors.vn.composite_pipeline import run_composite_pipeline
            result = await asyncio.to_thread(run_composite_pipeline, self.trade_date)
            logger.info("ETL: composite_scoring — %s", result.get("status", "unknown"))
            return {"status": "success", **result}
        except Exception as e:
            logger.error("ETL: composite_scoring failed: %s", e)
            return {"status": "failed", "error": str(e)}

    # ── Step: Signals ────────────────────────────────────────────

    async def step_signals(self) -> Dict[str, Any]:
        """Compute buy/sell signals from factor scores + risk flags."""
        logger.info("ETL: signals — computing buy/sell recommendations...")
        try:
            from app.brain.dataflows.vendors.vn.signals import refresh_all as signal_refresh
            result = await asyncio.to_thread(signal_refresh, self.trade_date)
            logger.info("ETL: signals — %d rows", result.get("rows", 0))
            return {"status": "success", **result}
        except Exception as e:
            logger.error("ETL: signals failed: %s", e)
            return {"status": "failed", "error": str(e)}

    # ── Step: Screener Cache ──────────────────────────────────────────

    async def step_screener_cache(self) -> Dict[str, Any]:
        """Pre-compute screener presets."""
        logger.info("ETL: screener_cache — stub")
        return {"status": "stub"}

    # ── Step: Macro Indicators ────────────────────────────────────────

    async def step_macro_indicators(self) -> Dict[str, Any]:
        """Fetch macro indicators from public APIs and persist to macro_indicators table.

        Sources:
          - yfinance: global commodities (oil, gold), USD index, US 10y yield, VIX, USD/VND
          - VietFin (DNSE): VNINDEX returns (1d, 1m, 3m, 1y)
          - vi.money (GSO): CPI (free, no key)
          - SBV web: policy rates (refinancing, discount)
          - Vimo MCP (optional): lending rates with API key

        This step is idempotent — upsert by (indicator_date, indicator_name).
        """
        logger.info("ETL: macro_indicators — fetching from public APIs...")

        try:
            from app.services.macro_service import refresh_macro, clear_cache
            clear_cache()
            data = refresh_macro()
            logger.info("ETL: macro_indicators persisted %d indicators", len(data))
            return {
                "status": "success",
                "indicators_count": len(data),
                "trade_date": str(self.trade_date),
            }
        except Exception as e:
            logger.error("ETL: macro_indicators failed: %s", e)
            return {"status": "failed", "error": str(e)}


# ── Standalone runner ────────────────────────────────────────────────────

async def run_etl(trade_date: Optional[str] = None) -> Dict[str, Any]:
    """Run full ETL pipeline. Accepts optional trade_date string (YYYY-MM-DD)."""
    pipeline = DailyETLPipeline()
    d = date.fromisoformat(trade_date) if trade_date else None
    return await pipeline.run(d)


def run_etl_sync(trade_date: Optional[str] = None) -> Dict[str, Any]:
    """Synchronous wrapper for CLI usage."""
    return asyncio.run(run_etl(trade_date))
