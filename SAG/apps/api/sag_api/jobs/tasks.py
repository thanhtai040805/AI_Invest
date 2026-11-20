"""Trình xử lý tác vụ —— Phân phối theo JobType.

Bộ xử lý chỉ quan tâm đến "làm gì"; máy trạng thái (queued/running/succeeded/failed) được duy trì thống nhất bởi queue worker.
Bên trong bộ xử lý chịu trách nhiệm cập nhật trạng thái giai đoạn và bộ đếm của đối tượng miền (Document/Source).
"""

from __future__ import annotations

import os
import re
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.core.config import settings
from sag_api.core.db import SessionLocal
from sag_api.core.error_taxonomy import ErrorLayer, ErrorStage
from sag_api.core.errors import ApiError, NotFoundError, is_retryable_error
from sag_api.core.logging import get_logger
from sag_api.core.sanitizer import sanitize_error_message
from sag_api.db.models import Document, Job, Source
from sag_api.db.models.routing_rag import DocumentVersion, IngestionRun, StageRun
from sag_api.enums import DocumentStatus, JobStatus, JobType
from sag_api.jobs.control import JobDeferred, JobPaused, JobYielded
from sag_api.jobs.octx_tasks import (
    export_octx,
    gc_octx_installation,
    gc_octx_transfers,
    import_octx,
    preflight_octx,
)
from sag_api.jobs.scheduling import SOURCE_MAINTENANCE
from sag_api.parsing import ParsePaused, prepare_document
from sag_api.sag import EngineManager
from sag_api.sag.dto import ProcessCheckpoint, ProcessOutcome
from sag_api.services.source_operation_service import (
    acquire_operation_lease,
    acquire_source_exclusive_lease,
    acquire_source_processing_lease,
    touch_source_revision,
)

log = get_logger("jobs")

TaskHandler = Callable[[AsyncSession, Job], Awaitable[None]]

# Trạng thái hiện tại khi tài liệu thất bại → bước trong chuỗi xử lý. Đây là ánh xạ dự phòng tại "nơi duy nhất biết stage":
# Khi bản thân ngoại lệ không mang stage (không phải ApiError, ví dụ lỗi jsonschema thoát ra ngoài), dùng trạng thái
# hiện tại của tài liệu để suy đoán nó bị nghẽn ở bước nào.
_STATUS_TO_STAGE: dict[DocumentStatus, ErrorStage] = {
    DocumentStatus.PENDING: ErrorStage.PARSE,
    DocumentStatus.LOADING: ErrorStage.PARSE,
    DocumentStatus.EXTRACTING: ErrorStage.EXTRACT,
}
_CONTROL_TRANSITION_STATES = {
    DocumentStatus.PAUSING,
    DocumentStatus.DELETING,
    DocumentStatus.DELETE_FAILED,
}
_DELETE_CONTROL_STATES = {
    DocumentStatus.DELETING,
    DocumentStatus.DELETE_FAILED,
}
_PARSER_UPLOAD_STATES = {"uploading", "uploaded"}
_PARSER_QUEUE_STATES = {"created", "pending", "queued", "queueing"}
_PARSER_DONE_STATES = {"done", "success", "succeeded", "completed", "finished"}
_PARSER_FAILED_STATES = {"failed", "failure", "error", "cancelled", "canceled", "fallback_failed"}
_SECRET_QUERY_KEYS = re.compile(r"(?i)([?&](?:token|key|signature|credential|authorization|x-amz-[^=]+)=)[^&#\s]+")
_BEARER_TOKEN = re.compile(r"(?i)bearer\s+\S+")
_API_KEY = re.compile(r"(?i)\b(?:sk|ak)-[a-z0-9._-]{6,}\b")
_URL = re.compile(r"https?://[^\s；，,]+")


async def _finalize_research_task_for_run(session: AsyncSession, run_id: str | None) -> None:
    if not run_id:
        return
    try:
        run = await session.get(IngestionRun, run_id)
        if run and run.research_task_id:
            from sag_api.services.research_agent_service import finalize_research_task

            await finalize_research_task(session, run.research_task_id)
            await session.commit()
    except Exception as exc:  # noqa: BLE001 - research status must not fail document processing
        await session.rollback()
        log.warning("Could not finalize research task for ingestion run %s: %s", run_id, sanitize_error_message(exc))


def _mineru_details(state: dict[str, Any] | None = None) -> tuple[str, str]:
    current = state or {}
    model = current.get("mineru_model")
    default_model = settings.mineru_official_model if settings.mineru_official_model in {"vlm", "pipeline"} else "vlm"
    return "official", model if model in {"vlm", "pipeline"} else default_model


def _redact_parser_reason(value: object) -> str | None:
    if value is None:
        return None
    message = " ".join(str(value).split())
    if not message:
        return None
    message = _SECRET_QUERY_KEYS.sub(r"\1[REDACTED]", message)
    message = _BEARER_TOKEN.sub("Bearer [REDACTED]", message)
    message = _API_KEY.sub("[REDACTED]", message)
    message = _URL.sub("[URL REDACTED]", message)
    return message[:300]


def _fallback_reason(state: dict[str, Any]) -> str | None:
    fallback = state.get("fallback")
    if isinstance(fallback, dict):
        return _redact_parser_reason(fallback.get("mineru_error") or fallback.get("markitdown_error"))
    return _redact_parser_reason(state.get("error") or state.get("message"))


def _parser_state_values(state: dict[str, Any]) -> dict[str, str | None]:
    raw_provider = state.get("provider")
    provider = raw_provider if raw_provider in {"mineru", "markitdown", "original"} else None
    raw_status = str(state.get("status") or "").lower()
    fallback = isinstance(state.get("fallback"), dict)
    if raw_status.startswith("fallback_") or fallback:
        status = "failed" if raw_status == "fallback_failed" else "fallback"
        parser_provider = "markitdown" if raw_status != "fallback_failed" else provider
        fallback_from = "mineru"
    elif raw_status in _PARSER_UPLOAD_STATES or state.get("upload_url") and not state.get("task_id"):
        status = "uploading"
        parser_provider = provider
        fallback_from = None
    elif raw_status in _PARSER_QUEUE_STATES or (state.get("task_id") or state.get("batch_id")) and not raw_status:
        status = "queued"
        parser_provider = provider
        fallback_from = None
    elif raw_status in _PARSER_DONE_STATES:
        status = "done"
        parser_provider = provider
        fallback_from = None
    elif raw_status in _PARSER_FAILED_STATES:
        status = "failed"
        parser_provider = provider
        fallback_from = None
    else:
        status = "running"
        parser_provider = provider
        fallback_from = None
    mineru_provider = mineru_model = None
    if provider == "mineru" or fallback_from == "mineru":
        mineru_provider, mineru_model = _mineru_details(state)
    return {
        "parser_provider": parser_provider,
        "mineru_provider": mineru_provider,
        "mineru_model": mineru_model,
        "parser_status": status,
        "fallback_from": fallback_from,
        "fallback_reason": _fallback_reason(state) if fallback_from else None,
    }


def _prepared_parser_values(prepared, state: dict[str, Any] | None) -> dict[str, str | None]:  # noqa: ANN001
    mineru_provider = mineru_model = None
    if prepared.provider == "mineru" or prepared.fallback_from == "mineru":
        mineru_provider, mineru_model = _mineru_details(state)
    return {
        "parser_provider": prepared.provider,
        "mineru_provider": mineru_provider,
        "mineru_model": mineru_model,
        "parser_status": "fallback" if prepared.fallback_from else "done",
        "fallback_from": prepared.fallback_from,
        "fallback_reason": _redact_parser_reason(prepared.fallback_error),
    }


async def _yield_after_document_transition_lost(
    session: AsyncSession,
    document: Document,
) -> None:
    """Converge a winning pause/delete intent without overwriting it."""
    document_id = document.id
    await session.rollback()
    current = await session.get(Document, document_id, populate_existing=True)
    if current is not None and current.status == DocumentStatus.PAUSING:
        paused = await session.execute(
            update(Document)
            .where(
                Document.id == document_id,
                Document.status == DocumentStatus.PAUSING,
            )
            .values(status=DocumentStatus.PAUSED, error=None)
            .execution_options(synchronize_session=False)
        )
        if paused.rowcount == 1:
            await session.commit()
        else:
            await session.rollback()
    raise JobPaused()


def _classify_document_failure(e: Exception, current_status: DocumentStatus) -> tuple[ErrorLayer, ErrorStage]:
    """Suy đoán tầng trách nhiệm và bước trong chuỗi khi thất bại.

    Ưu tiên tin tưởng layer/stage đi kèm ngoại lệ miền (phân loại LLM, tầng dịch engine đều sẽ điền);
    nếu không thì hạ cấp xuống "đoán bước theo trạng thái hiện tại của tài liệu", tầng trách nhiệm gán cho engine (ngoại lệ trần
    thoát ra trong quá trình trích xuất/nạp zleap-sag, như jsonschema.ValidationError, hầu hết xảy ra ở phía engine).
    """
    if isinstance(e, ApiError) and e.layer is not None and e.stage is not None:
        return e.layer, e.stage
    stage = _STATUS_TO_STAGE.get(current_status, ErrorStage.EXTRACT)
    return ErrorLayer.ENGINE, stage


async def process_document(session: AsyncSession, job: Job, *, engine_manager: EngineManager, job_queue=None) -> None:
    document = await session.get(Document, job.document_id) if job.document_id else None
    if document is None:
        raise NotFoundError("Tài liệu không tồn tại")
    async with acquire_source_processing_lease(SessionLocal, document.source_id, job.id):
        await _process_document_unlocked(
            session,
            job,
            engine_manager=engine_manager,
            job_queue=job_queue,
        )


async def _process_document_unlocked(
    session: AsyncSession, job: Job, *, engine_manager: EngineManager, job_queue=None
) -> None:
    """Publish canonical search readiness; legacy event extraction is explicitly opt-in."""
    document = await session.get(Document, job.document_id) if job.document_id else None
    if document is None:
        raise NotFoundError("Tài liệu không tồn tại")
    source = await session.get(Source, document.source_id)
    if source is None:
        raise NotFoundError("Nguồn dữ liệu không tồn tại")
    checkpoint = ProcessCheckpoint.from_payload(job.payload)
    scheduler_yield_reason: str | None = None
    run_id = (job.payload or {}).get("run_id")
    if run_id:
        ingestion_run = await session.get(IngestionRun, run_id)
        if ingestion_run and ingestion_run.status != "RUNNING":
            ingestion_run.status = "RUNNING"
            ingestion_run.started_at = datetime.now(UTC)
            await session.commit()

    # A worker retry reuses the document row. Clear the previous attempt's
    # failure before parsing can block for a long time, so active processing
    # never carries a stale terminal error.
    if (
        document.error is not None
        or document.error_layer is not None
        or document.error_stage is not None
    ) and document.status not in _CONTROL_TRANSITION_STATES:
        document.error = None
        document.error_layer = None
        document.error_stage = None
        await session.commit()

    async def refresh_payload() -> dict:
        await session.refresh(job, attribute_names=["payload"])
        return dict(job.payload or {})

    async def on_stage(stage: str) -> None:
        await session.refresh(document)
        if document.status in _CONTROL_TRANSITION_STATES:
            return
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
        if run_id:
            ingestion_run = await session.get(IngestionRun, run_id)
            if ingestion_run:
                ingestion_run.current_stage = stage.upper()
                session.add(
                    StageRun(
                        id=str(uuid.uuid4()),
                        run_id=run_id,
                        stage=stage,
                        status="SUCCESS",
                        duration_ms=1.0,
                        metrics_json={},
                    )
                )
        await session.commit()

    async def on_parser_state(state: dict) -> None:
        await session.refresh(document)
        if document.status in _CONTROL_TRANSITION_STATES:
            return
        document.status = DocumentStatus.LOADING
        document.progress = max(document.progress, 25)
        for field, value in _parser_state_values(state).items():
            setattr(document, field, value)
        job.progress = document.progress / 100
        job.payload = {**(await refresh_payload()), "document_parser": state}
        await session.commit()

    async def _advance_canonical_stage(
        stage_name: str,
        min_progress: int,
        doc_status: DocumentStatus | None = None,
    ) -> None:
        await session.refresh(document)
        if document.status in _CONTROL_TRANSITION_STATES:
            return
        if doc_status is not None and document.status in (
            DocumentStatus.PENDING,
            DocumentStatus.LOADING,
            DocumentStatus.EXTRACTING,
        ):
            document.status = doc_status
        document.progress = max(int(document.progress or 0), min_progress)
        job.progress = document.progress / 100
        if run_id:
            ing_run_row = await session.get(IngestionRun, run_id)
            if ing_run_row is not None:
                ing_run_row.current_stage = stage_name
        await session.commit()

    async def on_checkpoint(value: ProcessCheckpoint) -> None:
        nonlocal checkpoint
        checkpoint = value
        await session.refresh(document)
        job.payload = value.merge_payload(await refresh_payload())
        document.chunk_count = len(value.chunk_ids)
        document.event_count = value.event_count
        document.sag_source_id = value.source_id
        document.token_usage = value.token_usage
        if document.status not in _CONTROL_TRANSITION_STATES:
            total = len(value.chunk_ids)
            completed = len(value.processed_chunk_ids)
            document.progress = 20 + round(80 * completed / total) if total else 20
            job.progress = document.progress / 100
        await session.commit()

    async def should_pause() -> bool:
        nonlocal scheduler_yield_reason
        async with SessionLocal() as control_session:
            current_job = await control_session.get(Job, job.id)
            if current_job is None:
                return True
            if current_job.status == JobStatus.PAUSED or (current_job.payload or {}).get("pause_requested"):
                scheduler_yield_reason = None
                return True
        if job_queue is not None and job_queue.source_maintenance_requested(source.id):
            scheduler_yield_reason = SOURCE_MAINTENANCE
            return True
        # Nguồn dữ liệu đang bị xóa: yêu cầu tác vụ xử lý đang chạy nhường đường và giải phóng lease xử lý sớm nhất có thể,
        # để việc xóa đồng bộ có thể hoàn thành trong cửa sổ HTTP, thay vì chờ đợi thụ động quá trình parse/extract kết thúc tự nhiên.
        # Ở đây xử lý theo ngữ nghĩa "tạm dừng" (không thiết lập lý do yield) —— tài liệu sau đó sẽ bị xóa cascade theo nguồn dữ liệu.
        if job_queue is not None and job_queue.source_stop_requested(source.id):
            return True
        return False

    async def _pause_or_yield() -> None:
        """Chuyển tài liệu hiện tại sang PAUSED hoặc nhường lượt, dùng chung cho cả hai giai đoạn parse/extract."""
        await session.refresh(document)
        if scheduler_yield_reason == SOURCE_MAINTENANCE and document.status not in _CONTROL_TRANSITION_STATES:
            raise JobYielded(SOURCE_MAINTENANCE)
        if document.status in _DELETE_CONTROL_STATES:
            raise JobPaused()
        expected_status = document.status
        paused = await session.execute(
            update(Document)
            .where(
                Document.id == document.id,
                Document.status == expected_status,
            )
            .values(status=DocumentStatus.PAUSED, error=None)
            .execution_options(synchronize_session=False)
        )
        if paused.rowcount != 1:
            await _yield_after_document_transition_lost(session, document)
        await session.commit()
        await session.refresh(document)
        raise JobPaused()

    try:
        prepared = None
        parser_stage = False
        initial_payload = dict(job.payload or {})
        target_storage_path = initial_payload.get("storage_path") or document.storage_path
        remote_object_sourced = bool(initial_payload.get("remote_object_sourced"))
        remote_source_url = (
            initial_payload.get("remote_source_url")
            if isinstance(initial_payload.get("remote_source_url"), str)
            else None
        )
        job_project_id = (
            str(initial_payload.get("project_id"))
            if initial_payload.get("project_id")
            else document.project_id
        )
        canonical_checkpoint = str(initial_payload.get("canonical_checkpoint_stage") or "").upper()
        skip_initial_prepare = bool(
            run_id
            and not settings.document_event_extraction_enabled
            and canonical_checkpoint in ("PARSE", "DEDUP_TEMPORAL")
        )
        if not checkpoint.chunk_ids and not skip_initial_prepare:
            from sag_api.parsing.service import has_cached_prepared_document

            needs_remote_fetch = bool(
                remote_object_sourced
                and remote_source_url
                and not (
                    target_storage_path
                    and (
                        os.path.isfile(target_storage_path)
                        or has_cached_prepared_document(target_storage_path, settings)
                    )
                )
            )
            if needs_remote_fetch and remote_source_url:
                import time as _time

                from sag_api.parsing.remote_object import stream_remote_object_to_snapshot

                await _advance_canonical_stage("RECEIVE", 10, DocumentStatus.LOADING)
                dl_start = _time.monotonic()
                try:
                    target_storage_path = await stream_remote_object_to_snapshot(
                        remote_source_url,
                        target_path=target_storage_path,
                        original_filename=str(
                            initial_payload.get("original_filename") or document.filename
                        ),
                        expected_sha256=str(initial_payload.get("expected_sha256") or ""),
                        expected_size_bytes=int(
                            initial_payload.get("expected_size_bytes") or document.size_bytes or 0
                        ),
                        settings=settings,
                        should_pause=should_pause,
                    )
                except ParsePaused:
                    await _pause_or_yield()
                dl_ms = round((_time.monotonic() - dl_start) * 1000, 2)
                if run_id:
                    session.add(
                        StageRun(
                            id=str(uuid.uuid4()),
                            run_id=run_id,
                            stage="receive",
                            status="SUCCESS",
                            duration_ms=dl_ms,
                            metrics_json={
                                "byte_size": int(
                                    initial_payload.get("expected_size_bytes")
                                    or document.size_bytes
                                    or 0
                                ),
                                "source": "remote_object_stream",
                            },
                        )
                    )
                    await session.commit()

            parser_stage = True
            await _advance_canonical_stage("PARSE", 25, DocumentStatus.LOADING)
            try:
                prepared = await prepare_document(
                    target_storage_path,
                    settings,
                    state=(job.payload or {}).get("document_parser"),
                    on_state=on_parser_state,
                    should_pause=should_pause,
                    remote_source_url=remote_source_url,
                    project_id=job_project_id,
                )
            except ParsePaused:
                await _pause_or_yield()
            parser_stage = False
            parser_state = (job.payload or {}).get("document_parser")
            for field, value in _prepared_parser_values(prepared, parser_state).items():
                setattr(document, field, value)
            if remote_source_url:
                scrubbed_payload = await refresh_payload()
                if "remote_source_url" in scrubbed_payload:
                    scrubbed_payload.pop("remote_source_url", None)
                    job.payload = scrubbed_payload
            await session.commit()
            if prepared.fallback_from:
                log.warning(
                    "Phân tích tài liệu đã hạ cấp doc=%s job=%s from=%s to=%s cached=%s error=%s",
                    document.id,
                    getattr(job, "id", None),
                    prepared.fallback_from,
                    prepared.provider,
                    prepared.cached,
                    _redact_parser_reason(prepared.fallback_error),
                )

        # Phase 2 Pipeline: Canonical Extraction -> Dedup & Temporal -> Search Units
        if run_id:
            current_pipeline_stage = "PARSE"
            try:
                ing_run = await session.get(IngestionRun, run_id)
                if not ing_run:
                    raise RuntimeError(f"IngestionRun {run_id} not found for Phase 2 processing (fail-closed)")
                doc_ver = await session.get(DocumentVersion, ing_run.document_version_id)
                if not doc_ver:
                    raise RuntimeError(
                        f"DocumentVersion {ing_run.document_version_id} not found for IngestionRun {run_id} (fail-closed)"
                    )

                # 2A: Parse & Persist Canonical Blocks (35%)
                if canonical_checkpoint not in ("PARSE", "DEDUP_TEMPORAL"):
                    if await should_pause():
                        await _pause_or_yield()
                    current_pipeline_stage = "PARSE"
                    await _advance_canonical_stage("PARSE", 35, DocumentStatus.LOADING)
                    # [P2 Fix]: Safe prepared document resolution on resume to prevent raw binary UTF-8 decoding
                    if prepared is None:
                        try:
                            prepared = await prepare_document(
                                target_storage_path,
                                settings,
                                state=(job.payload or {}).get("document_parser"),
                                on_state=on_parser_state,
                                should_pause=should_pause,
                                project_id=job_project_id,
                            )
                        except ParsePaused:
                            await _pause_or_yield()

                    if prepared is None or not prepared.path or not os.path.exists(prepared.path):
                        raise RuntimeError(f"Prepared markdown path missing for document {document.id}")

                    with open(prepared.path, "r", encoding="utf-8", errors="replace") as f:
                        doc_content = f.read()

                    from sag_api.services.canonical_service import parse_and_persist_document_content
                    await parse_and_persist_document_content(
                        session, doc_ver.id, doc_content, run_id=run_id
                    )
                    job.payload = {
                        **(await refresh_payload()),
                        "canonical_checkpoint_stage": "PARSE",
                    }
                    await session.commit()

                # 2B: Dedup & Temporal Lineage (65%)
                if canonical_checkpoint != "DEDUP_TEMPORAL":
                    if await should_pause():
                        await _pause_or_yield()
                    current_pipeline_stage = "DEDUP"
                    await _advance_canonical_stage("DEDUP_TEMPORAL", 65, DocumentStatus.EXTRACTING)
                    ing_run = await session.get(IngestionRun, run_id) or ing_run
                    doc_ver = await session.get(DocumentVersion, ing_run.document_version_id) or doc_ver
                    from sag_api.services.dedup_and_temporal_service import run_dedup_and_temporal_stage
                    await run_dedup_and_temporal_stage(
                        session,
                        document_version=doc_ver,
                        document_id=document.id,
                        project_id=ing_run.project_id,
                        run_id=run_id,
                    )
                    job.payload = {
                        **(await refresh_payload()),
                        "canonical_checkpoint_stage": "DEDUP_TEMPORAL",
                    }
                    await session.commit()

                # 2C: Embed & Search Index Units into Qdrant (85%)
                if await should_pause():
                    await _pause_or_yield()
                await _advance_canonical_stage("INDEX_SEARCH", 85, DocumentStatus.EXTRACTING)
                ing_run = await session.get(IngestionRun, run_id) or ing_run
                doc_ver = await session.get(DocumentVersion, ing_run.document_version_id) or doc_ver

                # [P1 Fix]: Fail-closed on missing security_partition_id
                sec_partition = (doc_ver.metadata_json or {}).get("security_partition_id")
                if not sec_partition or not str(sec_partition).strip():
                    raise ValueError(f"Missing security_partition_id for document_version {doc_ver.id} (fail-closed)")

                # [P1 Fix]: Worker passes real Qdrant client and Embedder to search index stage
                current_pipeline_stage = "EMBED"
                embedder = None
                try:
                    embedder = await engine_manager.get_sag_embedding(source.sag_source_config_id, source)
                except Exception as emb_exc:
                    log.warning("Could not get sag embedding client: %s", sanitize_error_message(emb_exc))

                current_pipeline_stage = "INDEX"
                from sag_api.services.search_index_service import run_search_indexing_stage
                shared_qdrant_client = None
                if engine_manager is not None and hasattr(engine_manager, "get_search_unit_qdrant_client"):
                    try:
                        shared_qdrant_client = await engine_manager.get_search_unit_qdrant_client()
                    except Exception:
                        shared_qdrant_client = None
                if shared_qdrant_client is not None:
                    search_units = await run_search_indexing_stage(
                        session,
                        project_id=ing_run.project_id,
                        document_version=doc_ver,
                        security_partition_id=sec_partition,
                        tenant_id=ing_run.tenant_id,
                        qdrant_client=shared_qdrant_client,
                        embedder=embedder,
                        run_id=run_id,
                    )
                else:
                    qdrant_url = getattr(settings, "sag_qdrant_url", "http://localhost:6333").rstrip("/")
                    qdrant_headers = {}
                    if getattr(settings, "sag_qdrant_api_key", None):
                        qdrant_headers["api-key"] = settings.sag_qdrant_api_key
                    async with httpx.AsyncClient(base_url=qdrant_url, headers=qdrant_headers, timeout=30.0) as qdrant_client:
                        search_units = await run_search_indexing_stage(
                            session,
                            project_id=ing_run.project_id,
                            document_version=doc_ver,
                            security_partition_id=sec_partition,
                            tenant_id=ing_run.tenant_id,
                            qdrant_client=qdrant_client,
                            embedder=embedder,
                            run_id=run_id,
                        )
                next_payload = await refresh_payload()
                next_payload.pop("canonical_checkpoint_stage", None)
                job.payload = next_payload
                await session.commit()
            except ParsePaused:
                await _pause_or_yield()
            except (JobPaused, JobYielded):
                raise
            except Exception as pipe_err:
                pipe_err_str = sanitize_error_message(pipe_err)
                log.error("Canonical pipeline failed run_id=%s stage=%s error_type=%s cause_type=%s retryable=%s",
                          run_id, current_pipeline_stage, type(pipe_err).__name__,
                          type(pipe_err.__cause__).__name__ if pipe_err.__cause__ else None,
                          is_retryable_error(pipe_err))
                await session.rollback()

                # Determine accurate error layer & stage
                err_layer = ErrorLayer.API
                if current_pipeline_stage == "PARSE":
                    err_stage = ErrorStage.PARSE
                elif current_pipeline_stage == "DEDUP":
                    err_stage = ErrorStage.EXTRACT
                elif current_pipeline_stage == "EMBED":
                    err_stage = ErrorStage.EMBED
                    err_layer = ErrorLayer.ENGINE
                elif current_pipeline_stage == "INDEX":
                    err_stage = ErrorStage.PERSIST
                    err_layer = ErrorLayer.STORE
                else:
                    err_stage = ErrorStage.EXTRACT

                if isinstance(pipe_err, ApiError):
                    if pipe_err.layer:
                        err_layer = pipe_err.layer
                    if pipe_err.stage:
                        err_stage = pipe_err.stage

                err_layer_val = err_layer.value if hasattr(err_layer, "value") else str(err_layer)
                err_stage_val = err_stage.value if hasattr(err_stage, "value") else str(err_stage)
                err_code = "EMPTY_INDEX" if "EMPTY_INDEX" in str(pipe_err) else f"{err_layer_val}_{err_stage_val}_FAILED"

                ing_run = await session.get(IngestionRun, run_id) if run_id else None
                if ing_run:
                    ing_run.status = "FAILED"
                    ing_run.error_layer = err_layer_val
                    ing_run.error_stage = err_stage_val
                    ing_run.error_code = err_code
                    ing_run.error_message = pipe_err_str
                    session.add(ing_run)

                    stage_name = "INDEX_SEARCH" if current_pipeline_stage == "INDEX" else (
                        "CANONICAL_EXTRACTION" if current_pipeline_stage == "PARSE" else (
                            "DEDUP_TEMPORAL" if current_pipeline_stage == "DEDUP" else "INGEST"
                        )
                    )
                    failed_stage_run = StageRun(
                        id=str(uuid.uuid4()),
                        run_id=run_id,
                        stage=stage_name,
                        status="FAILED",
                        error_message=pipe_err_str,
                        duration_ms=0.0,
                        metrics_json={"error": pipe_err_str},
                    )
                    session.add(failed_stage_run)

                    doc_ver = await session.get(DocumentVersion, ing_run.document_version_id)
                    if doc_ver:
                        doc_ver.search_status = "INDEX_FAILED"
                        doc_ver.search_ready_at = None
                        session.add(doc_ver)
                    await session.commit()

                # Fail the document and preserve error layer and stage
                raise ApiError(
                    pipe_err_str,
                    layer=err_layer,
                    stage=err_stage,
                    retryable=is_retryable_error(pipe_err),
                ) from pipe_err

        if run_id and not settings.document_event_extraction_enabled:
            if doc_ver.search_status != "SEARCH_READY":
                raise RuntimeError("Canonical search manifest was not verified")
            if await should_pause():
                await _pause_or_yield()
            outcome = ProcessOutcome(
                source_id=document.sag_source_id,
                chunk_count=len(search_units),
                chunk_ids=[unit.id for unit in search_units],
            )
        else:
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
                original_path=target_storage_path if prepared is not None else None,
                document_version_id=doc_ver.id if (run_id and "doc_ver" in locals() and doc_ver is not None) else None,
            )
        if outcome.paused:
            await _pause_or_yield()
    except (JobPaused, JobYielded):
        raise
    except Exception as e:  # noqa: BLE001 - Ghi nhận vào tài liệu trước khi ném tiếp cho worker
        await session.refresh(document)
        try:
            await session.refresh(job)
        except Exception:
            pass
        if document.status in _CONTROL_TRANSITION_STATES or document.status == DocumentStatus.PAUSED:
            await _yield_after_document_transition_lost(session, document)
        layer, stage = _classify_document_failure(e, document.status)
        expected_status = document.status
        raw_message = getattr(e, "message", None) or str(e)
        message = sanitize_error_message(raw_message)
        public_message = message
        parser_failure_values: dict[str, str | None] = {}
        parser_state = (job.payload or {}).get("document_parser")
        parser_failed = parser_stage
        if parser_failed:
            public_message = _redact_parser_reason(message) or "Phân tích cú pháp tài liệu thất bại"
        if (
            parser_failed
            and isinstance(parser_state, dict)
            and str(parser_state.get("status") or "").lower() == "fallback_failed"
        ):
            parser_failure_values = _parser_state_values(parser_state)
            parser_failure_values["parser_status"] = "failed"
            parser_failure_values["fallback_reason"] = _redact_parser_reason(message)
        # Kiểm tra xem tài liệu đã đạt SEARCH_READY trước đó hay chưa (Phase 2C đã commit)
        is_search_ready = False
        if run_id:
            ing_run_chk = await session.get(IngestionRun, run_id)
            if ing_run_chk:
                ver_chk = await session.get(DocumentVersion, ing_run_chk.document_version_id)
                if ver_chk and ver_chk.search_status == "SEARCH_READY":
                    is_search_ready = True

        doc_target_status = DocumentStatus.READY if is_search_ready else DocumentStatus.FAILED

        failed = await session.execute(
            update(Document)
            .where(
                Document.id == document.id,
                Document.status == expected_status,
            )
            .values(
                status=doc_target_status,
                error=public_message,
                error_layer=layer.value,
                error_stage=stage.value,
                **parser_failure_values,
            )
            .execution_options(synchronize_session=False)
        )
        if failed.rowcount != 1:
            await _yield_after_document_transition_lost(session, document)
        log.warning(
            "Xử lý tài liệu thất bại doc=%s layer=%s stage=%s error=%s",
            document.id,
            layer.value,
            stage.value,
            public_message,
        )
        if run_id:
            ingestion_run = await session.get(IngestionRun, run_id)
            if ingestion_run:
                ingestion_run.status = "FAILED"
                ingestion_run.error_layer = layer.value
                ingestion_run.error_stage = stage.value
                if not ingestion_run.error_code:
                    ingestion_run.error_code = "EMPTY_INDEX" if "EMPTY_INDEX" in raw_message else f"{layer.value}_{stage.value}_FAILED"
                ingestion_run.error_message = public_message
                ver = await session.get(DocumentVersion, ingestion_run.document_version_id)
                if ver:
                    # Nếu Phase 2C đã hoàn tất và SEARCH_READY, lỗi extraction/LLM hoặc enrichment
                    # phía sau không được phép hạ cấp hoặc xóa trạng thái tìm kiếm
                    if is_search_ready:
                        ver.status = "SEARCH_READY"
                        ver.knowledge_status = "FAILED"
                    else:
                        ver.status = "FAILED"
                        if ver.search_status != "INDEX_FAILED":
                            ver.search_status = "FAILED"
                        ver.search_ready_at = None
                    session.add(ver)
        await session.commit()
        await _finalize_research_task_for_run(session, run_id)
        raise

    await session.refresh(document)
    if document.status in _DELETE_CONTROL_STATES:
        raise JobPaused()
    if document.status == DocumentStatus.PAUSING:
        document.status = DocumentStatus.PAUSED
        await session.commit()
        raise JobPaused()
    effective_token_usage = int(outcome.token_usage or 0)
    if run_id and "doc_ver" in locals() and doc_ver is not None:
        from sag_api.core.token_usage import TokenLedger, sync_document_token_usage

        if effective_token_usage > 0:
            legacy_ledger = TokenLedger()
            legacy_ledger.record_event(
                component="legacy_event_extraction",
                model=getattr(settings, "llm_model", None),
                delta={
                    "llm_input_tokens": effective_token_usage,
                    "total_tokens": effective_token_usage,
                    "llm_calls": 1,
                },
            )
            ver_obj = await session.get(DocumentVersion, doc_ver.id)
            if ver_obj is not None:
                ver_obj.metadata_json = {
                    **(ver_obj.metadata_json or {}),
                    "legacy_extraction_token_usage": legacy_ledger.to_summary(),
                }
                session.add(ver_obj)
                await session.flush()
        synced_usage = await sync_document_token_usage(session, doc_ver.id)
        effective_token_usage = max(effective_token_usage, int(synced_usage["totals"]["total_tokens"]))

    completed = await session.execute(
        update(Document)
        .where(
            Document.id == document.id,
            Document.status.in_(
                [
                    DocumentStatus.PENDING,
                    DocumentStatus.LOADING,
                    DocumentStatus.EXTRACTING,
                ]
            ),
        )
        .values(
            status=DocumentStatus.READY,
            chunk_count=outcome.chunk_count,
            event_count=outcome.event_count,
            sag_source_id=outcome.source_id,
            progress=100,
            token_usage=effective_token_usage,
            error=None,
            error_layer=None,
            error_stage=None,
        )
        .execution_options(synchronize_session=False)
    )
    if completed.rowcount != 1:
        await _yield_after_document_transition_lost(session, document)
    # Bộ đếm tổng hợp của nguồn dữ liệu được cập nhật bằng SQL nguyên tử để tránh mất mát do đọc-ghi đồng thời
    await session.execute(
        update(Source)
        .where(Source.id == source.id)
        .values(
            chunk_count=Source.chunk_count + outcome.chunk_count,
            event_count=Source.event_count + outcome.event_count,
        )
    )
    await touch_source_revision(session, source.id)
    if run_id:
        ingestion_run = await session.get(IngestionRun, run_id)
        if ingestion_run:
            ingestion_run.status = "SUCCEEDED"
            ingestion_run.current_stage = "COMPLETE"
            ingestion_run.completed_at = datetime.now(UTC)
            ver = await session.get(DocumentVersion, ingestion_run.document_version_id)
            if ver:
                # Chỉ công nhận SEARCH_READY khi Phase 2C đã xác minh manifest nhất quán, tuyệt đối không dùng chunk_count
                if ver.search_status == "SEARCH_READY":
                    ver.status = "SEARCH_READY"
                    if not ver.search_ready_at:
                        ver.search_ready_at = datetime.now(UTC)
                else:
                    ver.status = "FAILED"
                    if ver.search_status != "INDEX_FAILED":
                        ver.search_status = "FAILED"
                    ver.search_ready_at = None
                # knowledge_status duy trì độc lập theo hợp đồng Phase 0 (chỉ chuyển khi tree/graph hoàn tất)
                # The independent producer owns knowledge_status, including DATA_READY/QUEUED.
    await session.commit()
    await session.refresh(document)
    await _finalize_research_task_for_run(session, run_id)

    # Post-index Disk GC: when the immutable raw binary lives permanently on R2,
    # reclaim heavy binary snapshots on local SAG disk while retaining the lightweight
    # `.parsed.<signature>.md` sidecar cache for fast re-chunking / reprocessing.
    from sag_api.parsing.service import has_cached_prepared_document

    if (
        getattr(settings, "remote_snapshot_gc_enabled", True)
        and remote_object_sourced
        and target_storage_path
        and prepared is not None
        and prepared.path != target_storage_path
        and os.path.splitext(target_storage_path)[1].lower() not in {".md", ".markdown"}
        and os.path.isfile(prepared.path)
        and os.path.isfile(target_storage_path)
        and has_cached_prepared_document(target_storage_path, settings)
    ):
        try:
            import asyncio as _asyncio

            from sqlalchemy import select as _select

            active_sibling = await session.scalar(
                _select(Document.id)
                .where(
                    Document.storage_path == target_storage_path,
                    Document.id != document.id,
                    Document.status.in_(
                        [
                            DocumentStatus.PENDING,
                            DocumentStatus.LOADING,
                            DocumentStatus.EXTRACTING,
                            DocumentStatus.PAUSED,
                            DocumentStatus.PAUSING,
                        ]
                    ),
                )
                .limit(1)
            )
            if active_sibling is None:
                await _asyncio.to_thread(os.remove, target_storage_path)
        except Exception as gc_err:  # noqa: BLE001
            log.debug("Skipped post-index raw snapshot GC for %s: %s", document.id, gc_err)

    log.info(
        "Xử lý tài liệu hoàn thành doc=%s parser=%s cached=%s chunks=%d events=%d tokens=%d",
        document.id,
        prepared.provider if prepared is not None else "checkpoint",
        prepared.cached if prepared is not None else True,
        outcome.chunk_count,
        outcome.event_count,
        outcome.token_usage,
    )

    if job_queue is not None:
        try:
            from sag_api.services.universe_service import schedule_universe_refresh

            await schedule_universe_refresh(
                session,
                job_queue,
                source_id=source.id,
                reason="document_processed",
            )
        except Exception as uni_err:
            log.warning(
                "Secondary queue universe refresh failed or delayed doc=%s source=%s: %s",
                document.id,
                source.id,
                sanitize_error_message(uni_err),
            )


async def delete_document_task(
    session: AsyncSession, job: Job, *, engine_manager: EngineManager, job_queue=None
) -> None:
    if not job.source_id:
        raise NotFoundError("Tác vụ xóa thiếu nguồn dữ liệu")
    async with acquire_source_exclusive_lease(SessionLocal, job.source_id, f"document-delete:{job.id}"):
        await _delete_document_task_unlocked(session, job, engine_manager=engine_manager, job_queue=job_queue)


async def _delete_document_task_unlocked(
    session: AsyncSession, job: Job, *, engine_manager: EngineManager, job_queue=None
) -> None:
    """Dọn dẹp dữ liệu phái sinh, tệp tin và bản ghi của tài liệu đã lấy được cửa sổ bảo trì nguồn dữ liệu."""
    target_document_id = job.document_id or str((job.payload or {}).get("target_document_id") or "")
    if not target_document_id:
        raise NotFoundError("Tác vụ xóa thiếu tài liệu")
    document = await session.get(Document, target_document_id)
    if document is None:
        return
    source = await session.get(Source, document.source_id)
    if source is None:
        raise NotFoundError("Nguồn dữ liệu không tồn tại")

    await session.refresh(document)
    derived_source_ids = {
        value.strip()
        for value in (job.payload or {}).get("derived_source_ids", [])
        if isinstance(value, str) and value.strip()
    }
    if document.sag_source_id:
        derived_source_ids.add(document.sag_source_id)
    await _cleanup_legacy_engine_document_data(
        engine_manager,
        source,
        document,
        sorted(derived_source_ids),
    )

    # Phase 2 Cleanup: Delete Qdrant points for all document versions belonging to this document
    from sqlalchemy import select

    from sag_api.db.models.routing_rag import DocumentVersion
    version_ids = (
        await session.execute(
            select(DocumentVersion.id).where(DocumentVersion.document_id == document.id)
        )
    ).scalars().all()
    if version_ids and document.project_id:
        qdrant_url = getattr(settings, "sag_qdrant_url", "http://localhost:6333").rstrip("/")
        qdrant_headers = {}
        if getattr(settings, "sag_qdrant_api_key", None):
            qdrant_headers["api-key"] = settings.sag_qdrant_api_key
        try:
            async with httpx.AsyncClient(base_url=qdrant_url, headers=qdrant_headers, timeout=30.0) as qdrant_client:
                from sag_api.services.search_index_service import delete_document_points_from_qdrant
                await delete_document_points_from_qdrant(
                    qdrant_client,
                    project_id=document.project_id,
                    document_version_ids=version_ids,
                )
        except Exception as qdel_err:
            log.error("Failed to delete Qdrant points on document deletion %s: %s", document.id, qdel_err)
            raise RuntimeError(f"Qdrant cleanup failed during document deletion: {qdel_err}") from qdel_err

    path = document.storage_path
    job.payload = {**(job.payload or {}), "target_document_id": document.id}
    job.document_id = None
    await session.flush()
    await session.delete(document)
    await session.flush()
    remaining_path_refs = 0
    if path:
        from sqlalchemy import func as sa_func
        from sag_api.db.models.routing_rag import SourceSnapshot

        doc_refs = int(
            await session.scalar(
                select(sa_func.count()).select_from(Document).where(Document.storage_path == path)
            )
            or 0
        )
        snap_refs = int(
            await session.scalar(
                select(sa_func.count())
                .select_from(SourceSnapshot)
                .where(SourceSnapshot.storage_uri == path)
            )
            or 0
        )
        remaining_path_refs = doc_refs + snap_refs
    from sag_api.services.document_service import _refresh_source_counts

    await _refresh_source_counts(session, source)
    tree_job = None
    if job_queue is not None and document.tenant_id and document.project_id:
        from sag_api.services.universe_tree_service import enqueue_project_tree_rebuild

        tree_job = await enqueue_project_tree_rebuild(
            session, job_queue, tenant_id=document.tenant_id, project_ids=[document.project_id],
            reason="document_deleted", persist_only=True)
    await session.commit()
    if tree_job is not None:
        from sag_api.services.document_service import _enqueue_persisted_job

        await _enqueue_persisted_job(job_queue, tree_job.id)
    if path and remaining_path_refs == 0:
        from sag_api.parsing.service import parsed_sidecar_paths

        for candidate in [path, *parsed_sidecar_paths(path)]:
            try:
                if os.path.exists(candidate):
                    os.remove(candidate)
            except OSError:
                pass
    if job_queue is not None:
        from sag_api.services.universe_service import schedule_universe_refresh

        try:
            await schedule_universe_refresh(
                session,
                job_queue,
                source_id=source.id,
                reason="document_deleted",
            )
        except Exception:  # noqa: BLE001 - Làm mới view phái sinh không ảnh hưởng đến kết quả xóa cốt lõi
            log.exception("Tài liệu đã bị xóa, nhưng lập lịch làm mới knowledge universe thất bại source=%s", source.id)
            # Core deletion was committed above. Roll back only the optional
            # refresh scheduling transaction so the worker can still persist
            # the delete Job's SUCCEEDED terminal state and release maintenance.
            await session.rollback()


def _should_cleanup_legacy_engine_data(document: Document) -> bool:
    """Return True only when legacy zleap-sag relational/event records could exist for this document."""
    if not document.project_id:
        return True
    if settings.document_event_extraction_enabled:
        return True
    return int(document.event_count or 0) > 0


async def _cleanup_legacy_engine_document_data(
    engine_manager: EngineManager | None,
    source: Source,
    document: Document,
    derived_source_ids: list[str],
) -> None:
    if not derived_source_ids or engine_manager is None or not _should_cleanup_legacy_engine_data(document):
        return
    for derived_source_id in derived_source_ids:
        try:
            await engine_manager.delete_document_data(
                source.sag_source_config_id,
                derived_source_id,
                source=source,
            )
        except Exception as cleanup_exc:
            if document.project_id and "sag_schema_meta" in str(cleanup_exc):
                log.warning(
                    "Skipping legacy zleap-sag cleanup for project document %s because sag_schema_meta is uninitialized",
                    document.id,
                )
                return
            raise


async def reprocess_document_task(
    session: AsyncSession, job: Job, *, engine_manager: EngineManager, job_queue=None
) -> None:
    if not job.source_id:
        raise NotFoundError("Tác vụ xử lý lại thiếu nguồn dữ liệu")
    async with acquire_source_exclusive_lease(SessionLocal, job.source_id, f"document-reprocess:{job.id}"):
        await _reprocess_document_task_unlocked(session, job, engine_manager=engine_manager, job_queue=job_queue)


async def _reprocess_document_task_unlocked(
    session: AsyncSession, job: Job, *, engine_manager: EngineManager, job_queue=None
) -> None:
    """Trong cửa sổ bảo trì nguồn dữ liệu dọn dẹp dữ liệu phái sinh cũ, sau đó xếp vào hàng đợi tác vụ xử lý tài liệu thông thường."""
    target_document_id = job.document_id or str((job.payload or {}).get("target_document_id") or "")
    if not target_document_id:
        raise NotFoundError("Tác vụ xử lý lại thiếu tài liệu")
    document = await session.get(Document, target_document_id)
    if document is None:
        raise NotFoundError("Tài liệu không tồn tại")
    source = await session.get(Source, document.source_id)
    if source is None:
        raise NotFoundError("Nguồn dữ liệu không tồn tại")

    payload = dict(job.payload or {})
    derived_source_ids = sorted(
        {value.strip() for value in payload.get("derived_source_ids", []) if isinstance(value, str) and value.strip()}
    )
    if not payload.get("cleanup_completed"):
        await _cleanup_legacy_engine_document_data(
            engine_manager,
            source,
            document,
            derived_source_ids,
        )

    await session.refresh(document)
    if document.status in _DELETE_CONTROL_STATES:
        job.payload = {
            **payload,
            "cleanup_completed": True,
        }
        await session.commit()
        return

    process_job_id = payload.get("process_job_id")
    process_job = await session.get(Job, process_job_id) if isinstance(process_job_id, str) and process_job_id else None
    if process_job is None:
        process_job = Job(
            type=JobType.PROCESS_DOCUMENT,
            source_id=source.id,
            document_id=document.id,
            status=JobStatus.QUEUED,
        )
        session.add(process_job)
        await session.flush()

    # Create an IngestionRun for Phase 2 processing on reprocess
    from sqlalchemy import select

    from sag_api.db.models.routing_rag import DocumentVersion, IngestionRun
    latest_ver = (
        await session.execute(
            select(DocumentVersion)
            .where(DocumentVersion.document_id == document.id)
            .order_by(DocumentVersion.version_no.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    new_run_id = None
    process_job_payload = {
        **(process_job.payload or {}),
    }
    if document.storage_path and "storage_path" not in process_job_payload:
        process_job_payload["storage_path"] = document.storage_path
    for forwarded_key in (
        "remote_object_sourced",
        "remote_source_url",
        "expected_sha256",
        "expected_size_bytes",
        "original_filename",
    ):
        if forwarded_key in payload and payload[forwarded_key] is not None:
            process_job_payload[forwarded_key] = payload[forwarded_key]
    if latest_ver and document.project_id:
        new_run_id = str(uuid.uuid4())
        tenant_id = document.tenant_id or "tenant_default"
        idempotency_key = f"reprocess:{document.id}:{latest_ver.version_no}:{new_run_id[:8]}"
        payload_hash = latest_ver.file_hash or "reprocess"
        document.is_active = True
        latest_ver.status = "RECEIVED"
        latest_ver.search_status = "PENDING"
        latest_ver.search_ready_at = None
        if latest_ver.knowledge_status in ("FAILED", "REVOKED"):
            latest_ver.knowledge_status = "NOT_STARTED"
        ing_run = IngestionRun(
            id=new_run_id,
            tenant_id=tenant_id,
            project_id=document.project_id,
            document_version_id=latest_ver.id,
            idempotency_key=idempotency_key,
            payload_hash=payload_hash,
            current_stage="RECEIVE",
            status="QUEUED",
            attempt_count=1,
            max_attempts=3,
        )
        session.add(ing_run)
        await session.flush()
        process_job_payload["run_id"] = new_run_id
        process_job_payload["document_version_id"] = latest_ver.id
        process_job_payload["project_id"] = document.project_id
        process_job_payload["tenant_id"] = tenant_id
    else:
        process_job_payload["phase2_skipped"] = True
        process_job_payload["phase2_skip_reason"] = "MISSING_PROJECT_OR_VERSION"
        log.warning(
            "Reprocess document %s lacks project_id (%s) or DocumentVersion (%s); Phase 2 skipped with legacy fallback",
            document.id,
            document.project_id,
            bool(latest_ver),
        )
    process_job.payload = process_job_payload

    scrubbed_reprocess_payload = dict(payload)
    scrubbed_reprocess_payload.pop("remote_source_url", None)
    job.payload = {
        **scrubbed_reprocess_payload,
        "cleanup_completed": True,
        "process_job_id": process_job.id,
    }
    await session.commit()
    await session.refresh(process_job)
    if job_queue is not None:
        from sag_api.services.document_service import _enqueue_persisted_job

        await _enqueue_persisted_job(job_queue, process_job.id)


async def sync_source(session: AsyncSession, job: Job, *, engine_manager=None, job_queue=None) -> None:
    if not job.source_id:
        raise NotFoundError("Nguồn dữ liệu không tồn tại")
    async with acquire_operation_lease(
        SessionLocal,
        [f"source:{job.source_id}"],
        owner=f"source-sync:{job.id}",
    ):
        await _sync_source_unlocked(session, job, job_queue=job_queue)


async def _sync_source_unlocked(session: AsyncSession, job: Job, *, job_queue=None) -> None:
    """Đồng bộ connector động: discover → fetch → đăng ký tài liệu và xếp hàng xử lý (tái sử dụng pipeline ingest→extract)."""
    # Import trễ để tránh phụ thuộc vòng với package jobs
    from sag_api.connectors import registry
    from sag_api.core.config import settings
    from sag_api.services.document_service import create_document_from_upload

    source = await session.get(Source, job.source_id) if job.source_id else None
    if source is None:
        raise NotFoundError("Nguồn dữ liệu không tồn tại")

    connector = registry.get(source.connector_kind)
    discovered = await connector.discover(source.config or {})
    fetched = 0
    for d in discovered:
        try:
            local = await connector.fetch(source.config or {}, d)
        except Exception as e:  # noqa: BLE001 - Thất bại từng bài không ảnh hưởng đến đồng bộ tổng thể
            log.warning("Đồng bộ thu thập thất bại %s: %s", d.external_id, getattr(e, "message", None) or e)
            continue
        try:
            with open(local.path, "rb") as f:
                data = f.read()
            await create_document_from_upload(
                session,
                source,
                filename=local.filename,
                content_type=local.content_type,
                data=data,
                upload_dir=settings.upload_dir,
                job_queue=job_queue,
            )
        finally:
            try:
                os.remove(local.path)
            except OSError:
                pass
        fetched += 1

    job.progress = 1.0
    job.payload = {**(job.payload or {}), "discovered": len(discovered), "fetched": fetched}
    await session.commit()
    log.info("Đồng bộ hoàn tất source=%s tìm thấy=%d thu thập=%d", source.id, len(discovered), fetched)


async def index_universe(session: AsyncSession, job: Job, *, engine_manager: EngineManager, job_queue=None) -> None:
    """Rebuild one user's aggregate universe overview from authoritative graph data."""
    from sag_api.db.models import User

    user_id = str((job.payload or {}).get("user_id") or "")
    if not user_id or await session.get(User, user_id) is None:
        raise NotFoundError("Người dùng sở hữu knowledge universe không tồn tại")
    job.progress = 0.1
    await session.commit()


async def rebuild_universe_tree(
    session: AsyncSession,
    job: Job,
    *,
    engine_manager: EngineManager,
    job_queue=None,
) -> None:
    from sag_api.core.errors import ServiceUnavailableError
    from sag_api.services.tree_publish_service import TreePublishInProgress, TreeSlotInUse
    from sag_api.services.universe_tree_service import rebuild_project_universe_tree

    payload = job.payload or {}
    tenant_id = payload.get("tenant_id")
    project_ids = payload.get("project_ids")
    if (
        not isinstance(tenant_id, str)
        or not tenant_id.strip()
        or not isinstance(project_ids, list)
        or not project_ids
        or any(not isinstance(project_id, str) or not project_id.strip() for project_id in project_ids)
        or len(set(project_ids)) != len(project_ids)
    ):
        raise ValueError("Universe tree rebuild job scope is invalid")

    results = []
    for index, project_id in enumerate(project_ids):
        from sqlalchemy import and_, or_, select

        from sag_api.db.models import KnowledgeJob, KnowledgeUnit

        ingesting = await session.scalar(select(Job.id).join(Document, Document.id == Job.document_id).where(
            Document.project_id == project_id, Document.tenant_id == tenant_id,
            Job.type.in_((JobType.PROCESS_DOCUMENT, JobType.REPROCESS_DOCUMENT)),
            Job.status.in_((JobStatus.QUEUED, JobStatus.RUNNING)),
        ).limit(1))
        if ingesting:
            raise JobDeferred()
        # Discovery is asynchronous: SEARCH_READY may precede the FOUNDATION row.
        now = datetime.now(UTC)
        undiscovered = await session.scalar(select(DocumentVersion.id).join(Document).where(
            Document.project_id == project_id, Document.tenant_id == tenant_id, Document.is_active.is_(True),
            Document.status.in_((DocumentStatus.PENDING, DocumentStatus.LOADING,
                                 DocumentStatus.EXTRACTING, DocumentStatus.READY)),
            DocumentVersion.search_status.in_(("READY", "SEARCH_READY")),
            DocumentVersion.search_ready_at.is_not(None),
            DocumentVersion.valid_from <= now, DocumentVersion.valid_to > now,
            DocumentVersion.knowledge_status.in_(("NOT_STARTED", "QUEUED")),
        ).limit(1))
        if undiscovered:
            raise JobDeferred()
        pending = await session.scalar(
            select(KnowledgeJob.id)
            .join(DocumentVersion, DocumentVersion.id == KnowledgeJob.document_version_id)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(
                Document.project_id == project_id,
                Document.tenant_id == tenant_id,
                Document.is_active.is_(True),
                DocumentVersion.valid_from <= now,
                DocumentVersion.valid_to > now,
                KnowledgeJob.kind == "FOUNDATION",
                KnowledgeJob.status.in_(("QUEUED", "RUNNING", "RETRY")),
            )
            .limit(1)
        )
        if pending:
            raise JobDeferred()
        try:
            result = await rebuild_project_universe_tree(
                session,
                engine_manager,
                tenant_id=tenant_id,
                project_id=project_id,
            )
        except (TreePublishInProgress, TreeSlotInUse) as error:
            raise JobDeferred() from error
        except ServiceUnavailableError as error:
            if isinstance(error.__cause__, TreePublishInProgress):
                raise JobDeferred() from error
            raise
        results.append(
            {"project_id": project_id, "status": "empty"}
            if result is None
            else {
                "project_id": result.project_id,
                "tree_version": result.tree_version,
                "search_epoch": result.search_epoch,
                "point_count": result.point_count,
            }
        )
        job.progress = 0.9 * (index + 1) / len(project_ids)
        job.payload = {**payload, "results": results}
        await session.commit()
    job.progress = 1.0
    await session.commit()


TASK_HANDLERS: dict[JobType, TaskHandler] = {
    JobType.PROCESS_DOCUMENT: process_document,
    JobType.REPROCESS_DOCUMENT: reprocess_document_task,
    JobType.DELETE_DOCUMENT: delete_document_task,
    JobType.SYNC_SOURCE: sync_source,
    JobType.INDEX_UNIVERSE: index_universe,
    JobType.REBUILD_UNIVERSE_TREE: rebuild_universe_tree,
    JobType.OCTX_PREFLIGHT: preflight_octx,
    JobType.OCTX_IMPORT: import_octx,
    JobType.OCTX_EXPORT: export_octx,
    JobType.OCTX_GC_INSTALLATION: gc_octx_installation,
    JobType.OCTX_GC_TRANSFER: gc_octx_transfers,
}
