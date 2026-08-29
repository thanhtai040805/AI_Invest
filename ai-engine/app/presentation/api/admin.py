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
