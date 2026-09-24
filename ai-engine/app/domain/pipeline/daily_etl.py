"""Daily ETL Pipeline — post-market-close data ingestion & derived metrics orchestrator (IOS v5.1).

Runs at 18:00-19:00 VN time sequentially:
  18:00 → OHLCV backfill (from DNSE REST, sync stocks + 1D OHLCV)
  18:05 → Technical indicators (40+ per symbol, SMA/EMA/RSI/MACD/BB/ATR)
  18:10 → GARCH/EWMA Volatility (top liquid symbols)
  18:15 → Insider trades (CafeF API)
  18:20 → Foreign flow (Vietstock / DNSE flow)
  18:25 → Financial ratios (CafeF API incremental refresh for newly published reports)
  18:30 → Factor scores (Pre-compute F1-F6 factor scores for all HOSE stocks)
  18:35 → Macro indicators (SBV, vi.money, yfinance, VietFin)

Note on Architectural Separation & Ingestion Policy:
- Market-relative risk metrics (Beta/Alpha): Excluded from Daily ETL as Agents compute them directly.
- Financial Ratios (CafeF): Runs incrementally daily to capture newly filed quarterly earnings automatically.
- External Data (Foreign Flow, Insider Trades): Must run daily as Agents cannot calculate external market activity.
- Factor Scores (F1-F6): Pre-computed across all stocks for O(1) cache lookup by Agent-02 and Agent-03.
- Composite Scoring & Execution: Handled dynamically by `DailyInvestmentPipeline` (12 Agents) using real-time
  regime and Agent-10 RL weights; not statically computed in ETL.
- News Crawling (CafeF/Vietstock/Deep crawl): Temporarily disabled to avoid anti-bot blocks and long latency.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Coroutine, Dict, List, Optional

from app.infrastructure.monitoring.job_state_service import (
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
    """Orchestrates daily data ingestion and derived metrics pipeline. Runs after market close."""

    def __init__(self):
        self.trade_date: Optional[date] = None

    async def run(
        self,
        trade_date: Optional[date] = None,
        include_news: bool = False,
        include_financials: bool = False,
    ) -> Dict[str, Any]:
        """Run daily data ETL pipeline sequentially.
        
        Args:
            trade_date: Date to run ETL for (default: today).
            include_news: If True, run news crawlers (default: False, temporarily disabled).
            include_financials: If True, run quarterly CafeF BCTC ETL (default: False).
        """
        self.trade_date = trade_date or datetime.now(TZ_VN).date()
        logger.info("=== DailyETL starting for %s ===", self.trade_date)

        if not is_trading_day(self.trade_date):
            logger.info("Not a trading day, skipping")
            return {"status": "skipped", "reason": "non_trading_day"}

        set_running(JOB_NAME, {"trade_date": str(self.trade_date)})
        results: Dict[str, Any] = {}

        try:
            # Các bước ETL cốt lõi cho toàn bộ hệ thống: Ingestion & Derived Metrics
            step_callables: List[tuple[str, Callable[[], Coroutine[Any, Any, Dict[str, Any]]]]] = [
                ("ohlcv_backfill", self.step_ohlcv_backfill),
                ("technical_indicators", self.step_technical_indicators),
                ("garch_volatility", self.step_garch_volatility),
                ("beta_alpha", self.step_beta_alpha),
                ("insider_trades", self.step_insider_trades),
                ("foreign_flow", self.step_foreign_flow),
                ("financial_ratios", self.step_financial_ratios),
                ("corporate_documents", self.step_corporate_documents),
                ("factor_scores", self.step_factor_scores),
                ("macro_indicators", self.step_macro_indicators),
            ]

            if include_news:
                step_callables.extend([
                    ("news_events", self.step_news_events),
                    ("vietstock_news", self.step_vietstock_news),
                    ("deep_crawl_news", self.step_deep_crawl_news),
                ])

            for name, func in step_callables:
                try:
                    logger.info("ETL executing step: %s", name)
                    results[name] = await func()
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
        from app.infrastructure.data_pipelines.ohlcv_backfill import run_daily_backfill, sync_stocks
        stock_count = await asyncio.to_thread(sync_stocks, exchanges=["STO"])
        ohlcv_result = await asyncio.to_thread(
            run_daily_backfill,
            exchanges=["STO"],
            target_date=self.trade_date,
            days_back=14,
        )
        return {"stocks_upserted": stock_count, **ohlcv_result}

    # ── Step: GARCH/EWMA Volatility ──────────────────────────────────

    async def step_garch_volatility(self) -> Dict[str, Any]:
        """GARCH(1,1) for top 50 liquid symbols; EWMA already in technical_indicators."""
        logger.info("ETL: garch_volatility — computing for top 50 liquid symbols...")
        try:
            from app.infrastructure.data_pipelines.volatility_etl import refresh_garch
            result = await asyncio.to_thread(refresh_garch)
            logger.info("ETL: garch_volatility — %s", result.get("status", "unknown"))
            return {"status": "success", **result}
        except Exception as e:
            logger.error("ETL: garch_volatility failed: %s", e)
            return {"status": "failed", "error": str(e)}

    # ── Step: Beta/Alpha ────────────────────────────────────────────

    async def step_beta_alpha(self) -> Dict[str, Any]:
        """Compute real Beta/Alpha using VNINDEX covariance."""
        logger.info("ETL: beta_alpha — computing market-relative risk metrics...")
        try:
            from app.infrastructure.data_pipelines.beta_alpha_etl import refresh_beta_alpha
            result = await asyncio.to_thread(refresh_beta_alpha)
            logger.info("ETL: beta_alpha — %s", result.get("status", "unknown"))
            return {"status": "success", **result}
        except Exception as e:
            logger.error("ETL: beta_alpha failed: %s", e)
            return {"status": "failed", "error": str(e)}

    async def step_technical_indicators(self) -> Dict[str, Any]:
        """Pre-compute 40+ technical indicators — incremental update."""
        logger.info("ETL: technical_indicators — incremental update...")
        try:
            from app.infrastructure.vendors.vn.technical_indicators import refresh_incremental
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
            from app.infrastructure.vendors.vn.insider_trades import refresh_incremental
            result = await asyncio.to_thread(refresh_incremental)
            logger.info("ETL: insider_trades — %d new rows", result.get("new_rows", 0))
            return {"status": "success", **result}
        except Exception as e:
            logger.error("ETL: insider_trades failed: %s", e)
            return {"status": "failed", "error": str(e)}

    # ── Step: Foreign Flow ───────────────────────────────────────────

    async def step_foreign_flow(self) -> Dict[str, Any]:
        """Pre-compute foreign trading flow from Vietstock API."""
        logger.info("ETL: foreign_flow — fetching from Vietstock API...")
        try:
            from app.infrastructure.vendors.vn.foreign_flow import refresh_incremental
            result = await asyncio.to_thread(refresh_incremental)
            logger.info("ETL: foreign_flow — %d rows", result.get("rows", 0))
            return {"status": "success", **result}
        except Exception as e:
            logger.error("ETL: foreign_flow failed: %s", e)
            return {"status": "failed", "error": str(e)}

    # ── Step: News Events ────────────────────────────────────────────

    async def step_news_events(self) -> Dict[str, Any]:
        """Fetch news listing from all CafeF categories (4 cats, 10 pages deep for du-lieu)."""
        logger.info("ETL: listing_crawl — fetching 4 CafeF categories...")
        try:
            from app.infrastructure.knowledge_base.crawlers.vn.cafef_listing_crawl import refresh_listing
            result = await asyncio.to_thread(refresh_listing, 10)
            logger.info("ETL: listing_crawl — %d inserted", result.get("inserted", 0))
            return {"status": "success", **result}
        except Exception as e:
            logger.error("ETL: listing_crawl failed: %s", e)
            return {"status": "failed", "error": str(e)}

    # ── Step: Vietstock News ───────────────────────────────────────────

    async def step_vietstock_news(self) -> Dict[str, Any]:
        """Fetch news listing from Vietstock channels (chung-khoan + doanh-nghiep)."""
        logger.info("ETL: vietstock_news — fetching from Vietstock...")
        try:
            from app.infrastructure.knowledge_base.crawlers.vn.vietstock_news_crawl import refresh_listing
            result = await asyncio.to_thread(refresh_listing, 100, [144, 733], True)
            logger.info("ETL: vietstock_news — %d inserted", result.get("inserted", 0))
            return {"status": "success", **result}
        except Exception as e:
            logger.error("ETL: vietstock_news failed: %s", e)
            return {"status": "failed", "error": str(e)}

    # ── Step: Deep Crawl News ─────────────────────────────────────────

    async def step_deep_crawl_news(self) -> Dict[str, Any]:
        """Fetch full article content for news_events missing content."""
        logger.info("ETL: deep_crawl_news — fetching article content...")
        try:
            from app.infrastructure.knowledge_base.crawlers.vn.deep_crawl_news import refresh_deep_crawl, count_missing_content
            missing = await asyncio.to_thread(count_missing_content)
            if missing == 0:
                logger.info("ETL: deep_crawl_news — all articles have content")
                return {"status": "no_content_needed", "crawled": 0}

            result = await asyncio.to_thread(refresh_deep_crawl, limit=200)
            after = await asyncio.to_thread(count_missing_content)
            logger.info("ETL: deep_crawl_news — %d crawled, %d remaining, failed=%d",
                        result.get("crawled", 0), after, result.get("failed", 0))
            return {"status": "success", "before": missing, **result, "remaining": after}
        except Exception as e:
            logger.error("ETL: deep_crawl_news failed: %s", e)
            return {"status": "failed", "error": str(e)}

    # ── Step: Financial Ratios ────────────────────────────────────────

    async def step_financial_ratios(self) -> Dict[str, Any]:
        """Fetch latest financial statements from CafeF REST API, upsert to DB."""
        logger.info("ETL: financial_ratios — fetching from CafeF...")
        try:
            from app.infrastructure.data_pipelines.financial_etl_cafef import refresh_incremental
            result = await asyncio.to_thread(refresh_incremental)
            logger.info("ETL: financial_ratios — %d rows", result.get("rows", 0))
            return {"status": "success", **result}
        except Exception as e:
            logger.error("ETL: financial_ratios failed: %s", e)
            return {"status": "failed", "error": str(e)}

    # ── Step: Corporate Documents (BCTC & Governance PDFs) ───────────

    async def step_corporate_documents(self) -> Dict[str, Any]:
        """Crawl new BCTC and Corporate Governance PDF documents into knowledge_documents.
        
        Tài liệu chỉ được lưu làm metadata; việc phân tích SAG thuộc quy trình
        nghiên cứu tách biệt và không được ETL runtime kích hoạt.
        """
        logger.info("ETL: corporate_documents — fetching BCTC, BCTN, nghị quyết & quản trị PDFs from Vietstock...")
        try:
            from app.infrastructure.knowledge_base.crawlers.vn.vietstock_document_crawl import run as crawl_docs
            # Vietstock: 1 = BCTC, 2 = BCTN, 4 = nghị quyết, 9 = BCQT.
            result = await asyncio.to_thread(crawl_docs, types=[1, 2, 4, 9], exchange="HOSE", max_years=1)
            inserted = result.get("inserted", 0)
            logger.info(
                "ETL: corporate_documents — %d inserted / %d total; SAG research dispatch is disabled",
                inserted,
                result.get("total", 0),
            )

            return {"status": "success", **result}
        except Exception as e:
            logger.error("ETL: corporate_documents failed: %s", e)
            return {"status": "failed", "error": str(e)}

    # ── Step: Factor Scores ───────────────────────────────────────────

    async def step_factor_scores(self) -> Dict[str, Any]:
        """Compute F1-F6 factor scores for all HOSE stocks and save to factor_scores table."""
        logger.info("ETL: factor_scores — computing F1-F6 factor scores for all stocks...")
        try:
            from app.domain.services.factor_service import FactorService
            service = FactorService()
            result = await asyncio.to_thread(service.compute_daily_factors, self.trade_date)
            logger.info("ETL: factor_scores — completed: %s", result)
            return {"status": "success", **result}
        except Exception as e:
            logger.warning("ETL: FactorService compute_daily_factors failed, trying vendor fallback: %s", e)
            try:
                from app.infrastructure.vendors.vn.factor_scores import refresh_all
                result = await asyncio.to_thread(refresh_all, self.trade_date)
                logger.info("ETL: factor_scores (fallback) — %d rows", result.get("rows", 0))
                return {"status": "success", **result}
            except Exception as e2:
                logger.error("ETL: factor_scores fallback also failed: %s", e2)
                return {"status": "failed", "error": str(e2)}

    # ── Step: Composite Scoring ─────────────────────────────────────

    async def step_composite_scoring(self) -> Dict[str, Any]:
        """IC-weighted Z-score composite + risk gate + portfolio construction."""
        logger.info("ETL: composite_scoring — running IC-weighted pipeline...")
        try:
            from app.infrastructure.vendors.vn.composite_pipeline import run_composite_pipeline
            result = await asyncio.to_thread(run_composite_pipeline, self.trade_date)
            logger.info("ETL: composite_scoring — %s", result.get("status", "unknown"))
            return {"status": "success", **result}
        except Exception as e:
            logger.error("ETL: composite_scoring failed: %s", e)
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
            from app.domain.rules.market.macro_service import refresh_macro, clear_cache
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

async def run_etl(
    trade_date: Optional[str] = None,
    include_news: bool = False,
    include_financials: bool = False,
) -> Dict[str, Any]:
    """Run daily ETL pipeline. Accepts optional trade_date string (YYYY-MM-DD)."""
    pipeline = DailyETLPipeline()
    d = date.fromisoformat(trade_date) if trade_date else None
    return await pipeline.run(d, include_news=include_news, include_financials=include_financials)


def run_etl_sync(
    trade_date: Optional[str] = None,
    include_news: bool = False,
    include_financials: bool = False,
) -> Dict[str, Any]:
    """Synchronous wrapper for CLI usage."""
    return asyncio.run(run_etl(trade_date, include_news=include_news, include_financials=include_financials))
