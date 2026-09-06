"""Daily ETL Pipeline Compatibility Wrapper.

This file re-exports everything from `app.domain.pipeline.daily_etl` to maintain 100%
backward compatibility for existing callers and external tools.
"""

from app.domain.pipeline.daily_etl import (
    DailyETLPipeline,
    run_etl,
    run_etl_sync,
    is_trading_day,
    is_market_closed,
    JOB_NAME,
    TZ_VN,
)

__all__ = [
    "DailyETLPipeline",
    "run_etl",
    "run_etl_sync",
    "is_trading_day",
    "is_market_closed",
    "JOB_NAME",
    "TZ_VN",
]
