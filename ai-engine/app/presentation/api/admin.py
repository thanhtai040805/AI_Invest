"""Admin API — job states, backfill triggers, and system monitoring."""

import asyncio
from typing import Optional
from fastapi import APIRouter, Query

from app.infrastructure.monitoring.job_state_service import get_job, list_jobs
from app.infrastructure.monitoring.monitoring import run_all_health_checks, monitoring_svc
from app.infrastructure.data_pipelines.backfill_service import trigger_run

router = APIRouter()


@router.get("/admin/jobs")
async def get_all_jobs():
    return {"jobs": list_jobs()}


@router.get("/admin/jobs/{job_name}")
async def get_job_status(job_name: str):
    job = get_job(job_name)
    if not job:
        return {"job_name": job_name, "status": "not_found"}
    return job


@router.post("/admin/backfill/trigger")
async def trigger_backfill():
    asyncio.create_task(trigger_run())
    return {"message": "Backfill triggered, running in background"}


@router.post("/admin/daily-pipeline/trigger")
async def trigger_daily_pipeline(
    target_date: Optional[str] = Query(None, description="Ngày chạy định dạng YYYY-MM-DD (mặc định hôm nay)"),
    force: bool = Query(False, description="Bắt buộc chạy lại nếu đã chạy trong ngày"),
):
    """Kích hoạt thủ công chu trình Daily Investment Pipeline (12 Agents + Standalone ML)."""
    from app.infrastructure.workers.daily_pipeline_daemon import daily_daemon
    res = await daily_daemon.trigger_manual(target_date=target_date, force=force)
    return {
        "message": f"Daily Pipeline completed for date {res.get('date')}",
        "status": res.get("status"),
        "multi_agent_orders": len(res.get("multi_agent_instructions", [])),
        "standalone_ml_orders": len(res.get("standalone_ml_instructions", [])),
        "governance_status": res.get("governance_status"),
        "audit_sha256": res.get("audit_sha256"),
    }


@router.get("/admin/daily-pipeline/status")
async def get_daily_pipeline_status():
    """Kiểm tra trạng thái Daily Pipeline Daemon và lần chạy sáng gần nhất."""
    from app.infrastructure.workers.daily_pipeline_daemon import daily_daemon
    return daily_daemon.status


@router.post("/admin/eod-pipeline/trigger")
async def trigger_eod_pipeline(
    target_date: Optional[str] = Query(None, description="Ngày chạy định dạng YYYY-MM-DD (mặc định hôm nay)"),
    force: bool = Query(False, description="Bắt buộc chạy lại nếu đã chạy trong ngày"),
):
    """Kích hoạt thủ công quy trình EOD Causal Learning & Paper Trades Settlement."""
    from app.infrastructure.workers.eod_learning_daemon import eod_daemon
    res = await eod_daemon.trigger_manual(target_date=target_date, force=force)
    return {
        "message": f"EOD Pipeline completed for date {res.get('run_date')}",
        "status": res.get("status"),
        "result": res,
    }


@router.get("/admin/eod-pipeline/status")
async def get_eod_pipeline_status():
    """Kiểm tra trạng thái EOD Learning Daemon và lần chạy gần nhất."""
    from app.infrastructure.workers.eod_learning_daemon import eod_daemon
    return eod_daemon.status


@router.post("/admin/daily-etl/trigger")
async def trigger_daily_etl(
    target_date: Optional[str] = Query(None, description="Ngày chạy định dạng YYYY-MM-DD (mặc định hôm nay)"),
):
    """Kích hoạt thủ công quy trình Daily ETL (OHLCV, indicators, foreign flow, factor scores)."""
    from app.infrastructure.workers.daily_etl_daemon import etl_daemon
    res = await etl_daemon.trigger_manual(target_date=target_date)
    return {
        "message": f"Daily ETL completed for date {target_date or 'today'}",
        "status": res.get("status"),
        "result": res,
    }


@router.get("/admin/daily-etl/status")
async def get_daily_etl_status():
    """Kiểm tra trạng thái Daily ETL Daemon và lần chạy gần nhất."""
    from app.infrastructure.workers.daily_etl_daemon import etl_daemon
    return etl_daemon.status



@router.get("/admin/monitoring/health")
async def get_monitoring_health():
    """Get complete aggregated system health status."""
    return run_all_health_checks()


@router.get("/admin/monitoring/alerts")
async def get_monitoring_alerts(
    limit: int = Query(50, ge=1, le=500),
    severity: Optional[str] = Query(None, description="INFO, WARNING, or CRITICAL"),
    source: Optional[str] = Query(None, description="Source filter"),
):
    """Get recent system alerts."""
    return {
        "summary": monitoring_svc.get_status_summary(),
        "alerts": monitoring_svc.get_recent(limit=limit, severity=severity, source=source),
    }


@router.delete("/admin/monitoring/alerts")
async def clear_monitoring_alerts():
    """Clear recorded alert history."""
    monitoring_svc.clear_alerts()
    return {"message": "Alerts cleared successfully"}
