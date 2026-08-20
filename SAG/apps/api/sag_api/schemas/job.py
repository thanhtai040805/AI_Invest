from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from sag_api.enums import JobStatus, JobType


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    type: JobType
    status: JobStatus
    source_id: str | None
    document_id: str | None
    progress: float
    attempts: int
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
