"""Durable, leased Phase 5 FOUNDATION knowledge work (`E0/E1` + `DenseGraph`).

`SEARCH_READY` is only read, never written.

Architectural Note — Removal of Phase 5B E2 LLM Enrichment (2026-10-29):
------------------------------------------------------------------------
Selective E2 LLM enrichment (`KnowledgeJob.kind == "E2"`, `llm_enrich`, `apply_e2`) was
permanently removed after controlled A/B benchmarking with exact provider token metering on
the full 81-file / 50-question RetrievalCI enterprise corpus (`docs/adr-remove-e2-llm-enrichment.md`):
  - Without E2 (`20261029T031043Z-bb191f`, FOUNDATION E0/E1 + DenseGraph only):
      Score = 93.64 | Recall = 99.17% (100% of top_k=5 oracle ceiling) | Precision = 80.73%
      Abstention = 100.0% (5/5) | Ingest tokens = 375,463 (0 LLM calls) | Ingest cost = $0.003755
  - With Full E2 (`20261029T103921Z-183d1c`, +258 E2 LLM jobs / 1,768 E2 rows):
      Score = 93.42 (-0.22) | Recall = 99.17% (+0.00%) | Precision = 80.00% (-0.73%)
      Abstention = 80.0% (-20.0%) | Ingest tokens = 526,898 (+151,435 LLM tokens) | Ingest cost = $0.012841 (3.42x)
  - Forensic DB audit proved: 51.2% of E2 rows (905/1,768 claims & relations) were never read by
    graph/retrieval; 7.4% of E2 LLM calls (19/258) yielded 0 items on short headers/table dividers;
    19.2% duplicated deterministic E0/E1 entities; and novel cross-doc E2 labels were predominantly
    generic nouns (`customer`, `latency`, `configuration`) that added false-positive neighbor docs
    on 8/50 queries while adding 0 ground-truth docs.

Phase 5A `FOUNDATION` (`E0/E1` + `DenseGraph` reusing 100% of Phase 2C preloaded embeddings at
0 extra token cost + `Leiden` routing tree) remains 100% active at ingest.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import uuid
from collections.abc import Sequence
from contextlib import suppress
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, update

from sag_api.db.models import (
    Document,
    DocumentVersion,
    KnowledgeJob,
)
from sag_api.enums import DocumentStatus
from sag_api.services.knowledge_service import (
    KnowledgeConfig,
    knowledge_lock,
    rebuild_knowledge_version,
    stable_id,
    version_input,
)

log = logging.getLogger(__name__)


async def _embed_blocks(embed, non_heading_blocks: Sequence[tuple[str, str]]) -> dict[str, list[float]]:
    if not non_heading_blocks:
        return {}
    from sag_api.core.token_usage import (
        extract_embedding_usage,
        record_embedding_cache_hit,
        record_embedding_usage,
    )

    emb_model = getattr(embed, "model", None)
    metered = bool(getattr(embed, "_sag_token_metered_cache_wrapped", False))
    texts = [text for _, text in non_heading_blocks]
    if hasattr(embed, "batch_generate"):
        from sag_api.services.search_index_service import (
            get_cached_text_embedding,
            put_cached_text_embedding,
        )

        cached_by_idx: dict[int, list[float]] = {}
        missing_indices: list[int] = []
        missing_texts: list[str] = []
        for idx, t in enumerate(texts):
            cached = get_cached_text_embedding(t)
            if cached is not None:
                cached_by_idx[idx] = cached
            else:
                missing_indices.append(idx)
                missing_texts.append(t)
        cache_hits = len(texts) - len(missing_texts)
        if cache_hits > 0:
            record_embedding_cache_hit(model=emb_model, count=cache_hits)
        if missing_texts:
            fetched = await embed.batch_generate(missing_texts)
            if not metered:
                record_embedding_usage(
                    model=emb_model,
                    usage=extract_embedding_usage(None, missing_texts),
                )
            for idx, txt, vec in zip(missing_indices, missing_texts, fetched, strict=True):
                vec_f = [float(v) for v in vec]
                put_cached_text_embedding(txt, vec_f)
                cached_by_idx[idx] = vec_f
        vecs = [cached_by_idx[i] for i in range(len(texts))]
    elif hasattr(embed, "generate"):
        vecs = [await embed.generate(t) for t in texts]
        if not metered:
            record_embedding_usage(model=emb_model, usage=extract_embedding_usage(None, texts))
    elif hasattr(embed, "embed_documents"):
        res = embed.embed_documents(texts)
        vecs = await res if inspect.isawaitable(res) else res
        if not metered:
            record_embedding_usage(model=emb_model, usage=extract_embedding_usage(None, texts))
    elif callable(embed):
        res = embed(texts)
        vecs = await res if inspect.isawaitable(res) else res
        if not metered:
            record_embedding_usage(model=emb_model, usage=extract_embedding_usage(None, texts))
    else:
        return {}
    return {
        block_id: [float(v) for v in vec]
        for (block_id, _), vec in zip(non_heading_blocks, vecs, strict=True)
    }


class QueuePolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    concurrency: int = Field(default=2, ge=1, le=16)
    capacity: int = Field(default=256, ge=1, le=10000)
    max_attempts: int = Field(default=3, ge=1, le=10)
    max_age_seconds: int = Field(default=86400, ge=1)
    timeout_seconds: int = Field(default=60, ge=1, le=600)
    retry_seconds: int = Field(default=30, ge=1)
    poll_seconds: float = Field(default=2, ge=0.1)


async def _queue_lock(session) -> None:
    # ponytail: global admission lock; use per-tenant admission if dispatch throughput becomes limiting.
    await knowledge_lock(session, "queue")


async def enqueue(
    session,
    version_id: str,
    tenant_id: str,
    kind: str,
    input_hash: str,
    policy: QueuePolicy,
    *,
    unit_id: str | None = None,
    priority: int = 0,
) -> KnowledgeJob | None:
    if kind != "FOUNDATION":
        return None
    await _queue_lock(session)
    job_id = stable_id(version_id, unit_id or "", kind, input_hash)
    existing = await session.get(KnowledgeJob, job_id)
    if existing:
        return existing
    count = await session.scalar(
        select(func.count())
        .select_from(KnowledgeJob)
        .where(
            KnowledgeJob.status.in_(("QUEUED", "RUNNING", "RETRY")),
            KnowledgeJob.kind == "FOUNDATION",
        )
    )
    oldest = await session.scalar(
        select(func.min(KnowledgeJob.created_at)).where(
            KnowledgeJob.status.in_(("QUEUED", "RETRY")),
            KnowledgeJob.kind == "FOUNDATION",
        )
    )
    now = datetime.now(UTC)
    if count >= policy.capacity or oldest and (now - oldest).total_seconds() >= policy.max_age_seconds:
        return None  # Re-discovery from canonical data retries admission without blocking ingestion.
    job = KnowledgeJob(
        id=job_id,
        document_version_id=version_id,
        tenant_id=tenant_id,
        unit_id=unit_id,
        kind="FOUNDATION",
        input_checksum=input_hash,
        status="QUEUED",
        priority=priority,
        attempts=0,
        reserved_tokens=0,
        available_at=now,
        created_at=now,
    )
    session.add(job)
    await session.flush()
    return job


async def claim_job(
    session,
    policy: QueuePolicy,
    *,
    now: datetime | None = None,
    foundation_timeout_seconds: float | None = None,
) -> KnowledgeJob | None:
    now = now or datetime.now(UTC)
    await _queue_lock(session)
    expired = (
        await session.scalars(
            select(KnowledgeJob)
            .where(
                KnowledgeJob.kind == "FOUNDATION",
                KnowledgeJob.status == "RUNNING",
                KnowledgeJob.lease_until <= now,
            )
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
    ).all()
    for job in expired:
        job.status = "FAILED" if job.attempts >= policy.max_attempts else "RETRY"
        job.available_at, job.error_code, job.lease_token = now, "LEASE_EXPIRED", None
    await session.execute(
        update(KnowledgeJob)
        .where(
            KnowledgeJob.kind == "FOUNDATION",
            KnowledgeJob.status.in_(("QUEUED", "RETRY")),
            KnowledgeJob.created_at < now - timedelta(seconds=policy.max_age_seconds),
        )
        .values(status="EXPIRED", error_code="QUEUE_MAX_AGE")
    )
    await session.flush()
    running = await session.scalar(
        select(func.count())
        .select_from(KnowledgeJob)
        .where(KnowledgeJob.kind == "FOUNDATION", KnowledgeJob.status == "RUNNING")
    )
    if running >= policy.concurrency:
        return None
    jobs = (
        await session.scalars(
            select(KnowledgeJob)
            .where(
                KnowledgeJob.status.in_(("QUEUED", "RETRY")),
                KnowledgeJob.available_at <= now,
                KnowledgeJob.kind == "FOUNDATION",
            )
            .order_by(KnowledgeJob.priority.desc(), KnowledgeJob.created_at, KnowledgeJob.id)
            .limit(32)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
    ).all()
    for job in jobs:
        document = await session.scalar(
            select(Document)
            .join(DocumentVersion, DocumentVersion.document_id == Document.id)
            .where(DocumentVersion.id == job.document_version_id, Document.tenant_id == job.tenant_id)
        )
        if document is None or not document.project_id:
            job.status, job.error_code = "FAILED", "INVALID_PROJECT_SCOPE"
            continue
        job.attempts += 1
        job.status, job.error_code = "RUNNING", None
        job.reserved_tokens = 0
        job.lease_token = str(uuid.uuid4())
        job.lease_until = now + timedelta(seconds=(foundation_timeout_seconds or policy.timeout_seconds) + 30)
        await session.flush()
        return job
    return None


class KnowledgeWorker:
    """Executes Phase 5A FOUNDATION (`E0/E1` + `DenseGraph`) jobs and requests Leiden tree publication."""

    def __init__(
        self,
        session_factory,
        settings,
        *,
        config: KnowledgeConfig = KnowledgeConfig(),
        policy: QueuePolicy | None = None,
        embed=None,
        job_queue=None,
    ):
        self.sessions, self.settings, self.config = session_factory, settings, config
        self.policy = policy or QueuePolicy(
            concurrency=settings.knowledge_concurrency,
            capacity=settings.knowledge_queue_capacity,
        )
        self.embed = embed
        self.job_queue = job_queue
        self.task = None
        self.running: set[asyncio.Task] = set()
        self.scheduling_tree_jobs: set[str] = set()
        self.discovery_after = ""
        self._wakeup = asyncio.Event()
        retries = getattr(settings, "embedding_max_retries", 3)
        provider_timeout = getattr(settings, "embedding_timeout", 60)
        envelope = (
            max(30, provider_timeout)
            + provider_timeout * (retries + 1)
            + sum(min(0.5 * 2**i, 8) for i in range(retries))
            + 30
        )
        self.foundation_timeout = getattr(settings, "knowledge_foundation_timeout_seconds", None) or (
            policy.timeout_seconds if policy else envelope
        )

    async def discover(self):
        if self.job_queue is not None:
            await self._request_completed_trees()
        async with self.sessions() as session:
            versions = (
                await session.scalars(
                    select(DocumentVersion)
                    .join(Document)
                    .where(
                        Document.is_active.is_(True),
                        Document.status.in_(
                            (
                                DocumentStatus.PENDING,
                                DocumentStatus.LOADING,
                                DocumentStatus.EXTRACTING,
                                DocumentStatus.READY,
                            )
                        ),
                        DocumentVersion.search_status.in_(("READY", "SEARCH_READY")),
                        DocumentVersion.search_ready_at.is_not(None),
                        DocumentVersion.valid_from <= datetime.now(UTC),
                        DocumentVersion.valid_to > datetime.now(UTC),
                        DocumentVersion.knowledge_status.in_(("NOT_STARTED", "FAILED")),
                        DocumentVersion.id > self.discovery_after,
                    )
                    .order_by(DocumentVersion.id)
                    .limit(32)
                )
            ).all()
            self.discovery_after = versions[-1].id if versions else ""
            for version in versions:
                try:
                    _, _, provenance, digest = await version_input(session, version.id, self.config)
                    job = await enqueue(
                        session,
                        version.id,
                        provenance["tenant_id"],
                        "FOUNDATION",
                        digest,
                        self.policy,
                        priority=100,
                    )
                    if job:
                        version.knowledge_status = (
                            "DATA_READY"
                            if job.status == "SUCCEEDED"
                            else "FAILED"
                            if job.status in {"FAILED", "EXPIRED"}
                            else "QUEUED"
                        )
                except ValueError:
                    version.knowledge_status = "FAILED"
            await session.commit()

    async def _request_completed_trees(self):
        from sag_api.services.universe_tree_service import enqueue_tree_rebuild_for_version

        async with self.sessions() as session:
            jobs = list(
                (
                    await session.scalars(
                        select(KnowledgeJob)
                        .where(
                            KnowledgeJob.kind == "FOUNDATION",
                            KnowledgeJob.status == "SUCCEEDED",
                            KnowledgeJob.tree_requested_at.is_(None),
                        )
                        .order_by(KnowledgeJob.created_at)
                        .limit(32)
                    )
                ).all()
            )
            for job in jobs:
                if job.id in self.scheduling_tree_jobs or job.tree_requested_at is not None:
                    continue
                await session.refresh(job)
                if job.id in self.scheduling_tree_jobs or job.tree_requested_at is not None:
                    continue
                await enqueue_tree_rebuild_for_version(
                    session,
                    self.job_queue,
                    job.document_version_id,
                    reason="knowledge_recovery",
                )
                job.tree_requested_at = datetime.now(UTC)
                await session.commit()

    async def run_one(self) -> bool:
        from sag_api.core.token_usage import (
            TokenLedger,
            merge_token_summaries,
            sync_document_token_usage,
            token_usage_scope,
        )

        async with self.sessions() as session:
            job = await claim_job(
                session,
                self.policy,
                foundation_timeout_seconds=self.foundation_timeout,
            )
            await session.commit()
            if job is None:
                return False
            job_id, token = job.id, job.lease_token
        job_ledger = TokenLedger()
        timeout = self.foundation_timeout
        try:
            async with asyncio.timeout(timeout):
                dense_by_block_id = None
                embedder = self.embed
                if embedder is None and getattr(self.settings, "embedding_api_key", None):
                    try:
                        from sag_api.sag.engine_manager import EngineManager

                        embedder = await EngineManager(self.settings).get_sag_embedding()
                        self.embed = embedder
                    except Exception as error:  # noqa: BLE001
                        log.warning("Fallback knowledge embedder unavailable: %s", error)
                if embedder is not None:
                    async with self.sessions() as session:
                        from sag_api.services.search_index_service import contextualize_block_text

                        _, blocks, provenance, actual = await version_input(
                            session, job.document_version_id, self.config
                        )
                        if actual != job.input_checksum:
                            raise ValueError("Stale foundation input")
                        non_heading_blocks = [
                            (b.id, contextualize_block_text(b.section_path, b.normalized_text))
                            for b in blocks
                            if b.block_type != "heading" and b.normalized_text.strip()
                        ]
                    if non_heading_blocks:
                        with token_usage_scope(
                            component="knowledge_foundation_embedding",
                            project_id=provenance.get("project_id"),
                            tenant_id=provenance.get("tenant_id"),
                            document_id=provenance.get("document_id"),
                            document_version_id=job.document_version_id,
                        ) as scoped_ledger:
                            try:
                                dense_by_block_id = await _embed_blocks(embedder, non_heading_blocks)
                            finally:
                                job_ledger.merge_summary(scoped_ledger.to_summary())
                async with self.sessions() as session:
                    current = await session.scalar(
                        select(KnowledgeJob).where(KnowledgeJob.id == job_id).with_for_update()
                    )
                    if not current or current.status != "RUNNING" or current.lease_token != token:
                        return True  # A recovered lease owns the result now.
                    _, _, _, actual = await version_input(session, current.document_version_id, self.config)
                    if actual != current.input_checksum:
                        raise ValueError("Stale foundation input")
                    await rebuild_knowledge_version(
                        session,
                        current.document_version_id,
                        self.config,
                        dense_by_block_id=dense_by_block_id,
                        extra_entities=(
                            None if getattr(self.settings, "document_event_extraction_enabled", False) else ()
                        ),
                    )
                    attempt_summary = job_ledger.to_summary()
                    current.usage_json = merge_token_summaries(current.usage_json, attempt_summary)
                    current.status, current.lease_token, current.lease_until = "SUCCEEDED", None, None
                    await session.flush()
                    await sync_document_token_usage(session, current.document_version_id)
                    if self.job_queue is None:
                        await session.commit()
                    else:
                        self.scheduling_tree_jobs.add(job_id)
                        current.tree_requested_at = datetime.now(UTC)
                        await session.flush()
                        try:
                            from sag_api.services.universe_tree_service import enqueue_tree_rebuild_for_version

                            await enqueue_tree_rebuild_for_version(
                                session,
                                self.job_queue,
                                current.document_version_id,
                                reason="knowledge_foundation_ready",
                            )
                            await session.commit()
                        except Exception as error:  # noqa: BLE001 - tree scheduling must not retry completed extraction
                            current.tree_requested_at = None
                            await session.commit()
                            log.warning("Universe tree rebuild scheduling delayed (%s)", type(error).__name__)
                        finally:
                            self.scheduling_tree_jobs.discard(job_id)
        except Exception as error:
            # Store only error class; provider bodies/credentials/document text never enter job errors.
            attempt_summary = job_ledger.to_summary()
            async with self.sessions() as session:
                current = await session.scalar(select(KnowledgeJob).where(KnowledgeJob.id == job_id).with_for_update())
                if current and current.status == "RUNNING" and current.lease_token == token:
                    if attempt_summary["totals"]["total_tokens"] > 0:
                        current.usage_json = merge_token_summaries(current.usage_json, attempt_summary)
                        await session.flush()
                        await sync_document_token_usage(session, current.document_version_id)
                    current.status = "FAILED" if current.attempts >= self.policy.max_attempts else "RETRY"
                    current.error_code = type(error).__name__[:64]
                    current.available_at = datetime.now(UTC) + timedelta(
                        seconds=self.policy.retry_seconds * 2 ** (current.attempts - 1)
                    )
                    current.lease_token, current.lease_until = None, None
                    if current.status == "FAILED":
                        await session.execute(
                            update(DocumentVersion)
                            .where(
                                DocumentVersion.id == current.document_version_id,
                                DocumentVersion.knowledge_status == "QUEUED",
                            )
                            .values(knowledge_status="FAILED")
                        )
                    await session.commit()
            log.warning(
                "Knowledge job failed job_id=%s kind=%s attempt=%s error_type=%s cause_type=%s budget_seconds=%s",
                job_id,
                job.kind,
                job.attempts,
                type(error).__name__,
                type(error.__cause__).__name__ if error.__cause__ else None,
                timeout,
            )
        return True

    async def _loop(self):
        while True:
            self._wakeup.clear()
            try:
                await self.discover()
                while len(self.running) < self.policy.concurrency:
                    task = asyncio.create_task(self.run_one())
                    self.running.add(task)
                    task.add_done_callback(self._finished)
                    if len(self.running) >= self.policy.concurrency:
                        break
            except Exception as error:
                log.warning("Knowledge dispatch delayed (%s)", type(error).__name__)
            with suppress(TimeoutError):
                await asyncio.wait_for(self._wakeup.wait(), timeout=self.policy.poll_seconds)

    def _finished(self, task):
        self.running.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            log.warning("Knowledge claim delayed (%s)", type(exc).__name__)
            return
        if task.result():
            self._wakeup.set()

    async def start(self):
        self.task = asyncio.create_task(self._loop(), name="knowledge-worker")

    async def stop(self):
        if self.task:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
            self.task = None
        tasks = list(self.running)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
