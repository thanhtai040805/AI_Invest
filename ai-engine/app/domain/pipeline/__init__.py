"""Autonomous System Pipelines Package (IOS v5.1).

Tập trung toàn bộ các Pipelines cốt lõi của hệ thống tự hành:
  1. daily_pipeline_orchestrator: Chu trình đầu tư tự hành 12 Agents (Start-of-Day / Intraday)
  2. eod_pipeline: Chu trình học tăng cường & chốt vị thế cuối ngày (15:15 End-of-Day)
  3. daily_etl: Pipeline nạp và làm giàu dữ liệu thị trường (Post-market ETL)
  4. bctc_to_sag_pipeline: Pipeline nạp tài liệu BCTC & xử lý OCR sang SAG
"""

from app.domain.pipeline.daily_pipeline_orchestrator import (
    DailyInvestmentPipeline,
    ExecutionMode,
    pipeline,
    daily_pipeline,
)
from app.domain.pipeline.eod_pipeline import (
    EODPipelineRunner,
    eod_runner,
)
from app.domain.pipeline.daily_etl import (
    DailyETLPipeline,
    run_etl,
    run_etl_sync,
)
from app.domain.pipeline.bctc_to_sag_pipeline import (
    BctcToSagPipeline,
)
from app.domain.repositories.bctc_pipeline_repository import (
    BctcPipelineRepository,
)

__all__ = [
    "DailyInvestmentPipeline",
    "ExecutionMode",
    "pipeline",
    "daily_pipeline",
    "EODPipelineRunner",
    "eod_runner",
    "DailyETLPipeline",
    "run_etl",
    "run_etl_sync",
    "BctcToSagPipeline",
    "BctcPipelineRepository",
]
