"""Daily Pipeline Orchestrator Compatibility Wrapper.

This file re-exports everything from `app.domain.pipeline.daily_pipeline_orchestrator`
to maintain 100% backward compatibility for all existing scripts, tests, and callers.
"""

from app.domain.pipeline.daily_pipeline_orchestrator import (
    DailyInvestmentPipeline,
    ExecutionMode,
    pipeline,
    daily_pipeline,
)

__all__ = [
    "DailyInvestmentPipeline",
    "ExecutionMode",
    "pipeline",
    "daily_pipeline",
]
