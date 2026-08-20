from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.core.db import get_session
from sag_api.core.deps import get_current_user
from sag_api.core.errors import NotFoundError
from sag_api.db.models import Job, User
from sag_api.schemas.job import JobOut

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/{job_id}", response_model=JobOut)
async def get_job(
    job_id: str,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> JobOut:
    job = await session.get(Job, job_id)
    if job is None:
        raise NotFoundError("Nhiệm vụ không tồn tại")
    return JobOut.model_validate(job)
