"""Hàng đợi tác vụ asyncio trong tiến trình —— khởi/dừng cùng tiến trình API.

- N coroutine worker lấy job_id từ hàng đợi, nạp Job, duy trì máy trạng thái và phân phối trình xử lý.
- Khi khởi động «khôi phục» các tác vụ QUEUED/RUNNING còn sót lại lần trước (RUNNING đặt lại thành QUEUED để chạy lại).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker

from sag_api.core.config import settings
from sag_api.core.errors import ServiceUnavailableError, UpstreamError
from sag_api.core.logging import get_logger
from sag_api.db.models import Document
from sag_api.enums import DocumentStatus, JobStatus, JobType
from sag_api.jobs.control import JobPaused
from sag_api.jobs.queue import JobQueue
from sag_api.jobs.tasks import TASK_HANDLERS
from sag_api.sag import EngineManager

log = get_logger("jobs")

# Cơ số backoff (giây): lần thử thứ n chờ base**n. Test có thể monkeypatch để rút ngắn.
_BACKOFF_BASE_SECONDS = 2.0
_RECOVERY_LOCK_RETRIES = 4


def _now() -> datetime:
    return datetime.now(UTC)


def _is_retryable(exc: Exception) -> bool:
    """Lỗi tức thời (giới hạn tốc độ/hết thời gian/upstream tạm không khả dụng) có thể thử lại; lỗi loại đầu vào/cấu hình không thử lại."""
    return isinstance(exc, (ServiceUnavailableError, UpstreamError))


async def _mark_document_waiting_retry(session, job) -> None:
    """Keep a retryable document active without discarding its checkpoint."""
    if not job.document_id:
        return
    document = await session.get(Document, job.document_id)
    if document is None:
        return
    document.status = DocumentStatus.PENDING
    document.error = None


class InProcessAsyncQueue(JobQueue):
    def __init__(
        self,
        session_factory: async_sessionmaker,
        engine_manager: EngineManager,
        *,
        concurrency: int = 2,
    ) -> None:
        self._session_factory = session_factory
        self._engine_manager = engine_manager
        self._concurrency = concurrency
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._workers: list[asyncio.Task] = []
        self._retry_tasks: set[asyncio.Task] = set()
        self._universe_user_locks: dict[str, asyncio.Lock] = {}
        self._started = False

    async def enqueue(self, job_id: str) -> None:
        await self._queue.put(job_id)

    def _schedule_retry(self, job_id: str, delay: float) -> None:
        """Vào lại hàng đợi sau backoff (không chặn worker)."""

        async def _later() -> None:
            try:
                await asyncio.sleep(delay)
                await self._queue.put(job_id)
            except asyncio.CancelledError:
                pass

        task = asyncio.create_task(_later(), name=f"sag-retry-{job_id}")
        self._retry_tasks.add(task)
        task.add_done_callback(self._retry_tasks.discard)

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        try:
            # Recover before workers begin consuming so a failed startup cannot
            # leave detached workers holding database sessions.
            await self._recover()
            for i in range(self._concurrency):
                self._workers.append(
                    asyncio.create_task(self._worker_loop(i), name=f"sag-worker-{i}")
                )
        except BaseException:
            await self.stop()
            raise
        log.info("Hàng đợi tác vụ đã khởi động (song song=%d)", self._concurrency)

    async def stop(self) -> None:
        retry_tasks = list(self._retry_tasks)
        for t in retry_tasks:
            t.cancel()
        if retry_tasks:
            await asyncio.gather(*retry_tasks, return_exceptions=True)
        self._retry_tasks.clear()
        for w in self._workers:
            w.cancel()
        for w in self._workers:
            try:
                await w
            except asyncio.CancelledError:
                pass
        self._workers.clear()
        self._universe_user_locks.clear()
        self._started = False

    async def _recover(self) -> None:
        from sag_api.db.models import Job

        rows = []
        for attempt in range(_RECOVERY_LOCK_RETRIES):
            try:
                async with self._session_factory() as session:
                    rows = (
                        await session.execute(
                            select(Job).where(
                                Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING])
                            )
                        )
                    ).scalars().all()
                    for job in rows:
                        if job.status == JobStatus.RUNNING:
                            job.status = JobStatus.QUEUED
                    await session.commit()
                break
            except OperationalError as error:
                locked = "database is locked" in str(error).lower()
                if not locked or attempt == _RECOVERY_LOCK_RETRIES - 1:
                    raise
                await asyncio.sleep(0.08 * (2**attempt))
        for job in rows:
            await self._queue.put(job.id)
        if rows:
            log.info("Khôi phục %d tác vụ chưa hoàn thành", len(rows))

    async def _worker_loop(self, idx: int) -> None:
        while True:
            job_id = await self._queue.get()
            try:
                await self._run(job_id)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("worker#%d xử lý job=%s gặp ngoại lệ", idx, job_id)
            finally:
                self._queue.task_done()

    async def _run(self, job_id: str) -> None:
        from sag_api.db.models import Job

        async with self._session_factory() as session:
            job = await session.get(Job, job_id)
            # Trong hàng đợi có thể còn job_id từ trước khi tạm dừng, hoặc bị enqueue trùng; chỉ QUEUED
            # mới được khởi động, tránh cùng một tác vụ bị hai worker chạy đồng thời.
            if job is None or job.status != JobStatus.QUEUED:
                return
            universe_user_id = (
                str((job.payload or {}).get("user_id") or "")
                if job.type == JobType.INDEX_UNIVERSE
                else ""
            )

        if universe_user_id:
            lock = self._universe_user_locks.setdefault(universe_user_id, asyncio.Lock())
            async with lock:
                await self._run_job(job_id)
            return
        await self._run_job(job_id)

    async def _run_job(self, job_id: str) -> None:
        from sag_api.db.models import Job

        async with self._session_factory() as session:
            job = await session.get(Job, job_id)
            if job is None or job.status != JobStatus.QUEUED:
                return
            payload = dict(job.payload or {})
            is_resume = bool(payload.pop("resume_requested", False))
            claim = await session.execute(
                update(Job)
                .where(Job.id == job_id, Job.status == JobStatus.QUEUED)
                .values(
                    payload=payload,
                    status=JobStatus.RUNNING,
                    started_at=_now(),
                    finished_at=None,
                    attempts=job.attempts if is_resume else job.attempts + 1,
                    progress=job.progress if is_resume else max(job.progress, 0.05),
                    error=None,
                )
            )
            await session.commit()
            if claim.rowcount != 1:
                return
            await session.refresh(job)

            handler = TASK_HANDLERS.get(job.type)
            if handler is None:
                job.status = JobStatus.FAILED
                job.error = f"Loại tác vụ không xác định: {job.type}"
                job.finished_at = _now()
                await session.commit()
                return

            try:
                await handler(session, job, engine_manager=self._engine_manager, job_queue=self)
                job.status = JobStatus.SUCCEEDED
                job.progress = 1.0
                job.finished_at = _now()
                job.error = None
            except JobPaused:
                await session.rollback()
                job = await session.get(Job, job_id)
                if job is not None:
                    payload = dict(job.payload or {})
                    payload.pop("pause_requested", None)
                    job.payload = payload
                    job.status = JobStatus.PAUSED
                    job.finished_at = None
                    job.error = None
                    log.info("Tác vụ đã tạm dừng job=%s progress=%.0f%%", job_id, job.progress * 100)
            except Exception as e:  # noqa: BLE001
                await session.rollback()
                job = await session.get(Job, job_id)
                msg = getattr(e, "message", None) or str(e)
                attempts = job.attempts if job is not None else settings.job_max_attempts
                retry = job is not None and _is_retryable(e) and attempts < settings.job_max_attempts
                if job is not None:
                    if retry:
                        # Xếp lại sau backoff: trạng thái về QUEUED, sau delay base**attempts giây thì vào lại hàng đợi
                        job.status = JobStatus.QUEUED
                        job.progress = 0.0
                        job.error = f"Lần {attempts} thất bại, sẽ thử lại: {msg}"
                        await _mark_document_waiting_retry(session, job)
                        delay = _BACKOFF_BASE_SECONDS**attempts
                        self._schedule_retry(job_id, delay)
                        log.warning(
                            "Tác vụ có thể thử lại job=%s (lần %d/%d), xếp lại sau %.1fs: %s",
                            job_id, attempts, settings.job_max_attempts, delay, msg,
                        )
                    else:
                        job.status = JobStatus.FAILED
                        job.error = msg
                        job.finished_at = _now()
                        log.warning("Tác vụ thất bại job=%s (thử %d lần): %s", job_id, attempts, msg)
            await session.commit()
