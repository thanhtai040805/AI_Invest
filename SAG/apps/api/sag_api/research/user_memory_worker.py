"""Dedicated async `UserMemoryWorker` (following `KnowledgeWorker` pattern in `main.py`).

Completely decoupled from `InProcessAsyncQueue` / MinerU OCR so slow document parsing NEVER
blocks memory ingestion, outbox sweeps, or cascade deletes. Receives `llm` and `engine_manager`
directly via lifespan DI.
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select

from sag_api.core.config import Settings
from sag_api.core.logging import get_logger
from sag_api.db.models.user_memory import UserMemoryJob
from sag_api.research.user_memory_pipeline import (
    cascade_delete_session_memories,
    flush_user_memory_outbox,
    process_ingest_turn_payload,
    propagate_document_version_staleness,
)

log = get_logger("user_memory_worker")


class UserMemoryWorker:
    """Durable async worker polling `user_memory_jobs` + sweeping 3-state Qdrant Outbox."""

    def __init__(
        self,
        sessions: Any,
        settings: Settings,
        *,
        llm: Any = None,
        engine_manager: Any = None,
        poll_seconds: float = 2.0,
        concurrency: int = 2,
        max_attempts: int = 4,
        lease_seconds: int = 120,
    ) -> None:
        self.sessions = sessions
        self.settings = settings
        self.llm = llm
        self.engine_manager = engine_manager
        self.poll_seconds = max(0.2, float(poll_seconds))
        self.concurrency = max(1, int(concurrency))
        self.max_attempts = max(1, int(max_attempts))
        self.lease_seconds = max(15, int(lease_seconds))
        self.running: set[asyncio.Task[Any]] = set()
        self.task: asyncio.Task[Any] | None = None
        self._wakeup = asyncio.Event()

    def wakeup(self) -> None:
        self._wakeup.set()

    async def _resolve_qdrant_and_embedder(self) -> tuple[Any, Any]:
        q_client = None
        embedder = None
        if self.engine_manager is not None:
            get_q = getattr(self.engine_manager, "get_search_unit_qdrant_client", None)
            if callable(get_q):
                try:
                    q_client = await get_q()
                except Exception as exc:  # noqa: BLE001
                    log.debug("UserMemoryWorker Qdrant client unavailable (%s)", type(exc).__name__)
            get_emb = getattr(self.engine_manager, "get_sag_embedding", None)
            if callable(get_emb):
                try:
                    embedder = await get_emb()
                except Exception as exc:  # noqa: BLE001
                    log.debug("UserMemoryWorker embedder unavailable (%s)", type(exc).__name__)
        return q_client, embedder

    async def claim_one(self, *, now: datetime | None = None) -> UserMemoryJob | None:
        """Claim one due job using `FOR UPDATE SKIP LOCKED` on PostgreSQL (plain select on SQLite)."""
        ts_now = now or datetime.now(UTC)
        token = str(uuid.uuid4())
        async with self.sessions() as session:
            bind = session.get_bind()
            dialect = getattr(getattr(bind, "dialect", None), "name", "")
            stmt = (
                select(UserMemoryJob)
                .where(
                    or_(
                        UserMemoryJob.status.in_(("QUEUED", "RETRY")),
                        (UserMemoryJob.status == "RUNNING") & (UserMemoryJob.lease_until <= ts_now),
                    ),
                    UserMemoryJob.available_at <= ts_now,
                )
                .order_by(UserMemoryJob.available_at.asc(), UserMemoryJob.created_at.asc())
                .limit(1)
            )
            if dialect == "postgresql":
                stmt = stmt.with_for_update(skip_locked=True)

            job = await session.scalar(stmt)
            if job is None:
                return None
            job.status = "RUNNING"
            job.attempts = int(job.attempts or 0) + 1
            job.lease_token = token
            job.lease_until = ts_now + timedelta(seconds=self.lease_seconds)
            await session.commit()
            return job

    async def run_one(self, *, now: datetime | None = None) -> bool:
        """Claim and execute a single due job. Returns True if a job was processed."""
        job = await self.claim_one(now=now)
        if job is None:
            return False

        job_id = job.job_id
        token = job.lease_token
        q_client, embedder = await self._resolve_qdrant_and_embedder()

        try:
            async with self.sessions() as session:
                if job.job_kind == "INGEST_TURN":
                    await process_ingest_turn_payload(
                        session,
                        tenant_id=job.tenant_id,
                        user_id=job.user_id,
                        payload=job.payload_json or {},
                        llm=self.llm,
                        qdrant_client=q_client,
                        embedder=embedder,
                    )
                elif job.job_kind == "CASCADE_DELETE_SESSION":
                    await cascade_delete_session_memories(
                        session,
                        tenant_id=job.tenant_id,
                        user_id=job.user_id,
                        session_id=str(job.session_id or (job.payload_json or {}).get("session_id") or ""),
                        reason=str((job.payload_json or {}).get("reason") or "USER_DELETED_SESSION"),
                        qdrant_client=q_client,
                    )
                elif job.job_kind == "STALENESS_PROPAGATE":
                    ver_ids = (job.payload_json or {}).get("superseded_version_ids") or []
                    reason_status = str((job.payload_json or {}).get("reason_status") or "STALE_NEW_FILING")
                    await propagate_document_version_staleness(
                        session,
                        ver_ids,
                        reason_status=reason_status,
                    )
                    await session.commit()

            async with self.sessions() as session:
                current = await session.scalar(select(UserMemoryJob).where(UserMemoryJob.job_id == job_id))
                if current is not None and current.lease_token == token:
                    current.status = "SUCCEEDED"
                    current.lease_token = None
                    current.lease_until = None
                    current.error = None
                    await session.commit()
        except Exception as exc:  # noqa: BLE001
            err_label = f"{type(exc).__name__}: {str(exc)[:180]}"
            async with self.sessions() as session:
                current = await session.scalar(select(UserMemoryJob).where(UserMemoryJob.job_id == job_id))
                if current is not None and current.lease_token == token:
                    current.status = "FAILED" if current.attempts >= self.max_attempts else "RETRY"
                    current.error = err_label
                    current.available_at = datetime.now(UTC) + timedelta(
                        seconds=15 * (2 ** max(0, int(current.attempts) - 1))
                    )
                    current.lease_token = None
                    current.lease_until = None
                    await session.commit()
            log.warning("UserMemoryJob failed job_id=%s kind=%s err=%s", job_id, job.job_kind, err_label)
        return True

    async def sweep_outbox(self, *, limit: int = 100) -> dict[str, int]:
        q_client, embedder = await self._resolve_qdrant_and_embedder()
        if q_client is None:
            return {"synced": 0, "failed": 0, "skipped_version_conflict": 0}
        async with self.sessions() as session:
            return await flush_user_memory_outbox(
                session,
                qdrant_client=q_client,
                embedder=embedder,
                limit=limit,
            )

    async def _loop(self) -> None:
        sweep_tick = 0
        while True:
            self._wakeup.clear()
            try:
                while len(self.running) < self.concurrency:
                    t = asyncio.create_task(self.run_one())
                    self.running.add(t)
                    t.add_done_callback(self._finished)
                    if len(self.running) >= self.concurrency:
                        break
                sweep_tick += 1
                if sweep_tick % 5 == 0:
                    await self.sweep_outbox(limit=50)
            except Exception as exc:  # noqa: BLE001
                log.warning("UserMemoryWorker loop delayed (%s)", type(exc).__name__)
            with suppress(TimeoutError):
                await asyncio.wait_for(self._wakeup.wait(), timeout=self.poll_seconds)

    def _finished(self, task: asyncio.Task[Any]) -> None:
        self.running.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            log.warning("UserMemoryWorker task error (%s)", type(exc).__name__)
            return
        if task.result():
            self._wakeup.set()

    async def start(self) -> None:
        if self.task is None:
            self.task = asyncio.create_task(self._loop(), name="user-memory-worker")

    async def stop(self) -> None:
        if self.task is not None:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
            self.task = None
        tasks = list(self.running)
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
