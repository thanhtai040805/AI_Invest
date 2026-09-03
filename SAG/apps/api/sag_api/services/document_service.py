"""Logic miền tài liệu: upload ghi đĩa → đăng ký → đưa vào hàng đợi xử lý."""

from __future__ import annotations

import os

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.core.errors import ConflictError, NotFoundError
from sag_api.db.base import new_id
from sag_api.db.models import Document, Job, Source
from sag_api.enums import DocumentStatus, JobStatus, JobType
from sag_api.jobs import JobQueue
from sag_api.sag import EngineManager


async def list_documents(session: AsyncSession, source_id: str) -> list[Document]:
    rows = await session.execute(
        select(Document).where(Document.source_id == source_id).order_by(Document.created_at.desc())
    )
    return list(rows.scalars().all())


async def get_document(session: AsyncSession, source: Source, document_id: str) -> Document:
    doc = await session.get(Document, document_id)
    if doc is None or doc.source_id != source.id:
        raise NotFoundError("Tài liệu không tồn tại")
    return doc


async def create_document_from_upload(
    session: AsyncSession,
    source: Source,
    *,
    filename: str,
    content_type: str,
    data: bytes,
    upload_dir: str,
    job_queue: JobQueue,
    doc_role: str | None = None,
    is_active: bool = True,
    fiscal_year: int | None = None,
    fiscal_quarter: int | None = None,
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
        doc_role=doc_role,
        is_active=is_active,
        fiscal_year=fiscal_year,
        fiscal_quarter=fiscal_quarter,
    )
    session.add(document)
    await session.execute(
        update(Source).where(Source.id == source.id).values(document_count=Source.document_count + 1)
    )
    job = Job(
        type=JobType.PROCESS_DOCUMENT,
        source_id=source.id,
        document_id=doc_id,
        status=JobStatus.QUEUED,
    )
    session.add(job)
    await session.commit()
    await session.refresh(document)
    await session.refresh(job)

    # Nếu đây là LATEST_QUARTER mới, tự động chuyển các quý trước thành ARCHIVED
    if doc_role == "LATEST_QUARTER":
        await session.execute(
            update(Document)
            .where(
                Document.source_id == source.id,
                Document.doc_role == "LATEST_QUARTER",
                Document.id != doc_id,
            )
            .values(doc_role="ARCHIVED", is_active=False)
        )
        await session.commit()

    await job_queue.enqueue(job.id)
    return document, job


def _format_messages(messages: list[dict]) -> str:
    lines = ["# Tin nhắn", ""]
    for m in messages:
        who = m.get("author") or m.get("role") or "Tin nhắn"
        ts = f"({m['ts']})" if m.get("ts") else ""
        lines.append(f"**{who}**{ts}: {m.get('text') or ''}")
    return "\n\n".join(lines)


async def ingest_content(
    session: AsyncSession,
    source: Source,
    *,
    text: str | None = None,
    title: str | None = None,
    messages: list[dict] | None = None,
    upload_dir: str,
    job_queue: JobQueue,
    doc_role: str | None = None,
    is_active: bool = True,
    fiscal_year: int | None = None,
    fiscal_quarter: int | None = None,
) -> Document:
    """Ghi thống nhất: đưa văn bản / một loạt tin nhắn về dạng tài liệu → tái sử dụng pipeline ingest/extract (ghi liên tục)."""
    from sag_api.core.errors import ValidationError

    if messages:
        content = _format_messages(messages)
        filename = f"{title or f'{len(messages)} tin nhắn'}.md"
    elif text:
        content = (f"# {title}\n\n" if title else "") + text
        filename = f"{title or 'Văn bản'}.md"
    else:
        raise ValidationError("Vui lòng cung cấp text hoặc messages")

    document, _job = await create_document_from_upload(
        session,
        source,
        filename=filename,
        content_type="text/markdown",
        data=content.encode("utf-8"),
        upload_dir=upload_dir,
        job_queue=job_queue,
        doc_role=doc_role,
        is_active=is_active,
        fiscal_year=fiscal_year,
        fiscal_quarter=fiscal_quarter,
    )
    return document


async def reprocess_document(
    session: AsyncSession,
    source: Source,
    document_id: str,
    *,
    job_queue: JobQueue,
    engine_manager: EngineManager,
) -> Job:
    document = await get_document(session, source, document_id)
    latest = await session.scalar(
        select(Job).where(Job.document_id == document.id).order_by(Job.created_at.desc())
    )
    if latest is not None and latest.status in {
        JobStatus.QUEUED,
        JobStatus.RUNNING,
        JobStatus.PAUSED,
    }:
        return latest
    restart_from_scratch = document.status == DocumentStatus.READY
    if restart_from_scratch:
        derived_source_ids = {
            value
            for value in [
                document.sag_source_id,
                *[
                    _checkpoint_source_id(candidate.payload)
                    for candidate in (
                        await session.scalars(
                            select(Job).where(Job.document_id == document.id)
                        )
                    ).all()
                ],
            ]
            if value
        }
        # Phiên bản cũ của "xử lý lại" mỗi lần đều tạo Article mới; thu thập
        # source_id từ tất cả điểm dừng Job lịch sử, vừa dọn bản ghi hiện tại, vừa dọn dữ liệu dẫn xuất trùng đã để lại trước đó.
        for derived_source_id in sorted(derived_source_ids):
            await engine_manager.delete_document_data(
                source.sag_source_config_id,
                derived_source_id,
                source=source,
            )

    document.status = DocumentStatus.PENDING
    document.error = None
    if restart_from_scratch:
        document.progress = 0
        document.chunk_count = 0
        document.event_count = 0
        document.token_usage = 0
        document.sag_source_id = None
        await session.flush()
        await _refresh_source_counts(session, source)
    payload = dict(latest.payload or {}) if latest is not None and not restart_from_scratch else {}
    payload.pop("pause_requested", None)
    payload.pop("resume_requested", None)
    job = Job(
        type=JobType.PROCESS_DOCUMENT,
        source_id=source.id,
        document_id=document.id,
        status=JobStatus.QUEUED,
        # Nếu lần thất bại trước đã tạo task MinerU, xử lý lại nên tiếp tục polling thay vì tính phí lần nữa.
        payload=payload,
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)
    await job_queue.enqueue(job.id)
    return job


def _checkpoint_source_id(payload: dict | None) -> str | None:
    checkpoint = (payload or {}).get("process_checkpoint")
    value = checkpoint.get("source_id") if isinstance(checkpoint, dict) else None
    return value.strip() if isinstance(value, str) and value.strip() else None


async def _refresh_source_counts(session: AsyncSession, source: Source) -> None:
    document_count, chunk_count, event_count = (
        await session.execute(
            select(
                func.count(Document.id),
                func.coalesce(func.sum(Document.chunk_count), 0),
                func.coalesce(func.sum(Document.event_count), 0),
            ).where(Document.source_id == source.id)
        )
    ).one()
    source.document_count = int(document_count)
    source.chunk_count = int(chunk_count)
    source.event_count = int(event_count)


async def pause_document(session: AsyncSession, source: Source, document_id: str) -> Job:
    """Tạm dừng hợp tác: các chunk đã bắt đầu chạy xong và lưu điểm dừng, không nhận chunk mới."""
    document = await get_document(session, source, document_id)
    job = await session.scalar(
        select(Job)
        .where(
            Job.document_id == document.id,
            Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
        )
        .order_by(Job.created_at.desc())
        .limit(1)
    )
    if job is None:
        raise ConflictError("Tài liệu hiện tại không có nhiệm vụ trích xuất nào để dừng")

    if job.status == JobStatus.QUEUED:
        paused = await session.execute(
            update(Job)
            .where(Job.id == job.id, Job.status == JobStatus.QUEUED)
            .values(status=JobStatus.PAUSED)
        )
        if paused.rowcount == 1:
            document.status = DocumentStatus.PAUSED
            await session.commit()
            await session.refresh(job)
            return job
        await session.refresh(job)

    if job.status != JobStatus.RUNNING:
        raise ConflictError("Nhiệm vụ trích xuất đã kết thúc, không thể dừng")
    job.payload = {**(job.payload or {}), "pause_requested": True}
    await session.commit()
    await session.refresh(job)
    return job


async def resume_document(
    session: AsyncSession,
    source: Source,
    document_id: str,
    *,
    job_queue: JobQueue,
) -> Job:
    """Đưa nhiệm vụ tạm dừng trở lại hàng đợi nguyên trạng, bộ xử lý sẽ bỏ qua các chunk đã hoàn thành trong điểm dừng."""
    document = await get_document(session, source, document_id)
    job = await session.scalar(
        select(Job)
        .where(Job.document_id == document.id, Job.status == JobStatus.PAUSED)
        .order_by(Job.created_at.desc())
        .limit(1)
    )
    if job is None:
        raise ConflictError("Tài liệu hiện tại không có nhiệm vụ tạm dừng nào để tiếp tục")

    payload = dict(job.payload or {})
    payload.pop("pause_requested", None)
    payload["resume_requested"] = True
    job.payload = payload
    job.status = JobStatus.QUEUED
    job.finished_at = None
    job.error = None
    document.status = (
        DocumentStatus.EXTRACTING if payload.get("process_checkpoint") else DocumentStatus.PENDING
    )
    document.error = None
    await session.commit()
    await session.refresh(job)
    await job_queue.enqueue(job.id)
    return job


async def delete_document(
    session: AsyncSession,
    source: Source,
    document_id: str,
    *,
    engine_manager: EngineManager,
    job_queue: JobQueue | None = None,
) -> None:
    document = await get_document(session, source, document_id)
    path = document.storage_path
    sag_source_id = document.sag_source_id

    active_jobs = list(
        (
            await session.scalars(
                select(Job).where(
                    Job.document_id == document.id,
                    Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
                )
            )
        ).all()
    )
    for job in active_jobs:
        job.payload = {**(job.payload or {}), "pause_requested": True}
        if job.status == JobStatus.QUEUED:
            job.status = JobStatus.PAUSED
    if active_jobs:
        await session.commit()

    if sag_source_id:
        await engine_manager.delete_document_data(
            source.sag_source_config_id,
            sag_source_id,
            source=source,
        )

    await session.delete(document)
    await session.flush()
    await _refresh_source_counts(session, source)
    await session.commit()
    if path:
        from sag_api.parsing.service import parsed_sidecar_paths

        for candidate in [path, *parsed_sidecar_paths(path)]:
            try:
                if os.path.exists(candidate):
                    os.remove(candidate)
            except OSError:
                pass
    from sag_api.services.universe_service import schedule_universe_refresh

    await schedule_universe_refresh(
        session,
        job_queue,
        source_id=source.id,
        reason="document_deleted",
    )
