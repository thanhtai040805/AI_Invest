"""Trình xử lý tác vụ —— phân phối theo JobType.

Trình xử lý chỉ quan tâm «làm gì»; máy trạng thái (queued/running/succeeded/failed) do worker của hàng đợi duy trì thống nhất.
Bên trong trình xử lý phụ trách cập nhật trạng thái khâu và số đếm của đối tượng miền (Document/Source).
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from pathlib import Path

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.core.config import settings
from sag_api.core.db import SessionLocal
from sag_api.core.error_taxonomy import ErrorLayer, ErrorStage
from sag_api.core.errors import ApiError, NotFoundError
from sag_api.core.logging import get_logger
from sag_api.db.models import Document, Job, Source
from sag_api.enums import DocumentStatus, JobType
from sag_api.jobs.control import JobPaused
from sag_api.parsing import prepare_document
from sag_api.sag import EngineManager
from sag_api.sag.dto import ProcessCheckpoint, estimate_llm_cost


log = get_logger("jobs")

TaskHandler = Callable[[AsyncSession, Job], Awaitable[None]]

# Trạng thái hiện tại của tài liệu khi thất bại → khâu chuỗi xử lý. Đây là ánh xạ dự phòng của «nơi duy nhất biết stage»:
# khi bản thân ngoại lệ không kèm stage (không phải ApiError, ví dụ lỗi jsonschema lọt ra), dùng tài liệu đang ở
# trạng thái nào để suy ra nó kẹt ở khâu nào.
_STATUS_TO_STAGE: dict[DocumentStatus, ErrorStage] = {
    DocumentStatus.PENDING: ErrorStage.PARSE,
    DocumentStatus.LOADING: ErrorStage.PARSE,
    DocumentStatus.EXTRACTING: ErrorStage.EXTRACT,
}


def _classify_document_failure(
    e: Exception, current_status: DocumentStatus
) -> tuple[ErrorLayer, ErrorStage]:
    """Suy luận lớp chịu trách nhiệm và khâu chuỗi xử lý của lỗi.

    Ưu tiên tin layer/stage mà ngoại lệ miền tự mang (phân loại LLM, lớp dịch engine đều điền);
    nếu không thì thoái lui về «đoán khâu theo trạng thái hiện tại của tài liệu», lớp chịu trách nhiệm về engine (các ngoại lệ trần
    lọt ra trong quá trình trích xuất/nhập kho zleap-sag, như jsonschema.ValidationError, gần như đều xảy ra phía engine).
    """
    if isinstance(e, ApiError) and e.layer is not None and e.stage is not None:
        return e.layer, e.stage
    stage = _STATUS_TO_STAGE.get(current_status, ErrorStage.EXTRACT)
    return ErrorLayer.ENGINE, stage


async def process_document(
    session: AsyncSession, job: Job, *, engine_manager: EngineManager, job_queue=None
) -> None:
    """Phân tích, nhập kho và trích xuất song song theo chunk; mỗi chunk hoàn thành là lưu điểm dừng."""
    document = await session.get(Document, job.document_id) if job.document_id else None
    if document is None:
        raise NotFoundError("Tài liệu không tồn tại")
    source = await session.get(Source, document.source_id)
    if source is None:
        raise NotFoundError("Nguồn không tồn tại")
    checkpoint = ProcessCheckpoint.from_payload(job.payload)

    # A worker retry reuses the document row. Clear the previous attempt's
    # failure before parsing can block for a long time, so active processing
    # never carries a stale terminal error.
    if document.error is not None:
        document.error = None
        await session.commit()

    async def refresh_payload() -> dict:
        await session.refresh(job, attribute_names=["payload"])
        return dict(job.payload or {})

    async def on_stage(stage: str) -> None:
        if stage == "loading":
            document.status = DocumentStatus.LOADING
            document.progress = max(document.progress, 5)
            job.progress = document.progress / 100
        elif stage == "extracting":
            document.status = DocumentStatus.EXTRACTING
            completed = len(checkpoint.processed_chunk_ids)
            total = len(checkpoint.chunk_ids)
            document.progress = 20 + round(80 * completed / total) if total else 20
            job.progress = document.progress / 100
        await session.commit()

    async def on_parser_state(state: dict) -> None:
        document.status = DocumentStatus.LOADING
        document.progress = max(document.progress, 10)
        job.progress = document.progress / 100
        job.payload = {**(await refresh_payload()), "document_parser": state}
        await session.commit()

    async def on_checkpoint(value: ProcessCheckpoint) -> None:
        nonlocal checkpoint
        checkpoint = value
        job.payload = value.merge_payload(await refresh_payload())
        document.chunk_count = len(value.chunk_ids)
        document.event_count = value.event_count
        document.sag_source_id = value.source_id
        document.token_usage = value.token_usage
        total = len(value.chunk_ids)
        completed = len(value.processed_chunk_ids)
        document.progress = 20 + round(80 * completed / total) if total else 20
        job.progress = document.progress / 100
        await session.commit()

    async def should_pause() -> bool:
        async with SessionLocal() as control_session:
            current_job = await control_session.get(Job, job.id)
            if current_job is None:
                return True
            return bool((current_job.payload or {}).get("pause_requested"))

    try:
        prepared = None
        if not checkpoint.chunk_ids:
            prepared = await prepare_document(
                document.storage_path,
                settings,
                state=(job.payload or {}).get("document_parser"),
                on_state=on_parser_state,
            )
            if prepared.fallback_from:
                log.warning(
                    "Phân tích tài liệu đã hạ cấp doc=%s job=%s from=%s to=%s cached=%s error=%s",
                    document.id,
                    getattr(job, "id", None),
                    prepared.fallback_from,
                    prepared.provider,
                    prepared.cached,
                    prepared.fallback_error,
                )
        outcome = await engine_manager.process_document(
            source.sag_source_config_id,
            str(prepared.path) if prepared is not None else None,
            source=source,
            on_stage=on_stage,
            checkpoint=checkpoint,
            on_checkpoint=on_checkpoint,
            should_pause=should_pause,
            max_concurrency=settings.document_extract_concurrency,
            document_title=Path(document.filename).stem.strip(),
            **({"doc_type": getattr(document, "doc_type", None)} if getattr(document, "doc_type", None) else {}),
        )


        if outcome.paused:
            document.status = DocumentStatus.PAUSED
            document.error = None
            await session.commit()
            raise JobPaused()
    except JobPaused:
        raise
    except Exception as e:  # noqa: BLE001 - Ghi vào tài liệu rồi mới ném lại cho worker
        layer, stage = _classify_document_failure(e, document.status)
        document.status = DocumentStatus.FAILED
        document.error = getattr(e, "message", None) or str(e)
        document.error_layer = layer.value
        document.error_stage = stage.value
        log.warning(
            "Xử lý tài liệu thất bại doc=%s layer=%s stage=%s error=%s",
            document.id,
            layer.value,
            stage.value,
            document.error,
        )
        await session.commit()
        raise

    document.status = DocumentStatus.READY
    document.chunk_count = outcome.chunk_count
    document.event_count = outcome.event_count
    document.sag_source_id = outcome.source_id
    document.progress = 100
    document.token_usage = outcome.token_usage
    document.error = None
    # Số đếm gộp của nguồn dùng SQL nguyên tử để cập nhật, tránh đọc-sửa-ghi lạc mất khi song song
    await session.execute(
        update(Source)
        .where(Source.id == source.id)
        .values(
            chunk_count=Source.chunk_count + outcome.chunk_count,
            event_count=Source.event_count + outcome.event_count,
        )
    )
    await session.commit()
    cost_info = estimate_llm_cost(outcome.token_usage, settings.llm_model)
    log.info(
        "Xử lý tài liệu hoàn thành doc=%s parser=%s cached=%s chunks=%d events=%d tokens=%d cost=%s (%s)",
        document.id,
        prepared.provider if prepared is not None else "checkpoint",
        prepared.cached if prepared is not None else True,
        outcome.chunk_count,
        outcome.event_count,
        outcome.token_usage,
        cost_info["formatted_usd"],
        cost_info["formatted_vnd"],
    )

    if job_queue is not None:
        from sag_api.services.universe_service import schedule_universe_refresh

        await schedule_universe_refresh(
            session,
            job_queue,
            source_id=source.id,
            reason="document_processed",
        )


async def sync_source(session: AsyncSession, job: Job, *, engine_manager=None, job_queue=None) -> None:
    """Đồng bộ connector động: discover → fetch → đăng ký tài liệu và đưa vào hàng đợi xử lý (tái sử dụng pipeline ingest→extract)."""
    # Import trễ để tránh phụ thuộc vòng với gói jobs
    from sag_api.connectors import registry
    from sag_api.core.config import settings
    from sag_api.services.document_service import create_document_from_upload

    source = await session.get(Source, job.source_id) if job.source_id else None
    if source is None:
        raise NotFoundError("Nguồn không tồn tại")

    connector = registry.get(source.connector_kind)
    discovered = await connector.discover(source.config or {})
    fetched = 0
    for d in discovered:
        try:
            local = await connector.fetch(source.config or {}, d)
            with open(local.path, "rb") as f:
                data = f.read()
        except Exception as e:  # noqa: BLE001 - Một bài thất bại không ảnh hưởng đồng bộ tổng thể
            log.warning("Đồng bộ lấy nội dung thất bại %s: %s", d.external_id, getattr(e, "message", None) or e)
            continue
        await create_document_from_upload(
            session,
            source,
            filename=local.filename,
            content_type=local.content_type,
            data=data,
            upload_dir=settings.upload_dir,
            job_queue=job_queue,
        )
        try:
            os.remove(local.path)
        except OSError:
            pass
        fetched += 1

    job.progress = 1.0
    job.payload = {**(job.payload or {}), "discovered": len(discovered), "fetched": fetched}
    await session.commit()
    log.info("Đồng bộ hoàn thành source=%s phát hiện=%d lấy=%d", source.id, len(discovered), fetched)


async def index_universe(
    session: AsyncSession, job: Job, *, engine_manager: EngineManager, job_queue=None
) -> None:
    """Rebuild one user's aggregate universe overview from authoritative graph data."""
    from sag_api.db.models import User
    from sag_api.services.universe_service import rebuild_universe_overview

    user_id = str((job.payload or {}).get("user_id") or "")
    if not user_id or await session.get(User, user_id) is None:
        raise NotFoundError("Người dùng thuộc vũ trụ tri thức không tồn tại")
    job.progress = 0.1
    await session.commit()
    overview = await rebuild_universe_overview(session, engine_manager, user_id)
    job.progress = 1.0
    job.payload = {**(job.payload or {}), "overview_id": overview.id}
    await session.commit()


TASK_HANDLERS: dict[JobType, TaskHandler] = {
    JobType.PROCESS_DOCUMENT: process_document,
    JobType.SYNC_SOURCE: sync_source,
    JobType.INDEX_UNIVERSE: index_universe,
}
