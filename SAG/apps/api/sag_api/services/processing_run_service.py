from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.db.models import ProcessingRun
from sag_api.enums import ProcessingStageStatus


async def enqueue_processing_run(
    session: AsyncSession,
    *,
    document_id: str,
    stage: str,
    processing_version: int,
    idempotency_key: str,
    metadata: dict[str, Any] | None = None,
) -> tuple[ProcessingRun, bool]:
    existing = await session.scalar(
        select(ProcessingRun).where(ProcessingRun.idempotency_key == idempotency_key).limit(1)
    )
    if existing is not None:
        if existing.status in {
            ProcessingStageStatus.COMPLETE.value,
            ProcessingStageStatus.INCOMPLETE.value,
            ProcessingStageStatus.FAILED.value,
        }:
            # Same canonical artifact may be explicitly ingested again after a
            # validator/configuration fix. A terminal run must be revivable;
            # only a live RUNNING lease remains deduplicated.
            existing.status = ProcessingStageStatus.PENDING.value
            existing.attempt = 0
            existing.error = None
            existing.lease_expires_at = None
            existing.heartbeat_at = None
            existing.metadata_json = {**(metadata or {}), "requeued": True}
            await session.flush()
            return existing, True
        return existing, False
    run = ProcessingRun(
        document_id=document_id,
        processing_version=processing_version,
        stage=stage,
        status=ProcessingStageStatus.PENDING.value,
        attempt=0,
        idempotency_key=idempotency_key,
        metadata_json=metadata or {},
    )
    try:
        # The preliminary read is only an optimization. The unique index is
        # authoritative when two requests enqueue the same artifact together.
        async with session.begin_nested():
            session.add(run)
            await session.flush()
    except IntegrityError:
        existing = await session.scalar(
            select(ProcessingRun).where(ProcessingRun.idempotency_key == idempotency_key).limit(1)
        )
        if existing is None:
            raise
        return existing, False
    return run, True


async def claim_next_processing_run(
    session: AsyncSession,
    *,
    stage: str | None = None,
    lease_seconds: int = 300,
) -> ProcessingRun | None:
    now = datetime.now(UTC)
    stmt = select(ProcessingRun).where(
        or_(
            ProcessingRun.status == ProcessingStageStatus.PENDING.value,
            ProcessingRun.status == ProcessingStageStatus.RUNNING.value,
        ),
        or_(ProcessingRun.lease_expires_at.is_(None), ProcessingRun.lease_expires_at < now),
    )
    if stage:
        stmt = stmt.where(ProcessingRun.stage == stage)
    stmt = stmt.order_by(ProcessingRun.created_at.asc()).limit(1).with_for_update(skip_locked=True)
    run = await session.scalar(stmt)
    if run is None:
        return None
    run.status = ProcessingStageStatus.RUNNING.value
    run.attempt = int(run.attempt or 0) + 1
    run.lease_expires_at = now + timedelta(seconds=lease_seconds)
    run.heartbeat_at = now
    run.metadata_json = {**(run.metadata_json or {}), "claimed_at": now.isoformat()}
    await session.flush()
    return run


async def heartbeat_processing_run(
    session: AsyncSession,
    run: ProcessingRun,
    *,
    lease_seconds: int = 300,
    metadata: dict[str, Any] | None = None,
) -> None:
    now = datetime.now(UTC)
    run.heartbeat_at = now
    run.lease_expires_at = now + timedelta(seconds=lease_seconds)
    if metadata:
        run.metadata_json = {**(run.metadata_json or {}), **metadata}
    await session.flush()


async def complete_processing_run(
    session: AsyncSession,
    run: ProcessingRun,
    *,
    status: str = ProcessingStageStatus.COMPLETE.value,
    metadata: dict[str, Any] | None = None,
) -> None:
    now = datetime.now(UTC)
    run.status = status
    run.heartbeat_at = now
    run.lease_expires_at = None
    if metadata:
        run.metadata_json = {**(run.metadata_json or {}), **metadata, "finished_at": now.isoformat()}
    else:
        run.metadata_json = {**(run.metadata_json or {}), "finished_at": now.isoformat()}
    await session.flush()


async def fail_processing_run(
    session: AsyncSession,
    run: ProcessingRun,
    *,
    error: str,
    retryable: bool = False,
    metadata: dict[str, Any] | None = None,
) -> None:
    now = datetime.now(UTC)
    run.status = ProcessingStageStatus.PENDING.value if retryable else ProcessingStageStatus.FAILED.value
    run.heartbeat_at = now
    run.lease_expires_at = None
    run.error = error[:2000]
    run.metadata_json = {
        **(run.metadata_json or {}),
        **(metadata or {}),
        "failed_at": now.isoformat(),
        "retryable": retryable,
    }
    await session.flush()
