"""Admin API — job states, backfill triggers."""

import asyncio
from fastapi import APIRouter

from app.services.job_state_service import get_job, list_jobs
from app.services.backfill_service import trigger_run

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
