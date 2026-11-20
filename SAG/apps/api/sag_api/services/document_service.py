"""Document domain logic: upload persistence -> registration -> enqueue processing."""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import case, func, select, tuple_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.core.config import settings
from sag_api.core.error_taxonomy import ErrorLayer, ErrorStage
from sag_api.core.errors import ConflictError, ForbiddenError, NotFoundError, ValidationError
from sag_api.core.identity import (
    VerifiedPrincipal,
    compute_payload_hash,
    generate_doc_id,
    generate_version_id,
    normalize_idempotency_key,
)
from sag_api.core.logging import get_logger
from sag_api.core.principal_assertion import VerifiedPrincipal as AssertionVerifiedPrincipal
from sag_api.db.base import new_id
from sag_api.db.models import Document, Job, Source, SourceProjectMapping
from sag_api.db.models.routing_rag import (
    CanonicalBlock,
    DocumentVersion,
    IngestionRun,
    SearchUnit,
    SourceSnapshot,
    StageRun,
    UploadIdempotencyKey,
)
from sag_api.enums import ConnectorKind, DocumentStatus, JobStatus, JobType, SourceType
from sag_api.jobs import JobQueue
from sag_api.jobs.scheduling import DELETE_PRIORITY, RESUME_PRIORITY, set_scheduler
from sag_api.schemas.routing_rag import (
    DocumentExtractedChunkItem,
    DocumentRevokeResponse,
    DocumentUploadResponse,
    DocumentVersionChunksResponse,
    DocumentVersionStatusResponse,
    ProjectDocumentReadinessBatchResponse,
    ProjectDocumentReadinessItem,
    ProjectDocumentVersionRef,
    StageProgressItem,
)
from sag_api.services.source_operation_service import touch_source_revision

log = get_logger("services.document")


async def _enqueue_persisted_job(job_queue: JobQueue, job_id: str) -> None:
    """Dispatch a committed job with queue-level retry supervision when available."""
    durable = getattr(job_queue, "enqueue_durably", None)
    if callable(durable):
        await durable(job_id)
        return
    await job_queue.enqueue(job_id)


async def _find_upload_run(
    session: AsyncSession, *, tenant_id: str, project_id: str, idempotency_key: str
) -> IngestionRun | None:
    run = await session.scalar(
        select(IngestionRun).where(
            IngestionRun.tenant_id == tenant_id,
            IngestionRun.project_id == project_id,
            IngestionRun.idempotency_key == idempotency_key,
        )
    )
    if run is not None:
        return run
    key = await session.get(UploadIdempotencyKey, (tenant_id, project_id, idempotency_key))
    return await session.get(IngestionRun, key.run_id) if key is not None else None


async def list_documents(session: AsyncSession, source_id: str) -> list[Document]:
    rows = await session.execute(
        select(Document)
        .where(
            Document.source_id == source_id,
            Document.is_active.is_(True),
            Document.status.not_in([DocumentStatus.DELETING, DocumentStatus.DELETE_FAILED]),
        )
        .order_by(Document.created_at.desc())
    )
    return list(rows.scalars().all())


async def get_document(session: AsyncSession, source: Source, document_id: str) -> Document:
    doc = await session.get(Document, document_id)
    if doc is None or doc.source_id != source.id or not doc.is_active:
        raise NotFoundError("Document not found")
    return doc


async def get_public_document(
    session: AsyncSession,
    source: Source,
    document_id: str,
    *,
    principal: AssertionVerifiedPrincipal,
) -> Document:
    """Resolve a document for public reads after the logical-delete barrier."""
    from sag_api.services.source_service import get_authorized_document

    document = await get_authorized_document(
        session,
        principal=principal,
        source_ids=[source.id],
        document_id=document_id,
    )
    if document is None:
        raise NotFoundError("Document not found")
    if document.status in {
        DocumentStatus.DELETING,
        DocumentStatus.DELETE_FAILED,
    }:
        raise NotFoundError("Document not found")
    return document


async def create_document_from_upload(
    session: AsyncSession,
    source: Source,
    *,
    filename: str,
    content_type: str,
    data: bytes,
    upload_dir: str,
    job_queue: JobQueue,
) -> tuple[Document, Job]:
    doc_id = new_id()
    safe_name = os.path.basename(filename) or "upload"

    dest_dir = os.path.join(upload_dir, source.id)
    os.makedirs(dest_dir, exist_ok=True)
    storage_path = os.path.join(dest_dir, f"{doc_id}_{safe_name}")
    with open(storage_path, "wb") as f:
        f.write(data)

    document = Document(
        id=doc_id,
        source_id=source.id,
        filename=safe_name,
        content_type=content_type or "application/octet-stream",
        size_bytes=len(data),
        storage_path=storage_path,
        status=DocumentStatus.PENDING,
    )
    session.add(document)
    await session.execute(update(Source).where(Source.id == source.id).values(document_count=Source.document_count + 1))
    job = Job(
        type=JobType.PROCESS_DOCUMENT,
        source_id=source.id,
        document_id=doc_id,
        status=JobStatus.QUEUED,
    )
    session.add(job)
    await touch_source_revision(session, source.id)
    await session.commit()
    await session.refresh(document)
    await session.refresh(job)

    await _enqueue_persisted_job(job_queue, job.id)
    return document, job


def _format_messages(messages: list[dict]) -> str:
    lines = ["# Messages", ""]
    for m in messages:
        who = m.get("author") or m.get("role") or "Message"
        ts = f" ({m['ts']})" if m.get("ts") else ""
        lines.append(f"**{who}**{ts}: {m.get('text') or ''}")
    return "\n\n".join(lines)


async def ingest_content(
    session: AsyncSession,
    source: Source,
    *,
    tenant_id: str,
    project_id: str,
    owner_id: str,
    security_partition_id: str,
    client_token: str,
    text: str | None = None,
    title: str | None = None,
    messages: list[dict] | None = None,
    logical_source_id: str | None = None,
    job_queue: JobQueue | None = None,
) -> Document:
    """Normalize text or messages and send them through canonical partitioned ingestion."""
    from sag_api.core.errors import ValidationError

    if messages:
        content = _format_messages(messages)
        filename = f"{title or f'messages-{len(messages)}'}.md"
    elif text:
        content = (f"# {title}\n\n" if title else "") + text
        filename = f"{title or 'text'}.md"
    else:
        raise ValidationError("Please provide text or messages")

    result = await handle_document_upload(
        session,
        tenant_id=tenant_id,
        project_id=project_id,
        owner_id=owner_id,
        security_partition_id=security_partition_id,
        client_token=client_token,
        file_bytes=content.encode("utf-8"),
        original_filename=filename,
        content_type="text/markdown",
        logical_source_id=logical_source_id or f"source:{source.id}:{filename}",
        job_queue=job_queue,
        source_id=source.id,
    )
    document = await session.get(Document, result.document_id)
    if document is None:
        raise NotFoundError("Document not found after ingestion")
    return document


async def reprocess_document(
    session: AsyncSession,
    source: Source,
    document_id: str,
    *,
    job_queue: JobQueue,
    remote_source_url: str | None = None,
    expected_sha256: str | None = None,
    expected_size_bytes: int | None = None,
    original_filename: str | None = None,
) -> Job:
    if remote_source_url:
        from sag_api.parsing.remote_object import validate_remote_object_url

        validate_remote_object_url(remote_source_url, settings)
    document = await get_document(session, source, document_id)
    if document.status in {DocumentStatus.DELETING, DocumentStatus.DELETE_FAILED}:
        raise ConflictError("Document is deleting or deletion failed, cannot reprocess")
    latest = await session.scalar(select(Job).where(Job.document_id == document.id).order_by(Job.created_at.desc()))
    if latest is not None and latest.status in {
        JobStatus.QUEUED,
        JobStatus.RUNNING,
        JobStatus.PAUSED,
    }:
        return latest
    restart_from_scratch = document.status == DocumentStatus.READY
    retrying_cleanup = bool(
        latest is not None and latest.type == JobType.REPROCESS_DOCUMENT and latest.status == JobStatus.FAILED
    )
    requires_maintenance = restart_from_scratch or retrying_cleanup
    original_status = document.status
    derived_source_ids: set[str] = set()
    if restart_from_scratch:
        derived_source_ids = {
            value
            for value in [
                document.sag_source_id,
                *[
                    _checkpoint_source_id(candidate.payload)
                    for candidate in (await session.scalars(select(Job).where(Job.document_id == document.id))).all()
                ],
            ]
            if value
        }

    has_legacy_checkpoint = bool(
        latest is not None and isinstance(latest.payload, dict) and latest.payload.get("process_checkpoint")
    )
    values: dict = {
        "status": DocumentStatus.PENDING,
        "is_active": True,
        "error": None,
        "error_layer": None,
        "error_stage": None,
        **({} if restart_from_scratch or has_legacy_checkpoint else {"progress": 0}),
    }
    if restart_from_scratch:
        values.update(
            progress=0,
            chunk_count=0,
            event_count=0,
            token_usage=0,
            sag_source_id=None,
            parser_provider=None,
            mineru_provider=None,
            mineru_model=None,
            parser_status=None,
            fallback_from=None,
            fallback_reason=None,
        )
    claimed = await session.execute(
        update(Document)
        .where(Document.id == document.id, Document.status == original_status)
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if claimed.rowcount != 1:
        await session.rollback()
        existing = await session.scalar(
            select(Job)
            .where(
                Job.document_id == document_id,
                Job.type.in_([JobType.PROCESS_DOCUMENT, JobType.REPROCESS_DOCUMENT]),
                Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.PAUSED]),
            )
            .order_by(Job.created_at.desc(), Job.id.desc())
            .limit(1)
        )
        if existing is not None:
            return existing
        raise ConflictError("Document state has changed, please refresh and retry")
    await session.refresh(document)
    if restart_from_scratch:
        await _refresh_source_counts(session, source)
    payload = dict(latest.payload or {}) if latest is not None and not restart_from_scratch else {}
    payload.pop("pause_requested", None)
    payload.pop("resume_requested", None)
    payload.pop("canonical_checkpoint_stage", None)
    remote_payload: dict = {}
    if remote_source_url:
        remote_payload = {
            "remote_object_sourced": True,
            "remote_source_url": remote_source_url,
            **(
                {"expected_sha256": expected_sha256.strip().lower()}
                if expected_sha256
                else {}
            ),
            **(
                {"expected_size_bytes": int(expected_size_bytes)}
                if expected_size_bytes
                else {}
            ),
            **(
                {"original_filename": original_filename}
                if original_filename
                else {}
            ),
        }
    if restart_from_scratch:
        payload = set_scheduler(
            {
                "target_document_id": document.id,
                "derived_source_ids": sorted(derived_source_ids),
                **remote_payload,
            },
            priority=DELETE_PRIORITY,
        )
    elif retrying_cleanup:
        payload = set_scheduler(
            {**payload, **remote_payload},
            priority=DELETE_PRIORITY,
            blocked_reason=None,
        )
    elif remote_payload:
        payload = {**payload, **remote_payload}
    job = Job(
        type=(JobType.REPROCESS_DOCUMENT if requires_maintenance else JobType.PROCESS_DOCUMENT),
        source_id=source.id,
        document_id=document.id,
        status=JobStatus.QUEUED,
        # Reuse a previous MinerU task instead of charging again after a failed job.
        payload=payload,
    )
    session.add(job)
    latest_ver = await session.scalar(
        select(DocumentVersion)
        .where(DocumentVersion.document_id == document.id)
        .order_by(DocumentVersion.version_no.desc())
        .limit(1)
    )
    if latest_ver is not None and not requires_maintenance:
        latest_ver.status = "RECEIVED"
        if latest_ver.search_status in ("FAILED", "INDEX_FAILED"):
            latest_ver.search_status = "PENDING"
        if latest_ver.knowledge_status == "FAILED":
            latest_ver.knowledge_status = "NOT_STARTED"
        run_id = (payload or {}).get("run_id")
        if isinstance(run_id, str) and run_id:
            ing_run = await session.get(IngestionRun, run_id)
            if ing_run is not None:
                ing_run.status = "QUEUED"
                ing_run.current_stage = "RECEIVE"
                ing_run.error_layer = None
                ing_run.error_stage = None
                ing_run.error_code = None
                ing_run.error_message = None
    await touch_source_revision(session, source.id)
    await session.commit()
    await session.refresh(job)
    if requires_maintenance:
        job_queue.begin_source_maintenance(source.id, job.id)
    await _enqueue_persisted_job(job_queue, job.id)
    return job


def _checkpoint_source_id(payload: dict | None) -> str | None:
    checkpoint = (payload or {}).get("process_checkpoint")
    value = checkpoint.get("source_id") if isinstance(checkpoint, dict) else None
    return value.strip() if isinstance(value, str) and value.strip() else None


async def _document_derived_source_ids(
    session: AsyncSession,
    document: Document,
) -> set[str]:
    """Collect every engine article id ever associated with one document."""
    values = {document.sag_source_id} if document.sag_source_id else set()
    jobs = (await session.scalars(select(Job).where(Job.document_id == document.id))).all()
    for candidate in jobs:
        payload = candidate.payload or {}
        checkpoint_id = _checkpoint_source_id(payload)
        if checkpoint_id:
            values.add(checkpoint_id)
        values.update(
            value.strip() for value in payload.get("derived_source_ids", []) if isinstance(value, str) and value.strip()
        )
    return values


def _source_document_filter(source_id: str):
    return (
        Document.source_id == source_id,
        Document.is_active.is_(True),
        Document.status.not_in([DocumentStatus.DELETING, DocumentStatus.DELETE_FAILED]),
    )


def _source_sum_clause(source_id: str, column):
    """Sum chunk and event counts only for READY documents."""
    return (
        select(
            func.coalesce(
                func.sum(
                    case(
                        (Document.status == DocumentStatus.READY, column),
                        else_=0,
                    )
                ),
                0,
            )
        )
        .where(*_source_document_filter(source_id))
        .scalar_subquery()
    )


async def _refresh_source_counts(session: AsyncSession, source: Source) -> None:
    """Atomically recalculate source aggregate counts.

    Deletion tasks and document-level changes can commit concurrently. A single
    database UPDATE avoids lost counts from interleaved SELECT-then-assign.
    """
    document_count = (
        select(func.count(Document.id))
        .where(*_source_document_filter(source.id))
        .scalar_subquery()
    )
    await session.execute(
        update(Source)
        .where(Source.id == source.id)
        .values(
            document_count=document_count,
            chunk_count=_source_sum_clause(source.id, Document.chunk_count),
            event_count=_source_sum_clause(source.id, Document.event_count),
        )
        .execution_options(synchronize_session=False)
    )
    await session.refresh(
        source,
        attribute_names=["document_count", "chunk_count", "event_count"],
    )


async def _commit_document_job_transition(
    session: AsyncSession,
    document: Document,
    job: Job,
    *,
    expected_document_status: DocumentStatus,
    document_values: dict,
    expected_job_status: JobStatus,
    job_values: dict,
) -> bool:
    """Atomically claim one document control transition and its process job."""
    claimed_document = await session.execute(
        update(Document)
        .where(
            Document.id == document.id,
            Document.status == expected_document_status,
        )
        .values(**document_values)
        .execution_options(synchronize_session=False)
    )
    if claimed_document.rowcount != 1:
        await session.rollback()
        return False

    claimed_job = await session.execute(
        update(Job)
        .where(
            Job.id == job.id,
            Job.document_id == document.id,
            Job.type == JobType.PROCESS_DOCUMENT,
            Job.status == expected_job_status,
        )
        .values(**job_values)
        .execution_options(synchronize_session=False)
    )
    if claimed_job.rowcount != 1:
        await session.rollback()
        return False

    await session.commit()
    await session.refresh(document)
    await session.refresh(job)
    return True


async def _raise_document_control_conflict(
    session: AsyncSession,
    document_id: str,
    *,
    action: str,
    fallback: str,
) -> None:
    """Report the winning concurrent transition after a failed control CAS."""
    await session.rollback()
    current = await session.get(Document, document_id, populate_existing=True)
    if current is None:
        raise ConflictError("Document has been deleted, please refresh and retry")
    if current.status in {DocumentStatus.DELETING, DocumentStatus.DELETE_FAILED}:
        raise ConflictError(f"Document is deleting or deletion failed, cannot {action}")
    raise ConflictError(fallback)


async def pause_document(session: AsyncSession, source: Source, document_id: str) -> Job:
    """Cooperative pause: complete and save checkpoint for in-flight chunks, then stop picking up new chunks."""
    document = await get_document(session, source, document_id)
    if document.status in {DocumentStatus.DELETING, DocumentStatus.DELETE_FAILED}:
        raise ConflictError("Document is deleting or deletion failed, cannot stop extraction")
    if document.status not in {
        DocumentStatus.PENDING,
        DocumentStatus.LOADING,
        DocumentStatus.EXTRACTING,
    }:
        raise ConflictError("Extraction task has completed or state has changed, cannot stop")
    document_record_id = document.id
    expected_document_status = document.status
    job = await session.scalar(
        select(Job)
        .where(
            Job.document_id == document_record_id,
            Job.type == JobType.PROCESS_DOCUMENT,
            Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
        )
        .order_by(Job.created_at.desc())
        .limit(1)
    )
    if job is None:
        await _raise_document_control_conflict(
            session,
            document_record_id,
            action="stop extraction",
            fallback="The current document has no stoppable extraction task",
        )

    if job.status == JobStatus.QUEUED:
        if await _commit_document_job_transition(
            session,
            document,
            job,
            expected_document_status=expected_document_status,
            document_values={"status": DocumentStatus.PAUSED},
            expected_job_status=JobStatus.QUEUED,
            job_values={"status": JobStatus.PAUSED},
        ):
            return job
        await _raise_document_control_conflict(
            session,
            document_record_id,
            action="stop extraction",
            fallback="Document or extraction task state has changed, please refresh and retry",
        )

    if job.status != JobStatus.RUNNING:
        raise ConflictError("Extraction task has ended, cannot stop")
    if await _commit_document_job_transition(
        session,
        document,
        job,
        expected_document_status=expected_document_status,
        document_values={"status": DocumentStatus.PAUSING},
        expected_job_status=JobStatus.RUNNING,
        job_values={"status": JobStatus.PAUSED},
    ):
        return job
    await _raise_document_control_conflict(
        session,
        document_record_id,
        action="stop extraction",
        fallback="Document or extraction task state has changed, please refresh and retry",
    )


async def resume_document(
    session: AsyncSession,
    source: Source,
    document_id: str,
    *,
    job_queue: JobQueue,
) -> Job:
    """Re-enqueue paused task as-is; processor will skip chunks already completed in checkpoint."""
    document = await get_document(session, source, document_id)
    if document.status in {DocumentStatus.DELETING, DocumentStatus.DELETE_FAILED}:
        raise ConflictError("Document is deleting or deletion failed, cannot resume")
    if document.status != DocumentStatus.PAUSED:
        raise ConflictError("The current document is not paused, cannot resume")
    document_record_id = document.id
    job = await session.scalar(
        select(Job)
        .where(
            Job.document_id == document_record_id,
            Job.type == JobType.PROCESS_DOCUMENT,
            Job.status == JobStatus.PAUSED,
        )
        .order_by(Job.created_at.desc())
        .limit(1)
    )
    if job is None:
        await _raise_document_control_conflict(
            session,
            document_record_id,
            action="resume",
            fallback="The current document has no resumable paused task",
        )

    payload = dict(job.payload or {})
    checkpoint = payload.get("process_checkpoint")
    is_legacy_checkpoint = (
        isinstance(checkpoint, dict)
        and bool(checkpoint.get("chunk_ids"))
        and not checkpoint.get("chunk_version")
    )
    if is_legacy_checkpoint:
        if not os.path.isfile(document.storage_path):
            raise ConflictError(
                "Legacy extraction checkpoint cannot be restored and original file no longer "
                "exists; please re-upload original file"
            )
        payload.pop("process_checkpoint", None)
    payload.pop("pause_requested", None)
    payload["resume_requested"] = True
    resumed_payload = set_scheduler(
        payload,
        priority=RESUME_PRIORITY,
        blocked_reason=None,
    )
    has_resumable_checkpoint = bool(
        payload.get("process_checkpoint") or payload.get("canonical_checkpoint_stage")
    )
    resumed_status = DocumentStatus.EXTRACTING if has_resumable_checkpoint else DocumentStatus.PENDING
    if not await _commit_document_job_transition(
        session,
        document,
        job,
        expected_document_status=DocumentStatus.PAUSED,
        document_values={
            "status": resumed_status,
            "error": None,
            "error_layer": None,
            "error_stage": None,
        },
        expected_job_status=JobStatus.PAUSED,
        job_values={
            "payload": resumed_payload,
            "status": JobStatus.QUEUED,
            "started_at": None,
            "finished_at": None,
            "error": None,
        },
    ):
        await _raise_document_control_conflict(
            session,
            document_record_id,
            action="resume",
            fallback="Document or extraction task state has changed, please refresh and retry",
        )
    await _enqueue_persisted_job(job_queue, job.id)
    return job


async def delete_document(
    session: AsyncSession,
    source: Source,
    document_id: str,
    *,
    job_queue: JobQueue | None = None,
) -> Job:
    try:
        document = await get_document(session, source, document_id)
    except NotFoundError:
        completed_jobs = await session.scalars(
            select(Job)
            .where(
                Job.source_id == source.id,
                Job.type == JobType.DELETE_DOCUMENT,
                Job.status == JobStatus.SUCCEEDED,
                Job.payload["target_document_id"].as_string() == document_id,
            )
            .order_by(Job.created_at.desc(), Job.id.desc())
            .limit(1)
        )
        completed = completed_jobs.first()
        if completed is None:
            raise
        return completed
    source_record_id = source.id
    derived_source_ids = await _document_derived_source_ids(session, document)
    existing = await session.scalar(
        select(Job)
        .where(
            Job.document_id == document.id,
            Job.type == JobType.DELETE_DOCUMENT,
            Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
        )
        .order_by(Job.created_at.desc())
        .limit(1)
    )
    active_jobs = list(
        (
            await session.scalars(
                select(Job).where(
                    Job.document_id == document.id,
                    Job.type == JobType.PROCESS_DOCUMENT,
                    Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
                )
            )
        ).all()
    )
    active_reprocess_jobs = list(
        (
            await session.scalars(
                select(Job).where(
                    Job.document_id == document.id,
                    Job.type == JobType.REPROCESS_DOCUMENT,
                    Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
                )
            )
        ).all()
    )

    # A document whose processor has never started cannot have written any
    # engine-side records. Delete it inside this request so it is never held up
    # by unrelated long-running ingestion or cleanup work.
    metadata_only = (
        document.status == DocumentStatus.PENDING
        and not document.sag_source_id
        and not active_reprocess_jobs
        and all(
            active_job.status == JobStatus.QUEUED
            and active_job.started_at is None
            and active_job.attempts == 0
            and not _checkpoint_source_id(active_job.payload)
            for active_job in active_jobs
        )
    )
    if metadata_only:
        path = document.storage_path
        target_document_id = document.id
        candidate_job_ids = [candidate.id for candidate in active_jobs]
        if candidate_job_ids:
            fenced_jobs = await session.execute(
                update(Job)
                .where(
                    Job.id.in_(candidate_job_ids),
                    Job.document_id == target_document_id,
                    Job.type == JobType.PROCESS_DOCUMENT,
                    Job.status == JobStatus.QUEUED,
                    Job.started_at.is_(None),
                    Job.attempts == 0,
                )
                .values(status=JobStatus.PAUSED)
                .execution_options(synchronize_session=False)
            )
            if fenced_jobs.rowcount != len(candidate_job_ids):
                # A worker claimed the processor after the optimistic read.
                # Roll back any partial fence and use cooperative deletion.
                await session.rollback()
                completed = await session.scalar(
                    select(Job)
                    .where(
                        Job.source_id == source_record_id,
                        Job.type == JobType.DELETE_DOCUMENT,
                        Job.status == JobStatus.SUCCEEDED,
                        Job.payload["target_document_id"].as_string() == target_document_id,
                    )
                    .order_by(Job.created_at.desc(), Job.id.desc())
                    .limit(1)
                )
                if completed is not None:
                    return completed
                current_source = await session.get(
                    Source,
                    source_record_id,
                    populate_existing=True,
                )
                if current_source is None:
                    raise NotFoundError("Source not found")
                return await delete_document(
                    session,
                    current_source,
                    target_document_id,
                    job_queue=job_queue,
                )
        claimed = await session.execute(
            update(Document)
            .where(
                Document.id == target_document_id,
                Document.status == DocumentStatus.PENDING,
                Document.sag_source_id.is_(None),
            )
            .values(status=DocumentStatus.DELETING, error=None)
            .execution_options(synchronize_session=False)
        )
        if claimed.rowcount != 1:
            await session.rollback()
            completed = await session.scalar(
                select(Job)
                .where(
                    Job.source_id == source_record_id,
                    Job.type == JobType.DELETE_DOCUMENT,
                    Job.status == JobStatus.SUCCEEDED,
                    Job.payload["target_document_id"].as_string() == target_document_id,
                )
                .order_by(Job.created_at.desc(), Job.id.desc())
                .limit(1)
            )
            if completed is not None:
                return completed
            raise ConflictError("Document state has changed, please refresh and retry")
        completed_at = datetime.now(UTC)
        completed = Job(
            type=JobType.DELETE_DOCUMENT,
            source_id=source.id,
            document_id=None,
            status=JobStatus.SUCCEEDED,
            progress=1.0,
            attempts=1,
            payload=set_scheduler(
                {"target_document_id": target_document_id, "cleanup_mode": "metadata_only"},
                priority=DELETE_PRIORITY,
            ),
            started_at=completed_at,
            finished_at=completed_at,
        )
        session.add(completed)
        await session.delete(document)
        await session.flush()
        remaining_path_refs = 0
        if path:
            doc_refs = int(
                await session.scalar(
                    select(func.count()).select_from(Document).where(Document.storage_path == path)
                )
                or 0
            )
            snap_refs = int(
                await session.scalar(
                    select(func.count())
                    .select_from(SourceSnapshot)
                    .where(SourceSnapshot.storage_uri == path)
                )
                or 0
            )
            remaining_path_refs = doc_refs + snap_refs
        await _refresh_source_counts(session, source)
        await touch_source_revision(session, source.id)
        await session.commit()
        await session.refresh(completed)
        if path and remaining_path_refs == 0:
            from sag_api.parsing.service import parsed_sidecar_paths

            for candidate in [path, *parsed_sidecar_paths(path)]:
                try:
                    if os.path.exists(candidate):
                        os.remove(candidate)
                except OSError:
                    pass
        return completed

    if existing is None:
        original_status = document.status
        claimed = await session.execute(
            update(Document)
            .where(Document.id == document.id, Document.status == original_status)
            .values(status=DocumentStatus.DELETING, error=None)
            .execution_options(synchronize_session=False)
        )
        if claimed.rowcount != 1:
            await session.rollback()
            existing = await session.scalar(
                select(Job)
                .where(
                    Job.document_id == document_id,
                    Job.type == JobType.DELETE_DOCUMENT,
                    Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
                )
                .order_by(Job.created_at.desc(), Job.id.desc())
                .limit(1)
            )
            if existing is None:
                raise ConflictError("Document state has changed, please refresh and retry")
            if job_queue is not None:
                job_queue.begin_source_maintenance(source_record_id, existing.id)
                await _enqueue_persisted_job(job_queue, existing.id)
            return existing
        await session.refresh(document)

    for job in active_jobs:
        job.payload = {**(job.payload or {}), "pause_requested": True}
        if job.status == JobStatus.QUEUED:
            job.status = JobStatus.PAUSED
    document.status = DocumentStatus.DELETING
    document.error = None
    if existing is not None:
        await _refresh_source_counts(session, source)
        await session.commit()
        await session.refresh(existing)
        if job_queue is not None:
            job_queue.begin_source_maintenance(source.id, existing.id)
            await _enqueue_persisted_job(job_queue, existing.id)
        return existing

    delete_job = Job(
        type=JobType.DELETE_DOCUMENT,
        source_id=source.id,
        document_id=document.id,
        status=JobStatus.QUEUED,
        payload=set_scheduler(
            {
                "target_document_id": document.id,
                "derived_source_ids": sorted(derived_source_ids),
            },
            priority=DELETE_PRIORITY,
        ),
    )
    session.add(delete_job)
    await _refresh_source_counts(session, source)
    await touch_source_revision(session, source.id)
    await session.commit()
    await session.refresh(delete_job)
    if job_queue is not None:
        job_queue.begin_source_maintenance(source.id, delete_job.id)
        await _enqueue_persisted_job(job_queue, delete_job.id)
    return delete_job


# ============================================================================
# Knowledge Routing RAG: Multi-Tenant Project Document Upload & Versioning
# ============================================================================


def _check_upload_file(filename: str, file_bytes: bytes) -> None:
    """Validate file content, size, and allowed extensions."""
    if not file_bytes:
        raise ValidationError("Uploaded file cannot be empty")
    max_size = getattr(settings, "max_upload_size_bytes", 50 * 1024 * 1024)
    if len(file_bytes) > max_size:
        raise ValidationError(f"File size exceeds maximum allowed ({max_size} bytes)")
    allowed = settings.allowed_upload_exts
    if allowed:
        ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext not in allowed:
            pretty = ", ".join(sorted(e.lstrip(".") for e in allowed))
            raise ValidationError(
                f"Unsupported file extension. Allowed: {pretty}",
                layer=ErrorLayer.CLIENT,
                stage=ErrorStage.UPLOAD,
            )


def _save_snapshot_file(payload_hash: str, original_filename: str, file_bytes: bytes) -> str:
    """Persist raw file bytes to immutable, content-addressed snapshot storage.

    Storage path format: snapshots/{hash[:2]}/{hash}/{safe_name}
    Guarantees that different payloads never share paths and cannot overwrite each other,
    preserving immutability under concurrent version allocation races.
    """
    safe_name = os.path.basename(original_filename) or "source.bin"
    base_dir = Path(settings.effective_data_dir) / "snapshots" / payload_hash[:2] / payload_hash
    base_dir.mkdir(parents=True, exist_ok=True)
    target_path = base_dir / safe_name

    if target_path.exists() and target_path.stat().st_size == len(file_bytes):
        return str(target_path.resolve())

    tmp_path = base_dir / f".tmp_{uuid.uuid4().hex}"
    tmp_path.write_bytes(file_bytes)
    tmp_path.replace(target_path)
    return str(target_path.resolve())


async def _ensure_project_source_mapping(
    session: AsyncSession,
    *,
    source_id: str,
    tenant_id: str,
    project_id: str,
) -> None:
    if not tenant_id or not tenant_id.strip() or not project_id or not project_id.strip():
        return
    mapping_stmt = (
        select(SourceProjectMapping)
        .where(
            SourceProjectMapping.source_id == source_id,
            SourceProjectMapping.state.in_(("PENDING", "CONFIRMED")),
        )
        .limit(1)
    )
    mapping = (await session.execute(mapping_stmt)).scalar_one_or_none()
    now_utc = datetime.now(UTC)
    if mapping is None:
        try:
            async with session.begin_nested():
                session.add(
                    SourceProjectMapping(
                        source_id=source_id,
                        organization_id=tenant_id,
                        project_id=project_id,
                        state="CONFIRMED",
                        mapping_version=1,
                        confirmed_at=now_utc,
                        confirmed_by="system_project_auto_provision",
                        approval_ref=f"auto_project_{project_id}",
                    )
                )
                await session.flush()
        except IntegrityError:
            pass
    elif mapping.state == "PENDING":
        mapping.state = "CONFIRMED"
        mapping.confirmed_at = mapping.confirmed_at or now_utc
        mapping.confirmed_by = mapping.confirmed_by or "system_project_auto_provision"
        mapping.approval_ref = mapping.approval_ref or f"auto_project_{project_id}"
        await session.flush()


async def get_or_create_project_source(
    session: AsyncSession,
    tenant_id: str,
    project_id: str,
) -> Source:
    """Ánh xạ (tenant_id, project_id) thành Source để ingestion worker sử dụng."""
    hash_digest = hashlib.sha256(f"{tenant_id}:{project_id}".encode()).hexdigest()[:32]
    config_id = f"tp_{hash_digest}"
    stmt = select(Source).where(Source.sag_source_config_id == config_id).limit(1)
    source = (await session.execute(stmt)).scalar_one_or_none()
    if source is not None:
        await _ensure_project_source_mapping(
            session, source_id=source.id, tenant_id=tenant_id, project_id=project_id
        )
        return source

    source = Source(
        name=f"Project {project_id}",
        description=f"Auto-provisioned source for project {project_id} (tenant {tenant_id})",
        source_type=SourceType.DOCUMENT,
        connector_kind=ConnectorKind.FILE_UPLOAD,
        sag_source_config_id=config_id,
        config={
            "is_project_source": True,
            "tenant_id": tenant_id,
            "project_id": project_id,
        },
    )
    try:
        async with session.begin_nested():
            session.add(source)
            await session.flush()
    except IntegrityError:
        source = (await session.execute(stmt)).scalar_one_or_none()
        if source is None:
            raise
    await _ensure_project_source_mapping(
        session, source_id=source.id, tenant_id=tenant_id, project_id=project_id
    )
    return source


async def handle_document_upload(
    session: AsyncSession,
    *,
    tenant_id: str,
    project_id: str,
    owner_id: str,
    security_partition_id: str,
    client_token: str,
    file_bytes: bytes,
    original_filename: str,
    content_type: str = "application/octet-stream",
    content_hash: str | None = None,
    logical_source_id: str | None = None,
    source_published_at: datetime | None = None,
    research_task_id: str | None = None,
    job_queue: JobQueue | None = None,
    source_id: str | None = None,
) -> DocumentUploadResponse:
    """Process an upload request with strict ACL partition, idempotency, and versioning.

    Handles concurrent retries with race-condition recovery.
    """
    if not security_partition_id or not security_partition_id.strip():
        raise ValidationError(
            "X-Continuum-Security-Partition header is required to enforce data isolation"
        )
    if not client_token or not client_token.strip():
        raise ValidationError("Idempotency-Key header is required for upload")

    _check_upload_file(original_filename, file_bytes)

    payload_hash = compute_payload_hash(file_bytes)
    idempotency_key = normalize_idempotency_key(client_token)
    logical_id = (logical_source_id or original_filename).strip()

    # Step 1: Pre-allocation lookup by (tenant_id, project_id, idempotency_key)
    existing_run = await _find_upload_run(
        session, tenant_id=tenant_id, project_id=project_id, idempotency_key=idempotency_key
    )

    if existing_run:
        # Check payload match
        if existing_run.payload_hash != payload_hash:
            raise ConflictError(
                f"Idempotency key is already bound to a different file payload hash. "
                f"Reusing an idempotency key across different file contents is forbidden. "
                f"(incoming={payload_hash}, existing={existing_run.payload_hash})",
                layer=ErrorLayer.CLIENT,
                stage=ErrorStage.UPLOAD,
                code="IDEMPOTENCY_PAYLOAD_MISMATCH",
                retryable=False,
            )
        doc_ver = await session.get(DocumentVersion, existing_run.document_version_id)
        if doc_ver:
            bound_partition = (doc_ver.metadata_json or {}).get("security_partition_id")
            if bound_partition != security_partition_id:
                raise ConflictError(
                    "Idempotency key is already bound to a different security partition",
                    layer=ErrorLayer.CLIENT,
                    stage=ErrorStage.UPLOAD,
                    code="IDEMPOTENCY_PARTITION_MISMATCH",
                    retryable=False,
                )
            bound_doc = await session.get(Document, doc_ver.document_id)
            if bound_doc and bound_doc.logical_source_id != logical_id:
                raise ConflictError(
                    f"Idempotency key is already bound to logical source '{bound_doc.logical_source_id}'. "
                    f"Reusing an idempotency key across different logical sources is forbidden "
                    f"(incoming='{logical_id}', existing='{bound_doc.logical_source_id}')",
                    layer=ErrorLayer.CLIENT,
                    stage=ErrorStage.UPLOAD,
                    code="IDEMPOTENCY_IDENTITY_MISMATCH",
                    retryable=False,
                )
            is_revoked_replay = (
                doc_ver.status == "REVOKED"
                or doc_ver.search_status == "REVOKED"
                or (bound_doc is not None and not bound_doc.is_active)
            )
        else:
            is_revoked_replay = False
        if is_revoked_replay:
            idempotency_key = normalize_idempotency_key(
                f"{client_token}:revoked_resync:{uuid.uuid4().hex}"
            )
        else:
            return DocumentUploadResponse(
                document_id=doc_ver.document_id if doc_ver else "",
                version_no=doc_ver.version_no if doc_ver else 1,
                version_id=existing_run.document_version_id,
                run_id=existing_run.id,
                file_hash=payload_hash,
                status=doc_ver.status if doc_ver else "RECEIVED",
                search_status=doc_ver.search_status if doc_ver else "PENDING",
                knowledge_status=doc_ver.knowledge_status if doc_ver else "NOT_STARTED",
                is_duplicate=True,
            )

    if not source_id:
        proj_source = await get_or_create_project_source(session, tenant_id, project_id)
        source_id = proj_source.id

    # Step 3: Attempt creation in transaction with serialization and retry on race conditions
    max_retries = 3
    duplicate_response: DocumentUploadResponse | None = None
    for attempt in range(max_retries):
        try:
            async with session.begin_nested():
                # Check or create logical document with row lock
                doc_stmt = (
                    select(Document)
                    .where(
                        Document.tenant_id == tenant_id,
                        Document.project_id == project_id,
                        Document.logical_source_id == logical_id,
                    )
                    .with_for_update()
                )
                doc = (await session.execute(doc_stmt)).scalar_one_or_none()

                now_utc = datetime.now(UTC)
                is_new_doc = False
                if not doc:
                    is_new_doc = True
                    storage_path = _save_snapshot_file(payload_hash, original_filename, file_bytes)
                    doc_id = generate_doc_id(tenant_id, project_id, logical_id)
                    doc = Document(
                        id=doc_id,
                        tenant_id=tenant_id,
                        project_id=project_id,
                        owner_id=owner_id,
                        logical_source_id=logical_id,
                        source_id=source_id,
                        filename=original_filename,
                        content_type=content_type,
                        size_bytes=len(file_bytes),
                        storage_path=storage_path,
                        status=DocumentStatus.PENDING,
                    )
                    session.add(doc)
                    await session.flush()
                    version_no = 1
                    supersedes_id = None
                else:
                    # Find latest version for this document under row lock
                    v_stmt = (
                        select(DocumentVersion)
                        .where(DocumentVersion.document_id == doc.id)
                        .order_by(DocumentVersion.version_no.desc())
                        .limit(1)
                        .with_for_update()
                    )
                    latest_ver = (await session.execute(v_stmt)).scalar_one_or_none()
                    if latest_ver is None or (
                        (latest_ver.metadata_json or {}).get("security_partition_id") != security_partition_id
                    ):
                        raise ConflictError(
                            "Logical document already belongs to a different security partition",
                            layer=ErrorLayer.CLIENT,
                            stage=ErrorStage.UPLOAD,
                            code="DOCUMENT_PARTITION_MISMATCH",
                            retryable=False,
                        )
                    if (
                        latest_ver
                        and latest_ver.file_hash == payload_hash
                        and latest_ver.status != "REVOKED"
                        and doc.is_active
                    ):
                        # Exact duplicate: Cùng hash + cùng source identity
                        # Duplicate policy: Do NOT parse again or schedule pipeline!
                        run_stmt = (
                            select(IngestionRun)
                            .where(IngestionRun.document_version_id == latest_ver.id)
                            .order_by(IngestionRun.created_at.desc())
                            .limit(1)
                        )
                        existing_run = (await session.execute(run_stmt)).scalar_one_or_none()
                        if existing_run is None:
                            existing_run = IngestionRun(
                                id=str(uuid.uuid4()),
                                tenant_id=tenant_id,
                                project_id=project_id,
                                document_version_id=latest_ver.id,
                                idempotency_key=idempotency_key,
                                payload_hash=payload_hash,
                                content_hash=content_hash or payload_hash,
                                current_stage="DUPLICATE",
                                status="SUCCEEDED",
                                completed_at=now_utc,
                            )
                            session.add(existing_run)
                        else:
                            session.add(
                                UploadIdempotencyKey(
                                    tenant_id=tenant_id,
                                    project_id=project_id,
                                    idempotency_key=idempotency_key,
                                    run_id=existing_run.id,
                                )
                            )
                        await session.flush()
                        duplicate_response = DocumentUploadResponse(
                            document_id=doc.id,
                            version_no=latest_ver.version_no,
                            version_id=latest_ver.id,
                            run_id=existing_run.id,
                            file_hash=payload_hash,
                            status=latest_ver.status,
                            search_status=latest_ver.search_status,
                            knowledge_status=latest_ver.knowledge_status,
                            is_duplicate=True,
                        )
                        break
                    elif latest_ver:
                        # Đóng khoảng hiệu lực của phiên bản cũ tại thời điểm phiên bản mới bắt đầu (Comment #13)
                        latest_ver.valid_to = now_utc
                        version_no = latest_ver.version_no + 1
                        supersedes_id = latest_ver.id
                    else:
                        version_no = 1
                        supersedes_id = None
                    storage_path = _save_snapshot_file(payload_hash, original_filename, file_bytes)
                    doc.storage_path = storage_path
                    doc.is_active = True
                    doc.status = DocumentStatus.PENDING
                    doc.progress = 0
                    doc.filename = original_filename
                    doc.content_type = content_type
                    doc.size_bytes = len(file_bytes)
                    doc.error = None
                    doc.error_layer = None
                    doc.error_stage = None
                    if source_id and not doc.source_id:
                        doc.source_id = source_id

                version_id = generate_version_id(doc.id, version_no)

                existing_ver = await session.get(DocumentVersion, version_id)
                if not existing_ver:
                    doc_version = DocumentVersion(
                        id=version_id,
                        document_id=doc.id,
                        version_no=version_no,
                        file_hash=payload_hash,
                        supersedes_id=supersedes_id,
                        source_published_at=source_published_at,
                        valid_from=now_utc,
                        valid_to=datetime(9999, 12, 31, 23, 59, 59, tzinfo=UTC),
                        status="RECEIVED",
                        search_status="PENDING",
                        knowledge_status="NOT_STARTED",
                        metadata_json={"security_partition_id": security_partition_id},
                    )
                    session.add(doc_version)
                    await session.flush()

                    snapshot = SourceSnapshot(
                        id=str(uuid.uuid4()),
                        document_version_id=version_id,
                        storage_uri=storage_path,
                        original_filename=original_filename,
                        mime_type=content_type,
                        byte_size=len(file_bytes),
                        checksum_sha256=payload_hash,
                    )
                    session.add(snapshot)
                else:
                    doc_version = existing_ver

                run_id = str(uuid.uuid4())
                initial_status = "QUEUED" if job_queue is not None else "PENDING_DISPATCH"
                run = IngestionRun(
                    id=run_id,
                    tenant_id=tenant_id,
                    project_id=project_id,
                    document_version_id=doc_version.id,
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                    content_hash=content_hash or payload_hash,
                    research_task_id=research_task_id,
                    current_stage="RECEIVE",
                    status=initial_status,
                )
                session.add(run)

                stage_run = StageRun(
                    id=str(uuid.uuid4()),
                    run_id=run_id,
                    stage="receive",
                    status="SUCCESS",
                    duration_ms=10.0,
                    metrics_json={"byte_size": len(file_bytes)},
                )
                session.add(stage_run)

                # Create durable Job record for background processing with immutable snapshot storage_path
                job = Job(
                    type=JobType.PROCESS_DOCUMENT,
                    source_id=doc.source_id,
                    document_id=doc.id,
                    status=JobStatus.QUEUED,
                    payload={
                        "run_id": run_id,
                        "document_version_id": doc_version.id,
                        "project_id": project_id,
                        "tenant_id": tenant_id,
                        "storage_path": storage_path,
                    },
                )
                session.add(job)

                if source_id and is_new_doc:
                    await session.execute(
                        update(Source)
                        .where(Source.id == source_id)
                        .values(document_count=Source.document_count + 1)
                    )
                    await touch_source_revision(session, source_id)

                await session.flush()
                break
        except IntegrityError:
            # Race condition recovery: concurrent insert with same (tenant_id, project_id, idempotency_key)
            existing = await _find_upload_run(
                session, tenant_id=tenant_id, project_id=project_id, idempotency_key=idempotency_key
            )
            if existing:
                if existing.payload_hash != payload_hash:
                    raise ConflictError(
                        f"Idempotency key is already bound to a different file payload hash. "
                        f"Reusing an idempotency key across different file contents is forbidden. "
                        f"(incoming={payload_hash}, existing={existing.payload_hash})",
                        layer=ErrorLayer.CLIENT,
                        stage=ErrorStage.UPLOAD,
                        code="IDEMPOTENCY_PAYLOAD_MISMATCH",
                        retryable=False,
                    ) from None
                doc_ver = await session.get(DocumentVersion, existing.document_version_id)
                if doc_ver:
                    if (doc_ver.metadata_json or {}).get("security_partition_id") != security_partition_id:
                        raise ConflictError(
                            "Idempotency key is already bound to a different security partition",
                            layer=ErrorLayer.CLIENT,
                            stage=ErrorStage.UPLOAD,
                            code="IDEMPOTENCY_PARTITION_MISMATCH",
                            retryable=False,
                        ) from None
                    bound_doc = await session.get(Document, doc_ver.document_id)
                    if bound_doc and bound_doc.logical_source_id != logical_id:
                        raise ConflictError(
                            f"Idempotency key is already bound to logical source '{bound_doc.logical_source_id}'. "
                            f"Reusing an idempotency key across different logical sources is forbidden "
                            f"(incoming='{logical_id}', existing='{bound_doc.logical_source_id}')",
                            layer=ErrorLayer.CLIENT,
                            stage=ErrorStage.UPLOAD,
                            code="IDEMPOTENCY_IDENTITY_MISMATCH",
                            retryable=False,
                        ) from None
                return DocumentUploadResponse(
                    document_id=doc_ver.document_id if doc_ver else "",
                    version_no=doc_ver.version_no if doc_ver else 1,
                    version_id=existing.document_version_id,
                    run_id=existing.id,
                    file_hash=payload_hash,
                    status=doc_ver.status if doc_ver else "RECEIVED",
                    search_status=doc_ver.search_status if doc_ver else "PENDING",
                    knowledge_status=doc_ver.knowledge_status if doc_ver else "NOT_STARTED",
                    is_duplicate=True,
                )
            if attempt == max_retries - 1:
                raise

    await session.commit()

    if duplicate_response is not None:
        return duplicate_response

    # Durable enqueue after transaction commit
    if job_queue is not None:
        try:
            await _enqueue_persisted_job(job_queue, job.id)
        except Exception as exc:
            log.warning("Failed to enqueue job %s to queue: %s", job.id, exc)

    return DocumentUploadResponse(
        document_id=doc.id,
        version_no=doc_version.version_no,
        version_id=doc_version.id,
        run_id=run.id,
        file_hash=payload_hash,
        status=doc_version.status,
        search_status=doc_version.search_status,
        knowledge_status=doc_version.knowledge_status,
        is_duplicate=False,
    )


def _check_object_ingest_metadata(filename: str, expected_size_bytes: int) -> None:
    if expected_size_bytes <= 0:
        raise ValidationError("Object size must be positive")
    max_size = getattr(settings, "max_upload_size_bytes", None) or (settings.max_upload_mb * 1024 * 1024)
    if expected_size_bytes > max_size:
        raise ValidationError(
            f"File size exceeds maximum allowed ({max_size} bytes)",
            layer=ErrorLayer.CLIENT,
            stage=ErrorStage.UPLOAD,
        )
    allowed = settings.allowed_upload_exts
    if allowed:
        ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext not in allowed:
            pretty = ", ".join(sorted(e.lstrip(".") for e in allowed))
            raise ValidationError(
                f"Unsupported file extension. Allowed: {pretty}",
                layer=ErrorLayer.CLIENT,
                stage=ErrorStage.UPLOAD,
            )


async def handle_document_object_ingest(
    session: AsyncSession,
    *,
    tenant_id: str,
    project_id: str,
    owner_id: str,
    security_partition_id: str,
    client_token: str,
    source_url: str,
    original_filename: str,
    expected_sha256: str,
    expected_size_bytes: int,
    content_type: str = "application/octet-stream",
    logical_source_id: str | None = None,
    source_published_at: datetime | None = None,
    job_queue: JobQueue | None = None,
    source_id: str | None = None,
) -> DocumentUploadResponse:
    """Zero-buffer control-plane registration for an object already uploaded to R2.

    Validates ACL partition, idempotency, extension, size budget, and R2 host allowlist,
    then persists Document/DocumentVersion/IngestionRun/Job so the background worker
    streams the object in 64KB chunks directly to disk.
    """
    from sag_api.parsing.remote_object import (
        resolve_snapshot_target_path,
        validate_remote_object_url,
    )

    if not security_partition_id or not security_partition_id.strip():
        raise ValidationError(
            "X-Continuum-Security-Partition header is required to enforce data isolation"
        )
    if not client_token or not client_token.strip():
        raise ValidationError("Idempotency-Key header is required for object ingest")

    safe_filename = os.path.basename(original_filename.replace("\\", "/")) or "upload"
    _check_object_ingest_metadata(safe_filename, expected_size_bytes)
    validate_remote_object_url(source_url, settings)

    payload_hash = expected_sha256.strip().lower()
    if len(payload_hash) != 64:
        raise ValidationError(
            "expected_sha256 must be a 64-character hex SHA-256 digest",
            layer=ErrorLayer.CLIENT,
            stage=ErrorStage.UPLOAD,
        )
    idempotency_key = normalize_idempotency_key(client_token)
    logical_id = (logical_source_id or safe_filename).strip()

    existing_run = await _find_upload_run(
        session, tenant_id=tenant_id, project_id=project_id, idempotency_key=idempotency_key
    )
    if existing_run:
        if existing_run.payload_hash != payload_hash:
            raise ConflictError(
                f"Idempotency key is already bound to a different file payload hash. "
                f"(incoming={payload_hash}, existing={existing_run.payload_hash})",
                layer=ErrorLayer.CLIENT,
                stage=ErrorStage.UPLOAD,
                code="IDEMPOTENCY_PAYLOAD_MISMATCH",
                retryable=False,
            )
        doc_ver = await session.get(DocumentVersion, existing_run.document_version_id)
        if doc_ver:
            bound_partition = (doc_ver.metadata_json or {}).get("security_partition_id")
            if bound_partition != security_partition_id:
                raise ConflictError(
                    "Idempotency key is already bound to a different security partition",
                    layer=ErrorLayer.CLIENT,
                    stage=ErrorStage.UPLOAD,
                    code="IDEMPOTENCY_PARTITION_MISMATCH",
                    retryable=False,
                )
            bound_doc = await session.get(Document, doc_ver.document_id)
            if bound_doc and bound_doc.logical_source_id != logical_id:
                raise ConflictError(
                    f"Idempotency key is already bound to logical source '{bound_doc.logical_source_id}'.",
                    layer=ErrorLayer.CLIENT,
                    stage=ErrorStage.UPLOAD,
                    code="IDEMPOTENCY_IDENTITY_MISMATCH",
                    retryable=False,
                )
            is_revoked_replay = (
                doc_ver.status == "REVOKED"
                or doc_ver.search_status == "REVOKED"
                or (bound_doc is not None and not bound_doc.is_active)
            )
        else:
            is_revoked_replay = False
        if is_revoked_replay:
            idempotency_key = normalize_idempotency_key(
                f"{client_token}:revoked_resync:{uuid.uuid4().hex}"
            )
        else:
            return DocumentUploadResponse(
                document_id=doc_ver.document_id if doc_ver else "",
                version_no=doc_ver.version_no if doc_ver else 1,
                version_id=existing_run.document_version_id,
                run_id=existing_run.id,
                file_hash=payload_hash,
                status=doc_ver.status if doc_ver else "RECEIVED",
                search_status=doc_ver.search_status if doc_ver else "PENDING",
                knowledge_status=doc_ver.knowledge_status if doc_ver else "NOT_STARTED",
                is_duplicate=True,
            )

    if not source_id:
        proj_source = await get_or_create_project_source(session, tenant_id, project_id)
        source_id = proj_source.id

    storage_path = resolve_snapshot_target_path(
        settings.effective_data_dir, payload_hash, safe_filename
    )

    max_retries = 3
    duplicate_response: DocumentUploadResponse | None = None
    for attempt in range(max_retries):
        try:
            async with session.begin_nested():
                doc_stmt = (
                    select(Document)
                    .where(
                        Document.tenant_id == tenant_id,
                        Document.project_id == project_id,
                        Document.logical_source_id == logical_id,
                    )
                    .with_for_update()
                )
                doc = (await session.execute(doc_stmt)).scalar_one_or_none()

                now_utc = datetime.now(UTC)
                is_new_doc = False
                if not doc:
                    is_new_doc = True
                    doc_id = generate_doc_id(tenant_id, project_id, logical_id)
                    doc = Document(
                        id=doc_id,
                        tenant_id=tenant_id,
                        project_id=project_id,
                        owner_id=owner_id,
                        logical_source_id=logical_id,
                        source_id=source_id,
                        filename=safe_filename,
                        content_type=content_type,
                        size_bytes=expected_size_bytes,
                        storage_path=storage_path,
                        status=DocumentStatus.PENDING,
                    )
                    session.add(doc)
                    await session.flush()
                    version_no = 1
                    supersedes_id = None
                else:
                    v_stmt = (
                        select(DocumentVersion)
                        .where(DocumentVersion.document_id == doc.id)
                        .order_by(DocumentVersion.version_no.desc())
                        .limit(1)
                        .with_for_update()
                    )
                    latest_ver = (await session.execute(v_stmt)).scalar_one_or_none()
                    if latest_ver is None or (
                        (latest_ver.metadata_json or {}).get("security_partition_id")
                        != security_partition_id
                    ):
                        raise ConflictError(
                            "Logical document already belongs to a different security partition",
                            layer=ErrorLayer.CLIENT,
                            stage=ErrorStage.UPLOAD,
                            code="DOCUMENT_PARTITION_MISMATCH",
                            retryable=False,
                        )
                    if (
                        latest_ver
                        and latest_ver.file_hash == payload_hash
                        and latest_ver.status != "REVOKED"
                        and doc.is_active
                    ):
                        run_stmt = (
                            select(IngestionRun)
                            .where(IngestionRun.document_version_id == latest_ver.id)
                            .order_by(IngestionRun.created_at.desc())
                            .limit(1)
                        )
                        existing_run = (await session.execute(run_stmt)).scalar_one_or_none()
                        if existing_run is None:
                            existing_run = IngestionRun(
                                id=str(uuid.uuid4()),
                                tenant_id=tenant_id,
                                project_id=project_id,
                                document_version_id=latest_ver.id,
                                idempotency_key=idempotency_key,
                                payload_hash=payload_hash,
                                content_hash=payload_hash,
                                current_stage="DUPLICATE",
                                status="SUCCEEDED",
                                completed_at=now_utc,
                            )
                            session.add(existing_run)
                        else:
                            session.add(
                                UploadIdempotencyKey(
                                    tenant_id=tenant_id,
                                    project_id=project_id,
                                    idempotency_key=idempotency_key,
                                    run_id=existing_run.id,
                                )
                            )
                        await session.flush()
                        duplicate_response = DocumentUploadResponse(
                            document_id=doc.id,
                            version_no=latest_ver.version_no,
                            version_id=latest_ver.id,
                            run_id=existing_run.id,
                            file_hash=payload_hash,
                            status=latest_ver.status,
                            search_status=latest_ver.search_status,
                            knowledge_status=latest_ver.knowledge_status,
                            is_duplicate=True,
                        )
                        break
                    elif latest_ver:
                        latest_ver.valid_to = now_utc
                        version_no = latest_ver.version_no + 1
                        supersedes_id = latest_ver.id
                    else:
                        version_no = 1
                        supersedes_id = None
                    doc.storage_path = storage_path
                    doc.is_active = True
                    doc.status = DocumentStatus.PENDING
                    doc.progress = 0
                    doc.filename = safe_filename
                    doc.content_type = content_type
                    doc.size_bytes = expected_size_bytes
                    doc.error = None
                    doc.error_layer = None
                    doc.error_stage = None
                    if source_id and not doc.source_id:
                        doc.source_id = source_id

                version_id = generate_version_id(doc.id, version_no)
                existing_ver = await session.get(DocumentVersion, version_id)
                if not existing_ver:
                    doc_version = DocumentVersion(
                        id=version_id,
                        document_id=doc.id,
                        version_no=version_no,
                        file_hash=payload_hash,
                        supersedes_id=supersedes_id,
                        source_published_at=source_published_at,
                        valid_from=now_utc,
                        valid_to=datetime(9999, 12, 31, 23, 59, 59, tzinfo=UTC),
                        status="RECEIVED",
                        search_status="PENDING",
                        knowledge_status="NOT_STARTED",
                        metadata_json={
                            "security_partition_id": security_partition_id,
                            "remote_object_sourced": True,
                        },
                    )
                    session.add(doc_version)
                    await session.flush()

                    snapshot = SourceSnapshot(
                        id=str(uuid.uuid4()),
                        document_version_id=version_id,
                        storage_uri=storage_path,
                        original_filename=safe_filename,
                        mime_type=content_type,
                        byte_size=expected_size_bytes,
                        checksum_sha256=payload_hash,
                    )
                    session.add(snapshot)
                else:
                    doc_version = existing_ver

                run_id = str(uuid.uuid4())
                initial_status = "QUEUED" if job_queue is not None else "PENDING_DISPATCH"
                run = IngestionRun(
                    id=run_id,
                    tenant_id=tenant_id,
                    project_id=project_id,
                    document_version_id=doc_version.id,
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                    content_hash=payload_hash,
                    current_stage="RECEIVE",
                    status=initial_status,
                )
                session.add(run)

                job = Job(
                    type=JobType.PROCESS_DOCUMENT,
                    source_id=doc.source_id,
                    document_id=doc.id,
                    status=JobStatus.QUEUED,
                    payload={
                        "run_id": run_id,
                        "document_version_id": doc_version.id,
                        "project_id": project_id,
                        "tenant_id": tenant_id,
                        "storage_path": storage_path,
                        "remote_object_sourced": True,
                        "remote_source_url": source_url,
                        "expected_sha256": payload_hash,
                        "expected_size_bytes": expected_size_bytes,
                        "original_filename": safe_filename,
                    },
                )
                session.add(job)

                if source_id and is_new_doc:
                    await session.execute(
                        update(Source)
                        .where(Source.id == source_id)
                        .values(document_count=Source.document_count + 1)
                    )
                    await touch_source_revision(session, source_id)

                await session.flush()
                break
        except IntegrityError:
            existing = await _find_upload_run(
                session, tenant_id=tenant_id, project_id=project_id, idempotency_key=idempotency_key
            )
            if existing:
                if existing.payload_hash != payload_hash:
                    raise ConflictError(
                        "Idempotency key is already bound to a different file payload hash.",
                        layer=ErrorLayer.CLIENT,
                        stage=ErrorStage.UPLOAD,
                        code="IDEMPOTENCY_PAYLOAD_MISMATCH",
                        retryable=False,
                    ) from None
                doc_ver = await session.get(DocumentVersion, existing.document_version_id)
                if doc_ver:
                    if (doc_ver.metadata_json or {}).get("security_partition_id") != security_partition_id:
                        raise ConflictError(
                            "Idempotency key is already bound to a different security partition",
                            layer=ErrorLayer.CLIENT,
                            stage=ErrorStage.UPLOAD,
                            code="IDEMPOTENCY_PARTITION_MISMATCH",
                            retryable=False,
                        ) from None
                    bound_doc = await session.get(Document, doc_ver.document_id)
                    if bound_doc and bound_doc.logical_source_id != logical_id:
                        raise ConflictError(
                            f"Idempotency key is already bound to logical source '{bound_doc.logical_source_id}'.",
                            layer=ErrorLayer.CLIENT,
                            stage=ErrorStage.UPLOAD,
                            code="IDEMPOTENCY_IDENTITY_MISMATCH",
                            retryable=False,
                        ) from None
                return DocumentUploadResponse(
                    document_id=doc_ver.document_id if doc_ver else "",
                    version_no=doc_ver.version_no if doc_ver else 1,
                    version_id=existing.document_version_id,
                    run_id=existing.id,
                    file_hash=payload_hash,
                    status=doc_ver.status if doc_ver else "RECEIVED",
                    search_status=doc_ver.search_status if doc_ver else "PENDING",
                    knowledge_status=doc_ver.knowledge_status if doc_ver else "NOT_STARTED",
                    is_duplicate=True,
                )
            if attempt == max_retries - 1:
                raise

    await session.commit()

    if duplicate_response is not None:
        return duplicate_response

    if job_queue is not None:
        try:
            await _enqueue_persisted_job(job_queue, job.id)
        except Exception as exc:
            log.warning("Failed to enqueue object ingest job %s to queue: %s", job.id, exc)

    return DocumentUploadResponse(
        document_id=doc.id,
        version_no=doc_version.version_no,
        version_id=doc_version.id,
        run_id=run.id,
        file_hash=payload_hash,
        status=doc_version.status,
        search_status=doc_version.search_status,
        knowledge_status=doc_version.knowledge_status,
        is_duplicate=False,
    )


async def get_document_version_status(
    session: AsyncSession,
    *,
    project_id: str,
    document_id: str,
    version_no: int,
    principal: VerifiedPrincipal | None = None,
) -> DocumentVersionStatusResponse:
    """Retrieve fine-grained readiness and stage execution progress for a document version."""
    stmt = (
        select(DocumentVersion, Document)
        .join(Document, Document.id == DocumentVersion.document_id)
        .where(
            Document.project_id == project_id,
            DocumentVersion.document_id == document_id,
            DocumentVersion.version_no == version_no,
        )
    )
    result = (await session.execute(stmt)).first()
    if not result:
        raise NotFoundError(
            f"Document version {document_id}/v{version_no} not found in project {project_id}"
        )
    doc_ver, doc = result

    # Kiểm tra ủy quyền tenant và security partition (Fail-closed)
    if principal is not None:
        if not principal.is_service:
            if doc.tenant_id != principal.tenant_id:
                raise ForbiddenError(
                    f"Principal '{principal.user_id}' belongs to tenant '{principal.tenant_id}' "
                    f"but document belongs to tenant '{doc.tenant_id}'"
                )
            partition = (doc_ver.metadata_json or {}).get("security_partition_id")
            if not partition or not principal.has_partition_access(partition):
                raise ForbiddenError(
                    f"Principal '{principal.user_id}' is not authorized to access security partition "
                    f"'{partition or 'NONE'}'"
                )

    run_stmt = (
        select(IngestionRun)
        .where(IngestionRun.document_version_id == doc_ver.id)
        .order_by(IngestionRun.created_at.desc())
        .limit(1)
    )
    latest_run = (await session.execute(run_stmt)).scalar_one_or_none()

    stage_progress: dict[str, StageProgressItem] = {}
    err_info = None
    current_stage = "UNKNOWN"

    if latest_run:
        current_stage = latest_run.current_stage
        sr_stmt = (
            select(StageRun)
            .where(StageRun.run_id == latest_run.id)
            .order_by(StageRun.created_at.asc())
        )
        stages = (await session.execute(sr_stmt)).scalars().all()
        for s in stages:
            stage_progress[s.stage] = StageProgressItem(
                status=s.status, duration_ms=s.duration_ms
            )
        if latest_run.error_code or latest_run.error_message:
            code = latest_run.error_code or (
                f"{latest_run.error_layer or 'API'}_{latest_run.error_stage or 'INGEST'}_FAILED"
            )
            err_info = {
                "layer": latest_run.error_layer or "API",
                "stage": latest_run.error_stage or "INGEST",
                "code": code,
                "message": latest_run.error_message,
            }

    doc_status_str = doc.status.value if hasattr(doc.status, "value") else str(doc.status)
    is_reprocessing = (
        doc.status in (DocumentStatus.PENDING, DocumentStatus.LOADING, DocumentStatus.EXTRACTING)
        and latest_run is not None
        and latest_run.status == "SUCCEEDED"
    )
    raw_progress = int(doc.progress or 0)
    if doc.status == DocumentStatus.READY:
        computed_progress = 100
    elif (
        raw_progress == 0
        and doc.status in (DocumentStatus.PENDING, DocumentStatus.LOADING)
        and not is_reprocessing
        and latest_run is not None
        and latest_run.status in ("QUEUED", "PENDING_DISPATCH", "RUNNING")
    ):
        computed_progress = 5
    else:
        computed_progress = raw_progress
    if err_info is None and doc.error:
        err_info = {
            "layer": doc.error_layer or "API",
            "stage": doc.error_stage or "INGEST",
            "code": f"{doc.error_layer or 'API'}_{doc.error_stage or 'INGEST'}_FAILED",
            "message": doc.error,
        }
    effective_status = doc_ver.status
    effective_search_status = doc_ver.search_status
    effective_knowledge_status = doc_ver.knowledge_status
    effective_search_ready = (
        doc_ver.search_status == "SEARCH_READY" and doc_ver.search_ready_at is not None
    )
    if is_reprocessing:
        current_stage = "RECEIVE"
        effective_status = "RECEIVED"
        effective_search_status = "PENDING"
        effective_knowledge_status = "NOT_STARTED"
        effective_search_ready = False
        stage_progress = {}
    if doc.status in (DocumentStatus.PAUSED, DocumentStatus.PAUSING) and current_stage not in (
        "PAUSED",
        "PAUSING",
    ):
        current_stage = "PAUSED" if doc.status == DocumentStatus.PAUSED else "PAUSING"

    return DocumentVersionStatusResponse(
        document_id=doc_ver.document_id,
        version_no=doc_ver.version_no,
        status=effective_status,
        search_status=effective_search_status,
        knowledge_status=effective_knowledge_status,
        search_ready=effective_search_ready,
        knowledge_ready=(
            not is_reprocessing
            and effective_knowledge_status in ("DATA_READY", "KNOWLEDGE_READY", "READY")
        ),
        current_stage=current_stage,
        stage_progress=stage_progress,
        error=err_info,
        progress=computed_progress,
        document_status=doc_status_str,
        parser_provider=doc.parser_provider,
        parser_status=doc.parser_status,
        chunk_count=int(doc.chunk_count or 0),
        event_count=int(doc.event_count or 0),
        token_usage=int(doc.token_usage or 0),
    )


async def get_project_document_readiness_batch(
    session: AsyncSession,
    *,
    project_id: str,
    documents: list[ProjectDocumentVersionRef],
    principal: VerifiedPrincipal,
) -> ProjectDocumentReadinessBatchResponse:
    """Read search readiness for exact Project versions within confirmed SAG scope."""
    if (
        not principal.tenant_id
        or not principal.has_project_access(project_id)
        or not principal.allowed_partitions
    ):
        raise ForbiddenError("A signed tenant, Project, and partition scope are required")

    refs = [(item.document_id, item.version_no) for item in documents]
    if not refs or len(refs) > 100 or len(refs) != len(set(refs)):
        raise ValidationError("Provide 1 to 100 distinct document version references")

    now = datetime.now(UTC)
    partition = DocumentVersion.metadata_json["security_partition_id"].as_string()
    rows = await session.execute(
        select(DocumentVersion, Document)
        .join(Document, Document.id == DocumentVersion.document_id)
        .join(SourceProjectMapping, SourceProjectMapping.source_id == Document.source_id)
        .where(
            tuple_(DocumentVersion.document_id, DocumentVersion.version_no).in_(refs),
            Document.tenant_id == principal.tenant_id,
            Document.project_id == project_id,
            Document.is_active.is_(True),
            Document.status.not_in([DocumentStatus.DELETING, DocumentStatus.DELETE_FAILED]),
            SourceProjectMapping.organization_id == principal.tenant_id,
            SourceProjectMapping.project_id == project_id,
            SourceProjectMapping.state == "CONFIRMED",
            partition.in_(principal.allowed_partitions),
            DocumentVersion.valid_to > now,
        )
    )
    visible_by_key: dict[tuple[str, int], tuple[DocumentVersion, Document]] = {
        (version.document_id, version.version_no): (version, document)
        for version, document in rows.all()
    }

    items: list[ProjectDocumentReadinessItem] = []
    for document_id, version_no in refs:
        match = visible_by_key.get((document_id, version_no))
        if match is None:
            items.append(
                ProjectDocumentReadinessItem(
                    document_id=document_id,
                    version_no=version_no,
                    found=False,
                    status="NOT_FOUND",
                    search_status="NOT_FOUND",
                    search_ready=False,
                    document_status=None,
                    chunk_count=0,
                )
            )
            continue

        version, document = match
        status_value = getattr(version.status, "value", version.status)
        search_status_value = getattr(version.search_status, "value", version.search_status)
        document_status_value = getattr(document.status, "value", document.status)
        is_ready = (
            document.status == DocumentStatus.READY
            and version.search_status == "SEARCH_READY"
            and version.search_ready_at is not None
            and int(document.chunk_count or 0) > 0
        )
        items.append(
            ProjectDocumentReadinessItem(
                document_id=document_id,
                version_no=version_no,
                found=True,
                status=str(status_value),
                search_status=str(search_status_value),
                search_ready=is_ready,
                document_status=str(document_status_value),
                chunk_count=int(document.chunk_count or 0),
            )
        )

    return ProjectDocumentReadinessBatchResponse(verified_at=now, items=items)


def _dominant_block_type(block_types: list[str]) -> str:
    if not block_types:
        return "paragraph"
    if "table" in block_types:
        return "table"
    if "code" in block_types:
        return "code"
    if all(t == "heading" for t in block_types):
        return "heading"
    for t in block_types:
        if t and t != "heading":
            return t
    return block_types[0] or "paragraph"


async def get_document_version_chunks(
    session: AsyncSession,
    *,
    project_id: str,
    document_id: str,
    version_no: int,
    principal: VerifiedPrincipal | None = None,
    limit: int = 50,
    offset: int = 0,
) -> DocumentVersionChunksResponse:
    """Retrieve ordered extracted chunks (SearchUnits or CanonicalBlocks) for a document version."""
    stmt = (
        select(DocumentVersion, Document)
        .join(Document, Document.id == DocumentVersion.document_id)
        .where(
            Document.project_id == project_id,
            DocumentVersion.document_id == document_id,
            DocumentVersion.version_no == version_no,
        )
    )
    result = (await session.execute(stmt)).first()
    if not result:
        raise NotFoundError(
            f"Document version {document_id}/v{version_no} not found in project {project_id}"
        )
    doc_ver, doc = result

    if principal is not None:
        if not principal.is_service:
            if doc.tenant_id != principal.tenant_id:
                raise ForbiddenError(
                    f"Principal '{principal.user_id}' belongs to tenant '{principal.tenant_id}' "
                    f"but document belongs to tenant '{doc.tenant_id}'"
                )
            partition = (doc_ver.metadata_json or {}).get("security_partition_id")
            if not partition or not principal.has_partition_access(partition):
                raise ForbiddenError(
                    f"Principal '{principal.user_id}' is not authorized to access security partition "
                    f"'{partition or 'NONE'}'"
                )

    if doc_ver.status == "REVOKED" or doc.status == DocumentStatus.DELETING:
        return DocumentVersionChunksResponse(
            document_id=doc_ver.document_id,
            version_no=doc_ver.version_no,
            source_stage="EMPTY",
            total_chunks=0,
            total_tokens=0,
            items=[],
        )

    blocks = list(
        (
            await session.scalars(
                select(CanonicalBlock)
                .where(CanonicalBlock.document_version_id == doc_ver.id)
                .order_by(CanonicalBlock.ordinal.asc())
            )
        ).all()
    )
    if not blocks:
        return DocumentVersionChunksResponse(
            document_id=doc_ver.document_id,
            version_no=doc_ver.version_no,
            source_stage="EMPTY",
            total_chunks=0,
            total_tokens=0,
            items=[],
        )

    units = list(
        (
            await session.scalars(
                select(SearchUnit).where(SearchUnit.document_version_id == doc_ver.id)
            )
        ).all()
    )

    safe_offset = max(0, int(offset))
    safe_limit = max(1, min(200, int(limit)))

    if units:
        block_index_by_id = {b.id: idx for idx, b in enumerate(blocks)}
        units.sort(
            key=lambda u: (
                block_index_by_id.get(u.block_from_id, 0),
                int(u.page_from or 0),
                str(u.id),
            )
        )
        extracted_items: list[DocumentExtractedChunkItem] = []
        total_tokens = 0
        for unit in units:
            start_idx = block_index_by_id.get(unit.block_from_id, 0)
            end_idx = block_index_by_id.get(unit.block_to_id, start_idx)
            if end_idx < start_idx:
                start_idx, end_idx = end_idx, start_idx
            unit_blocks = blocks[start_idx : end_idx + 1]
            combined_text = "\n\n".join(
                b.normalized_text.strip()
                for b in unit_blocks
                if b.normalized_text and b.normalized_text.strip()
            ).strip()
            if not combined_text:
                continue
            tok = int(unit.token_count or 0)
            total_tokens += tok
            ordinal = len(extracted_items) + 1
            extracted_items.append(
                DocumentExtractedChunkItem(
                    chunk_id=str(unit.id),
                    ordinal=ordinal,
                    section_path=(unit.section_path or "").strip(),
                    block_type=_dominant_block_type([b.block_type for b in unit_blocks]),
                    page_from=int(unit.page_from or 1),
                    page_to=int(unit.page_to or unit.page_from or 1),
                    token_count=tok,
                    content=combined_text,
                )
            )
        return DocumentVersionChunksResponse(
            document_id=doc_ver.document_id,
            version_no=doc_ver.version_no,
            source_stage="SEARCH_UNITS" if extracted_items else "EMPTY",
            total_chunks=len(extracted_items),
            total_tokens=total_tokens,
            items=extracted_items[safe_offset : safe_offset + safe_limit],
        )

    block_items: list[DocumentExtractedChunkItem] = []
    total_tokens = 0
    for block in blocks:
        content = (block.normalized_text or "").strip()
        if not content:
            continue
        est_tokens = max(1, len(content.split()))
        total_tokens += est_tokens
        ordinal = len(block_items) + 1
        block_items.append(
            DocumentExtractedChunkItem(
                chunk_id=str(block.id),
                ordinal=ordinal,
                section_path=(block.section_path or "").strip(),
                block_type=(block.block_type or "paragraph").strip(),
                page_from=int(block.page_from or 1),
                page_to=int(block.page_to or block.page_from or 1),
                token_count=est_tokens,
                content=content,
            )
        )
    return DocumentVersionChunksResponse(
        document_id=doc_ver.document_id,
        version_no=doc_ver.version_no,
        source_stage="CANONICAL_BLOCKS" if block_items else "EMPTY",
        total_chunks=len(block_items),
        total_tokens=total_tokens,
        items=block_items[safe_offset : safe_offset + safe_limit],
    )


async def resolve_project_document(
    session: AsyncSession,
    *,
    tenant_id: str,
    project_id: str,
    document_id_or_logical_id: str,
) -> Document:
    """Resolve a project-scoped Document by SAG document_id or external logical_source_id."""
    target = (document_id_or_logical_id or "").strip()
    if not target:
        raise NotFoundError(f"Document not found in project {project_id}")

    doc = (
        await session.execute(
            select(Document)
            .where(
                Document.tenant_id == tenant_id,
                Document.project_id == project_id,
                Document.id == target,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if doc is None:
        doc = (
            await session.execute(
                select(Document)
                .where(
                    Document.tenant_id == tenant_id,
                    Document.project_id == project_id,
                    Document.logical_source_id == target,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
    if doc is None:
        raise NotFoundError(f"Document '{target}' not found in project '{project_id}'")
    return doc


async def revoke_project_document(
    session: AsyncSession,
    *,
    tenant_id: str,
    project_id: str,
    logical_source_id: str | None = None,
    document_id: str | None = None,
    reason: str | None = None,
) -> DocumentRevokeResponse:
    """Revoke a project document from retrieval and knowledge indexing."""
    logical_id = (logical_source_id or "").strip()
    doc_id = (document_id or "").strip()
    if not logical_id and not doc_id:
        raise ValidationError("Either logical_source_id or document_id is required to revoke a document")

    doc: Document | None = None
    if doc_id:
        doc = (
            await session.execute(
                select(Document)
                .where(
                    Document.tenant_id == tenant_id,
                    Document.project_id == project_id,
                    Document.id == doc_id,
                )
                .with_for_update()
                .limit(1)
            )
        ).scalar_one_or_none()
    if doc is None and logical_id:
        doc = (
            await session.execute(
                select(Document)
                .where(
                    Document.tenant_id == tenant_id,
                    Document.project_id == project_id,
                    Document.logical_source_id == logical_id,
                )
                .with_for_update()
                .limit(1)
            )
        ).scalar_one_or_none()

    if doc is None:
        return DocumentRevokeResponse(status="NOT_FOUND", document_id=None)

    versions = list(
        (
            await session.scalars(
                select(DocumentVersion)
                .where(DocumentVersion.document_id == doc.id)
                .order_by(DocumentVersion.version_no.asc())
                .with_for_update()
            )
        ).all()
    )

    if not doc.is_active and all(v.status == "REVOKED" for v in versions):
        from sag_api.api.v1.universe import clear_universe_caches

        clear_universe_caches(project_id)
        return DocumentRevokeResponse(status="ALREADY_REVOKED", document_id=doc.id)

    now_utc = datetime.now(UTC)
    doc.is_active = False

    active_jobs = list(
        (
            await session.scalars(
                select(Job).where(
                    Job.document_id == doc.id,
                    Job.type.in_([JobType.PROCESS_DOCUMENT, JobType.REPROCESS_DOCUMENT]),
                    Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
                )
            )
        ).all()
    )
    for active_job in active_jobs:
        active_job.payload = {**(active_job.payload or {}), "pause_requested": True}
        if active_job.status == JobStatus.QUEUED:
            active_job.status = JobStatus.PAUSED

    if doc.status in {
        DocumentStatus.PENDING,
        DocumentStatus.LOADING,
        DocumentStatus.EXTRACTING,
    }:
        doc.status = DocumentStatus.PAUSED

    for ver in versions:
        ver.status = "REVOKED"
        ver.search_status = "REVOKED"
        ver.knowledge_status = "REVOKED"
        ver.search_ready_at = None
        ver.knowledge_ready_at = None
        if ver.valid_to is None or ver.valid_to > now_utc:
            ver.valid_to = now_utc
        meta = dict(ver.metadata_json or {})
        meta["revoked_at"] = now_utc.isoformat()
        if reason:
            meta["revoke_reason"] = reason
        ver.metadata_json = meta

    if doc.source_id:
        source = await session.get(Source, doc.source_id)
        if source is not None:
            await _refresh_source_counts(session, source)
        await touch_source_revision(session, doc.source_id)

    await session.commit()
    from sag_api.api.v1.universe import clear_universe_caches

    clear_universe_caches(project_id)
    return DocumentRevokeResponse(status="REVOKED", document_id=doc.id)
